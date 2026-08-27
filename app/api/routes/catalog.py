from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_catalog_service, get_current_user
from app.api.schemas import KnowledgeNodeResponse, SchoolResponse
from app.application.auth import CurrentUser
from app.application.catalog import CatalogService

router = APIRouter(tags=["catalog"])


@router.get("/schools")
async def list_schools(
    _: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[CatalogService, Depends(get_catalog_service)],
) -> list[SchoolResponse]:
    schools = await service.list_schools()
    return [SchoolResponse.model_validate(item, from_attributes=True) for item in schools]


@router.get("/schools/{school_id}/knowledge-tree")
async def get_knowledge_tree(
    school_id: uuid.UUID,
    _: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[CatalogService, Depends(get_catalog_service)],
) -> list[KnowledgeNodeResponse]:
    nodes = await service.get_knowledge_tree(school_id)
    return [KnowledgeNodeResponse.model_validate(item, from_attributes=True) for item in nodes]
