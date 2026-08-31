from __future__ import annotations

import httpx

from app.harness.errors import ModelUnavailableError, ToolBusinessError
from app.workers.tasks import _error_code, _is_transient, _retry_delay


def test_job_failure_taxonomy_distinguishes_transient_errors() -> None:
    assert _is_transient(TimeoutError()) is True
    assert _is_transient(httpx.ConnectError("offline")) is True
    assert _is_transient(ModelUnavailableError("model down")) is True
    assert _is_transient(ToolBusinessError("invalid business state")) is False


def test_job_retry_delay_is_positive_and_capped() -> None:
    for attempt in range(1, 20):
        delay = _retry_delay(attempt)
        assert delay > 0
        assert delay <= 72


def test_job_error_code_prefers_typed_error_code() -> None:
    assert _error_code(ModelUnavailableError("down")) == "MODEL_UNAVAILABLE"
    assert _error_code(ValueError("bad")) == "ValueError"
