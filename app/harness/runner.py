from __future__ import annotations

import time
from dataclasses import dataclass

import structlog
from opentelemetry import trace

from app.harness.budget import BudgetManager, RunBudget
from app.harness.contracts import (
    CheckpointStore,
    ModelAction,
    ModelGateway,
    NoopRunControl,
    RunControl,
    RuntimePhase,
    RuntimeState,
    TerminationReason,
    TraceRecorder,
)
from app.harness.errors import (
    AgentHarnessError,
    BudgetExceededError,
    ModelUnavailableError,
    StaleWorkerError,
    StructuredOutputError,
    ToolError,
)
from app.harness.termination import StallDetector
from app.harness.tools import ToolAvailabilityContext, ToolExecutor, ToolRegistry
from app.observability.metrics import (
    AGENT_BUDGET_EXCEEDED,
    AGENT_LOOP_STALLED,
    AGENT_MODEL_CALLS,
    AGENT_MODEL_INPUT_TOKENS,
    AGENT_MODEL_LATENCY,
    AGENT_MODEL_OUTPUT_TOKENS,
    AGENT_RUNS,
    AGENT_STEPS_PER_RUN,
    AGENT_TOKENS_PER_RUN,
    AGENT_TOOL_CALLS,
    AGENT_TOOL_CALLS_PER_RUN,
    AGENT_TOOL_LATENCY,
    AGENT_TOOL_RETRIES,
)

