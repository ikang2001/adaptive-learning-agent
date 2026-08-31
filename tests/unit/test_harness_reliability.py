from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.application.agent_runs import AgentDiagnosisService
from app.application.shadow_evaluations import ShadowEvaluationService
from app.harness.contracts import (
    ModelAction,
    ModelResult,
    RuntimePhase,
    RuntimeState,
    StallReason,
    TerminationReason,
    ToolCall,
)
from app.harness.fakes import (
    MemoryCheckpointStore,
    MemoryToolExecutionLedger,
    MemoryTraceRecorder,
    ScriptedModelGateway,
)
from app.harness.policy import AgentPolicyEngine
from app.harness.retry import RetryPolicy
from app.harness.runner import AgentRunner, AgentRunnerConfig
from app.harness.schemas import deserialize_runtime_state, serialize_runtime_state
from app.harness.termination import StallDetector
from app.harness.tools import (
    ToolAvailabilityContext,
    ToolDefinition,
    ToolExecutor,
    ToolRegistry,
    ToolRisk,
    ToolSideEffect,
)
from app.infrastructure.adapters.learning_tools import ProposalArgs


class StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    limit: int = Field(ge=1, le=20)


async def no_sleep(_: float) -> None:
    return None


async def test_pending_tool_checkpoint_resumes_without_repeating_model_step() -> None:
    calls = 0

    async def read(_: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"attempt_ids": ["a1"]}

    registry = ToolRegistry()
    registry.register(ToolDefinition("search_recent_attempts", "read", {}, read))
    state = RuntimeState("run", "student", "diagnose", loop_count=1)
    state.phase = RuntimePhase.TOOL_PENDING
    state.pending_tool_call = ToolCall("search_recent_attempts", {})
    state.pending_step_id = "step-1"
    runner = AgentRunner(
        ScriptedModelGateway(
            [ModelResult(ModelAction(decision="NO_CHANGE", confidence=0.9, finish=True), "fake")]
        ),
        registry,
        ToolExecutor(registry, AgentPolicyEngine()),
        MemoryCheckpointStore(),
        MemoryTraceRecorder(),
    )

    result = await runner.run(state)

    assert result.termination_reason == "COMPLETED"
    assert result.state.loop_count == 2
    assert result.state.tool_call_count == 1
    assert calls == 1


async def test_completed_checkpoint_is_returned_without_another_model_call() -> None:
    state = RuntimeState("run", "student", "diagnose", loop_count=3)
    state.phase = RuntimePhase.TERMINATED
    state.termination_reason = TerminationReason.COMPLETED
    state.final_action = ModelAction(decision="MINOR_ADJUST", confidence=0.88, finish=True)
    runner = AgentRunner(
        ScriptedModelGateway([]),
        ToolRegistry(),
        ToolExecutor(ToolRegistry(), AgentPolicyEngine()),
        MemoryCheckpointStore(),
        MemoryTraceRecorder(),
    )

    result = await runner.run(state)

    assert result.termination_reason == "COMPLETED"
    assert result.decision == "MINOR_ADJUST"
    assert result.state.loop_count == 3


def test_retryable_terminal_state_returns_to_safe_phase() -> None:
    state = RuntimeState("run", "student", "diagnose")
    state.phase = RuntimePhase.TERMINATED
    state.termination_reason = TerminationReason.TOOL_FAILED
    state.last_error_code = "TOOL_TIMEOUT"
    state.pending_tool_call = ToolCall("read", {})
    state.pending_step_id = "step-id"

    AgentDiagnosisService._prepare_retryable_state(state)

    assert state.phase is RuntimePhase.TOOL_PENDING
    assert state.termination_reason is None
    assert state.last_error_code is None


async def test_idempotent_write_replays_ledger_result_once() -> None:
    calls = 0

    async def write(_: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"proposal_id": "p1"}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "propose_minor_adjustment",
            "proposal",
            {},
            write,
            risk=ToolRisk.PROPOSAL,
            side_effect_level=ToolSideEffect.IDEMPOTENT_WRITE,
            idempotency_required=True,
        )
    )
    ledger = MemoryToolExecutionLedger()
    executor = ToolExecutor(registry, AgentPolicyEngine(), ledger=ledger)
    call = ToolCall("propose_minor_adjustment", {}, "same-key")
    state = RuntimeState("run", "student", "diagnose")

    first = await executor.execute(state, call)
    second = await executor.execute(state, call)

    assert first.replayed is False
    assert second.replayed is True
    assert second.observation == {"proposal_id": "p1"}
    assert calls == 1


