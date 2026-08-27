from __future__ import annotations

import time
from dataclasses import dataclass

from app.harness.contracts import (
    CheckpointStore,
    ModelGateway,
    RuntimeState,
    TraceRecorder,
)
from app.harness.tools import ToolExecutor, ToolRegistry, action_fingerprint


@dataclass(frozen=True, slots=True)
class AgentRunnerConfig:
    max_steps: int = 8
    max_tool_calls: int = 12
    max_runtime_seconds: int = 600


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    decision: str | None
    confidence: float
    reason_codes: tuple[str, ...]
    termination_reason: str
    state: RuntimeState


class AgentRunner:
    def __init__(
        self,
        model: ModelGateway,
        registry: ToolRegistry,
        executor: ToolExecutor,
        checkpoints: CheckpointStore,
        trace: TraceRecorder,
        config: AgentRunnerConfig | None = None,
    ) -> None:
        self._model = model
        self._registry = registry
        self._executor = executor
        self._checkpoints = checkpoints
        self._trace = trace
        self._config = config or AgentRunnerConfig()

    async def run(self, state: RuntimeState) -> AgentRunResult:
        started = time.monotonic()
        while state.loop_count < self._config.max_steps:
            if time.monotonic() - started >= self._config.max_runtime_seconds:
                return self._result(state, "TIME_BUDGET_EXCEEDED")
            model_result = await self._model.decide(state, self._registry.model_tools())
            state.loop_count += 1
            await self._trace.record_step(state, model_result)
            if model_result.action.finish:
                await self._checkpoints.save(state)
                return AgentRunResult(
                    decision=model_result.action.decision,
                    confidence=model_result.action.confidence,
                    reason_codes=model_result.action.reason_codes,
                    termination_reason="COMPLETED",
                    state=state,
                )
            call = model_result.action.tool_call
            if call is None:
                await self._checkpoints.save(state)
                return self._result(state, "INVALID_ACTION")
            if state.tool_call_count >= self._config.max_tool_calls:
                return self._result(state, "TOOL_BUDGET_EXCEEDED")
            fingerprint = action_fingerprint(call)
            state.last_action_fingerprints.append(fingerprint)
            if state.last_action_fingerprints[-2:] == [fingerprint, fingerprint]:
                await self._checkpoints.save(state)
                return self._result(state, "LOOP_STALLED")
            tool_started = time.monotonic()
            observation = await self._executor.execute(call)
            state.tool_call_count += 1
            state.observations.append({"tool": call.name, "result": observation})
            await self._trace.record_tool(
                state,
                call,
                observation,
                round((time.monotonic() - tool_started) * 1000),
            )
            await self._checkpoints.save(state)
        return self._result(state, "MAX_STEPS")

    @staticmethod
    def _result(state: RuntimeState, reason: str) -> AgentRunResult:
        return AgentRunResult(None, 0, (), reason, state)
