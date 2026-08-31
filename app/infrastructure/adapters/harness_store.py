from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from opentelemetry import trace
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.harness.contracts import ModelResult, RuntimeState, ToolCall
from app.harness.errors import CheckpointValidationError
from app.harness.lease import assert_fence
from app.harness.schemas import deserialize_runtime_state, serialize_runtime_state
from app.infrastructure.db.models import (
    AgentStep,
    Checkpoint,
    GuardrailEvent,
    ModelInvocation,
    ToolInvocation,
)
from app.observability.metrics import AGENT_CHECKPOINT_SAVES, AGENT_GUARDRAIL_BLOCK

tracer = trace.get_tracer(__name__)


class DatabaseCheckpointStore:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        run_id: uuid.UUID,
        fencing_token: int,
    ) -> None:
        self._factory = factory
        self._run_id = run_id
        self._fencing_token = fencing_token

    async def save(self, state: RuntimeState) -> None:
        with tracer.start_as_current_span(
            "checkpoint.save", attributes={"agent.step": state.loop_count}
        ):
            serialized = serialize_runtime_state(state)
            state_hash = self._digest(serialized)
            async with self._factory() as session:
                await assert_fence(session, self._run_id, self._fencing_token)
                checkpoint = await session.scalar(
                    select(Checkpoint)
                    .where(
                        Checkpoint.run_id == self._run_id,
                        Checkpoint.step_number == state.loop_count,
                    )
                    .with_for_update()
                )
                if checkpoint is None:
                    checkpoint = Checkpoint(
                        run_id=self._run_id,
                        step_number=state.loop_count,
                        state=serialized,
                        checkpoint_version=state.checkpoint_version,
                        state_hash=state_hash,
                        resume_safe=True,
                        fencing_token=self._fencing_token,
                    )
                    session.add(checkpoint)
                else:
                    checkpoint.state = serialized
                    checkpoint.checkpoint_version = state.checkpoint_version
                    checkpoint.state_hash = state_hash
                    checkpoint.resume_safe = True
                    checkpoint.fencing_token = self._fencing_token
                await session.commit()
                AGENT_CHECKPOINT_SAVES.inc()

    async def load_latest(self) -> RuntimeState | None:
        with tracer.start_as_current_span("checkpoint.load_latest"):
            async with self._factory() as session:
                await assert_fence(session, self._run_id, self._fencing_token)
                checkpoint = await session.scalar(
                    select(Checkpoint)
                    .where(Checkpoint.run_id == self._run_id, Checkpoint.resume_safe.is_(True))
                    .order_by(Checkpoint.step_number.desc(), Checkpoint.created_at.desc())
                    .limit(1)
                )
        if checkpoint is None:
            return None
        if checkpoint.checkpoint_version != 2:
            raise CheckpointValidationError("checkpoint version is not supported")
        if checkpoint.state_hash != self._digest(checkpoint.state):
            raise CheckpointValidationError("checkpoint state hash does not match")
        state = deserialize_runtime_state(checkpoint.state)
        if state.run_id != str(self._run_id):
            raise CheckpointValidationError("checkpoint run identity does not match")
        return state

    @staticmethod
    def _digest(value: object) -> str:
        serialized = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        )
        return hashlib.sha256(serialized.encode()).hexdigest()


