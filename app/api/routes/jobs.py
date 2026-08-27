from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_current_user, get_job_service
from app.api.schemas import JobResponse
from app.application.auth import CurrentUser
from app.application.jobs import JobService

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}")
async def get_job(
    job_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[JobService, Depends(get_job_service)],
) -> JobResponse:
    job = await service.get_owned(current_user.user_id, job_id)
    return JobResponse.model_validate(job, from_attributes=True)
