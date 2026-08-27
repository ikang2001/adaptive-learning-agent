from __future__ import annotations

import math
from dataclasses import dataclass

from app.domain.enums import Difficulty

DIFFICULTY_WEIGHTS: dict[Difficulty, float] = {
    Difficulty.BASIC: 0.8,
    Difficulty.MEDIUM: 1.0,
    Difficulty.COMPREHENSIVE: 1.2,
    Difficulty.TRUE_EXAM: 1.3,
}


@dataclass(frozen=True, slots=True)
class MasteryObservation:
    score_ratio: float
    difficulty: Difficulty
    looked_at_solution: bool

    def __post_init__(self) -> None:
        if not 0 <= self.score_ratio <= 1:
            raise ValueError("score_ratio must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class MasteryResult:
    score: float
    confidence: float
    evidence_count: int


class MasteryStrategy:
    version = "mastery_v1"

    def __init__(self, alpha: float = 0.2) -> None:
        if not 0 < alpha <= 1:
            raise ValueError("alpha must be between 0 and 1")
        self._alpha = alpha

    def update(
        self,
        current_score: float,
        evidence_count: int,
        observation: MasteryObservation,
    ) -> MasteryResult:
        if not 0 <= current_score <= 1:
            raise ValueError("current_score must be between 0 and 1")
        observed_score = (
            min(observation.score_ratio, 0.6)
            if observation.looked_at_solution
            else observation.score_ratio
        )
        weight = DIFFICULTY_WEIGHTS[observation.difficulty]
        updated = current_score + self._alpha * weight * (observed_score - current_score)
        new_evidence_count = evidence_count + 1
        confidence = min(0.95, 1 - math.exp(-new_evidence_count / 5))
        return MasteryResult(
            score=round(min(1.0, max(0.0, updated)), 4),
            confidence=round(confidence, 4),
            evidence_count=new_evidence_count,
        )
