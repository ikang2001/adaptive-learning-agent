from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.harness.contracts import ModelResult, RuntimeState, ToolCall
from app.infrastructure.db.models import AgentStep, Checkpoint, ToolInvocation


class DatabaseCheckpointStore:
    def __init__(self, factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID) -> None:
        self._factory = factory
        self._run_id = run_id

    async def save(self, state: RuntimeState) -> None:
        async with self._factory() as session:
            checkpoint = Checkpoint(
                run_id=self._run_id,
                step_number=state.loop_count,
                state={
                    "run_id": state.run_id,
                    "student_id": state.student_id,
                    "goal": state.goal,
                    "loop_count": state.loop_count,
                    "tool_call_count": state.tool_call_count,
                    "observations": state.observations,
                    "last_action_fingerprints": state.last_action_fingerprints,
                },
            )
            session.add(checkpoint)
            await session.commit()


class DatabaseTraceRecorder:
    def __init__(self, factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID) -> None:
        self._factory = factory
        self._run_id = run_id
        self._last_step_id: uuid.UUID | None = None

    async def record_step(self, state: RuntimeState, result: ModelResult) -> None:
        async with self._factory() as session:
            step = AgentStep(
                run_id=self._run_id,
                step_number=state.loop_count,
                action=self._action_json(result),
                observation_digest=None,
                model_name=result.model_name,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                latency_ms=result.latency_ms,
            )
            session.add(step)
            await session.commit()
            self._last_step_id = step.id

    async def record_tool(
        self,
        state: RuntimeState,
        tool_call: ToolCall,
        observation: dict[str, Any],
        latency_ms: int,
    ) -> None:
        if self._last_step_id is None:
            raise RuntimeError("tool trace requires a preceding agent step")
        async with self._factory() as session:
            session.add(
                ToolInvocation(
                    run_id=self._run_id,
                    step_id=self._last_step_id,
                    tool_name=tool_call.name,
                    tool_version="v1",
                    args_digest=self._digest(tool_call.arguments),
                    observation_digest=self._safe_observation(observation),
                    status="SUCCEEDED",
                    latency_ms=latency_ms,
                    idempotency_key=tool_call.idempotency_key,
                )
            )
            await session.commit()

    @staticmethod
    def _action_json(result: ModelResult) -> dict[str, Any]:
        action = result.action
        return {
            "decision": action.decision,
            "confidence": action.confidence,
            "reason_codes": list(action.reason_codes),
            "tool_name": action.tool_call.name if action.tool_call else None,
            "finish": action.finish,
        }

    @staticmethod
    def _digest(value: object) -> str:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()

    @staticmethod
    def _safe_observation(value: object) -> str:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return serialized[:4000]
