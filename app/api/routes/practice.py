from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Header

from app.api.dependencies import (
    get_current_user,
    get_learning_plan_service,
    get_practice_service,
)
from app.api.schemas import (
    AttemptCreateRequest,
    AttemptResponse,
    FeedbackCreateRequest,
    FeedbackResponse,
    PracticeQuestionResponse,
    PracticeTaskResponse,
    TaskFeedbackResponse,
    TaskFeedbackUpsertRequest,
    TodayTaskResponse,
)
from app.application.auth import CurrentUser
from app.application.learning_plans import LearningPlanService, LearningPlanTaskData
from app.application.practice import PracticeService
from app.errors import AppError

router = APIRouter(tags=["practice"])


@router.get("/me/practice/today")
async def get_today_practice(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[PracticeService, Depends(get_practice_service)],
    target_date: date | None = None,
) -> list[PracticeTaskResponse]:
    tasks = await service.today(current_user.user_id, target_date or date.today())
    return [
        PracticeTaskResponse(
            id=task.id,
            task_type=task.task_type,
            estimated_min_minutes=task.estimated_min_minutes,
            estimated_max_minutes=task.estimated_max_minutes,
            status=task.status,
            questions=[
                PracticeQuestionResponse.model_validate(question, from_attributes=True)
                for question in task.questions
            ],
        )
        for task in tasks
    ]


@router.get("/me/tasks/today")
async def get_today_tasks(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[LearningPlanService, Depends(get_learning_plan_service)],
    target_date: date | None = None,
) -> list[TodayTaskResponse]:
    day = target_date or date.today()
    tasks = await service.today(current_user.user_id, day)
    return [_today_task_response(item, day) for item in tasks]


@router.post("/questions/{question_id}/attempts")
async def create_attempt(
    question_id: uuid.UUID,
    body: AttemptCreateRequest,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[PracticeService, Depends(get_practice_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AttemptResponse:
    if not idempotency_key:
        raise AppError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
    attempt = await service.record_attempt(
        current_user.user_id,
        question_id,
        body.task_id,
        body.actual_duration_seconds,
        body.score_ratio,
        body.looked_at_solution,
        body.self_difficulty,
        body.error_note,
        idempotency_key,
    )
    return AttemptResponse.model_validate(attempt, from_attributes=True)


@router.post("/tasks/{task_id}/feedback")
async def create_feedback(
    task_id: uuid.UUID,
    body: FeedbackCreateRequest,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[PracticeService, Depends(get_practice_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> FeedbackResponse:
    if not idempotency_key:
        raise AppError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
    if body.correct_count > body.completed_count:
        raise AppError(
            422, "INVALID_FEEDBACK_COUNTS", "correct_count cannot exceed completed_count"
        )
    result = await service.submit_feedback(
        current_user.user_id,
        task_id,
        body.completion_ratio,
        body.actual_duration_seconds,
        body.completed_count,
        body.correct_count,
        body.looked_at_solution,
        body.perceived_difficulty,
        body.free_text,
        idempotency_key,
    )
    return FeedbackResponse(
        feedback_id=result.feedback_id,
        requires_agent=result.anomaly.requires_agent,
        reason_codes=list(result.anomaly.reason_codes),
        agent_job_id=result.agent_job.id if result.agent_job else None,
    )


@router.put("/tasks/{task_id}/feedback")
async def upsert_task_feedback(
    task_id: uuid.UUID,
    body: TaskFeedbackUpsertRequest,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[PracticeService, Depends(get_practice_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TaskFeedbackResponse:
    if not idempotency_key:
        raise AppError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
    if (
        body.correct_units is not None
        and body.completed_units is not None
        and body.correct_units > body.completed_units
    ):
        raise AppError(
            422, "INVALID_FEEDBACK_COUNTS", "correct units cannot exceed completed units"
        )
    detail: dict[str, object] = {
        "completed_units": body.completed_units,
        "correct_units": body.correct_units,
        "looked_at_solution": body.looked_at_solution,
        "summary_text": body.summary_text,
    }
    result = await service.upsert_learning_feedback(
        current_user.user_id,
        task_id,
        body.expected_version,
        body.completion_ratio,
        body.actual_duration_seconds,
        body.perceived_difficulty,
        body.free_text,
        body.progress_marker,
        body.mastery_self_score,
        detail,
        idempotency_key,
    )
    return TaskFeedbackResponse(
        feedback_id=result.feedback.id,
        feedback_version=result.feedback.feedback_version,
        requires_agent=result.anomaly.requires_agent,
        reason_codes=list(result.anomaly.reason_codes),
        agent_job_id=result.agent_job.id if result.agent_job else None,
    )


def _today_task_response(item: LearningPlanTaskData, today: date) -> TodayTaskResponse:
    task = item.task
    return TodayTaskResponse(
        id=task.id,
        task_date=task.task_date,
        task_type=task.task_type,
        target_count=task.target_count,
        estimated_min_minutes=task.estimated_min_minutes,
        estimated_max_minutes=task.estimated_max_minutes,
        priority=task.priority,
        status=task.status,
        reason=task.reason,
        sequence=task.sequence,
        title=task.title,
        description=task.description,
        knowledge_id=item.knowledge_id,
        resource_section_id=task.resource_section_id,
        resource_title=item.resource_title,
        resource_section_title=item.resource_section_title,
        suggested_scope=task.suggested_scope,
        planned_units=task.planned_units,
        unit_type=task.unit_type,
        system_suggested_minutes=task.system_suggested_minutes,
        student_estimated_minutes=task.student_estimated_minutes,
        effective_minutes=task.effective_minutes,
        origin=task.origin,
        is_personal=task.is_personal,
        has_capacity_warning=task.has_capacity_warning,
        version=task.version,
        is_overdue=task.task_date < today and task.status != "COMPLETED",
        feedback_version=item.feedback_version,
    )
