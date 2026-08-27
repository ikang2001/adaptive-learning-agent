from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header

from app.api.dependencies import get_current_user, get_unlock_service
from app.api.schemas import (
    ChapterSessionDetailResponse,
    ChapterSessionQuestionResponse,
    ChapterSessionResponse,
    ChapterSessionSubmitRequest,
    FullMockUnlockResponse,
    KnowledgeUnlockResponse,
    SpecializedScopeResponse,
    StrengtheningConfirmRequest,
    StrengtheningConfirmResponse,
)
from app.application.auth import CurrentUser
from app.application.unlocks import UnlockService
from app.errors import AppError

router = APIRouter(tags=["learning-unlocks"])


@router.get("/me/learning-unlocks")
async def get_learning_unlocks(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[UnlockService, Depends(get_unlock_service)],
) -> list[KnowledgeUnlockResponse]:
    return [
        KnowledgeUnlockResponse.model_validate(item, from_attributes=True)
        for item in await service.progress(current_user.user_id)
    ]


@router.get("/me/specialized-scopes")
async def get_specialized_scopes(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[UnlockService, Depends(get_unlock_service)],
) -> list[SpecializedScopeResponse]:
    return [
        SpecializedScopeResponse.model_validate(item)
        for item in await service.specialized_scopes(current_user.user_id)
    ]


@router.post("/knowledge/{knowledge_id}/strengthening/confirm")
async def confirm_strengthening(
    knowledge_id: uuid.UUID,
    body: StrengtheningConfirmRequest,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[UnlockService, Depends(get_unlock_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> StrengtheningConfirmResponse:
    if not idempotency_key:
        raise AppError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
    result = await service.confirm_strengthened(
        current_user.user_id, knowledge_id, body.expected_version
    )
    return StrengtheningConfirmResponse(**result)


@router.post("/true-exam/chapter-sessions")
async def create_chapter_session(
    knowledge_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[UnlockService, Depends(get_unlock_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ChapterSessionResponse:
    if not idempotency_key:
        raise AppError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
    session = await service.create_chapter_session(current_user.user_id, knowledge_id)
    return ChapterSessionResponse.model_validate(session, from_attributes=True)


@router.post("/me/full-mock/unlock/confirm")
async def confirm_full_mock_unlock(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[UnlockService, Depends(get_unlock_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> FullMockUnlockResponse:
    if not idempotency_key:
        raise AppError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
    return FullMockUnlockResponse(**await service.confirm_full_mock_unlock(current_user.user_id))


@router.get("/true-exam/chapter-sessions/{session_id}")
async def get_chapter_session(
    session_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[UnlockService, Depends(get_unlock_service)],
) -> ChapterSessionDetailResponse:
    session, rows = await service.chapter_session_detail(current_user.user_id, session_id)
    return ChapterSessionDetailResponse(
        id=session.id,
        knowledge_id=session.knowledge_id,
        question_snapshot_version=session.question_snapshot_version,
        status=session.status,
        total_questions=session.total_questions,
        completed_questions=session.completed_questions,
        completed_at=session.completed_at,
        questions=[
            ChapterSessionQuestionResponse(
                id=question.id,
                sequence=link.sequence,
                code=question.code,
                content=question.content,
                question_type=question.question_type,
                difficulty=question.difficulty,
                score=question.score,
                completed_at=link.completed_at,
            )
            for link, question in rows
        ],
    )


@router.post("/true-exam/chapter-sessions/{session_id}/submit")
async def submit_chapter_session(
    session_id: uuid.UUID,
    body: ChapterSessionSubmitRequest,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[UnlockService, Depends(get_unlock_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ChapterSessionResponse:
    if not idempotency_key:
        raise AppError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
    session = await service.submit_chapter_session(
        current_user.user_id,
        session_id,
        [item.model_dump() for item in body.results],
        idempotency_key,
    )
    return ChapterSessionResponse.model_validate(session, from_attributes=True)
