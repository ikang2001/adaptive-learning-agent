import pytest

from app.domain.enums import Difficulty
from app.domain.learning.mastery import MasteryObservation, MasteryStrategy


def test_mastery_stays_bounded_and_confidence_increases() -> None:
    result = MasteryStrategy().update(
        current_score=0.5,
        evidence_count=0,
        observation=MasteryObservation(1.0, Difficulty.COMPREHENSIVE, False),
    )

    assert 0.5 < result.score <= 1
    assert 0 < result.confidence <= 0.95
    assert result.evidence_count == 1


def test_looked_solution_caps_observation() -> None:
    strategy = MasteryStrategy()
    without_solution = strategy.update(0.5, 2, MasteryObservation(1.0, Difficulty.MEDIUM, False))
    with_solution = strategy.update(0.5, 2, MasteryObservation(1.0, Difficulty.MEDIUM, True))

    assert with_solution.score < without_solution.score


def test_invalid_score_is_rejected() -> None:
    with pytest.raises(ValueError):
        MasteryObservation(1.1, Difficulty.BASIC, False)
