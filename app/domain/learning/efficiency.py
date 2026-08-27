from __future__ import annotations

import math
from dataclasses import dataclass

from app.domain.enums import TaskType

COLD_START_SECONDS: dict[TaskType, int] = {
    TaskType.THEORY_REVIEW: 30 * 60,
    TaskType.BASIC_QUESTION: 8 * 60,
    TaskType.MEDIUM_QUESTION: 15 * 60,
    TaskType.COMPREHENSIVE_QUESTION: 25 * 60,
    TaskType.TRUE_EXAM_QUESTION: 25 * 60,
    TaskType.WRONG_QUESTION_REVIEW: 12 * 60,
    TaskType.MOCK_EXAM: 180 * 60,
}


@dataclass(frozen=True, slots=True)
class DurationEstimate:
    p50_seconds: int
    p75_seconds: int
    confidence: float
    sample_count: int


class EfficiencyEstimator:
    max_samples = 20

    def estimate(self, task_type: TaskType, samples_seconds: list[int]) -> DurationEstimate:
        valid = sorted(value for value in samples_seconds[-self.max_samples :] if value > 0)
        if not valid:
            baseline = COLD_START_SECONDS[task_type]
            return DurationEstimate(baseline, math.ceil(baseline * 1.25), 0.2, 0)
        return DurationEstimate(
            p50_seconds=self._percentile(valid, 0.5),
            p75_seconds=self._percentile(valid, 0.75),
            confidence=round(min(0.95, len(valid) / self.max_samples), 4),
            sample_count=len(valid),
        )

    @staticmethod
    def _percentile(values: list[int], percentile: float) -> int:
        index = (len(values) - 1) * percentile
        lower = math.floor(index)
        upper = math.ceil(index)
        if lower == upper:
            return values[lower]
        fraction = index - lower
        return round(values[lower] + (values[upper] - values[lower]) * fraction)
