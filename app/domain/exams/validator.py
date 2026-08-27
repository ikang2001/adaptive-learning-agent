from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TypeVar

from app.domain.enums import Difficulty

DistributionKey = TypeVar("DistributionKey")


@dataclass(frozen=True, slots=True)
class ExamSpec:
    total_score: int
    question_count: int
    difficulty_distribution: dict[Difficulty, float]
    knowledge_distribution: dict[str, float]
    distribution_tolerance: float = 0.10


@dataclass(frozen=True, slots=True)
class ExamQuestion:
    question_id: str
    score: int
    difficulty: Difficulty
    knowledge_id: str
    has_answer: bool
    is_valid: bool


@dataclass(frozen=True, slots=True)
class ExamValidation:
    valid: bool
    errors: tuple[str, ...]


class MockExamValidator:
    version = "mock_validator_v1"

    def validate(self, spec: ExamSpec, questions: list[ExamQuestion]) -> ExamValidation:
        errors: list[str] = []
        ids = [question.question_id for question in questions]
        if len(questions) != spec.question_count:
            errors.append("QUESTION_COUNT_MISMATCH")
        if len(ids) != len(set(ids)):
            errors.append("DUPLICATE_QUESTION")
        if sum(question.score for question in questions) != spec.total_score:
            errors.append("TOTAL_SCORE_MISMATCH")
        if any(not question.has_answer for question in questions):
            errors.append("ANSWER_MISSING")
        if any(not question.is_valid for question in questions):
            errors.append("INVALID_QUESTION")
        if questions:
            self._validate_distribution(
                Counter(question.difficulty for question in questions),
                spec.difficulty_distribution,
                len(questions),
                spec.distribution_tolerance,
                "DIFFICULTY_DISTRIBUTION_MISMATCH",
                errors,
            )
            self._validate_distribution(
                Counter(question.knowledge_id for question in questions),
                spec.knowledge_distribution,
                len(questions),
                spec.distribution_tolerance,
                "KNOWLEDGE_DISTRIBUTION_MISMATCH",
                errors,
            )
        return ExamValidation(valid=not errors, errors=tuple(dict.fromkeys(errors)))

    @staticmethod
    def _validate_distribution(
        actual: Counter[DistributionKey],
        expected: dict[DistributionKey, float],
        total: int,
        tolerance: float,
        error_code: str,
        errors: list[str],
    ) -> None:
        for key, expected_ratio in expected.items():
            actual_ratio = actual[key] / total
            if abs(actual_ratio - expected_ratio) > tolerance:
                errors.append(error_code)
                return
