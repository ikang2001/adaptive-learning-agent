from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol


class TerminationReason(StrEnum):
    COMPLETED = "COMPLETED"
    INVALID_ACTION = "INVALID_ACTION"
    STRUCTURED_OUTPUT_ERROR = "STRUCTURED_OUTPUT_ERROR"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    TOOL_VALIDATION_FAILED = "TOOL_VALIDATION_FAILED"
    TOOL_PERMISSION_DENIED = "TOOL_PERMISSION_DENIED"
    TOOL_FAILED = "TOOL_FAILED"
    TOOL_OUTCOME_UNKNOWN = "TOOL_OUTCOME_UNKNOWN"
    CHECKPOINT_INVALID = "CHECKPOINT_INVALID"
    STEP_BUDGET_EXCEEDED = "STEP_BUDGET_EXCEEDED"
    TOOL_BUDGET_EXCEEDED = "TOOL_BUDGET_EXCEEDED"
    TOKEN_BUDGET_EXCEEDED = "TOKEN_BUDGET_EXCEEDED"
    MODEL_CALL_BUDGET_EXCEEDED = "MODEL_CALL_BUDGET_EXCEEDED"
    REPAIR_BUDGET_EXCEEDED = "REPAIR_BUDGET_EXCEEDED"
    TIME_BUDGET_EXCEEDED = "TIME_BUDGET_EXCEEDED"
    LOOP_STALLED = "LOOP_STALLED"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    CANCELLED = "CANCELLED"
    STALE_WORKER = "STALE_WORKER"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class StallReason(StrEnum):
    REPEATED_ACTION = "REPEATED_ACTION"
    NO_NEW_EVIDENCE = "NO_NEW_EVIDENCE"
    ACTION_OSCILLATION = "ACTION_OSCILLATION"


class RuntimePhase(StrEnum):
    READY = "READY"
    TOOL_PENDING = "TOOL_PENDING"
    FINAL_PENDING = "FINAL_PENDING"
    TERMINATED = "TERMINATED"


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class ModelAction:
    decision: str | None = None
    confidence: float = 0.0
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    tool_call: ToolCall | None = None
    finish: bool = False


@dataclass(frozen=True, slots=True)
class ModelAttempt:
    model_name: str
    purpose: str = "PRIMARY"
    status: str = "SUCCEEDED"
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class ModelResult:
    action: ModelAction
    model_name: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    attempts: tuple[ModelAttempt, ...] = field(default_factory=tuple)
    repair_calls: int = 0
    error_code: str | None = None

    @property
    def model_call_count(self) -> int:
        return len(self.attempts) or 1


@dataclass(slots=True)
class RuntimeState:
    run_id: str
    student_id: str
    goal: str
    user_id: str | None = None
    roles: tuple[str, ...] = field(default_factory=tuple)
    loop_count: int = 0
    model_call_count: int = 0
    tool_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    repair_call_count: int = 0
    model_call_limit: int = 10
    repair_call_limit: int = 1
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    observations: list[dict[str, Any]] = field(default_factory=list)
    last_action_fingerprints: list[str] = field(default_factory=list)
    last_observation_fingerprints: list[str] = field(default_factory=list)
    checkpoint_version: int = 2
    fencing_token: int = 0
    resumed: bool = False
    phase: RuntimePhase = RuntimePhase.READY
    pending_tool_call: ToolCall | None = None
    pending_step_id: str | None = None
    final_action: ModelAction | None = None
    stall_reason: StallReason | None = None
    termination_reason: TerminationReason | None = None
    last_error_code: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def remaining_model_calls(self) -> int:
        return max(0, self.model_call_limit - self.model_call_count)

    @property
    def remaining_repair_calls(self) -> int:
        return max(0, self.repair_call_limit - self.repair_call_count)


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    observation: dict[str, Any]
    retry_count: int = 0
    replayed: bool = False


class ModelGateway(Protocol):
    async def decide(self, state: RuntimeState, tools: list[dict[str, Any]]) -> ModelResult: ...


class CheckpointStore(Protocol):
    async def save(self, state: RuntimeState) -> None: ...

    async def load_latest(self) -> RuntimeState | None: ...


class TraceRecorder(Protocol):
    async def record_step(self, state: RuntimeState, result: ModelResult) -> str: ...

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
    ) -> None: ...

    async def record_guardrail(
        self,
        state: RuntimeState,
        step_id: str | None,
        tool_name: str | None,
        decision: str,
        reason_code: str,
    ) -> None: ...


class RunControl(Protocol):
    async def assert_active(self, state: RuntimeState) -> None: ...


class NoopRunControl:
    async def assert_active(self, state: RuntimeState) -> None:
        del state
