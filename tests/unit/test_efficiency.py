from app.domain.enums import TaskType
from app.domain.learning.efficiency import EfficiencyEstimator


def test_cold_start_uses_default_range() -> None:
    estimate = EfficiencyEstimator().estimate(TaskType.BASIC_QUESTION, [])

    assert estimate.p50_seconds == 8 * 60
    assert estimate.p75_seconds == 10 * 60
    assert estimate.confidence == 0.2


def test_only_twenty_recent_samples_are_used() -> None:
    estimate = EfficiencyEstimator().estimate(TaskType.BASIC_QUESTION, list(range(1, 31)))

    assert estimate.sample_count == 20
    assert estimate.p50_seconds >= 20
