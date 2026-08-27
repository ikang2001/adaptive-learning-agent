from datetime import date

from app.domain.enums import TaskType
from app.domain.planning.planner import AvailabilityDay, LearningCandidate, PlanningStrategy


def test_foundation_plan_contains_learning_tasks_without_questions() -> None:
    today = date(2026, 8, 27)
    plan = PlanningStrategy().build_learning_week(
        [AvailabilityDay(today, 120)],
        [
            LearningCandidate(
                knowledge_id="k1",
                task_type=TaskType.COURSE_LEARNING,
                title="学习稳定性",
                description="观看课程",
                resource_section_id="s1",
                suggested_scope="第1-3页",
                planned_units=3,
                unit_type="节",
                estimated_p50_minutes=35,
                estimated_p75_minutes=50,
                priority=1,
            ),
            LearningCandidate(
                knowledge_id="k1",
                task_type=TaskType.HANDOUT_PRACTICE,
                title="完成稳定性讲义",
                description="外部讲义习题",
                resource_section_id="s2",
                suggested_scope="10题",
                planned_units=10,
                unit_type="题",
                estimated_p50_minutes=40,
                estimated_p75_minutes=60,
                priority=0.9,
            ),
        ],
    )

    assert {task.task_type for task in plan.tasks} == {
        TaskType.COURSE_LEARNING,
        TaskType.HANDOUT_PRACTICE,
    }
    assert all(task.question_ids == () for task in plan.tasks)
    assert plan.total_max_minutes(today) <= 120
