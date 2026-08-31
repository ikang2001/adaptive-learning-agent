from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from app.harness.contracts import (
    ModelAction,
    RuntimePhase,
    RuntimeState,
    StallReason,
    TerminationReason,
    ToolCall,
)
from app.harness.errors import CheckpointValidationError, StructuredOutputError


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ToolCallPayload(StrictPayload):
    name: str = Field(min_length=1, max_length=96, pattern=r"^[a-z][a-z0-9_]*$")
    arguments: dict[str, Any]
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class FinalDecisionPayload(StrictPayload):
    decision: str = Field(min_length=1, max_length=64)
    confidence: float = Field(ge=0, le=1)
    reason_codes: list[str] = Field(default_factory=list, max_length=32)
    finish: Literal[True]


class EvidenceRef(StrictPayload):
    evidence_type: str = Field(min_length=1, max_length=64)
    source_id: str = Field(min_length=1, max_length=128)
    version: str | None = Field(default=None, max_length=64)
    summary: str | None = Field(default=None, max_length=500)


class StoredToolCall(StrictPayload):
    name: str
    arguments: dict[str, Any]
    idempotency_key: str | None = None


class StoredModelAction(StrictPayload):
    decision: str | None = None
    confidence: float = Field(default=0, ge=0, le=1)
    reason_codes: list[str] = Field(default_factory=list)
    tool_call: StoredToolCall | None = None
    finish: bool = False

    @model_validator(mode="after")
    def validate_shape(self) -> StoredModelAction:
        if self.finish == (self.tool_call is not None):
            raise ValueError("action must contain exactly one final decision or tool call")
        if self.finish and self.decision is None:
            raise ValueError("final action requires decision")
        return self


