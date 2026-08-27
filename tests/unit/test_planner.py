from datetime import date

from app.domain.enums import Difficulty
from app.domain.planning.planner import (
    AvailabilityDay,
    CandidateQuestion,
    KnowledgePriority,
    PlanningStrategy,
)


def _candidate(index: int, p75: int = 20) -> CandidateQuestion:
    return CandidateQuestion(
        question_id=f"q{index}",
        knowledge_id="k1",
        difficulty=Difficulty.MEDIUM,
        question_type="CALCULATION",
        estimated_p50_minutes=10,
        estimated_p75_minutes=p75,
        school_weight=0.9,
        weakness=0.8,
        difficulty_fit=1,
        spacing_score=1,
        diversity_score=1,
    )


def test_plan_never_exceeds_daily_budget_or_repeats_questions() -> None:
    today = date(2026, 8, 26)
    plan = PlanningStrategy().build_week(
        availability=[AvailabilityDay(today, 60), AvailabilityDay(today.replace(day=27), 40)],
        priorities=[KnowledgePriority("k1", 1, 1, 0, 0, 0, 0)],
        candidates=[_candidate(index) for index in range(10)],
    )

    assert plan.total_max_minutes(today) <= 60
    assert plan.total_max_minutes(today.replace(day=27)) <= 40
    selected = [question for task in plan.tasks for question in task.question_ids]
    assert len(selected) == len(set(selected))
