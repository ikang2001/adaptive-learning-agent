from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_current_user, get_student_service
from app.api.schemas import (
    AvailabilityReplace,
    AvailabilityTemplateReplace,
    MessageResponse,
    StudentProfileResponse,
    StudentProfileUpsert,
)
from app.application.auth import CurrentUser
from app.application.students import StudentService

router = APIRouter(prefix="/me", tags=["students"])


@router.get("/student-profile")
async def get_profile(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[StudentService, Depends(get_student_service)],
) -> StudentProfileResponse:
    return StudentProfileResponse.model_validate(
        await service.get_profile(current_user.user_id), from_attributes=True
    )


@router.put("/student-profile")
async def upsert_profile(
    body: StudentProfileUpsert,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[StudentService, Depends(get_student_service)],
) -> StudentProfileResponse:
    profile = await service.upsert_profile(
        current_user.user_id,
        body.target_school_id,
        body.exam_date,
        body.expected_version,
    )
    return StudentProfileResponse.model_validate(profile, from_attributes=True)


@router.put("/availability")
async def replace_availability(
    body: AvailabilityReplace,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[StudentService, Depends(get_student_service)],
) -> MessageResponse:
    await service.replace_availability(
        current_user.user_id,
        [(item.date, item.available_minutes) for item in body.days],
    )
    return MessageResponse(status="updated")


@router.put("/availability-template")
async def replace_availability_template(
    body: AvailabilityTemplateReplace,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[StudentService, Depends(get_student_service)],
) -> MessageResponse:
    await service.replace_availability_template(
        current_user.user_id,
        [(item.weekday, item.available_minutes) for item in body.days],
    )
    return MessageResponse(status="updated")
