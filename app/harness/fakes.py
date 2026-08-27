from __future__ import annotations

from collections import deque

from app.harness.contracts import ModelResult, RuntimeState, ToolCall


class ScriptedModelGateway:
    def __init__(self, results: list[ModelResult]) -> None:
        self._results = deque(results)

    async def decide(self, state: RuntimeState, tools: list[dict[str, object]]) -> ModelResult:
        if not self._results:
            raise RuntimeError("scripted model has no result")
        return self._results.popleft()


class MemoryCheckpointStore:
    def __init__(self) -> None:
        self.states: list[RuntimeState] = []

    async def save(self, state: RuntimeState) -> None:
        self.states.append(state)


class MemoryTraceRecorder:
    def __init__(self) -> None:
        self.steps: list[ModelResult] = []
        self.tools: list[tuple[ToolCall, dict[str, object]]] = []

    async def record_step(self, state: RuntimeState, result: ModelResult) -> None:
        self.steps.append(result)

    async def record_tool(
        self,
        state: RuntimeState,
        tool_call: ToolCall,
        observation: dict[str, object],
        latency_ms: int,
    ) -> None:
        self.tools.append((tool_call, observation))
