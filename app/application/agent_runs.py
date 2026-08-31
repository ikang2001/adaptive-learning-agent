from __future__ import annotations

import os
import socket
import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.domain.enums import AgentRunStatus, ProposalStatus, ProposalType
from app.errors import AppError
from app.harness.contracts import RuntimePhase, RuntimeState, TerminationReason
from app.harness.errors import (
    AgentRunExecutionError,
    ModelUnavailableError,
    StaleWorkerError,
    ToolUpstreamError,
)
from app.harness.lease import RunLeaseManager, assert_fence
from app.harness.policy import AgentPolicyEngine
from app.harness.runner import AgentRunner, AgentRunnerConfig
from app.harness.tools import ToolAvailabilityContext, ToolExecutor
from app.infrastructure.adapters.harness_store import (
    DatabaseCheckpointStore,
    DatabaseTraceRecorder,
)
from app.infrastructure.adapters.learning_tools import (
    apply_approved_minor_proposal,
    apply_minor_proposals,
    build_learning_tool_registry,
)
from app.infrastructure.adapters.model_gateway import ModelRouter
from app.infrastructure.adapters.tool_ledger import DatabaseToolExecutionLedger
from app.infrastructure.db.models import (
    AgentRun,
    AgentStep,
    BackgroundJob,
    Checkpoint,
    GuardrailEvent,
    ModelInvocation,
    PlanTask,
    Proposal,
    QuestionAttempt,
    ToolInvocation,
    UserRole,
    WeeklyPlan,
)
from app.observability.metrics import AGENT_RESUMES

