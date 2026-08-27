from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import ProposalStatus, ProposalType, TaskOrigin
from app.harness.tools import ToolDefinition, ToolHandler, ToolRegistry, ToolRisk
from app.infrastructure.db.models import (
    AgentRun,
    BackgroundJob,
    PlanTask,
    Proposal,
    QuestionAttempt,
    SchoolKnowledgeStat,
    Student,
    StudentAvailability,
    StudentAvailabilityTemplate,
    StudentKnowledgeState,
    WeeklyPlan,
)


def build_learning_tool_registry(
    factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID, student_id: uuid.UUID
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
            existing = await session.scalar(
                select(Proposal).where(Proposal.run_id == run_id, Proposal.idempotency_key == key)
            )
            if existing is not None:
                return {"proposal_id": str(existing.id), "status": existing.status.value}
            evidence_refs = [str(item) for item in arguments.get("evidence_refs", [])]
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
                idempotency_key=key,
            )
            session.add(proposal)
            await session.commit()
            return {"proposal_id": str(proposal.id), "status": proposal.status.value}

    def proposal_handler(proposal_type: ProposalType) -> ToolHandler:
        async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
            return await propose(arguments, proposal_type)

        return handler

    registry.register(
        ToolDefinition(
            "search_recent_attempts",
            "Read recent attempts for evidence",
            {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 20}},
            },
            search_recent_attempts,
        )
    )
    registry.register(
        ToolDefinition(
            "get_student_knowledge_states",
            "Read current mastery estimates",
            {"type": "object", "properties": {}},
            get_knowledge_states,
        )
    )
    registry.register(
        ToolDefinition(
            "get_school_knowledge_stats",
            "Read target school knowledge weights",
            {"type": "object", "properties": {}},
            get_school_stats,
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
                "Create a guarded change proposal",
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
                retry_count=0,
            )
        )
    return registry


async def apply_minor_proposals(
    factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID
) -> None:
    async with factory() as session:
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
            if proposal.confidence < 0.75 or not proposal.evidence_refs:
                proposal.status = ProposalStatus.AWAITING_CONFIRMATION
                continue
            run = await session.get(AgentRun, proposal.run_id)
            background = await session.get(BackgroundJob, run.job_id) if run else None
            task_id = background.payload.get("task_id") if background else None
            task = await session.scalar(
                select(PlanTask)
                .join(WeeklyPlan, WeeklyPlan.id == PlanTask.plan_id)
                .where(
                    WeeklyPlan.student_id == proposal.student_id,
                    PlanTask.id == task_id,
                    PlanTask.status == "PENDING",
                    PlanTask.origin != TaskOrigin.LEGACY,
                )
                .with_for_update()
            )
            if task is None:
                proposal.status = ProposalStatus.AWAITING_CONFIRMATION
                continue
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
                proposal.status = ProposalStatus.AWAITING_CONFIRMATION
                continue
            task.system_suggested_minutes = adjusted
            task.effective_minutes = adjusted
            task.estimated_min_minutes = adjusted
            task.estimated_max_minutes = adjusted
            if task.planned_units:
                task.planned_units = max(1, round(task.planned_units * factor))
            task.origin = TaskOrigin.AGENT
            task.modified_reason = "automatic duration calibration from feedback"
            task.version += 1
            proposal.status = ProposalStatus.AUTO_COMMITTED
            proposal.decided_at = datetime.now(UTC)
        await session.commit()


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