class DatabaseTraceRecorder:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        run_id: uuid.UUID,
        fencing_token: int,
    ) -> None:
        self._factory = factory
        self._run_id = run_id
        self._fencing_token = fencing_token

    async def record_step(self, state: RuntimeState, result: ModelResult) -> str:
        action_json = self._action_json(result)
        async with self._factory() as session:
            run = await assert_fence(session, self._run_id, self._fencing_token)
            step = await session.scalar(
                select(AgentStep)
                .where(
                    AgentStep.run_id == self._run_id,
                    AgentStep.step_number == state.loop_count,
                )
                .with_for_update()
            )
            if step is None:
                step = AgentStep(
                    run_id=self._run_id,
                    step_number=state.loop_count,
                    action=action_json,
                    observation_digest=None,
                    model_name=result.model_name,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    latency_ms=result.latency_ms,
                    prompt_version=run.prompt_version,
                    policy_version=run.policy_version,
                    action_type=(
                        "MODEL_ERROR"
                        if result.error_code
                        else "FINAL"
                        if result.action.finish
                        else "TOOL_CALL"
                    ),
                    decision=result.action.decision,
                    confidence=result.action.confidence,
                    reason_codes=list(result.action.reason_codes),
                    stall_reason=state.stall_reason.value if state.stall_reason else None,
                )
                session.add(step)
                await session.flush()
            else:
                step.action = action_json
                step.model_name = result.model_name
                step.input_tokens += result.input_tokens
                step.output_tokens += result.output_tokens
                step.latency_ms += result.latency_ms
                step.action_type = (
                    "MODEL_ERROR"
                    if result.error_code
                    else "FINAL"
                    if result.action.finish
                    else "TOOL_CALL"
                )
                step.decision = result.action.decision
                step.confidence = result.action.confidence
                step.reason_codes = list(result.action.reason_codes)
            current_attempt = int(
                await session.scalar(
                    select(func.max(ModelInvocation.attempt_number)).where(
                        ModelInvocation.step_id == step.id
                    )
                )
                or 0
            )
            attempts = result.attempts or ()
            if not attempts:
                from app.harness.contracts import ModelAttempt

                attempts = (
                    ModelAttempt(
                        result.model_name,
                        input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens,
                        latency_ms=result.latency_ms,
                    ),
                )
            for offset, attempt in enumerate(attempts, start=1):
                is_last = offset == len(attempts)
                session.add(
                    ModelInvocation(
                        run_id=self._run_id,
                        step_id=step.id,
                        attempt_number=current_attempt + offset,
                        purpose=attempt.purpose,
                        model_name=attempt.model_name,
                        status=attempt.status,
                        input_tokens=attempt.input_tokens,
                        output_tokens=attempt.output_tokens,
                        latency_ms=attempt.latency_ms,
                        error_code=attempt.error_code,
                        response_action=action_json if is_last else None,
                    )
                )
            await session.commit()
            return str(step.id)

    async def record_tool(
        self,
        state: RuntimeState,
        step_id: str,
        tool_call: ToolCall,
        observation: dict[str, Any],
        latency_ms: int,
        retry_count: int = 0,
        status: str = "SUCCEEDED",
        error_code: str | None = None,
        replayed: bool = False,
    ) -> None:
        del state
        parsed_step_id = uuid.UUID(step_id)
        async with self._factory() as session:
            await assert_fence(session, self._run_id, self._fencing_token)
            invocation = await session.scalar(
                select(ToolInvocation)
                .where(
                    ToolInvocation.run_id == self._run_id,
                    ToolInvocation.step_id == parsed_step_id,
                )
                .with_for_update()
            )
            if invocation is None:
                invocation = ToolInvocation(
                    run_id=self._run_id,
                    step_id=parsed_step_id,
                    tool_name=tool_call.name,
                    tool_version="v1",
                    args_digest=self._digest(tool_call.arguments),
                    observation_digest=self._safe_observation(observation),
                    status=status,
                    latency_ms=latency_ms,
                    idempotency_key=tool_call.idempotency_key,
                    risk="PROPOSAL" if tool_call.name.startswith("propose_") else "READ",
                    retry_count=retry_count,
                    error_code=error_code,
                    replayed=replayed,
                )
                session.add(invocation)
            else:
                invocation.observation_digest = self._safe_observation(observation)
                invocation.status = status
                invocation.latency_ms += latency_ms
                invocation.retry_count += retry_count
                invocation.error_code = error_code
                invocation.replayed = invocation.replayed or replayed
            await session.commit()

    async def record_guardrail(
        self,
        state: RuntimeState,
        step_id: str | None,
        tool_name: str | None,
        decision: str,
        reason_code: str,
    ) -> None:
        del state
        async with self._factory() as session:
            run = await assert_fence(session, self._run_id, self._fencing_token)
            session.add(
                GuardrailEvent(
                    run_id=self._run_id,
                    step_id=uuid.UUID(step_id) if step_id else None,
                    tool_name=tool_name,
                    policy_version=run.policy_version,
                    decision=decision,
                    reason_code=reason_code,
                )
            )
            await session.commit()
            if decision == "BLOCKED":
                AGENT_GUARDRAIL_BLOCK.labels(reason_code).inc()

    @staticmethod
    def _action_json(result: ModelResult) -> dict[str, Any]:
        action = result.action
        return {
            "decision": action.decision,
            "confidence": action.confidence,
            "reason_codes": list(action.reason_codes),
            "tool_name": action.tool_call.name if action.tool_call else None,
            "finish": action.finish,
            "error_code": result.error_code,
        }

    @staticmethod
    def _digest(value: object) -> str:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()

    @staticmethod
    def _safe_observation(value: object) -> str:
        serialized = json.dumps(
            _redact_sensitive(value), ensure_ascii=False, sort_keys=True, default=str
        )
        return serialized[:4000]


def _redact_sensitive(value: object) -> object:
    sensitive = ("phone", "token", "secret", "password", "api_key")
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if any(part in str(key).lower() for part in sensitive)
                else _redact_sensitive(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    return value
