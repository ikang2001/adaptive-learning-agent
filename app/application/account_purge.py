from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import (
    AccountDeletion,
    BackgroundJob,
    DomainEvent,
    ReviewDecision,
    Student,
    User,
)


class AccountPurgeService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def purge_due(self, limit: int = 100) -> int:
        requests = list(
            (
                await self._session.scalars(
                    select(AccountDeletion)
                    .where(
                        AccountDeletion.purge_after <= datetime.now(UTC),
                        AccountDeletion.cancelled_at.is_(None),
                        AccountDeletion.purged_at.is_(None),
                    )
                    .order_by(AccountDeletion.purge_after)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        for request in requests:
            student_id = await self._session.scalar(
                select(Student.id).where(Student.user_id == request.user_id)
            )
            aggregate_ids = [request.user_id]
            if student_id:
                aggregate_ids.append(student_id)
            await self._session.execute(
                delete(DomainEvent).where(DomainEvent.aggregate_id.in_(aggregate_ids))
            )
            await self._session.execute(
                delete(BackgroundJob).where(BackgroundJob.user_id == request.user_id)
            )
            await self._session.execute(
                delete(ReviewDecision).where(ReviewDecision.reviewer_user_id == request.user_id)
            )
            await self._session.execute(delete(User).where(User.id == request.user_id))
        await self._session.commit()
        return len(requests)
