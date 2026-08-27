from app.config import Settings
from app.harness.contracts import RuntimeState
from app.infrastructure.adapters.model_gateway import ModelRouter


async def test_fake_router_builds_guarded_diagnosis_sequence() -> None:
    router = ModelRouter(Settings(use_fake_model=True))
    state = RuntimeState("run", "student", "FEEDBACK_DIAGNOSIS")
    state.observations.append({"reason_codes": ["LOW_ACCURACY"]})

    first = await router.decide(state, [])
    state.loop_count = 1
    second = await router.decide(state, [])

    assert first.action.tool_call is not None
    assert first.action.tool_call.name == "search_recent_attempts"
    assert second.action.tool_call is not None
    assert second.action.tool_call.name == "propose_major_replan"
    assert second.action.tool_call.idempotency_key