async def test_successful_proposal_finishes_without_redundant_model_call() -> None:
    async def proposal(_: dict[str, Any]) -> dict[str, Any]:
        return {"proposal_id": "p1", "status": "PENDING"}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "propose_minor_adjustment",
            "proposal",
            {},
            proposal,
            risk=ToolRisk.PROPOSAL,
            side_effect_level=ToolSideEffect.IDEMPOTENT_WRITE,
            idempotency_required=True,
            terminal_decision="MINOR_ADJUST",
        )
    )
    model = ScriptedModelGateway(
        [
            ModelResult(
                ModelAction(
                    tool_call=ToolCall(
                        "propose_minor_adjustment",
                        {"confidence": 0.88, "reason_codes": ["TIME_OVERRUN"]},
                        "stable-key",
                    )
                ),
                "fake",
            )
        ]
    )
    runner = AgentRunner(
        model,
        registry,
        ToolExecutor(registry, AgentPolicyEngine(), ledger=MemoryToolExecutionLedger()),
        MemoryCheckpointStore(),
        MemoryTraceRecorder(),
    )

    result = await runner.run(RuntimeState("run", "student", "diagnose"))

    assert result.decision == "MINOR_ADJUST"
    assert result.state.model_call_count == 1
    assert result.state.tool_call_count == 1


async def test_proposal_uses_harness_bound_evidence_not_model_supplied_ids() -> None:
    captured: dict[str, Any] = {}

    async def proposal(arguments: dict[str, Any]) -> dict[str, Any]:
        captured.update(arguments)
        return {"proposal_id": "p1"}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "propose_minor_adjustment",
            "proposal",
            {},
            proposal,
            risk=ToolRisk.PROPOSAL,
            args_model=ProposalArgs,
            side_effect_level=ToolSideEffect.IDEMPOTENT_WRITE,
            idempotency_required=True,
            terminal_decision="MINOR_ADJUST",
        )
    )
    state = RuntimeState("run", "student", "diagnose")
    state.observations.append(
        {
            "tool": "search_recent_attempts",
            "result": {"attempt_ids": ["00000000-0000-0000-0000-000000000001"]},
        }
    )
    call = ToolCall(
        "propose_minor_adjustment",
        {
            "reason_codes": ["TIME_OVERRUN"],
            "confidence": 0.88,
            "evidence_refs": ["hallucinated-invalid-uuid"],
        },
        "stable-key",
    )

    await ToolExecutor(registry, AgentPolicyEngine(), ledger=MemoryToolExecutionLedger()).execute(
        state, call
    )

    assert captured["evidence_refs"] == ["00000000-0000-0000-0000-000000000001"]


async def test_transient_read_retries_with_bounded_policy() -> None:
    calls = 0

    async def flaky(_: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError
        return {"ok": True}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "read_flaky",
            "read",
            {},
            flaky,
            retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0),
        )
    )
    result = await ToolExecutor(registry, AgentPolicyEngine(), sleep=no_sleep).execute(
        RuntimeState("run", "student", "diagnose"), ToolCall("read_flaky", {})
    )

    assert result.retry_count == 1
    assert result.observation == {"ok": True}
    assert calls == 2


async def test_tool_arguments_are_validated_before_handler() -> None:
    called = False

    async def handler(_: dict[str, Any]) -> dict[str, Any]:
        nonlocal called
        called = True
        return {}

    registry = ToolRegistry()
    registry.register(ToolDefinition("read", "read", {}, handler, args_model=StrictArgs))
    runner = AgentRunner(
        ScriptedModelGateway(
            [ModelResult(ModelAction(tool_call=ToolCall("read", {"limit": 0})), "fake")]
        ),
        registry,
        ToolExecutor(registry, AgentPolicyEngine()),
        MemoryCheckpointStore(),
        MemoryTraceRecorder(),
    )

    result = await runner.run(RuntimeState("run", "student", "diagnose"))

    assert result.termination_reason == "TOOL_VALIDATION_FAILED"
    assert called is False


