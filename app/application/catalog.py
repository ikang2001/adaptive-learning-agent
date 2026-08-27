from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError
from app.infrastructure.db.models import KnowledgeNode, SchoolProfile


class CatalogService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_schools(self) -> list[SchoolProfile]:
        result = await self._session.scalars(
            select(SchoolProfile)
            .where(SchoolProfile.status == "ACTIVE")
            .order_by(SchoolProfile.code)
        )
        return list(result.all())

    async def get_knowledge_tree(self, school_id: uuid.UUID) -> list[KnowledgeNode]:
        if await self._session.get(SchoolProfile, school_id) is None:
            raise AppError(404, "SCHOOL_NOT_FOUND", "school profile does not exist")
        result = await self._session.scalars(
            select(KnowledgeNode).order_by(KnowledgeNode.level, KnowledgeNode.code)
        )
        return list(result.all())
