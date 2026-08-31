from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.account_purge import AccountPurgeService
from app.application.agent_runs import AgentDiagnosisService
from app.application.mock_exams import MockExamGenerationService
from app.application.plans import PlanGenerationService
from app.application.resources import ResourceService
from app.config import get_settings
from app.domain.enums import AgentRunStatus, JobStatus, ProposalStatus
from app.harness.errors import (
    LeaseUnavailableError,
    ModelUnavailableError,
    RunCancelledError,
    StaleWorkerError,
    ToolTransientError,
)
from app.infrastructure.db.models import AgentRun, BackgroundJob, Proposal, ShadowEvaluation
from app.infrastructure.db.session import session_factory
from app.observability.metrics import AGENT_DEAD_LETTERS, AGENT_JOB_RETRIES

logger = structlog.get_logger(__name__)

TERMINAL_JOB_STATUSES = {
    JobStatus.SUCCEEDED,
    JobStatus.FAILED,
    JobStatus.DEAD_LETTER,
    JobStatus.CANCELLED,
    JobStatus.WAITING_FOR_REVIEW,
}


async def purge_due_accounts(ctx: dict[str, Any]) -> int:
    del ctx
    async with session_factory() as session:
        return await AccountPurgeService(session).purge_due()


async def execute_job(ctx: dict[str, Any], job_id: str) -> None:
    del ctx
    parsed_id = uuid.UUID(job_id)
    async with session_factory() as session:
        job = await session.get(BackgroundJob, parsed_id, with_for_update=True)
        if job is None or job.status in TERMINAL_JOB_STATUSES:
            return
        if job.status is JobStatus.RUNNING:
            logger.info("duplicate_running_job_ignored", job_id=job_id)
            return
        now = datetime.now(UTC)
        if job.status is JobStatus.RETRY_WAIT and job.next_retry_at and job.next_retry_at > now:
            return
        job.status = JobStatus.RUNNING
        job.started_at = now
        job.attempt_count += 1
        job.next_retry_at = None
        await _mark_proposal_applying(session, job)
        await session.commit()
    try:
        await _dispatch(job_id)
    except (LeaseUnavailableError, StaleWorkerError):
        logger.info("duplicate_agent_worker_fenced", job_id=job_id)
    except RunCancelledError:
        await _mark_cancelled(parsed_id)
        logger.info("background_job_cancelled", job_id=job_id)
    except Exception as exc:
        await _handle_failure(parsed_id, exc)
        logger.exception("background_job_failed", job_id=job_id, error_type=type(exc).__name__)


async def _dispatch(job_id: str) -> None:
    async with session_factory() as session:
        job = await session.get(BackgroundJob, uuid.UUID(job_id), with_for_update=True)
        if job is None or job.status is not JobStatus.RUNNING:
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
        elif job.job_type == "SHADOW_EVALUATION":
            from app.application.shadow_evaluations import ShadowEvaluationService

            await ShadowEvaluationService(session, get_settings()).execute_job(job)
        else:
            raise ValueError(f"unsupported job type: {job.job_type}")
        if job.status is JobStatus.RUNNING:
            cancelled = bool(job.result and job.result.get("termination_reason") == "CANCELLED")
            job.status = JobStatus.CANCELLED if cancelled else JobStatus.SUCCEEDED
            job.finished_at = datetime.now(UTC)
            if not cancelled:
                await _mark_proposal_applied(session, job)
        await session.commit()


async def _handle_failure(job_id: uuid.UUID, exc: Exception) -> None:
    async with session_factory() as session:
        job = await session.get(BackgroundJob, job_id, with_for_update=True)
        if job is None:
            return
        code = _error_code(exc)
        job.error_code = code[:64]
        job.finished_at = datetime.now(UTC)
        if _is_transient(exc) and job.attempt_count < job.max_attempts:
            delay = _retry_delay(job.attempt_count)
            retry_at = datetime.now(UTC) + timedelta(seconds=delay)
            job.status = JobStatus.RETRY_WAIT
            job.next_retry_at = retry_at
            job.available_at = retry_at
            job.dispatched_at = None
            AGENT_JOB_RETRIES.labels(job.job_type).inc()
        elif _is_transient(exc):
            job.status = JobStatus.DEAD_LETTER
            job.dead_lettered_at = datetime.now(UTC)
            AGENT_DEAD_LETTERS.labels(job.job_type).inc()
            await _mark_proposal_failed(session, job, code)
        else:
            job.status = JobStatus.FAILED
            await _mark_proposal_failed(session, job, code)
        if job.job_type == "SHADOW_EVALUATION" and job.status in {
            JobStatus.FAILED,
            JobStatus.DEAD_LETTER,
        }:
            evaluation_id = job.payload.get("shadow_evaluation_id")
            evaluation = (
                await session.get(ShadowEvaluation, uuid.UUID(str(evaluation_id)))
                if evaluation_id
                else None
            )
            if evaluation is not None:
                evaluation.status = "FAILED"
                evaluation.error_code = code[:64]
                evaluation.finished_at = datetime.now(UTC)
        await session.commit()


