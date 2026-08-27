from app.harness.contracts import ModelAction, ModelResult, RuntimeState, ToolCall
from app.harness.fakes import MemoryCheckpointStore, MemoryTraceRecorder, ScriptedModelGateway
from app.harness.runner import AgentRunner
from app.harness.tools import PolicyGuard, ToolDefinition, ToolExecutor, ToolRegistry, ToolRisk


async def test_agent_executes_read_tool_then_finishes() -> None:
    async def read_state(arguments: dict[str, object]) -> dict[str, object]:
        return {"mastery": 0.3, "knowledge_id": arguments["knowledge_id"]}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "get_student_knowledge_state",
            "Read a knowledge state",
            {"type": "object", "properties": {"knowledge_id": {"type": "string"}}},
            read_state,
        )
    )
    model = ScriptedModelGateway(
        [
            ModelResult(
                ModelAction(
                    tool_call=ToolCall("get_student_knowledge_state", {"knowledge_id": "k1"})
                ),
                "fake",
            ),
            ModelResult(ModelAction(decision="MAJOR_REPLAN", confidence=0.9, finish=True), "fake"),
        ]
    )
    checkpoints = MemoryCheckpointStore()
    trace = MemoryTraceRecorder()
    runner = AgentRunner(model, registry, ToolExecutor(registry, PolicyGuard()), checkpoints, trace)

    result = await runner.run(RuntimeState("r1", "s1", "diagnose"))

    assert result.decision == "MAJOR_REPLAN"
    assert result.termination_reason == "COMPLETED"
    assert result.state.tool_call_count == 1
    assert len(trace.tools) == 1


async def test_repeated_action_stalls_loop() -> None:
    async def read(_: dict[str, object]) -> dict[str, object]:
        return {"same": True}

    call = ToolCall("search_recent_attempts", {})
    registry = ToolRegistry()
    registry.register(ToolDefinition("search_recent_attempts", "read", {"type": "object"}, read))
    model = ScriptedModelGateway(
        [
            ModelResult(ModelAction(tool_call=call), "fake"),
            ModelResult(ModelAction(tool_call=call), "fake"),
        ]
    )
    runner = AgentRunner(
        model,
        registry,
        ToolExecutor(registry, PolicyGuard()),
        MemoryCheckpointStore(),
        MemoryTraceRecorder(),
    )

    result = await runner.run(RuntimeState("r1", "s1", "diagnose"))

    assert result.termination_reason == "LOOP_STALLED"


def test_direct_mutation_tool_cannot_be_registered_as_proposal() -> None:
    async def handler(_: dict[str, object]) -> dict[str, object]:
        return {}

    registry = ToolRegistry()
    try:
        registry.register(
            ToolDefinition("update_mastery", "bad", {}, handler, risk=ToolRisk.PROPOSAL)
        )
    except ValueError as exc:
        assert "proposal tools" in str(exc)
    else:
        raise AssertionError("direct mutation tool should be rejected")