logger = structlog.get_logger(__name__)


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
                    "fake-diagnosis-v2"
                    if self._settings.use_fake_model
                    else self._settings.qwen_plus_model
                ),
                prompt_version="diagnosis_v3",
                policy_version=AgentPolicyEngine.version,
            )
            self._session.add(run)
            await self._session.commit()
        owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}"
        lease_manager = RunLeaseManager(
            self._factory,
            run.id,
            owner,
            lease_seconds=self._settings.agent_lease_seconds,
            heartbeat_seconds=self._settings.agent_heartbeat_seconds,
        )
        async with lease_manager.hold() as lease:
            logger.info(
                "agent_run_lease_acquired",
                run_id=str(run.id),
                job_id=str(job.id),
                fencing_token=lease.fencing_token,
            )
            checkpoints = DatabaseCheckpointStore(self._factory, run.id, lease.fencing_token)
            state = await checkpoints.load_latest()
            if state is None:
                roles = tuple(
                    role.value
                    for role in (
                        await self._session.scalars(
                            select(UserRole.role).where(UserRole.user_id == job.user_id)
                        )
                    ).all()
                )
                state = RuntimeState(
                    str(run.id),
                    str(student_id),
                    run.goal,
                    user_id=str(job.user_id) if job.user_id else None,
                    roles=roles,
                    fencing_token=lease.fencing_token,
                )
                state.observations.append(
                    {
                        "reason_codes": list(job.payload.get("reason_codes", [])),
                        "task_id": str(job.payload.get("task_id", "")),
                    }
                )
            else:
                state.fencing_token = lease.fencing_token
                state.resumed = True
                self._prepare_retryable_state(state)
                async with self._factory() as resume_session:
                    resumed_run = await assert_fence(
                        resume_session, run.id, lease.fencing_token, owner
                    )
                    resumed_run.resumed_count += 1
                    await resume_session.commit()
                    AGENT_RESUMES.inc()
                    logger.info(
                        "agent_run_resumed",
                        run_id=str(run.id),
                        job_id=str(job.id),
                        step=state.loop_count,
                        fencing_token=lease.fencing_token,
                    )
            registry = build_learning_tool_registry(
                self._factory, run.id, student_id, lease.fencing_token
            )
            tool_context = ToolAvailabilityContext(
                environment=self._settings.app_env,
                roles=frozenset(state.roles),
                feature_flags=frozenset(self._settings.agent_tool_feature_flags),
            )
            executor = ToolExecutor(
                registry,
                AgentPolicyEngine(),
                ledger=DatabaseToolExecutionLedger(self._factory, run.id, lease.fencing_token),
                availability=tool_context,
                permissions=frozenset({"student:evidence:read", "student:proposal:create"}),
            )
            runner = AgentRunner(
                ModelRouter(self._settings),
                registry,
                executor,
                checkpoints,
                DatabaseTraceRecorder(self._factory, run.id, lease.fencing_token),
                AgentRunnerConfig(
                    max_steps=self._settings.agent_max_steps,
                    max_tool_calls=self._settings.agent_max_tool_calls,
                    max_runtime_seconds=self._settings.agent_max_runtime_seconds,
                    max_model_calls=self._settings.agent_max_model_calls,
                    max_input_tokens=self._settings.agent_max_input_tokens,
                    max_output_tokens=self._settings.agent_max_output_tokens,
                    max_total_tokens=self._settings.agent_max_total_tokens,
                    max_repair_calls=self._settings.agent_max_repair_calls,
                ),
                control=lease_manager,
                tool_context=tool_context,
            )
            result = await runner.run(state)
            if result.termination_reason == "STALE_WORKER":
                raise StaleWorkerError("agent run lost its fencing token")
            if result.termination_reason == "COMPLETED":
                await apply_minor_proposals(self._factory, run.id, lease.fencing_token)
            await self._finalize_run(run.id, lease.fencing_token, result)
            logger.info(
                "agent_run_finished",
                run_id=str(run.id),
                job_id=str(job.id),
                termination_reason=result.termination_reason,
                step=result.state.loop_count,
                model_calls=result.state.model_call_count,
                tool_calls=result.state.tool_call_count,
            )
            job.result = {
                "agent_run_id": str(run.id),
                "decision": result.decision,
                "termination_reason": result.termination_reason,
                "resumed": result.state.resumed,
            }
            self._raise_for_retry_or_failure(result)
        refreshed = await self._session.scalar(
            select(AgentRun).where(AgentRun.id == run.id).execution_options(populate_existing=True)
        )
        if refreshed is None:
            raise RuntimeError("agent run disappeared")
        return refreshed

    @staticmethod
    def _prepare_retryable_state(state: RuntimeState) -> None:
        retryable = {
            "MODEL_UNAVAILABLE",
            "TOOL_TIMEOUT",
            "TOOL_RATE_LIMITED",
            "TOOL_UPSTREAM_ERROR",
            "TOOL_OUTCOME_UNKNOWN",
        }
        if state.phase is not RuntimePhase.TERMINATED or state.last_error_code not in retryable:
            return
        state.phase = (
            RuntimePhase.TOOL_PENDING
            if state.pending_tool_call is not None and state.pending_step_id is not None
            else RuntimePhase.READY
        )
        state.termination_reason = None
        state.last_error_code = None
        state.final_action = None

    @staticmethod
    def _raise_for_retry_or_failure(result: object) -> None:
        from app.harness.runner import AgentRunResult

        if not isinstance(result, AgentRunResult):
            raise TypeError("result must be AgentRunResult")
        reason = TerminationReason(result.termination_reason)
        error_code = result.state.last_error_code
        if reason is TerminationReason.MODEL_UNAVAILABLE:
            raise ModelUnavailableError("agent model is unavailable")
        if error_code in {
            "TOOL_TIMEOUT",
            "TOOL_RATE_LIMITED",
            "TOOL_UPSTREAM_ERROR",
            "TOOL_OUTCOME_UNKNOWN",
        }:
            raise ToolUpstreamError(error_code)
        if reason not in {
            TerminationReason.COMPLETED,
            TerminationReason.LOOP_STALLED,
            TerminationReason.CANCELLED,
        }:
            raise AgentRunExecutionError(reason, error_code)

    async def _finalize_run(
        self,
        run_id: uuid.UUID,
        fencing_token: int,
        result: object,
    ) -> None:
        from app.harness.runner import AgentRunResult

        if not isinstance(result, AgentRunResult):
            raise TypeError("result must be AgentRunResult")
        async with self._factory() as session:
            run = await assert_fence(session, run_id, fencing_token)
            run.loop_count = result.state.loop_count
            run.model_call_count = result.state.model_call_count
            run.tool_call_count = result.state.tool_call_count
            run.input_tokens = result.state.input_tokens
            run.output_tokens = result.state.output_tokens
            run.termination_reason = result.termination_reason
            if result.termination_reason == "COMPLETED":
                run.status = AgentRunStatus.COMPLETED
            elif result.termination_reason == "LOOP_STALLED":
                run.status = AgentRunStatus.STALLED
            elif result.termination_reason == "CANCELLED":
                run.status = AgentRunStatus.CANCELLED
                run.cancelled_at = datetime.now(UTC)
            else:
                run.status = AgentRunStatus.FAILED
            await session.commit()


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

    async def replay(self, user_id: uuid.UUID, run_id: uuid.UUID) -> dict[str, object]:
        run, steps, tools, proposals = await self.owned_run(user_id, run_id)
        models = list(
            (
                await self._session.scalars(
                    select(ModelInvocation)
                    .where(ModelInvocation.run_id == run.id)
                    .order_by(ModelInvocation.created_at)
                )
            ).all()
        )
        checkpoints = list(
            (
                await self._session.scalars(
                    select(Checkpoint)
                    .where(Checkpoint.run_id == run.id)
                    .order_by(Checkpoint.step_number)
                )
            ).all()
        )
        guardrails = list(
            (
                await self._session.scalars(
                    select(GuardrailEvent)
                    .where(GuardrailEvent.run_id == run.id)
                    .order_by(GuardrailEvent.created_at)
                )
            ).all()
        )
        return {
            "run_id": str(run.id),
            "status": run.status.value,
            "termination_reason": run.termination_reason,
            "read_only": True,
            "side_effects_executed": False,
            "timeline": [
                {
                    "step_number": step.step_number,
                    "action": step.action,
                    "model_attempts": [
                        {
                            "attempt_number": item.attempt_number,
                            "purpose": item.purpose,
                            "model_name": item.model_name,
                            "status": item.status,
                            "input_tokens": item.input_tokens,
                            "output_tokens": item.output_tokens,
                            "latency_ms": item.latency_ms,
                            "error_code": item.error_code,
                        }
                        for item in models
                        if item.step_id == step.id
                    ],
                    "tool": next(
                        (
                            {
                                "name": item.tool_name,
                                "status": item.status,
                                "retry_count": item.retry_count,
                                "error_code": item.error_code,
                                "replayed": item.replayed,
                                "observation": item.observation_digest,
                            }
                            for item in tools
                            if item.step_id == step.id
                        ),
                        None,
                    ),
                    "guardrails": [
                        {
                            "decision": item.decision,
                            "reason_code": item.reason_code,
                            "tool_name": item.tool_name,
                        }
                        for item in guardrails
                        if item.step_id == step.id
                    ],
                    "checkpoint": next(
                        (
                            {
                                "version": item.checkpoint_version,
                                "state_hash": item.state_hash,
                                "resume_safe": item.resume_safe,
                                "fencing_token": item.fencing_token,
                            }
                            for item in checkpoints
                            if item.step_number == step.step_number
                        ),
                        None,
                    ),
                }
                for step in steps
            ],
            "proposal_ids": [str(item.id) for item in proposals],
        }

    async def request_cancel(self, user_id: uuid.UUID, run_id: uuid.UUID) -> AgentRun:
        run = await self._session.scalar(
            select(AgentRun)
            .join(BackgroundJob, BackgroundJob.id == AgentRun.job_id)
            .where(AgentRun.id == run_id, BackgroundJob.user_id == user_id)
            .with_for_update()
        )
        if run is None:
            raise AppError(404, "AGENT_RUN_NOT_FOUND", "agent run does not exist")
        if run.status in {
            AgentRunStatus.COMPLETED,
            AgentRunStatus.FAILED,
            AgentRunStatus.STALLED,
            AgentRunStatus.CANCELLED,
        }:
            return run
        run.status = AgentRunStatus.CANCEL_REQUESTED
        run.cancel_requested_at = datetime.now(UTC)
        await self._session.commit()
        return run


