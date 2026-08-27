from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header

from app.api.dependencies import get_current_user, get_true_exam_service
from app.api.schemas import (
    TrueExamAttemptResponse,
    TrueExamDetailResponse,
    TrueExamProfileResponse,
    TrueExamQuestionResponse,
    TrueExamResponse,
    TrueExamSubmitRequest,
)
from app.application.auth import CurrentUser
from app.application.true_exams import ExamQuestionResult, TrueExamService
from app.errors import AppError

router = APIRouter(tags=["true-exams"])


@router.get("/me/true-exams")
async def list_true_exams(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[TrueExamService, Depends(get_true_exam_service)],
) -> list[TrueExamResponse]:
    exams = await service.list_for_user(current_user.user_id)
    return [TrueExamResponse.model_validate(exam, from_attributes=True) for exam in exams]


@router.get("/true-exams/{exam_id}")
async def get_true_exam(
    exam_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[TrueExamService, Depends(get_true_exam_service)],
) -> TrueExamDetailResponse:
    exam, rows = await service.detail_for_user(current_user.user_id, exam_id)
    return TrueExamDetailResponse(
        id=exam.id,
        year=exam.year,
        title=exam.title,
        total_score=exam.total_score,
        duration_minutes=exam.duration_minutes,
        questions=[
            TrueExamQuestionResponse(
                id=question.id,
                sequence=link.sequence,
                code=question.code,
                content=question.content,
                question_type=question.question_type,
                difficulty=question.difficulty,
                score=question.score,
            )
            for link, question in rows
        ],
    )


@router.post("/true-exams/{exam_id}/attempts")
async def submit_true_exam(
    exam_id: uuid.UUID,
    body: TrueExamSubmitRequest,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[TrueExamService, Depends(get_true_exam_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TrueExamAttemptResponse:
    if not idempotency_key:
        raise AppError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
    attempt = await service.submit(
        current_user.user_id,
        exam_id,
        [
            ExamQuestionResult(
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
    return TrueExamAttemptResponse.model_validate(attempt, from_attributes=True)


@router.get("/me/true-exam-profile")
async def get_true_exam_profile(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[TrueExamService, Depends(get_true_exam_service)],
) -> list[TrueExamProfileResponse]:
    profiles = await service.profile(current_user.user_id)
    return [TrueExamProfileResponse.model_validate(item, from_attributes=True) for item in profiles]
