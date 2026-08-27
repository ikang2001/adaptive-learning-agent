from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import select

from app.config import get_settings
from app.domain.enums import JobStatus
from app.infrastructure.db.models import BackgroundJob
from app.infrastructure.db.session import session_factory


async def dispatch_once() -> int:
    redis = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    try:
        async with session_factory() as session:
            jobs = list(
                (
                    await session.scalars(
                        select(BackgroundJob)
                        .where(
                            BackgroundJob.status == JobStatus.QUEUED,
                            BackgroundJob.available_at <= datetime.now(UTC),
                            BackgroundJob.dispatched_at.is_(None),
                        )
                        .order_by(BackgroundJob.created_at)
                        .limit(100)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            for job in jobs:
                await redis.enqueue_job("execute_job", str(job.id), _job_id=str(job.id))
                job.dispatched_at = datetime.now(UTC)
            await session.commit()
            return len(jobs)
    finally:
        await redis.aclose()


async def run_forever() -> None:
    while True:
        dispatched = await dispatch_once()
        await asyncio.sleep(0.2 if dispatched else 1.0)


if __name__ == "__main__":
    asyncio.run(run_forever())