def test_dynamic_registry_filters_environment_flag_and_role() -> None:
    async def handler(_: dict[str, Any]) -> dict[str, Any]:
        return {}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "admin_debug_read",
            "debug",
            {},
            handler,
            enabled_environments=frozenset({"staging"}),
            feature_flag="debug-tools",
            minimum_role="ADMIN",
        )
    )

    assert registry.model_tools(ToolAvailabilityContext()) == []
    enabled = registry.model_tools(
        ToolAvailabilityContext(
            environment="staging",
            roles=frozenset({"ADMIN"}),
            feature_flags=frozenset({"debug-tools"}),
        )
    )
    assert enabled[0]["function"]["name"] == "admin_debug_read"


async def test_missing_permission_is_blocked_and_traced() -> None:
    called = False

    async def handler(_: dict[str, Any]) -> dict[str, Any]:
        nonlocal called
        called = True
        return {}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "private_read",
            "read",
            {},
            handler,
            required_permissions=frozenset({"private:read"}),
        )
    )
    trace = MemoryTraceRecorder()
    runner = AgentRunner(
        ScriptedModelGateway(
            [ModelResult(ModelAction(tool_call=ToolCall("private_read", {})), "fake")]
        ),
        registry,
        ToolExecutor(registry, AgentPolicyEngine()),
        MemoryCheckpointStore(),
        trace,
    )

    result = await runner.run(RuntimeState("run", "student", "diagnose"))

    assert result.termination_reason == "TOOL_PERMISSION_DENIED"
    assert called is False
    assert trace.guardrails == [("BLOCKED", "MISSING_PERMISSION")]


async def test_shadow_registry_never_executes_write_handler() -> None:
    called = False

    async def write(_: dict[str, Any]) -> dict[str, Any]:
        nonlocal called
        called = True
        return {"written": True}

    source = ToolRegistry()
    source.register(
        ToolDefinition(
            "propose_minor_adjustment",
            "proposal",
            {},
            write,
            risk=ToolRisk.PROPOSAL,
            side_effect_level=ToolSideEffect.IDEMPOTENT_WRITE,
            idempotency_required=True,
        )
    )
    shadow = ShadowEvaluationService._dry_run_registry(source)

    result = await ToolExecutor(shadow, AgentPolicyEngine()).execute(
        RuntimeState("shadow", "student", "diagnose"),
        ToolCall("propose_minor_adjustment", {}, "candidate"),
    )

    assert result.observation["side_effect_executed"] is False
    assert called is False


def test_runtime_checkpoint_round_trip_preserves_recovery_phase() -> None:
    state = RuntimeState("run", "student", "diagnose", roles=("STUDENT",), fencing_token=7)
    state.phase = RuntimePhase.TOOL_PENDING
    state.pending_tool_call = ToolCall("read", {"limit": 3})
    state.pending_step_id = "step-id"

    restored = deserialize_runtime_state(serialize_runtime_state(state))

    assert restored.resumed is True
    assert restored.fencing_token == 7
    assert restored.roles == ("STUDENT",)
    assert restored.pending_tool_call == state.pending_tool_call
    assert restored.phase is RuntimePhase.TOOL_PENDING


def test_stall_detector_finds_oscillation_and_no_new_evidence() -> None:
    detector = StallDetector()
    state = RuntimeState("run", "student", "diagnose")
    assert detector.record_action(state, ToolCall("a", {})) is None
    assert detector.record_action(state, ToolCall("b", {})) is None
    assert detector.record_action(state, ToolCall("a", {})) is None
    assert detector.record_action(state, ToolCall("b", {})) is StallReason.ACTION_OSCILLATION
    assert detector.record_observation(state, {"same": True}) is None
    assert detector.record_observation(state, {"same": True}) is StallReason.NO_NEW_EVIDENCE


@pytest.mark.parametrize(
    "config, expected",
    [
        (AgentRunnerConfig(max_model_calls=0), "MODEL_CALL_BUDGET_EXCEEDED"),
        (AgentRunnerConfig(max_steps=0), "STEP_BUDGET_EXCEEDED"),
    ],
)
async def test_budget_exits_are_explicit(config: AgentRunnerConfig, expected: str) -> None:
    runner = AgentRunner(
        ScriptedModelGateway([]),
        ToolRegistry(),
        ToolExecutor(ToolRegistry(), AgentPolicyEngine()),
        MemoryCheckpointStore(),
        MemoryTraceRecorder(),
        config,
    )

    result = await runner.run(RuntimeState("run", "student", "diagnose"))

    assert result.termination_reason == expected
