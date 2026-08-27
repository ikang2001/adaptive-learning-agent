from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import PlanStatus, TargetSchoolChangeStatus, TaskOrigin, TaskStatus
from app.errors import AppError
from app.infrastructure.db.models import (
    PlanTask,
    PlanTaskKnowledge,
    SchoolKnowledgeStat,
    SchoolProfile,
    Student,
    TargetSchoolChangePreview,
    WeeklyPlan,
)


class SchoolChangeService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def preview(self, user_id: uuid.UUID, school_id: uuid.UUID) -> TargetSchoolChangePreview:
        student = await self._student(user_id)
        school = await self._session.get(SchoolProfile, school_id)
        if school is None or school.status != "ACTIVE":
            raise AppError(404, "SCHOOL_NOT_FOUND", "target school does not exist")
        old_ids = await self._school_knowledge_ids(student.target_school_id)
        new_ids = await self._school_knowledge_ids(school.id)
        shared = old_ids & new_ids
        preview = TargetSchoolChangePreview(
            student_id=student.id,
            from_school_id=student.target_school_id,
            to_school_id=school.id,
            status=TargetSchoolChangeStatus.PREVIEW,
            preview={
                "shared_knowledge_count": len(shared),
                "new_knowledge_count": len(new_ids - old_ids),
                "removed_knowledge_count": len(old_ids - new_ids),
                "shared_knowledge_ids": [str(item) for item in shared],
                "new_knowledge_ids": [str(item) for item in new_ids - old_ids],
            },
            expires_at=datetime.now(UTC) + timedelta(hours=24),
            version=1,
        )
        self._session.add(preview)
        await self._session.commit()
        return preview

    async def apply(self, user_id: uuid.UUID, preview_id: uuid.UUID) -> TargetSchoolChangePreview:
        student = await self._student(user_id)
        preview = await self._session.scalar(
            select(TargetSchoolChangePreview)
            .where(
                TargetSchoolChangePreview.id == preview_id,
                TargetSchoolChangePreview.student_id == student.id,
            )
            .with_for_update()
        )
        if preview is None:
            raise AppError(404, "SCHOOL_CHANGE_PREVIEW_NOT_FOUND", "preview does not exist")
        if preview.status is not TargetSchoolChangeStatus.PREVIEW:
            return preview
        if preview.expires_at <= datetime.now(UTC):
            preview.status = TargetSchoolChangeStatus.EXPIRED
            await self._session.commit()
            raise AppError(409, "SCHOOL_CHANGE_PREVIEW_EXPIRED", "preview has expired")
        student.target_school_id = preview.to_school_id
        plan = await self._session.scalar(
            select(WeeklyPlan).where(
                WeeklyPlan.student_id == student.id, WeeklyPlan.status == PlanStatus.ACTIVE
            )
        )
        if plan is not None:
            new_ids = await self._school_knowledge_ids(preview.to_school_id)
            result = await self._session.execute(
                select(PlanTask, PlanTaskKnowledge.knowledge_id)
                .join(PlanTaskKnowledge, PlanTaskKnowledge.task_id == PlanTask.id)
                .where(PlanTask.plan_id == plan.id, PlanTask.status == TaskStatus.PENDING)
            )
            for task, knowledge_id in result.tuples().all():
                if not task.is_personal and task.origin is TaskOrigin.SYSTEM:
                    if knowledge_id not in new_ids:
                        task.status = TaskStatus.SUPERSEDED
        preview.status = TargetSchoolChangeStatus.APPLIED
        preview.applied_at = datetime.now(UTC)
        preview.version += 1
        await self._session.commit()
        return preview

    async def _school_knowledge_ids(self, school_id: uuid.UUID | None) -> set[uuid.UUID]:
        if school_id is None:
            return set()
        return set(
            (
                await self._session.scalars(
                    select(SchoolKnowledgeStat.knowledge_id).where(
                        SchoolKnowledgeStat.school_profile_id == school_id
                    )
                )
            ).all()
        )

    async def _student(self, user_id: uuid.UUID) -> Student:
        student = await self._session.scalar(select(Student).where(Student.user_id == user_id))
        if student is None:
            raise AppError(404, "STUDENT_PROFILE_NOT_FOUND", "student profile has not been created")
        return student
