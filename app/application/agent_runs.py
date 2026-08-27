from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.domain.enums import AgentRunStatus, ProposalStatus, ProposalType
from app.errors import AppError
from app.harness.contracts import RuntimeState
from app.harness.runner import AgentRunner
from app.harness.tools import PolicyGuard, ToolExecutor
from app.infrastructure.adapters.harness_store import (
    DatabaseCheckpointStore,
    DatabaseTraceRecorder,
)
from app.infrastructure.adapters.learning_tools import (
    apply_minor_proposals,
    build_learning_tool_registry,
)
from app.infrastructure.adapters.model_gateway import ModelRouter
from app.infrastructure.db.models import (
    AgentRun,
    AgentStep,
    BackgroundJob,
    Proposal,
    ToolInvocation,
)


class AgentDiagnosisService:
    def __init__(
        self,
        session: AsyncSession,
        factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self._session = session
        self._factory = factory
        self._settings = settings

    async def execute_job(self, job: BackgroundJob) -> AgentRun:
        student_id = uuid.UUID(str(job.payload["student_id"]))
        run = await self._session.scalar(select(AgentRun).where(AgentRun.job_id == job.id))
        if run is None:
            run = AgentRun(
                job_id=job.id,
                student_id=student_id,
                goal="FEEDBACK_DIAGNOSIS",
                status=AgentRunStatus.RUNNING,
                model_version=(
                    "fake-diagnosis-v1"
                    if self._settings.use_fake_model
                    else self._settings.qwen_plus_model
                ),
                prompt_version="diagnosis_v1",
                policy_version="agent_policy_v1",
            )
            self._session.add(run)
            await self._session.commit()
        registry = build_learning_tool_registry(self._factory, run.id, student_id)
        runner = AgentRunner(
            ModelRouter(self._settings),
            registry,
            ToolExecutor(registry, PolicyGuard()),
            DatabaseCheckpointStore(self._factory, run.id),
            DatabaseTraceRecorder(self._factory, run.id),
        )
        state = RuntimeState(str(run.id), str(student_id), run.goal)
        state.observations.append(
            {
                "reason_codes": list(job.payload.get("reason_codes", [])),
                "task_id": str(job.payload.get("task_id", "")),
            }
        )
        result = await runner.run(state)
        await apply_minor_proposals(self._factory, run.id)
        refreshed = await self._session.get(AgentRun, run.id, with_for_update=True)
        if refreshed is None:
            raise RuntimeError("agent run disappeared")
        refreshed.loop_count = result.state.loop_count
        refreshed.tool_call_count = result.state.tool_call_count
        refreshed.termination_reason = result.termination_reason
        refreshed.status = (
            AgentRunStatus.COMPLETED
            if result.termination_reason == "COMPLETED"
            else AgentRunStatus.STALLED
        )
        job.result = {
            "agent_run_id": str(run.id),
            "decision": result.decision,
            "termination_reason": result.termination_reason,
        }
        return refreshed


class AgentQueryService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def owned_run(
        self, user_id: uuid.UUID, run_id: uuid.UUID
    ) -> tuple[AgentRun, list[AgentStep], list[ToolInvocation], list[Proposal]]:
        run = await self._session.scalar(
            select(AgentRun)
            .join(BackgroundJob, BackgroundJob.id == AgentRun.job_id)
            .where(AgentRun.id == run_id, BackgroundJob.user_id == user_id)
        )
        if run is None:
            raise AppError(404, "AGENT_RUN_NOT_FOUND", "agent run does not exist")
        steps = list(
            (
                await self._session.scalars(
                    select(AgentStep)
                    .where(AgentStep.run_id == run.id)
                    .order_by(AgentStep.step_number)
                )
            ).all()
        )
        tools = list(
            (
                await self._session.scalars(
                    select(ToolInvocation).where(ToolInvocation.run_id == run.id)
                )
            ).all()
        )
        proposals = list(
            (await self._session.scalars(select(Proposal).where(Proposal.run_id == run.id))).all()
        )
        return run, steps, tools, proposals


class ProposalService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def decide(
        self,
        user_id: uuid.UUID,
        proposal_id: uuid.UUID,
        approve: bool,
        idempotency_key: str,
    ) -> tuple[Proposal, BackgroundJob | None]:
        proposal = await self._session.scalar(
            select(Proposal)
            .join(AgentRun, AgentRun.id == Proposal.run_id)
            .join(BackgroundJob, BackgroundJob.id == AgentRun.job_id)
            .where(Proposal.id == proposal_id, BackgroundJob.user_id == user_id)
            .with_for_update()
        )
        if proposal is None:
            raise AppError(404, "PROPOSAL_NOT_FOUND", "proposal does not exist")
        if proposal.status not in {ProposalStatus.AWAITING_CONFIRMATION, ProposalStatus.PENDING}:
            return proposal, None
        proposal.status = ProposalStatus.APPROVED if approve else ProposalStatus.REJECTED
        proposal.decided_at = datetime.now(UTC)
        job = None
        if approve and proposal.proposal_type is ProposalType.MAJOR_REPLAN:
            agent_run = await self._session.get(AgentRun, proposal.run_id)
            if agent_run is None:
                raise RuntimeError("proposal has no agent run")
            background = await self._session.get(BackgroundJob, agent_run.job_id)
            if background is None or background.user_id is None:
                raise RuntimeError("agent run has no owned job")
            job = await self._create_replan_job(background.user_id, proposal, idempotency_key)
        await self._session.commit()
        return proposal, job

    async def _create_replan_job(
        self, user_id: uuid.UUID, proposal: Proposal, idempotency_key: str
    ) -> BackgroundJob:
        from app.application.jobs import JobService

        return await JobService(self._session).create(
            user_id,
            "GENERATE_PLAN",
            {"start_date": date_today_iso(), "proposal_id": str(proposal.id)},
            idempotency_key,
            commit=False,
        )


def date_today_iso() -> str:
    return datetime.now(UTC).date().isoformat()
