from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Header, status

from app.api.dependencies import (
    get_current_user,
    get_job_service,
    get_learning_plan_service,
)
from app.api.schemas import (
    JobResponse,
    PlanChangeResponse,
    PlanCreateRequest,
    PlanResponse,
    PlanTaskResponse,
    PlanTasksPatchRequest,
)
from app.application.auth import CurrentUser
from app.application.jobs import JobService
from app.application.learning_plans import LearningPlanService, LearningPlanTaskData
from app.errors import AppError

router = APIRouter(tags=["plans"])


@router.post("/me/plans", status_code=status.HTTP_202_ACCEPTED)
async def create_plan(
    body: PlanCreateRequest,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[JobService, Depends(get_job_service)],
    plan_service: Annotated[LearningPlanService, Depends(get_learning_plan_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobResponse:
    if not idempotency_key:
        raise AppError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
    if await plan_service.has_active_plan(current_user.user_id):
        raise AppError(409, "ACTIVE_PLAN_EXISTS", "student already has an active learning plan")
    job = await service.create(
        current_user.user_id,
        "GENERATE_PLAN",
        {"start_date": body.start_date.isoformat()},
        idempotency_key,
    )
    return JobResponse.model_validate(job, from_attributes=True)


@router.get("/me/plans/current")
async def get_current_plan(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[LearningPlanService, Depends(get_learning_plan_service)],
    from_date: date | None = None,
    to_date: date | None = None,
) -> PlanResponse:
    plan, tasks = await service.current(current_user.user_id, from_date, to_date)
    return PlanResponse(
        id=plan.id,
        start_date=plan.start_date,
        end_date=plan.end_date,
        revision=plan.revision,
        status=plan.status,
        planner_version=plan.planner_version,
        timezone=plan.timezone,
        version=plan.version,
        tasks=[_task_response(item) for item in tasks],
    )


@router.patch("/plans/{plan_id}/tasks")
async def patch_plan_tasks(
    plan_id: uuid.UUID,
    body: PlanTasksPatchRequest,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[LearningPlanService, Depends(get_learning_plan_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> PlanResponse:
    if not idempotency_key:
        raise AppError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
    await service.patch_tasks(
        current_user.user_id,
        plan_id,
        body.expected_plan_version,
        body.allow_over_budget,
        [change.model_dump() for change in body.changes],
    )
    plan, tasks = await service.current(current_user.user_id)
    return PlanResponse(
        id=plan.id,
        start_date=plan.start_date,
        end_date=plan.end_date,
        revision=plan.revision,
        status=plan.status,
        planner_version=plan.planner_version,
        timezone=plan.timezone,
        version=plan.version,
        tasks=[_task_response(item) for item in tasks],
    )


@router.get("/plans/{plan_id}/changes")
async def get_plan_changes(
    plan_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[LearningPlanService, Depends(get_learning_plan_service)],
) -> list[PlanChangeResponse]:
    rows = await service.changes(current_user.user_id, plan_id)
    return [PlanChangeResponse.model_validate(row, from_attributes=True) for row in rows]


def _task_response(item: LearningPlanTaskData) -> PlanTaskResponse:
    task = item.task
    return PlanTaskResponse(
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
    )
