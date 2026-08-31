from typing import Any

from app.config import Settings
from app.harness.contracts import ModelAction, ModelAttempt, ModelResult, RuntimeState
from app.harness.errors import ModelUnavailableError
from app.infrastructure.adapters.model_gateway import ModelRouter, QwenModelGateway


async def test_fake_router_builds_guarded_diagnosis_sequence() -> None:
    router = ModelRouter(Settings(use_fake_model=True))
    state = RuntimeState("run", "student", "FEEDBACK_DIAGNOSIS")
    state.observations.append({"reason_codes": ["LOW_ACCURACY"]})

    first = await router.decide(state, [])
    state.loop_count = 1
    state.observations.append(
        {"tool": "search_recent_attempts", "result": {"attempt_ids": ["attempt-1"]}}
    )
    second = await router.decide(state, [])

    assert first.action.tool_call is not None
    assert first.action.tool_call.name == "search_recent_attempts"
    assert second.action.tool_call is not None
    assert second.action.tool_call.name == "propose_major_replan"
    assert second.action.tool_call.idempotency_key


class ScriptedGateway(QwenModelGateway):
    def __init__(self, settings: Settings, name: str, results: list[object]) -> None:
        super().__init__(settings, name)
        self._results = iter(results)

    async def decide(self, state: RuntimeState, tools: list[dict[str, Any]]) -> ModelResult:
        del state, tools
        result = next(self._results)
        if isinstance(result, Exception):
            raise result
        assert isinstance(result, ModelResult)
        return result


async def no_sleep(_: float) -> None:
    return None


async def test_plus_rate_limit_retries_once(monkeypatch: Any) -> None:
    settings = Settings(use_fake_model=False)
    router = ModelRouter(settings)
    failure = ModelUnavailableError(
        "rate limited",
        attempts=(ModelAttempt("plus", status="FAILED", error_code="MODEL_HTTP_429"),),
    )
    success = ModelResult(
        ModelAction(decision="NO_CHANGE", confidence=0.9, finish=True),
        "plus",
        attempts=(ModelAttempt("plus"),),
    )
    router._plus = ScriptedGateway(settings, settings.qwen_plus_model, [failure, success])
    monkeypatch.setattr("app.infrastructure.adapters.model_gateway.asyncio.sleep", no_sleep)

    result = await router.decide(RuntimeState("run", "student", "FEEDBACK_DIAGNOSIS"), [])

    assert result.action.decision == "NO_CHANGE"
    assert result.model_call_count == 2
    assert result.attempts[0].status == "FAILED"


async def test_flash_timeout_falls_back_to_plus() -> None:
    settings = Settings(use_fake_model=False)
    router = ModelRouter(settings)
    failure = ModelUnavailableError(
        "timeout",
        attempts=(ModelAttempt("flash", status="FAILED", error_code="MODEL_TIMEOUT"),),
    )
    success = ModelResult(
        ModelAction(decision="SUMMARY", confidence=0.9, finish=True),
        "plus",
        attempts=(ModelAttempt("plus"),),
    )
    router._flash = ScriptedGateway(settings, settings.qwen_flash_model, [failure])
    router._plus = ScriptedGateway(settings, settings.qwen_plus_model, [success])

    result = await router.decide(RuntimeState("run", "student", "EXPLAIN"), [])

    assert result.action.decision == "SUMMARY"
    assert [item.model_name for item in result.attempts] == ["flash", "plus"]


async def test_flash_does_not_fallback_when_call_budget_is_exhausted() -> None:
    settings = Settings(use_fake_model=False)
    router = ModelRouter(settings)
    failure = ModelUnavailableError(
        "timeout",
        attempts=(ModelAttempt("flash", status="FAILED", error_code="MODEL_TIMEOUT"),),
    )
    router._flash = ScriptedGateway(settings, settings.qwen_flash_model, [failure])
    state = RuntimeState("run", "student", "EXPLAIN", model_call_limit=1)

    try:
        await router.decide(state, [])
    except ModelUnavailableError as error:
        assert len(error.attempts) == 1
    else:
        raise AssertionError("fallback must not cross the model call budget")
