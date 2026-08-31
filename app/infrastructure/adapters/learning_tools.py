from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import ProposalStatus, ProposalType, TaskOrigin
from app.errors import AppError
from app.harness.lease import assert_fence
from app.harness.retry import RetryPolicy
from app.harness.tools import (
    ReconcileHandler,
    ToolDefinition,
    ToolHandler,
    ToolRegistry,
    ToolRisk,
    ToolSideEffect,
)
from app.infrastructure.db.models import (
    AgentRun,
    BackgroundJob,
    PlanTask,
    PlanTaskKnowledge,
    Proposal,
    QuestionAttempt,
    SchoolKnowledgeStat,
    Student,
    StudentAvailability,
    StudentAvailabilityTemplate,
    StudentKnowledgeState,
    WeeklyPlan,
)


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SearchRecentAttemptsArgs(ToolArgs):
    limit: int = Field(default=10, ge=1, le=20)


class EmptyArgs(ToolArgs):
    pass


class ProposalArgs(ToolArgs):
    reason_codes: list[str] = Field(min_length=1, max_length=16)
    confidence: float = Field(ge=0, le=1)
    adjustment_factor: float = Field(default=0.8, ge=0.75, le=1.25)


def build_learning_tool_registry(
    factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    student_id: uuid.UUID,
    fencing_token: int,
) -> ToolRegistry:
    registry = ToolRegistry()

    async def search_recent_attempts(arguments: dict[str, Any]) -> dict[str, Any]:
        limit = min(max(int(arguments.get("limit", 10)), 1), 20)
        async with factory() as session:
            attempts = list(
                (
                    await session.scalars(
                        select(QuestionAttempt)
                        .where(QuestionAttempt.student_id == student_id)
                        .order_by(QuestionAttempt.created_at.desc())
                        .limit(limit)
                    )
                ).all()
            )
        return {
            "attempt_ids": [str(item.id) for item in attempts],
            "attempts": [
                {
                    "question_id": str(item.question_id),
                    "score_ratio": item.score_ratio,
                    "duration_seconds": item.actual_duration_seconds,
                    "looked_at_solution": item.looked_at_solution,
                }
                for item in attempts
            ],
        }

    async def get_knowledge_states(_: dict[str, Any]) -> dict[str, Any]:
        async with factory() as session:
            states = list(
                (
                    await session.scalars(
                        select(StudentKnowledgeState).where(
                            StudentKnowledgeState.student_id == student_id
                        )
                    )
                ).all()
            )
        return {
            "states": [
                {
                    "knowledge_id": str(item.knowledge_id),
                    "mastery": item.mastery_score,
                    "confidence": item.confidence,
                    "evidence_count": item.evidence_count,
                }
                for item in states
            ]
        }

    async def get_school_stats(_: dict[str, Any]) -> dict[str, Any]:
        async with factory() as session:
            student = await session.get(Student, student_id)
            if student is None or student.target_school_id is None:
                return {"stats": []}
            stats = list(
                (
                    await session.scalars(
                        select(SchoolKnowledgeStat).where(
                            SchoolKnowledgeStat.school_profile_id == student.target_school_id
                        )
                    )
                ).all()
            )
        return {
            "stats": [
                {
                    "knowledge_id": str(item.knowledge_id),
                    "weight": item.normalized_weight,
                    "trend": item.trend,
                }
                for item in stats
            ]
        }

    async def propose(arguments: dict[str, Any], proposal_type: ProposalType) -> dict[str, Any]:
        key = str(arguments.pop("_idempotency_key"))
        async with factory() as session:
            await assert_fence(session, run_id, fencing_token)
            existing = await session.scalar(
                select(Proposal).where(Proposal.run_id == run_id, Proposal.idempotency_key == key)
            )
            if existing is not None:
                return {"proposal_id": str(existing.id), "status": existing.status.value}
            evidence_refs = [str(item) for item in arguments.get("evidence_refs", [])]
            if not evidence_refs:
                raise AppError(
                    422,
                    "PROPOSAL_EVIDENCE_REQUIRED",
                    "proposal requires evidence bound by the harness",
                )
            evidence_ids = [uuid.UUID(item) for item in evidence_refs]
            attempts = list(
                (
                    await session.scalars(
                        select(QuestionAttempt).where(
                            QuestionAttempt.student_id == student_id,
                            QuestionAttempt.id.in_(evidence_ids),
                        )
                    )
                ).all()
            )
            tasks = list(
                (
                    await session.scalars(
                        select(PlanTask)
                        .join(WeeklyPlan, WeeklyPlan.id == PlanTask.plan_id)
                        .where(
                            WeeklyPlan.student_id == student_id,
                            PlanTask.id.in_(evidence_ids),
                        )
                    )
                ).all()
            )
            owned_ids = {item.id for item in attempts} | {item.id for item in tasks}
            if owned_ids != set(evidence_ids):
                raise AppError(
                    422,
                    "PROPOSAL_EVIDENCE_INVALID",
                    "proposal evidence must exist and belong to the current student",
                )
            evidence_snapshot = [
                {
                    "evidence_type": "QUESTION_ATTEMPT",
                    "source_id": str(item.id),
                    "version": item.created_at.isoformat(),
                }
                for item in attempts
            ] + [
                {
                    "evidence_type": "PLAN_TASK",
                    "source_id": str(item.id),
                    "version": str(item.version),
                }
                for item in tasks
            ]
            proposal = Proposal(
                run_id=run_id,
                student_id=student_id,
                proposal_type=proposal_type,
                status=(
                    ProposalStatus.PENDING
                    if proposal_type is ProposalType.MINOR_ADJUST
                    else ProposalStatus.AWAITING_CONFIRMATION
                ),
                payload={"adjustment_factor": float(arguments.get("adjustment_factor", 0.8))},
                reason_codes=[str(item) for item in arguments.get("reason_codes", [])],
                confidence=float(arguments.get("confidence", 0)),
                evidence_refs=evidence_refs,
                evidence_snapshot=evidence_snapshot,
                idempotency_key=key,
                approval_expires_at=(
                    datetime.now(UTC) + timedelta(days=7)
                    if proposal_type is not ProposalType.MINOR_ADJUST
                    else None
                ),
            )
            session.add(proposal)
            await session.commit()
            return {"proposal_id": str(proposal.id), "status": proposal.status.value}

    def proposal_handler(proposal_type: ProposalType) -> ToolHandler:
        async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
            return await propose(arguments, proposal_type)

        return handler

    def proposal_reconciler(proposal_type: ProposalType) -> ReconcileHandler:
        async def reconcile(arguments: dict[str, Any]) -> dict[str, Any] | None:
            key = str(arguments["_idempotency_key"])
            async with factory() as session:
                existing = await session.scalar(
                    select(Proposal).where(
                        Proposal.run_id == run_id,
                        Proposal.proposal_type == proposal_type,
                        Proposal.idempotency_key == key,
                    )
                )
            if existing is None:
                return None
            return {"proposal_id": str(existing.id), "status": existing.status.value}

        return reconcile

    registry.register(
        ToolDefinition(
            "search_recent_attempts",
            "Feedback diagnosis must call this first. Read recent attempts owned by the student.",
            {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 20}},
            },
            search_recent_attempts,
            args_model=SearchRecentAttemptsArgs,
            required_permissions=frozenset({"student:evidence:read"}),
        )
    )
    registry.register(
        ToolDefinition(
            "get_student_knowledge_states",
            "After recent attempts, optionally read mastery when it changes the diagnosis.",
            {"type": "object", "properties": {}},
            get_knowledge_states,
            args_model=EmptyArgs,
            required_permissions=frozenset({"student:evidence:read"}),
        )
    )
    registry.register(
        ToolDefinition(
            "get_school_knowledge_stats",
            "After recent attempts, optionally read school weight for major replanning.",
            {"type": "object", "properties": {}},
            get_school_stats,
            args_model=EmptyArgs,
            required_permissions=frozenset({"student:evidence:read"}),
        )
    )
    for name, proposal_type in (
        ("propose_minor_adjustment", ProposalType.MINOR_ADJUST),
        ("propose_major_replan", ProposalType.MAJOR_REPLAN),
        ("propose_stage_transition", ProposalType.STAGE_TRANSITION),
    ):
        registry.register(
            ToolDefinition(
                name,
                "Create one guarded proposal only after owned evidence has been collected.",
                {
                    "type": "object",
                    "properties": {
                        "reason_codes": {"type": "array", "items": {"type": "string"}},
                        "confidence": {"type": "number"},
                        "evidence_refs": {"type": "array", "items": {"type": "string"}},
                        "adjustment_factor": {"type": "number"},
                    },
                    "required": ["reason_codes", "confidence", "evidence_refs"],
                },
                proposal_handler(proposal_type),
                risk=ToolRisk.PROPOSAL,
                args_model=ProposalArgs,
                side_effect_level=ToolSideEffect.IDEMPOTENT_WRITE,
                idempotency_required=True,
                required_permissions=frozenset({"student:proposal:create"}),
                requires_confirmation=proposal_type is not ProposalType.MINOR_ADJUST,
                reconcile_handler=proposal_reconciler(proposal_type),
                retry_policy=RetryPolicy(max_attempts=2),
                feature_flag=(
                    "agent-stage-transition"
                    if proposal_type is ProposalType.STAGE_TRANSITION
                    else None
                ),
                terminal_decision=proposal_type.value,
            )
        )
    return registry


