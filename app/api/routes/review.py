from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header

from app.api.dependencies import get_current_user, get_generated_question_review_service
from app.api.schemas import (
    GeneratedQuestionResponse,
    ReviewDecisionRequest,
    ReviewDecisionResponse,
)
from app.application.auth import CurrentUser
from app.application.mock_exams import GeneratedQuestionReviewService
from app.domain.enums import Role
from app.errors import AppError

router = APIRouter(prefix="/review/generated-questions", tags=["review"])


@router.get("")
async def list_pending_candidates(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[
        GeneratedQuestionReviewService, Depends(get_generated_question_review_service)
    ],
) -> list[GeneratedQuestionResponse]:
    _require_reviewer(current_user)
    candidates = await service.pending()
    return [
        GeneratedQuestionResponse.model_validate(item, from_attributes=True) for item in candidates
    ]


@router.post("/{candidate_id}/approve")
async def approve_candidate(
    candidate_id: uuid.UUID,
    body: ReviewDecisionRequest,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[
        GeneratedQuestionReviewService, Depends(get_generated_question_review_service)
    ],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ReviewDecisionResponse:
    return await _decide(candidate_id, body, current_user, service, idempotency_key, True)


@router.post("/{candidate_id}/reject")
async def reject_candidate(
    candidate_id: uuid.UUID,
    body: ReviewDecisionRequest,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[
        GeneratedQuestionReviewService, Depends(get_generated_question_review_service)
    ],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ReviewDecisionResponse:
    return await _decide(candidate_id, body, current_user, service, idempotency_key, False)


async def _decide(
    candidate_id: uuid.UUID,
    body: ReviewDecisionRequest,
    current_user: CurrentUser,
    service: GeneratedQuestionReviewService,
    idempotency_key: str | None,
    approve: bool,
) -> ReviewDecisionResponse:
    _require_reviewer(current_user)
    if not idempotency_key:
        raise AppError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
    candidate, job = await service.decide(
        current_user.user_id,
        candidate_id,
        approve,
        body.reason,
        idempotency_key,
    )
    return ReviewDecisionResponse(
        candidate=GeneratedQuestionResponse.model_validate(candidate, from_attributes=True),
        resume_job_id=job.id if job else None,
    )


def _require_reviewer(current_user: CurrentUser) -> None:
    if not current_user.roles.intersection({Role.REVIEWER, Role.ADMIN}):
        raise AppError(403, "REVIEWER_ROLE_REQUIRED", "reviewer or admin role is required")