class RuntimeStatePayload(StrictPayload):
    model_config = ConfigDict(extra="forbid", strict=False)

    run_id: str
    student_id: str
    goal: str
    user_id: str | None = None
    roles: list[str] = Field(default_factory=list)
    loop_count: int = Field(ge=0)
    model_call_count: int = Field(ge=0)
    tool_call_count: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    repair_call_count: int = Field(ge=0)
    model_call_limit: int = Field(ge=1)
    repair_call_limit: int = Field(ge=0)
    started_at: datetime
    observations: list[dict[str, Any]]
    last_action_fingerprints: list[str]
    last_observation_fingerprints: list[str]
    checkpoint_version: Literal[2]
    fencing_token: int = Field(ge=0)
    resumed: bool
    phase: RuntimePhase
    pending_tool_call: StoredToolCall | None = None
    pending_step_id: str | None = None
    final_action: StoredModelAction | None = None
    stall_reason: StallReason | None = None
    termination_reason: TerminationReason | None = None
    last_error_code: str | None = None

    @field_validator("started_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("started_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_phase(self) -> RuntimeStatePayload:
        if self.phase is RuntimePhase.TOOL_PENDING and self.pending_tool_call is None:
            raise ValueError("TOOL_PENDING requires pending_tool_call")
        if self.phase is RuntimePhase.FINAL_PENDING and self.final_action is None:
            raise ValueError("FINAL_PENDING requires final_action")
        return self


def parse_tool_call(name: object, arguments: object, idempotency_key: object = None) -> ToolCall:
    try:
        payload = ToolCallPayload.model_validate(
            {"name": name, "arguments": arguments, "idempotency_key": idempotency_key}
        )
    except (TypeError, ValueError) as exc:
        raise StructuredOutputError("model tool call failed schema validation") from exc
    return ToolCall(payload.name, payload.arguments, payload.idempotency_key)


def parse_final_decision(value: object) -> ModelAction:
    try:
        payload = FinalDecisionPayload.model_validate(value)
    except (TypeError, ValueError) as exc:
        raise StructuredOutputError("model final decision failed schema validation") from exc
    return ModelAction(
        decision=payload.decision,
        confidence=payload.confidence,
        reason_codes=tuple(payload.reason_codes),
        finish=True,
    )


def serialize_runtime_state(state: RuntimeState) -> dict[str, Any]:
    payload = RuntimeStatePayload(
        run_id=state.run_id,
        student_id=state.student_id,
        goal=state.goal,
        user_id=state.user_id,
        roles=list(state.roles),
        loop_count=state.loop_count,
        model_call_count=state.model_call_count,
        tool_call_count=state.tool_call_count,
        input_tokens=state.input_tokens,
        output_tokens=state.output_tokens,
        repair_call_count=state.repair_call_count,
        model_call_limit=state.model_call_limit,
        repair_call_limit=state.repair_call_limit,
        started_at=state.started_at,
        observations=state.observations,
        last_action_fingerprints=state.last_action_fingerprints,
        last_observation_fingerprints=state.last_observation_fingerprints,
        checkpoint_version=2,
        fencing_token=state.fencing_token,
        resumed=state.resumed,
        phase=state.phase,
        pending_tool_call=_stored_tool(state.pending_tool_call),
        pending_step_id=state.pending_step_id,
        final_action=_stored_action(state.final_action),
        stall_reason=state.stall_reason,
        termination_reason=state.termination_reason,
        last_error_code=state.last_error_code,
    )
    return payload.model_dump(mode="json")


def deserialize_runtime_state(value: object) -> RuntimeState:
    try:
        payload = TypeAdapter(RuntimeStatePayload).validate_python(value)
    except (TypeError, ValueError) as exc:
        raise CheckpointValidationError("checkpoint state failed schema validation") from exc
    return RuntimeState(
        run_id=payload.run_id,
        student_id=payload.student_id,
        goal=payload.goal,
        user_id=payload.user_id,
        roles=tuple(payload.roles),
        loop_count=payload.loop_count,
        model_call_count=payload.model_call_count,
        tool_call_count=payload.tool_call_count,
        input_tokens=payload.input_tokens,
        output_tokens=payload.output_tokens,
        repair_call_count=payload.repair_call_count,
        model_call_limit=payload.model_call_limit,
        repair_call_limit=payload.repair_call_limit,
        started_at=payload.started_at,
        observations=payload.observations,
        last_action_fingerprints=payload.last_action_fingerprints,
        last_observation_fingerprints=payload.last_observation_fingerprints,
        checkpoint_version=payload.checkpoint_version,
        fencing_token=payload.fencing_token,
        resumed=True,
        phase=payload.phase,
        pending_tool_call=_tool_from_stored(payload.pending_tool_call),
        pending_step_id=payload.pending_step_id,
        final_action=_action_from_stored(payload.final_action),
        stall_reason=payload.stall_reason,
        termination_reason=payload.termination_reason,
        last_error_code=payload.last_error_code,
    )


def _stored_tool(value: ToolCall | None) -> StoredToolCall | None:
    if value is None:
        return None
    return StoredToolCall(
        name=value.name, arguments=value.arguments, idempotency_key=value.idempotency_key
    )


def _stored_action(value: ModelAction | None) -> StoredModelAction | None:
    if value is None:
        return None
    return StoredModelAction(
        decision=value.decision,
        confidence=value.confidence,
        reason_codes=list(value.reason_codes),
        tool_call=_stored_tool(value.tool_call),
        finish=value.finish,
    )


def _tool_from_stored(value: StoredToolCall | None) -> ToolCall | None:
    if value is None:
        return None
    return ToolCall(value.name, value.arguments, value.idempotency_key)


def _action_from_stored(value: StoredModelAction | None) -> ModelAction | None:
    if value is None:
        return None
    return ModelAction(
        decision=value.decision,
        confidence=value.confidence,
        reason_codes=tuple(value.reason_codes),
        tool_call=_tool_from_stored(value.tool_call),
        finish=value.finish,
    )