async def apply_minor_proposals(
    factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID, fencing_token: int
) -> None:
    async with factory() as session:
        await assert_fence(session, run_id, fencing_token)
        proposals = list(
            (
                await session.scalars(
                    select(Proposal).where(
                        Proposal.run_id == run_id,
                        Proposal.proposal_type == ProposalType.MINOR_ADJUST,
                        Proposal.status == ProposalStatus.PENDING,
                    )
                )
            ).all()
        )
        for proposal in proposals:
            await _apply_minor_proposal(session, proposal, manual_approval=False)
        await session.commit()


async def apply_approved_minor_proposal(session: AsyncSession, proposal: Proposal) -> None:
    if proposal.proposal_type is not ProposalType.MINOR_ADJUST:
        raise ValueError("proposal is not a minor adjustment")
    await _apply_minor_proposal(session, proposal, manual_approval=True)


async def _apply_minor_proposal(
    session: AsyncSession, proposal: Proposal, *, manual_approval: bool
) -> None:
    if not proposal.evidence_refs or (proposal.confidence < 0.75 and not manual_approval):
        proposal.status = ProposalStatus.AWAITING_CONFIRMATION
        proposal.approval_expires_at = datetime.now(UTC) + timedelta(days=7)
        return
    run = await session.get(AgentRun, proposal.run_id)
    background = await session.get(BackgroundJob, run.job_id) if run else None
    task_id = background.payload.get("task_id") if background else None
    source_task = await session.scalar(
        select(PlanTask)
        .join(WeeklyPlan, WeeklyPlan.id == PlanTask.plan_id)
        .where(
            WeeklyPlan.student_id == proposal.student_id,
            PlanTask.id == task_id,
            PlanTask.origin != TaskOrigin.LEGACY,
        )
    )
    source_knowledge_id = (
        await session.scalar(
            select(PlanTaskKnowledge.knowledge_id).where(
                PlanTaskKnowledge.task_id == source_task.id
            )
        )
        if source_task
        else None
    )
    source_snapshot = next(
        (
            item
            for item in proposal.evidence_snapshot
            if item.get("evidence_type") == "PLAN_TASK"
            and item.get("source_id") == str(source_task.id if source_task else "")
        ),
        None,
    )
    if (
        source_task is None
        or source_snapshot is None
        or source_snapshot.get("version") != str(source_task.version)
    ):
        proposal.status = (
            ProposalStatus.APPLY_FAILED if manual_approval else ProposalStatus.AWAITING_CONFIRMATION
        )
        proposal.apply_error_code = "EVIDENCE_STALE"
        return
    task = (
        await session.scalar(
            select(PlanTask)
            .join(WeeklyPlan, WeeklyPlan.id == PlanTask.plan_id)
            .join(PlanTaskKnowledge, PlanTaskKnowledge.task_id == PlanTask.id)
            .where(
                WeeklyPlan.student_id == proposal.student_id,
                PlanTask.plan_id == source_task.plan_id,
                PlanTask.id != source_task.id,
                PlanTask.task_type == source_task.task_type,
                PlanTaskKnowledge.knowledge_id == source_knowledge_id,
                PlanTask.status == "PENDING",
                PlanTask.origin != TaskOrigin.LEGACY,
                PlanTask.task_date >= date.today(),
            )
            .order_by(PlanTask.task_date, PlanTask.sequence)
            .limit(1)
            .with_for_update()
        )
        if source_task is not None and source_knowledge_id is not None
        else None
    )
    if task is None:
        proposal.status = (
            ProposalStatus.APPLY_FAILED if manual_approval else ProposalStatus.AWAITING_CONFIRMATION
        )
        proposal.apply_error_code = "ELIGIBLE_TASK_NOT_FOUND"
        return
    factor = min(1.25, max(0.75, float(proposal.payload.get("adjustment_factor", 0.8))))
    adjusted = max(1, round(task.effective_minutes * factor))
    day_total = int(
        await session.scalar(
            select(func.sum(PlanTask.effective_minutes)).where(
                PlanTask.plan_id == task.plan_id,
                PlanTask.task_date == task.task_date,
                PlanTask.status == "PENDING",
                PlanTask.origin != TaskOrigin.LEGACY,
            )
        )
        or 0
    )
    capacity = await _task_day_capacity(session, proposal.student_id, task.task_date)
    if day_total - task.effective_minutes + adjusted > capacity:
        proposal.status = (
            ProposalStatus.APPLY_FAILED if manual_approval else ProposalStatus.AWAITING_CONFIRMATION
        )
        proposal.apply_error_code = "PLAN_CAPACITY_EXCEEDED"
        return
    task.system_suggested_minutes = adjusted
    task.effective_minutes = adjusted
    task.estimated_min_minutes = adjusted
    task.estimated_max_minutes = adjusted
    if task.planned_units:
        task.planned_units = max(1, round(task.planned_units * factor))
    task.origin = TaskOrigin.AGENT
    task.modified_reason = (
        "student-approved duration calibration"
        if manual_approval
        else "automatic duration calibration from feedback"
    )
    task.version += 1
    proposal.status = ProposalStatus.APPLIED if manual_approval else ProposalStatus.AUTO_COMMITTED
    proposal.applied_at = datetime.now(UTC)
    proposal.apply_error_code = None
    proposal.decided_at = datetime.now(UTC)


async def _task_day_capacity(session: AsyncSession, student_id: uuid.UUID, task_date: date) -> int:
    override = await session.scalar(
        select(StudentAvailability.available_minutes).where(
            StudentAvailability.student_id == student_id,
            StudentAvailability.available_date == task_date,
        )
    )
    if override is not None:
        return int(override)
    template = await session.scalar(
        select(StudentAvailabilityTemplate.available_minutes).where(
            StudentAvailabilityTemplate.student_id == student_id,
            StudentAvailabilityTemplate.weekday == task_date.weekday(),
        )
    )
    return int(template if template is not None else 120)
