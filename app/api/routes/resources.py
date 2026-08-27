from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, UploadFile, status

from app.api.dependencies import get_current_user, get_job_service, get_resource_service
from app.api.schemas import (
    LearningResourceResponse,
    PublishedResourceSectionResponse,
    ResourceImportResponse,
    ResourcePublishRequest,
    ResourceSectionReviewResponse,
    ResourceSectionUpdateRequest,
)
from app.application.auth import CurrentUser
from app.application.jobs import JobService
from app.application.resources import ResourceService
from app.domain.enums import ResourceType, Role
from app.errors import AppError

router = APIRouter(tags=["learning-resources"])


@router.get("/learning-resources/sections")
async def list_published_resource_sections(
    _: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[ResourceService, Depends(get_resource_service)],
    knowledge_id: uuid.UUID | None = None,
) -> list[PublishedResourceSectionResponse]:
    return [
        PublishedResourceSectionResponse.model_validate(item)
        for item in await service.published_sections(knowledge_id)
    ]


@router.post("/resources/uploads", status_code=status.HTTP_202_ACCEPTED)
async def upload_resource(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[ResourceService, Depends(get_resource_service)],
    job_service: Annotated[JobService, Depends(get_job_service)],
    title: Annotated[str, Form(min_length=1, max_length=256)],
    resource_type: Annotated[ResourceType, Form()],
    files: Annotated[list[UploadFile], File(...)],
    school_profile_id: Annotated[uuid.UUID | None, Form()] = None,
) -> ResourceImportResponse:
    _require_reviewer(current_user)
    payload = [
        (file.filename or "resource", file.content_type, await file.read()) for file in files
    ]
    run = await service.create_upload_bundle(
        current_user.user_id,
        title,
        resource_type,
        school_profile_id,
        payload,
    )
    await job_service.create(
        current_user.user_id,
        "PARSE_RESOURCE",
        {"resource_import_run_id": str(run.id)},
        f"parse-resource:{run.id}",
    )
    return ResourceImportResponse.model_validate(run, from_attributes=True)


@router.get("/resource-imports/{run_id}")
async def get_resource_import(
    run_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[ResourceService, Depends(get_resource_service)],
) -> ResourceImportResponse:
    _require_reviewer(current_user)
    run = await service.get_import(run_id)
    return ResourceImportResponse.model_validate(run, from_attributes=True)


@router.get("/review/resources")
async def list_resource_reviews(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[ResourceService, Depends(get_resource_service)],
) -> list[LearningResourceResponse]:
    _require_reviewer(current_user)
    return [
        LearningResourceResponse.model_validate(item, from_attributes=True)
        for item in await service.pending_review()
    ]


@router.get("/review/resources/{resource_id}/sections")
async def get_resource_sections(
    resource_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[ResourceService, Depends(get_resource_service)],
) -> list[ResourceSectionReviewResponse]:
    _require_reviewer(current_user)
    return [
        ResourceSectionReviewResponse.model_validate(item)
        for item in await service.sections_for_review(resource_id)
    ]


@router.patch("/review/resource-sections/{section_id}")
async def update_resource_section(
    section_id: uuid.UUID,
    body: ResourceSectionUpdateRequest,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[ResourceService, Depends(get_resource_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ResourceSectionReviewResponse:
    _require_reviewer(current_user)
    if not idempotency_key:
        raise AppError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
    section = await service.update_section(
        section_id,
        body.expected_version,
        body.title,
        body.page_start,
        body.page_end,
        body.knowledge_ids,
    )
    return ResourceSectionReviewResponse(
        id=section.id,
        title=section.title,
        section_path=section.section_path,
        level=section.level,
        sequence=section.sequence,
        page_start=section.page_start,
        page_end=section.page_end,
        version=section.version,
        mappings=[],
    )


@router.post("/review/resources/{resource_id}/publish")
async def publish_resource(
    resource_id: uuid.UUID,
    body: ResourcePublishRequest,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[ResourceService, Depends(get_resource_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> LearningResourceResponse:
    _require_reviewer(current_user)
    if not idempotency_key:
        raise AppError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
    resource = await service.publish(current_user.user_id, resource_id, body.reason)
    return LearningResourceResponse.model_validate(resource, from_attributes=True)


def _require_reviewer(current_user: CurrentUser) -> None:
    if not current_user.roles.intersection({Role.REVIEWER, Role.ADMIN}):
        raise AppError(403, "REVIEWER_ROLE_REQUIRED", "reviewer or admin role is required")
