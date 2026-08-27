from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, status

from app.api.dependencies import (
    get_current_user,
    get_job_service,
    get_mock_exam_service,
    get_unlock_service,
)
from app.api.schemas import (
    JobResponse,
    MockExamAttemptResponse,
    MockExamCreateRequest,
    MockExamResponse,
    MockExamSubmitRequest,
    MockQuestionResponse,
)
from app.application.auth import CurrentUser
from app.application.jobs import JobService
from app.application.mock_exams import MockExamService, MockSubmissionItem
from app.application.unlocks import UnlockService
from app.errors import AppError

router = APIRouter(tags=["mock-exams"])


@router.post("/me/mock-exams", status_code=status.HTTP_202_ACCEPTED)
async def create_mock_exam(
    body: MockExamCreateRequest,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[JobService, Depends(get_job_service)],
    unlock_service: Annotated[UnlockService, Depends(get_unlock_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobResponse:
    if not idempotency_key:
        raise AppError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
    if body.mock_type == "SPECIALIZED" and body.target_knowledge_id is None:
        raise AppError(422, "TARGET_KNOWLEDGE_REQUIRED", "specialized mock requires a target")
    job = await service.create(
        current_user.user_id,
        "GENERATE_MOCK",
        {
            "mock_type": body.mock_type,
            "target_knowledge_id": (
                str(body.target_knowledge_id) if body.target_knowledge_id else None
            ),
        },
        idempotency_key,
    )
    await unlock_service.assert_mock_access(
        current_user.user_id, body.mock_type, body.target_knowledge_id
    )
    return JobResponse.model_validate(job, from_attributes=True)


@router.get("/mock-exams/{mock_id}")
async def get_mock_exam(
    mock_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[MockExamService, Depends(get_mock_exam_service)],
) -> MockExamResponse:
    mock, rows = await service.owned_exam(current_user.user_id, mock_id)
    return MockExamResponse(
        id=mock.id,
        mock_type=mock.mock_type,
        status=mock.status,
        total_score=mock.total_score,
        duration_minutes=mock.duration_minutes,
        target_knowledge_id=mock.target_knowledge_id,
        strategy_version=mock.strategy_version,
        validation_result=mock.validation_result,
        questions=[
            MockQuestionResponse(
                id=question.id,
                sequence=link.sequence,
                score=link.score,
                code=question.code,
                content=question.content,
                question_type=question.question_type,
                difficulty=question.difficulty,
            )
            for link, question in rows
        ],
    )


@router.post("/mock-exams/{mock_id}/attempts")
async def submit_mock_exam(
    mock_id: uuid.UUID,
    body: MockExamSubmitRequest,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[MockExamService, Depends(get_mock_exam_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> MockExamAttemptResponse:
    if not idempotency_key:
        raise AppError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
    attempt = await service.submit(
        current_user.user_id,
        mock_id,
        [
            MockSubmissionItem(
                item.question_id,
                item.score_ratio,
                item.duration_seconds,
                item.looked_at_solution,
                item.error_note,
            )
            for item in body.results
        ],
        idempotency_key,
    )
    return MockExamAttemptResponse.model_validate(attempt, from_attributes=True)
