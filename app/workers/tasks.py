from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from app.application.account_purge import AccountPurgeService
from app.application.agent_runs import AgentDiagnosisService
from app.application.mock_exams import MockExamGenerationService
from app.application.plans import PlanGenerationService
from app.application.resources import ResourceService
from app.config import get_settings
from app.domain.enums import JobStatus
from app.infrastructure.db.models import BackgroundJob
from app.infrastructure.db.session import session_factory

logger = structlog.get_logger(__name__)


async def purge_due_accounts(ctx: dict[str, Any]) -> int:
    async with session_factory() as session:
        return await AccountPurgeService(session).purge_due()


async def execute_job(ctx: dict[str, Any], job_id: str) -> None:
    async with session_factory() as session:
        job = await session.get(BackgroundJob, uuid.UUID(job_id), with_for_update=True)
        if job is None or job.status in {JobStatus.SUCCEEDED, JobStatus.CANCELLED}:
            return
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(UTC)
        await session.commit()
    try:
        await _dispatch(job_id)
    except Exception as exc:
        await _mark_failed(job_id, type(exc).__name__)
        logger.exception("background_job_failed", job_id=job_id, error_type=type(exc).__name__)
        raise


async def _dispatch(job_id: str) -> None:
    async with session_factory() as session:
        job = await session.get(BackgroundJob, uuid.UUID(job_id), with_for_update=True)
        if job is None:
            return
        if job.job_type == "GENERATE_PLAN":
            await PlanGenerationService(session).execute_job(job)
        elif job.job_type == "AGENT_DIAGNOSIS":
            await AgentDiagnosisService(session, session_factory, get_settings()).execute_job(job)
        elif job.job_type == "GENERATE_MOCK":
            await MockExamGenerationService(session, get_settings()).execute_job(job)
        elif job.job_type == "PARSE_RESOURCE":
            run_id = uuid.UUID(str(job.payload["resource_import_run_id"]))
            run = await ResourceService(session, get_settings()).parse_run(run_id)
            job.result = {"resource_import_run_id": str(run.id), "status": run.status.value}
        else:
            raise ValueError(f"unsupported job type: {job.job_type}")
        if job.status is JobStatus.RUNNING:
            job.status = JobStatus.SUCCEEDED
            job.finished_at = datetime.now(UTC)
        await session.commit()


async def _mark_failed(job_id: str, error_code: str) -> None:
    async with session_factory() as session:
        job = await session.get(BackgroundJob, uuid.UUID(job_id))
        if job is None:
            return
        job.status = JobStatus.FAILED
        job.error_code = error_code[:64]
        job.finished_at = datetime.now(UTC)
        await session.commit()
