from app.domain.enums import Difficulty
from app.domain.exams.validator import ExamQuestion, ExamSpec, MockExamValidator


def test_valid_exam_passes() -> None:
    spec = ExamSpec(
        total_score=20,
        question_count=2,
        difficulty_distribution={Difficulty.BASIC: 0.5, Difficulty.MEDIUM: 0.5},
        knowledge_distribution={"k1": 0.5, "k2": 0.5},
    )
    questions = [
        ExamQuestion("q1", 10, Difficulty.BASIC, "k1", True, True),
        ExamQuestion("q2", 10, Difficulty.MEDIUM, "k2", True, True),
    ]

    assert MockExamValidator().validate(spec, questions).valid is True


def test_duplicate_and_wrong_score_are_rejected() -> None:
    spec = ExamSpec(30, 2, {Difficulty.BASIC: 1}, {"k1": 1})
    questions = [
        ExamQuestion("q1", 10, Difficulty.BASIC, "k1", True, True),
        ExamQuestion("q1", 10, Difficulty.BASIC, "k1", True, True),
    ]

    result = MockExamValidator().validate(spec, questions)

    assert result.valid is False
    assert "DUPLICATE_QUESTION" in result.errors
    assert "TOTAL_SCORE_MISMATCH" in result.errors
