from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import JobStatus
from app.errors import AppError
from app.infrastructure.db.models import BackgroundJob, DomainEvent


class JobService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        user_id: uuid.UUID,
        job_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
        *,
        commit: bool = True,
    ) -> BackgroundJob:
        existing = await self._session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.user_id == user_id,
                BackgroundJob.job_type == job_type,
                BackgroundJob.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.payload != payload:
                raise AppError(
                    409,
                    "IDEMPOTENCY_CONFLICT",
                    "idempotency key was already used with a different request",
                )
            return existing
        job = BackgroundJob(
            user_id=user_id,
            job_type=job_type,
            status=JobStatus.QUEUED,
            payload=payload,
            idempotency_key=idempotency_key,
            available_at=datetime.now(UTC),
        )
        self._session.add(job)
        await self._session.flush()
        self._session.add(
            DomainEvent(
                aggregate_type="BackgroundJob",
                aggregate_id=job.id,
                aggregate_version=1,
                event_type="BackgroundJobCreated",
                payload={"job_id": str(job.id), "job_type": job_type},
                strategy_versions={},
                occurred_at=datetime.now(UTC),
            )
        )
        if commit:
            await self._session.commit()
        return job

    async def get_owned(self, user_id: uuid.UUID, job_id: uuid.UUID) -> BackgroundJob:
        job = await self._session.get(BackgroundJob, job_id)
        if job is None or job.user_id != user_id:
            raise AppError(404, "JOB_NOT_FOUND", "job does not exist")
        return job


def request_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
