from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header

from app.api.dependencies import get_current_user, get_school_change_service
from app.api.schemas import (
    SchoolChangeApplyRequest,
    SchoolChangePreviewRequest,
    SchoolChangePreviewResponse,
)
from app.application.auth import CurrentUser
from app.application.school_change import SchoolChangeService
from app.errors import AppError

router = APIRouter(prefix="/me/target-school/change", tags=["school-change"])


@router.post("/preview")
async def preview_school_change(
    body: SchoolChangePreviewRequest,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[SchoolChangeService, Depends(get_school_change_service)],
) -> SchoolChangePreviewResponse:
    preview = await service.preview(current_user.user_id, body.target_school_id)
    return SchoolChangePreviewResponse.model_validate(preview, from_attributes=True)


@router.post("/apply")
async def apply_school_change(
    body: SchoolChangeApplyRequest,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[SchoolChangeService, Depends(get_school_change_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> SchoolChangePreviewResponse:
    if not idempotency_key:
        raise AppError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
    preview = await service.apply(current_user.user_id, body.preview_id)
    return SchoolChangePreviewResponse.model_validate(preview, from_attributes=True)
