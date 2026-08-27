from app.domain.learning.anomaly import AnomalyDetector, FeedbackSignals


def test_normal_feedback_uses_zero_token_path() -> None:
    result = AnomalyDetector().detect(FeedbackSignals(1, 900, 1000, 0.8, 5, 0, 0))

    assert result.requires_agent is False
    assert result.reason_codes == ()


def test_anomaly_collects_deterministic_reasons() -> None:
    result = AnomalyDetector().detect(FeedbackSignals(0.4, 2000, 1000, 0.2, 5, 3, 2))

    assert result.requires_agent is True
    assert set(result.reason_codes) == {
        "TIME_OVERRUN",
        "LOW_ACCURACY",
        "REPEATED_ERROR",
        "LOW_COMPLETION",
    }