async def _mark_cancelled(job_id: uuid.UUID) -> None:
    async with session_factory() as session:
        job = await session.get(BackgroundJob, job_id, with_for_update=True)
        if job is None:
            return
        job.status = JobStatus.CANCELLED
        job.finished_at = datetime.now(UTC)
        run = await session.scalar(
            select(AgentRun).where(AgentRun.job_id == job.id).with_for_update()
        )
        if run is not None:
            run.status = AgentRunStatus.CANCELLED
            run.cancelled_at = datetime.now(UTC)
            run.termination_reason = "CANCELLED"
        await session.commit()


async def reconcile_stale_jobs(ctx: dict[str, Any]) -> int:
    del ctx
    settings = get_settings()
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=settings.job_reconciliation_seconds)
    reconciled = 0
    async with session_factory() as session:
        jobs = list(
            (
                await session.scalars(
                    select(BackgroundJob)
                    .where(
                        BackgroundJob.status == JobStatus.RUNNING,
                        BackgroundJob.started_at < cutoff,
                    )
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        for job in jobs:
            if job.job_type == "AGENT_DIAGNOSIS":
                run = await session.scalar(select(AgentRun).where(AgentRun.job_id == job.id))
                if run and run.lease_expires_at and run.lease_expires_at > now:
                    continue
            elif job.started_at and job.started_at > now - timedelta(
                seconds=max(settings.job_reconciliation_seconds, 660)
            ):
                continue
            if job.attempt_count >= job.max_attempts:
                job.status = JobStatus.DEAD_LETTER
                job.dead_lettered_at = now
                await _mark_proposal_failed(session, job, "STALE_JOB_EXHAUSTED")
            else:
                job.status = JobStatus.RETRY_WAIT
                job.next_retry_at = now
                job.available_at = now
                job.dispatched_at = None
                job.error_code = "STALE_JOB_RECONCILED"
            reconciled += 1
        await session.commit()
    return reconciled


async def expire_proposals(ctx: dict[str, Any]) -> int:
    del ctx
    now = datetime.now(UTC)
    count = 0
    async with session_factory() as session:
        proposals = list(
            (
                await session.scalars(
                    select(Proposal)
                    .where(
                        Proposal.status == ProposalStatus.AWAITING_CONFIRMATION,
                        Proposal.approval_expires_at.is_not(None),
                        Proposal.approval_expires_at <= now,
                    )
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        for proposal in proposals:
            proposal.status = ProposalStatus.EXPIRED
            proposal.decided_at = now
            count += 1
        await session.commit()
    return count


async def _mark_proposal_applying(session: AsyncSession, job: BackgroundJob) -> None:
    proposal = await _job_proposal(session, job)
    if proposal and proposal.status is ProposalStatus.APPROVED:
        proposal.status = ProposalStatus.APPLYING


async def _mark_proposal_applied(session: AsyncSession, job: BackgroundJob) -> None:
    proposal = await _job_proposal(session, job)
    if proposal and proposal.status is ProposalStatus.APPLYING:
        proposal.status = ProposalStatus.APPLIED
        proposal.applied_at = datetime.now(UTC)
        proposal.apply_error_code = None


async def _mark_proposal_failed(session: AsyncSession, job: BackgroundJob, error_code: str) -> None:
    proposal = await _job_proposal(session, job)
    if proposal and proposal.status in {ProposalStatus.APPROVED, ProposalStatus.APPLYING}:
        proposal.status = ProposalStatus.APPLY_FAILED
        proposal.apply_error_code = error_code[:64]


async def _job_proposal(session: AsyncSession, job: BackgroundJob) -> Proposal | None:
    value = job.payload.get("proposal_id")
    if not value:
        return None
    return await session.get(Proposal, uuid.UUID(str(value)), with_for_update=True)


def _is_transient(exc: Exception) -> bool:
    return isinstance(
        exc,
        (
            ModelUnavailableError,
            ToolTransientError,
            httpx.TimeoutException,
            httpx.NetworkError,
            RedisError,
            OperationalError,
            TimeoutError,
        ),
    )


def _error_code(exc: Exception) -> str:
    return str(getattr(exc, "code", type(exc).__name__))


def _retry_delay(attempt_count: int) -> float:
    settings = get_settings()
    raw = min(
        settings.job_retry_max_seconds,
        settings.job_retry_base_seconds * (2 ** max(0, attempt_count - 1)),
    )
    result: float = float(raw * random.uniform(0.8, 1.2))
    return result if result > 0.1 else 0.1
