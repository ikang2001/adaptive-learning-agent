from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FeedbackSignals:
    completion_ratio: float
    actual_duration_seconds: int
    expected_p75_seconds: int
    recent_accuracy: float
    recent_attempt_count: int
    same_error_streak: int
    consecutive_low_completion_days: int


@dataclass(frozen=True, slots=True)
class AnomalyDecision:
    requires_agent: bool
    reason_codes: tuple[str, ...]


class AnomalyDetector:
    def detect(self, signals: FeedbackSignals) -> AnomalyDecision:
        reasons: list[str] = []
        if signals.actual_duration_seconds > signals.expected_p75_seconds * 1.5:
            reasons.append("TIME_OVERRUN")
        if signals.recent_attempt_count >= 5 and signals.recent_accuracy < 0.4:
            reasons.append("LOW_ACCURACY")
        if signals.same_error_streak >= 3:
            reasons.append("REPEATED_ERROR")
        if signals.consecutive_low_completion_days >= 2 and signals.completion_ratio < 0.6:
            reasons.append("LOW_COMPLETION")
        return AnomalyDecision(requires_agent=bool(reasons), reason_codes=tuple(reasons))
