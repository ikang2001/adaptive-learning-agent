from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.jobs import JobService
from app.config import Settings
from app.domain.enums import Role
from app.errors import AppError
from app.harness.contracts import ModelGateway, RuntimeState
from app.harness.fakes import MemoryCheckpointStore, MemoryTraceRecorder
from app.harness.policy import AgentPolicyEngine
from app.harness.runner import AgentRunner
from app.harness.tools import (
    ToolDefinition,
    ToolExecutor,
    ToolRegistry,
    ToolRisk,
    ToolSideEffect,
)
from app.infrastructure.adapters.learning_tools import build_learning_tool_registry
from app.infrastructure.adapters.model_gateway import FakeDiagnosisModelGateway, QwenModelGateway
from app.infrastructure.db.models import (
    AgentRun,
    AgentStep,
    BackgroundJob,
    Proposal,
    ShadowEvaluation,
    UserRole,
)
from app.infrastructure.db.session import session_factory


class ShadowEvaluationService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def request(
        self,
        user_id: uuid.UUID,
        source_run_id: uuid.UUID,
        idempotency_key: str,
    ) -> tuple[ShadowEvaluation, BackgroundJob]:
        await self._require_admin(user_id)
        if not self._settings.agent_shadow_enabled:
            raise AppError(409, "SHADOW_EVALUATION_DISABLED", "shadow evaluation is disabled")
        run = await self._session.get(AgentRun, source_run_id)
        if run is None:
            raise AppError(404, "AGENT_RUN_NOT_FOUND", "agent run does not exist")
        existing_job = await self._session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.user_id == user_id,
                BackgroundJob.job_type == "SHADOW_EVALUATION",
                BackgroundJob.idempotency_key == idempotency_key,
            )
        )
        if existing_job is not None:
            evaluation = await self._session.scalar(
                select(ShadowEvaluation).where(ShadowEvaluation.job_id == existing_job.id)
            )
            if evaluation is None:
                raise RuntimeError("shadow job has no evaluation")
            return evaluation, existing_job
        candidate_model = self._candidate_model()
        evaluation = ShadowEvaluation(
            source_run_id=run.id,
            requested_by_user_id=user_id,
            status="QUEUED",
            baseline_model=run.model_version,
            baseline_prompt_version=run.prompt_version,
            baseline_decision=None,
            baseline_confidence=None,
            candidate_model=candidate_model,
            candidate_prompt_version=self._settings.agent_shadow_prompt_version,
        )
        self._session.add(evaluation)
        await self._session.flush()
        job = await JobService(self._session).create(
            user_id,
            "SHADOW_EVALUATION",
            {"shadow_evaluation_id": str(evaluation.id)},
            idempotency_key,
            commit=False,
        )
        evaluation.job_id = job.id
        await self._session.commit()
        return evaluation, job

    async def get_owned(self, user_id: uuid.UUID, evaluation_id: uuid.UUID) -> ShadowEvaluation:
        evaluation = await self._session.get(ShadowEvaluation, evaluation_id)
        if evaluation is None or evaluation.requested_by_user_id != user_id:
            raise AppError(404, "SHADOW_EVALUATION_NOT_FOUND", "evaluation does not exist")
        return evaluation

    async def execute_job(self, job: BackgroundJob) -> ShadowEvaluation:
        evaluation_id = uuid.UUID(str(job.payload["shadow_evaluation_id"]))
        evaluation = await self._session.get(ShadowEvaluation, evaluation_id, with_for_update=True)
        if evaluation is None:
            raise AppError(404, "SHADOW_EVALUATION_NOT_FOUND", "evaluation does not exist")
        run = await self._session.get(AgentRun, evaluation.source_run_id)
        if run is None:
            raise RuntimeError("shadow evaluation source run disappeared")
        final_step = await self._session.scalar(
            select(AgentStep)
            .where(AgentStep.run_id == run.id, AgentStep.action_type == "FINAL")
            .order_by(AgentStep.step_number.desc())
            .limit(1)
        )
        evaluation.status = "RUNNING"
        evaluation.started_at = datetime.now(UTC)
        evaluation.baseline_decision = final_step.decision if final_step else None
        evaluation.baseline_confidence = final_step.confidence if final_step else None
        if final_step is None:
            proposal = await self._session.scalar(
                select(Proposal)
                .where(Proposal.run_id == run.id)
                .order_by(Proposal.created_at.desc())
                .limit(1)
            )
            if proposal is not None:
                evaluation.baseline_decision = proposal.proposal_type.value
                evaluation.baseline_confidence = proposal.confidence

        source_job = await self._session.get(BackgroundJob, run.job_id)
        state = RuntimeState(str(evaluation.id), str(run.student_id), run.goal)
        state.observations = [
            {
                "reason_codes": list(source_job.payload.get("reason_codes", []))
                if source_job
                else [],
                "task_id": str(source_job.payload.get("task_id", "")) if source_job else "",
                "shadow_source_run_id": str(run.id),
            }
        ]
        source_registry = build_learning_tool_registry(
            session_factory, run.id, run.student_id, run.fencing_token
        )
        registry = self._dry_run_registry(source_registry)
        if self._settings.use_fake_model:
            gateway: ModelGateway = FakeDiagnosisModelGateway()
        else:
            gateway = QwenModelGateway(self._settings, evaluation.candidate_model)
        runner = AgentRunner(
            gateway,
            registry,
            ToolExecutor(
                registry,
                AgentPolicyEngine(),
                permissions=frozenset({"student:evidence:read", "student:proposal:create"}),
            ),
            MemoryCheckpointStore(),
            MemoryTraceRecorder(),
        )
        result = await runner.run(state)
        evaluation.candidate_decision = result.decision
        evaluation.candidate_confidence = result.confidence
        evaluation.comparison = {
            "decision_match": evaluation.baseline_decision == result.decision,
            "confidence_delta": (
                result.confidence - evaluation.baseline_confidence
                if evaluation.baseline_confidence is not None
                else None
            ),
            "side_effects_executed": False,
            "termination_reason": result.termination_reason,
        }
        evaluation.status = "SUCCEEDED" if result.termination_reason == "COMPLETED" else "FAILED"
        evaluation.error_code = (
            None if result.termination_reason == "COMPLETED" else result.termination_reason
        )
        evaluation.finished_at = datetime.now(UTC)
        job.result = {"shadow_evaluation_id": str(evaluation.id), "status": evaluation.status}
        return evaluation

    @staticmethod
    def _dry_run_registry(source: ToolRegistry) -> ToolRegistry:
        registry = ToolRegistry()
        for definition in source.definitions():
            if definition.side_effect_level is ToolSideEffect.NONE:
                registry.register(definition)
                continue

            async def simulate(
                arguments: dict[str, object], tool_name: str = definition.name
            ) -> dict[str, object]:
                del arguments
                return {
                    "shadow": True,
                    "tool_name": tool_name,
                    "side_effect_executed": False,
                }

            dry_definition: ToolDefinition = replace(
                definition,
                handler=simulate,
                risk=ToolRisk.READ,
                side_effect_level=ToolSideEffect.NONE,
                idempotency_required=False,
                required_permissions=frozenset(),
                requires_confirmation=False,
                reconcile_handler=None,
            )
            registry.register(dry_definition)
        return registry

    def _candidate_model(self) -> str:
        if self._settings.use_fake_model:
            return FakeDiagnosisModelGateway.model_name
        if not self._settings.agent_shadow_model:
            raise AppError(
                422,
                "SHADOW_MODEL_REQUIRED",
                "configure AGENT_SHADOW_MODEL before using real shadow evaluation",
            )
        return self._settings.agent_shadow_model

    async def _require_admin(self, user_id: uuid.UUID) -> None:
        role = await self._session.scalar(
            select(UserRole.role).where(UserRole.user_id == user_id, UserRole.role == Role.ADMIN)
        )
        if role is None:
            raise AppError(403, "ADMIN_REQUIRED", "shadow evaluation requires ADMIN")
