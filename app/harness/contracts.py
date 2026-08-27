from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


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
class ModelResult:
    action: ModelAction
    model_name: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


@dataclass(slots=True)
class RuntimeState:
    run_id: str
    student_id: str
    goal: str
    loop_count: int = 0
    tool_call_count: int = 0
    observations: list[dict[str, Any]] = field(default_factory=list)
    last_action_fingerprints: list[str] = field(default_factory=list)


class ModelGateway(Protocol):
    async def decide(self, state: RuntimeState, tools: list[dict[str, Any]]) -> ModelResult: ...


class CheckpointStore(Protocol):
    async def save(self, state: RuntimeState) -> None: ...


class TraceRecorder(Protocol):
    async def record_step(self, state: RuntimeState, result: ModelResult) -> None: ...

    async def record_tool(
        self,
        state: RuntimeState,
        tool_call: ToolCall,
        observation: dict[str, Any],
        latency_ms: int,
    ) -> None: ...