tracer = trace.get_tracer(__name__)
logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AgentRunnerConfig:
    max_steps: int = 8
    max_tool_calls: int = 12
    max_runtime_seconds: int = 600
    max_model_calls: int = 10
    max_input_tokens: int = 64_000
    max_output_tokens: int = 8_192
    max_total_tokens: int = 72_192
    max_repair_calls: int = 1

    def as_budget(self) -> RunBudget:
        return RunBudget(
            max_steps=self.max_steps,
            max_model_calls=self.max_model_calls,
            max_tool_calls=self.max_tool_calls,
            max_input_tokens=self.max_input_tokens,
            max_output_tokens=self.max_output_tokens,
            max_total_tokens=self.max_total_tokens,
            max_runtime_seconds=self.max_runtime_seconds,
            max_repair_calls=self.max_repair_calls,
        )


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
        *,
        control: RunControl | None = None,
        stall_detector: StallDetector | None = None,
        tool_context: ToolAvailabilityContext | None = None,
    ) -> None:
        self._model = model
        self._registry = registry
        self._executor = executor
        self._checkpoints = checkpoints
        self._trace = trace
        self._config = config or AgentRunnerConfig()
        self._budget = BudgetManager(self._config.as_budget())
        self._control = control or NoopRunControl()
        self._stall = stall_detector or StallDetector()
        self._tool_context = tool_context or ToolAvailabilityContext()

    async def run(self, state: RuntimeState) -> AgentRunResult:
        state.model_call_limit = self._config.max_model_calls
        state.repair_call_limit = self._config.max_repair_calls
        with tracer.start_as_current_span(
            "agent.run", attributes={"agent.goal": state.goal, "agent.resumed": state.resumed}
        ) as span:
            result = await self._run(state)
            span.set_attribute("agent.termination_reason", result.termination_reason)
            span.set_attribute("agent.steps", result.state.loop_count)
            span.set_attribute("agent.tool_calls", result.state.tool_call_count)
            span.set_attribute("agent.total_tokens", result.state.total_tokens)
        AGENT_RUNS.labels(
            "COMPLETED" if result.termination_reason == "COMPLETED" else "TERMINATED",
            result.termination_reason,
        ).inc()
        AGENT_STEPS_PER_RUN.observe(result.state.loop_count)
        AGENT_TOOL_CALLS_PER_RUN.observe(result.state.tool_call_count)
        AGENT_TOKENS_PER_RUN.observe(result.state.total_tokens)
        if result.termination_reason == TerminationReason.LOOP_STALLED:
            reason = result.state.stall_reason.value if result.state.stall_reason else "UNKNOWN"
            AGENT_LOOP_STALLED.labels(reason).inc()
        if "BUDGET_EXCEEDED" in result.termination_reason:
            AGENT_BUDGET_EXCEEDED.labels(result.termination_reason).inc()
        return result

    async def _run(self, state: RuntimeState) -> AgentRunResult:
        try:
            if state.phase is RuntimePhase.TERMINATED and state.termination_reason is not None:
                if (
                    state.termination_reason is TerminationReason.COMPLETED
                    and state.final_action is not None
                ):
                    return AgentRunResult(
                        state.final_action.decision,
                        state.final_action.confidence,
                        state.final_action.reason_codes,
                        state.termination_reason.value,
                        state,
                    )
                return self._result(state, state.termination_reason)
            if state.phase is RuntimePhase.FINAL_PENDING and state.final_action is not None:
                return await self._complete(state)
            if state.phase is RuntimePhase.TOOL_PENDING:
                stalled = await self._execute_pending_tool(state)
                if stalled:
                    return await self._terminate(state, TerminationReason.LOOP_STALLED)
                if state.final_action is not None:
                    return await self._complete(state)

            while True:
                await self._control.assert_active(state)
                self._budget.check_before_model(state)
                model_started = time.monotonic()
                with tracer.start_as_current_span(
                    "model.decide", attributes={"agent.step": state.loop_count + 1}
                ) as span:
                    try:
                        result = await self._model.decide(
                            state, self._registry.model_tools(self._tool_context)
                        )
                    except (StructuredOutputError, ModelUnavailableError) as exc:
                        await self._record_model_failure(state, exc)
                        raise
                    span.set_attribute("agent.model", result.model_name)
                    span.set_attribute("model.input_tokens", result.input_tokens)
                    span.set_attribute("model.output_tokens", result.output_tokens)
                for attempt in result.attempts:
                    AGENT_MODEL_CALLS.labels(attempt.model_name, attempt.status).inc()
                    AGENT_MODEL_LATENCY.labels(attempt.model_name).observe(
                        attempt.latency_ms / 1000
                    )
                    AGENT_MODEL_INPUT_TOKENS.labels(attempt.model_name).inc(attempt.input_tokens)
                    AGENT_MODEL_OUTPUT_TOKENS.labels(attempt.model_name).inc(attempt.output_tokens)
                if not result.attempts:
                    AGENT_MODEL_CALLS.labels(result.model_name, "SUCCEEDED").inc()
                    AGENT_MODEL_LATENCY.labels(result.model_name).observe(
                        time.monotonic() - model_started
                    )
                state.loop_count += 1
                state.model_call_count += result.model_call_count
                state.input_tokens += result.input_tokens
                state.output_tokens += result.output_tokens
                state.repair_call_count += result.repair_calls
                self._budget.check_after_model(state)
                step_id = await self._trace.record_step(state, result)

                action = result.action
                if action.finish:
                    if action.tool_call is not None or action.decision is None:
                        return await self._terminate(state, TerminationReason.INVALID_ACTION)
                    state.final_action = action
                    state.pending_step_id = step_id
                    state.phase = RuntimePhase.FINAL_PENDING
                    await self._checkpoints.save(state)
                    return await self._complete(state)

                call = action.tool_call
                if call is None:
                    return await self._terminate(state, TerminationReason.INVALID_ACTION)
                stall_reason = self._stall.record_action(state, call)
                if stall_reason is not None:
                    state.stall_reason = stall_reason
                    await self._checkpoints.save(state)
                    return await self._terminate(state, TerminationReason.LOOP_STALLED)
                self._budget.check_before_tool(state)
                state.pending_tool_call = call
                state.pending_step_id = step_id
                state.phase = RuntimePhase.TOOL_PENDING
                await self._checkpoints.save(state)
                if await self._execute_pending_tool(state):
                    return await self._terminate(state, TerminationReason.LOOP_STALLED)
                if state.phase is RuntimePhase.FINAL_PENDING:
                    return await self._complete(state)
        except BudgetExceededError as exc:
            state.last_error_code = exc.code
            return await self._terminate(state, exc.termination_reason)
        except StaleWorkerError:
            state.last_error_code = "STALE_WORKER"
            return self._result(state, TerminationReason.STALE_WORKER)
        except ToolError as exc:
            state.last_error_code = exc.code
            await self._record_tool_failure(state, exc)
            if exc.termination_reason in {
                TerminationReason.TOOL_PERMISSION_DENIED,
                TerminationReason.UNKNOWN_TOOL,
            }:
                reason_code = str(exc.detail.get("reason_code", exc.code))
                await self._trace.record_guardrail(
                    state,
                    state.pending_step_id,
                    state.pending_tool_call.name if state.pending_tool_call else None,
                    "BLOCKED",
                    reason_code,
                )
            return await self._terminate(state, exc.termination_reason)
        except AgentHarnessError as exc:
            state.last_error_code = exc.code
            return await self._terminate(state, exc.termination_reason)
        except Exception as exc:
            state.last_error_code = type(exc).__name__
            logger.exception(
                "agent_run_internal_error",
                run_id=state.run_id,
                step=state.loop_count,
                error_type=type(exc).__name__,
            )
            return await self._terminate(state, TerminationReason.INTERNAL_ERROR)

    async def _execute_pending_tool(self, state: RuntimeState) -> bool:
        call = state.pending_tool_call
        step_id = state.pending_step_id
        if call is None or step_id is None:
            raise RuntimeError("pending tool state is incomplete")
        await self._control.assert_active(state)
        self._budget.check_before_tool(state)
        started = time.monotonic()
        definition = self._registry.get(call.name, self._tool_context)
        with tracer.start_as_current_span(
            "tool.execute",
            attributes={
                "tool.name": call.name,
                "tool.version": definition.version,
                "tool.risk": definition.risk.value,
                "agent.step": state.loop_count,
            },
        ):
            execution = await self._executor.execute(state, call)
        state.tool_call_count += 1
        observation = {
            "tool": call.name,
            "result": execution.observation,
            "replayed": execution.replayed,
        }
        state.observations.append(observation)
        await self._trace.record_tool(
            state,
            step_id,
            call,
            execution.observation,
            round((time.monotonic() - started) * 1000),
            retry_count=execution.retry_count,
            replayed=execution.replayed,
        )
        AGENT_TOOL_CALLS.labels(call.name, "SUCCEEDED").inc()
        AGENT_TOOL_LATENCY.labels(call.name).observe(time.monotonic() - started)
        if execution.retry_count:
            AGENT_TOOL_RETRIES.labels(call.name).inc(execution.retry_count)
        state.pending_tool_call = None
        state.pending_step_id = None
        if definition.terminal_decision:
            confidence = call.arguments.get("confidence", 0.0)
            reason_codes = call.arguments.get("reason_codes", [])
            state.final_action = ModelAction(
                decision=definition.terminal_decision,
                confidence=float(confidence) if isinstance(confidence, (int, float)) else 0.0,
                reason_codes=tuple(str(item) for item in reason_codes)
                if isinstance(reason_codes, list)
                else (),
                finish=True,
            )
            state.phase = RuntimePhase.FINAL_PENDING
            stall_reason = None
        else:
            state.phase = RuntimePhase.READY
            stall_reason = self._stall.record_observation(state, observation)
        if stall_reason is not None:
            state.stall_reason = stall_reason
        await self._control.assert_active(state)
        await self._checkpoints.save(state)
        return stall_reason is not None

    async def _record_tool_failure(self, state: RuntimeState, exc: ToolError) -> None:
        if state.pending_tool_call is None or state.pending_step_id is None:
            return
        await self._trace.record_tool(
            state,
            state.pending_step_id,
            state.pending_tool_call,
            {},
            0,
            status="FAILED",
            error_code=exc.code,
        )
        AGENT_TOOL_CALLS.labels(state.pending_tool_call.name, "FAILED").inc()

    async def _record_model_failure(
        self,
        state: RuntimeState,
        exc: StructuredOutputError | ModelUnavailableError,
    ) -> None:
        from app.harness.contracts import ModelAction, ModelResult

        attempts = exc.attempts
        state.loop_count += 1
        state.model_call_count += len(attempts)
        state.input_tokens += sum(item.input_tokens for item in attempts)
        state.output_tokens += sum(item.output_tokens for item in attempts)
        if isinstance(exc, StructuredOutputError) and len(attempts) > 1:
            state.repair_call_count += len(attempts) - 1
        model_name = attempts[-1].model_name if attempts else "unknown"
        for attempt in attempts:
            AGENT_MODEL_CALLS.labels(attempt.model_name, attempt.status).inc()
            AGENT_MODEL_LATENCY.labels(attempt.model_name).observe(attempt.latency_ms / 1000)
            AGENT_MODEL_INPUT_TOKENS.labels(attempt.model_name).inc(attempt.input_tokens)
            AGENT_MODEL_OUTPUT_TOKENS.labels(attempt.model_name).inc(attempt.output_tokens)
        await self._trace.record_step(
            state,
            ModelResult(
                ModelAction(),
                model_name,
                input_tokens=sum(item.input_tokens for item in attempts),
                output_tokens=sum(item.output_tokens for item in attempts),
                latency_ms=sum(item.latency_ms for item in attempts),
                attempts=attempts,
                repair_calls=max(0, len(attempts) - 1),
                error_code=exc.code,
            ),
        )

    async def _complete(self, state: RuntimeState) -> AgentRunResult:
        await self._control.assert_active(state)
        action = state.final_action
        if action is None:
            return await self._terminate(state, TerminationReason.INVALID_ACTION)
        state.phase = RuntimePhase.TERMINATED
        state.termination_reason = TerminationReason.COMPLETED
        state.last_error_code = None
        await self._checkpoints.save(state)
        return AgentRunResult(
            decision=action.decision,
            confidence=action.confidence,
            reason_codes=action.reason_codes,
            termination_reason=TerminationReason.COMPLETED.value,
            state=state,
        )

    async def _terminate(self, state: RuntimeState, reason: TerminationReason) -> AgentRunResult:
        state.phase = RuntimePhase.TERMINATED
        state.termination_reason = reason
        try:
            await self._checkpoints.save(state)
        except StaleWorkerError:
            return self._result(state, TerminationReason.STALE_WORKER)
        return self._result(state, reason)

    @staticmethod
    def _result(state: RuntimeState, reason: TerminationReason) -> AgentRunResult:
        return AgentRunResult(None, 0, (), reason.value, state)