class ProposalService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def decide(
        self,
        user_id: uuid.UUID,
        proposal_id: uuid.UUID,
        approve: bool,
        idempotency_key: str,
        review_reason: str | None = None,
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
        if proposal.approval_expires_at and proposal.approval_expires_at <= datetime.now(UTC):
            proposal.status = ProposalStatus.EXPIRED
            proposal.decided_at = datetime.now(UTC)
            await self._session.commit()
            return proposal, None
        if approve and not await self._evidence_is_current(proposal):
            proposal.status = ProposalStatus.EXPIRED
            proposal.decided_at = datetime.now(UTC)
            proposal.apply_error_code = "EVIDENCE_STALE"
            await self._session.commit()
            return proposal, None
        proposal.status = ProposalStatus.APPROVED if approve else ProposalStatus.REJECTED
        proposal.decided_at = datetime.now(UTC)
        proposal.reviewer_user_id = user_id
        proposal.review_reason = review_reason
        job = None
        if approve and proposal.proposal_type is ProposalType.MINOR_ADJUST:
            await apply_approved_minor_proposal(self._session, proposal)
        elif approve and proposal.proposal_type is ProposalType.MAJOR_REPLAN:
            agent_run = await self._session.get(AgentRun, proposal.run_id)
            if agent_run is None:
                raise RuntimeError("proposal has no agent run")
            background = await self._session.get(BackgroundJob, agent_run.job_id)
            if background is None or background.user_id is None:
                raise RuntimeError("agent run has no owned job")
            job = await self._create_replan_job(background.user_id, proposal, idempotency_key)
        elif approve:
            proposal.status = ProposalStatus.APPLY_FAILED
            proposal.apply_error_code = "DETERMINISTIC_APPLIER_NOT_AVAILABLE"
        await self._session.commit()
        return proposal, job

    async def _evidence_is_current(self, proposal: Proposal) -> bool:
        if not proposal.evidence_snapshot:
            return False
        for evidence in proposal.evidence_snapshot:
            source_id = evidence.get("source_id")
            version = evidence.get("version")
            if not source_id or not version:
                return False
            if evidence.get("evidence_type") == "QUESTION_ATTEMPT":
                attempt = await self._session.get(QuestionAttempt, uuid.UUID(str(source_id)))
                if (
                    attempt is None
                    or attempt.student_id != proposal.student_id
                    or attempt.created_at.isoformat() != str(version)
                ):
                    return False
            elif evidence.get("evidence_type") == "PLAN_TASK":
                task = await self._session.scalar(
                    select(PlanTask)
                    .join(WeeklyPlan, WeeklyPlan.id == PlanTask.plan_id)
                    .where(
                        PlanTask.id == uuid.UUID(str(source_id)),
                        WeeklyPlan.student_id == proposal.student_id,
                    )
                )
                if task is None or str(task.version) != str(version):
                    return False
            else:
                return False
        return True

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
