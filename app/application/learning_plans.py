from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import (
    ChangeActor,
    PlanStatus,
    ResourceStatus,
    TaskOrigin,
    TaskStatus,
    TaskType,
)
from app.errors import AppError
from app.infrastructure.db.models import (
    Feedback,
    LearningResource,
    PlanTask,
    PlanTaskChangeEvent,
    PlanTaskKnowledge,
    ResourceKnowledgeMapping,
    ResourceSection,
    ResourceVersion,
    Student,
    StudentAvailability,
    StudentAvailabilityTemplate,
    WeeklyPlan,
)


@dataclass(frozen=True, slots=True)
class LearningPlanTaskData:
    task: PlanTask
    knowledge_id: uuid.UUID | None
    resource_title: str | None
    resource_section_title: str | None
    feedback_version: int | None = None


class LearningPlanService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def has_active_plan(self, user_id: uuid.UUID) -> bool:
        student = await self._student(user_id)
        plan_id = await self._session.scalar(
            select(WeeklyPlan.id).where(
                WeeklyPlan.student_id == student.id, WeeklyPlan.status == PlanStatus.ACTIVE
            )
        )
        return plan_id is not None

    async def generate_or_roll(self, user_id: uuid.UUID, start_date: date) -> WeeklyPlan:
        student = await self._student(user_id)
        active = await self._session.scalar(
            select(WeeklyPlan).where(
                WeeklyPlan.student_id == student.id, WeeklyPlan.status == PlanStatus.ACTIVE
            )
        )
        if active is not None:
            return await self.roll_forward(student.id, start_date)
        plan = WeeklyPlan(
            student_id=student.id,
            start_date=start_date,
            end_date=start_date + timedelta(days=6),
            revision=1,
            planner_version="learning_planner_v1",
            status=PlanStatus.ACTIVE,
            generated_reason="INITIAL_LEARNING_PLAN",
            timezone="Asia/Shanghai",
            version=1,
        )
        self._session.add(plan)
        await self._session.flush()
        candidates = await self._resource_candidates(set(), student.target_school_id)
        if not candidates:
            raise AppError(
                422, "NO_PUBLISHED_RESOURCE", "publish a course or handout resource first"
            )
        sequence = 1
        used_sections: set[uuid.UUID] = set()
        summary_knowledge: set[uuid.UUID] = set()
        for offset in range(7):
            day = start_date + timedelta(days=offset)
            capacity = await self._capacity(student.id, day)
            remaining = capacity
            for candidate in candidates:
                if candidate[0].id in used_sections:
                    continue
                minutes = 35 if candidate[3].value == "COURSE" else 45
                if minutes > remaining:
                    continue
                task = self._new_task(plan.id, day, candidate, sequence, minutes)
                self._session.add(task)
                await self._session.flush()
                self._session.add(PlanTaskKnowledge(task_id=task.id, knowledge_id=candidate[2]))
                sequence += 1
                remaining -= minutes
                used_sections.add(candidate[0].id)
                if candidate[3].value == "HANDOUT" and candidate[2] not in summary_knowledge:
                    if remaining >= 20:
                        summary = self._new_summary_task(plan.id, day, candidate, sequence)
                        self._session.add(summary)
                        await self._session.flush()
                        self._session.add(
                            PlanTaskKnowledge(task_id=summary.id, knowledge_id=candidate[2])
                        )
                        summary_knowledge.add(candidate[2])
                        sequence += 1
                        remaining -= 20
                if remaining < 20:
                    break
        await self._session.commit()
        return plan

    async def current(
        self,
        user_id: uuid.UUID,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> tuple[WeeklyPlan, list[LearningPlanTaskData]]:
        student = await self._student(user_id)
        plan = await self._active_plan(student.id)
        start = from_date or date.today()
        end = to_date or start + timedelta(days=6)
        if from_date is None and to_date is None:
            await self._backfill_learning_window(student, plan, start, end)
            await self.roll_forward(student.id, start)
            await self._session.refresh(plan)
        query = (
            select(
                PlanTask,
                PlanTaskKnowledge.knowledge_id,
                LearningResource.title,
                ResourceSection.title,
                Feedback.feedback_version,
            )
            .outerjoin(PlanTaskKnowledge, PlanTaskKnowledge.task_id == PlanTask.id)
            .outerjoin(ResourceSection, ResourceSection.id == PlanTask.resource_section_id)
            .outerjoin(ResourceVersion, ResourceVersion.id == ResourceSection.resource_version_id)
            .outerjoin(LearningResource, LearningResource.id == ResourceVersion.resource_id)
            .outerjoin(Feedback, Feedback.task_id == PlanTask.id)
            .where(
                PlanTask.plan_id == plan.id,
                PlanTask.task_date.between(start, end),
                PlanTask.origin != TaskOrigin.LEGACY,
                PlanTask.status.notin_([TaskStatus.SUPERSEDED, TaskStatus.SUPERSEDED_LEGACY]),
            )
            .order_by(PlanTask.task_date, PlanTask.sequence)
        )
        result = await self._session.execute(query)
        tasks = [
            LearningPlanTaskData(
                task, knowledge_id, resource_title, section_title, feedback_version
            )
            for task, knowledge_id, resource_title, section_title, feedback_version in (
                result.tuples().all()
            )
        ]
        return plan, tasks

    async def _backfill_learning_window(
        self, student: Student, plan: WeeklyPlan, start: date, end: date
    ) -> None:
        learning_types = [
            TaskType.COURSE_LEARNING,
            TaskType.HANDOUT_PRACTICE,
            TaskType.ERROR_REVIEW,
            TaskType.KNOWLEDGE_SUMMARY,
        ]
        existing_count = int(
            await self._session.scalar(
                select(func.count(PlanTask.id)).where(
                    PlanTask.plan_id == plan.id,
                    PlanTask.task_date.between(start, end),
                    PlanTask.task_type.in_(learning_types),
                    PlanTask.status.notin_([TaskStatus.SKIPPED, TaskStatus.SUPERSEDED]),
                )
            )
            or 0
        )
        if existing_count:
            return
        candidates = await self._resource_candidates(set(), student.target_school_id)
        sequence = (
            int(
                await self._session.scalar(
                    select(func.max(PlanTask.sequence)).where(PlanTask.plan_id == plan.id)
                )
                or 0
            )
            + 1
        )
        used: set[uuid.UUID] = set()
        for offset in range((end - start).days + 1):
            day = start + timedelta(days=offset)
            remaining = await self._capacity(student.id, day)
            for candidate in candidates:
                if candidate[0].id in used:
                    continue
                minutes = 35 if candidate[3].value == "COURSE" else 45
                if minutes > remaining:
                    continue
                task = self._new_task(plan.id, day, candidate, sequence, minutes)
                self._session.add(task)
                await self._session.flush()
                self._session.add(PlanTaskKnowledge(task_id=task.id, knowledge_id=candidate[2]))
                used.add(candidate[0].id)
                remaining -= minutes
                sequence += 1
                if remaining < 20:
                    break
        plan.start_date = min(plan.start_date, start)
        plan.end_date = max(plan.end_date, end)
        plan.version += 1
        await self._session.commit()

    async def today(self, user_id: uuid.UUID, target_date: date) -> list[LearningPlanTaskData]:
        plan, tasks = await self.current(user_id, target_date, target_date)
        del plan
        return [
            item
            for item in tasks
            if item.task.status
            not in {TaskStatus.SKIPPED, TaskStatus.SUPERSEDED, TaskStatus.SUPERSEDED_LEGACY}
        ]

    async def changes(self, user_id: uuid.UUID, plan_id: uuid.UUID) -> list[PlanTaskChangeEvent]:
        student = await self._student(user_id)
        plan = await self._session.get(WeeklyPlan, plan_id)
        if plan is None or plan.student_id != student.id:
            raise AppError(404, "PLAN_NOT_FOUND", "plan does not exist")
        return list(
            (
                await self._session.scalars(
                    select(PlanTaskChangeEvent)
                    .where(PlanTaskChangeEvent.plan_id == plan.id)
                    .order_by(PlanTaskChangeEvent.change_sequence)
                )
            ).all()
        )

    async def patch_tasks(
        self,
        user_id: uuid.UUID,
        plan_id: uuid.UUID,
        expected_plan_version: int,
        allow_over_budget: bool,
        changes: list[dict[str, Any]],
    ) -> WeeklyPlan:
        student = await self._student(user_id)
        plan = await self._session.scalar(
            select(WeeklyPlan)
            .where(WeeklyPlan.id == plan_id, WeeklyPlan.student_id == student.id)
            .with_for_update()
        )
        if plan is None:
            raise AppError(404, "PLAN_NOT_FOUND", "plan does not exist")
        if plan.version != expected_plan_version:
            raise AppError(
                409,
                "VERSION_CONFLICT",
                "plan changed; reload before saving",
                {"current_version": plan.version},
            )
        sequence = await self._next_change_sequence(plan.id)
        for change in changes:
            sequence = await self._apply_change(plan, user_id, change, sequence)
        warnings = await self._capacity_warnings(student.id, plan.id)
        if warnings and not allow_over_budget:
            raise AppError(
                422, "PLAN_CAPACITY_EXCEEDED", "one or more days exceed capacity", warnings
            )
        for task in (
            await self._session.scalars(select(PlanTask).where(PlanTask.plan_id == plan.id))
        ).all():
            task.has_capacity_warning = task.task_date.isoformat() in warnings
        plan.version += 1
        await self._session.commit()
        return plan

    async def roll_forward(self, student_id: uuid.UUID, today: date) -> WeeklyPlan:
        plan = await self._active_plan(student_id, lock=True)
        student = await self._session.get(Student, student_id)
        if student is None:
            raise AppError(404, "STUDENT_PROFILE_NOT_FOUND", "student profile has not been created")
        target_end = today + timedelta(days=6)
        tasks = list(
            (await self._session.scalars(select(PlanTask).where(PlanTask.plan_id == plan.id))).all()
        )
        used_sections = {task.resource_section_id for task in tasks if task.resource_section_id}
        while plan.end_date < target_end:
            next_day = plan.end_date + timedelta(days=1)
            capacity = await self._capacity(student_id, next_day)
            candidates = await self._resource_candidates(used_sections, student.target_school_id)
            remaining = capacity
            for candidate in candidates:
                minutes = 35 if candidate[3].value == "COURSE" else 45
                if minutes > remaining:
                    continue
                task = self._new_task(plan.id, next_day, candidate, len(tasks) + 1, minutes)
                self._session.add(task)
                await self._session.flush()
                self._session.add(PlanTaskKnowledge(task_id=task.id, knowledge_id=candidate[2]))
                tasks.append(task)
                used_sections.add(candidate[0].id)
                remaining -= minutes
                if remaining < 20:
                    break
            plan.end_date = next_day
        plan.last_rolled_at = datetime.now(UTC)
        await self._session.commit()
        return plan

    async def _apply_change(
        self,
        plan: WeeklyPlan,
        user_id: uuid.UUID,
        change: dict[str, Any],
        sequence: int,
    ) -> int:
        operation = str(change["operation"])
        if operation == "CREATE":
            task = await self._create_personal_task(plan, change)
            await self._record_change(
                plan.id, task.id, user_id, "CREATE", None, self._snapshot(task), change, sequence
            )
            return sequence + 1
        task_id = change.get("task_id")
        if task_id is None:
            raise AppError(422, "PLAN_TASK_ID_REQUIRED", "task_id is required for update or delete")
        loaded_task = await self._session.get(PlanTask, task_id, with_for_update=True)
        if loaded_task is None or loaded_task.plan_id != plan.id:
            raise AppError(404, "PLAN_TASK_NOT_FOUND", "plan task does not exist")
        task = loaded_task
        if task.status == TaskStatus.COMPLETED:
            raise AppError(409, "COMPLETED_TASK_IMMUTABLE", "completed tasks cannot be edited")
        expected = change.get("expected_version")
        if expected != task.version:
            raise AppError(409, "VERSION_CONFLICT", "task changed; reload before saving")
        before = self._snapshot(task)
        if operation == "DELETE":
            task.status = TaskStatus.SKIPPED
            task.modified_reason = str(change.get("reason") or "student removed task")
        elif operation == "UPDATE":
            await self._update_task(task, change)
        else:
            raise AppError(422, "INVALID_PLAN_OPERATION", "unsupported plan task operation")
        task.version += 1
        await self._record_change(
            plan.id, task.id, user_id, operation, before, self._snapshot(task), change, sequence
        )
        return sequence + 1

    async def _create_personal_task(self, plan: WeeklyPlan, change: dict[str, Any]) -> PlanTask:
        required = ("task_date", "task_type", "title", "knowledge_id", "resource_section_id")
        if any(change.get(field) is None for field in required):
            raise AppError(
                422,
                "INCOMPLETE_PLAN_TASK",
                "new task requires date, type, title, knowledge and resource",
            )
        await self._validate_resource(
            uuid.UUID(str(change["resource_section_id"])), uuid.UUID(str(change["knowledge_id"]))
        )
        minutes = int(change.get("student_estimated_minutes") or 30)
        task = PlanTask(
            plan_id=plan.id,
            task_date=change["task_date"],
            task_type=TaskType(str(change["task_type"])),
            title=str(change["title"]),
            description=str(change.get("description") or ""),
            resource_section_id=change["resource_section_id"],
            suggested_scope=change.get("suggested_scope"),
            target_count=0,
            planned_units=change.get("planned_units"),
            unit_type=change.get("unit_type"),
            estimated_min_minutes=minutes,
            estimated_max_minutes=minutes,
            system_suggested_minutes=minutes,
            student_estimated_minutes=minutes,
            effective_minutes=minutes,
            priority=0.5,
            status=TaskStatus.PENDING,
            origin=TaskOrigin.STUDENT,
            is_personal=True,
            reason=str(change.get("reason") or "student created task"),
            sequence=int(change.get("sequence") or 1),
            version=1,
        )
        self._session.add(task)
        await self._session.flush()
        self._session.add(PlanTaskKnowledge(task_id=task.id, knowledge_id=change["knowledge_id"]))
        return task

    async def _update_task(self, task: PlanTask, change: dict[str, Any]) -> None:
        if change.get("resource_section_id") is not None or change.get("knowledge_id") is not None:
            knowledge_id = uuid.UUID(
                str(change.get("knowledge_id") or await self._task_knowledge(task.id))
            )
            section_id = uuid.UUID(
                str(change.get("resource_section_id") or task.resource_section_id)
            )
            await self._validate_resource(section_id, knowledge_id)
            task.resource_section_id = section_id
            existing = await self._session.get(
                PlanTaskKnowledge, (task.id, await self._task_knowledge(task.id))
            )
            if existing and existing.knowledge_id != knowledge_id:
                await self._session.delete(existing)
                self._session.add(PlanTaskKnowledge(task_id=task.id, knowledge_id=knowledge_id))
        for field in (
            "task_date",
            "title",
            "description",
            "suggested_scope",
            "planned_units",
            "unit_type",
            "sequence",
        ):
            if change.get(field) is not None:
                setattr(task, field, change[field])
        if change.get("task_type") is not None:
            task.task_type = TaskType(str(change["task_type"]))
        if change.get("student_estimated_minutes") is not None:
            task.student_estimated_minutes = int(change["student_estimated_minutes"])
            task.effective_minutes = task.student_estimated_minutes
            task.estimated_min_minutes = task.effective_minutes
            task.estimated_max_minutes = task.effective_minutes
        task.modified_reason = str(change.get("reason") or "student edited task")

    async def _validate_resource(self, section_id: uuid.UUID, knowledge_id: uuid.UUID) -> None:
        valid = await self._session.scalar(
            select(ResourceKnowledgeMapping.id)
            .join(ResourceSection, ResourceSection.id == ResourceKnowledgeMapping.section_id)
            .join(ResourceVersion, ResourceVersion.id == ResourceSection.resource_version_id)
            .join(LearningResource, LearningResource.id == ResourceVersion.resource_id)
            .where(
                ResourceSection.id == section_id,
                ResourceKnowledgeMapping.knowledge_id == knowledge_id,
                ResourceKnowledgeMapping.reviewer_confirmed.is_(True),
                LearningResource.status == ResourceStatus.PUBLISHED,
            )
        )
        if valid is None:
            raise AppError(
                422,
                "INVALID_RESOURCE_BINDING",
                "task must bind a published resource mapped to the knowledge",
            )

    async def _task_knowledge(self, task_id: uuid.UUID) -> uuid.UUID:
        knowledge_id = await self._session.scalar(
            select(PlanTaskKnowledge.knowledge_id).where(PlanTaskKnowledge.task_id == task_id)
        )
        if knowledge_id is None:
            raise AppError(422, "TASK_KNOWLEDGE_MISSING", "task has no knowledge binding")
        return knowledge_id

    async def _capacity_warnings(self, student_id: uuid.UUID, plan_id: uuid.UUID) -> dict[str, Any]:
        rows = (
            await self._session.execute(
                select(PlanTask.task_date, func.sum(PlanTask.effective_minutes))
                .where(
                    PlanTask.plan_id == plan_id,
                    PlanTask.origin != TaskOrigin.LEGACY,
                    PlanTask.status.notin_(
                        [TaskStatus.SKIPPED, TaskStatus.SUPERSEDED, TaskStatus.SUPERSEDED_LEGACY]
                    ),
                )
                .group_by(PlanTask.task_date)
            )
        ).all()
        warnings: dict[str, Any] = {}
        for day, total in rows:
            capacity = await self._capacity(student_id, day)
            if int(total or 0) > capacity:
                warnings[day.isoformat()] = {
                    "planned_minutes": int(total),
                    "capacity_minutes": capacity,
                    "over_by": int(total) - capacity,
                }
        return warnings

    async def _capacity(self, student_id: uuid.UUID, day: date) -> int:
        override = await self._session.scalar(
            select(StudentAvailability.available_minutes).where(
                StudentAvailability.student_id == student_id,
                StudentAvailability.available_date == day,
            )
        )
        if override is not None:
            return int(override)
        template = await self._session.scalar(
            select(StudentAvailabilityTemplate.available_minutes).where(
                StudentAvailabilityTemplate.student_id == student_id,
                StudentAvailabilityTemplate.weekday == day.weekday(),
            )
        )
        return int(template if template is not None else 120)

    async def _resource_candidates(
        self,
        excluded_sections: set[uuid.UUID],
        school_profile_id: uuid.UUID | None,
    ) -> list[tuple[ResourceSection, str, uuid.UUID, Any]]:
        result = await self._session.execute(
            select(
                ResourceSection,
                LearningResource.title,
                ResourceKnowledgeMapping.knowledge_id,
                LearningResource.resource_type,
            )
            .join(
                ResourceKnowledgeMapping, ResourceKnowledgeMapping.section_id == ResourceSection.id
            )
            .join(ResourceVersion, ResourceVersion.id == ResourceSection.resource_version_id)
            .join(LearningResource, LearningResource.id == ResourceVersion.resource_id)
            .where(
                LearningResource.status == ResourceStatus.PUBLISHED,
                LearningResource.school_profile_id == school_profile_id,
                ResourceKnowledgeMapping.reviewer_confirmed.is_(True),
                ResourceSection.id.notin_(excluded_sections or {uuid.uuid4()}),
            )
            .order_by(ResourceSection.sequence)
        )
        return list(result.tuples().all())

    @staticmethod
    def _new_task(
        plan_id: uuid.UUID,
        day: date,
        candidate: tuple[ResourceSection, str, uuid.UUID, Any],
        sequence: int,
        minutes: int,
    ) -> PlanTask:
        section, resource_title, _, resource_type = candidate
        task_type = (
            TaskType.COURSE_LEARNING
            if resource_type.value == "COURSE"
            else TaskType.HANDOUT_PRACTICE
        )
        return PlanTask(
            plan_id=plan_id,
            task_date=day,
            task_type=task_type,
            title=(
                f"学习：{section.title}"
                if task_type is TaskType.COURSE_LEARNING
                else f"讲义：{section.title}"
            ),
            description=f"使用正式资源《{resource_title}》完成对应章节。",
            resource_section_id=section.id,
            suggested_scope=(
                f"第 {section.page_start}-{section.page_end} 页"
                if section.page_start and section.page_end
                else None
            ),
            target_count=0,
            planned_units=section.suggested_units,
            unit_type=section.unit_type,
            estimated_min_minutes=minutes,
            estimated_max_minutes=minutes,
            system_suggested_minutes=minutes,
            effective_minutes=minutes,
            priority=0.5,
            status=TaskStatus.PENDING,
            origin=TaskOrigin.SYSTEM,
            is_personal=False,
            reason="daily rolling plan",
            sequence=sequence,
            version=1,
        )

    @staticmethod
    def _new_summary_task(
        plan_id: uuid.UUID,
        day: date,
        candidate: tuple[ResourceSection, str, uuid.UUID, Any],
        sequence: int,
    ) -> PlanTask:
        section, resource_title, _, _ = candidate
        return PlanTask(
            plan_id=plan_id,
            task_date=day,
            task_type=TaskType.KNOWLEDGE_SUMMARY,
            title=f"总结：{section.title}",
            description=f"基于《{resource_title}》整理定义、公式、解题步骤和疑问。",
            resource_section_id=section.id,
            suggested_scope="一页纸总结",
            target_count=0,
            planned_units=1,
            unit_type="份",
            estimated_min_minutes=20,
            estimated_max_minutes=20,
            system_suggested_minutes=20,
            effective_minutes=20,
            priority=0.45,
            status=TaskStatus.PENDING,
            origin=TaskOrigin.SYSTEM,
            is_personal=False,
            reason="knowledge consolidation",
            sequence=sequence,
            version=1,
        )

    async def _next_change_sequence(self, plan_id: uuid.UUID) -> int:
        maximum = await self._session.scalar(
            select(func.max(PlanTaskChangeEvent.change_sequence)).where(
                PlanTaskChangeEvent.plan_id == plan_id
            )
        )
        return int(maximum or 0) + 1

    async def _record_change(
        self,
        plan_id: uuid.UUID,
        task_id: uuid.UUID | None,
        user_id: uuid.UUID,
        operation: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        change: dict[str, Any],
        sequence: int,
    ) -> None:
        self._session.add(
            PlanTaskChangeEvent(
                plan_id=plan_id,
                task_id=task_id,
                change_sequence=sequence,
                actor=ChangeActor.STUDENT,
                actor_user_id=user_id,
                operation=operation,
                before=before,
                after=after,
                reason=str(change.get("reason") or "student plan edit"),
                occurred_at=datetime.now(UTC),
            )
        )

    @staticmethod
    def _snapshot(task: PlanTask) -> dict[str, Any]:
        return {
            "task_date": task.task_date.isoformat(),
            "task_type": task.task_type.value,
            "title": task.title,
            "description": task.description,
            "resource_section_id": str(task.resource_section_id)
            if task.resource_section_id
            else None,
            "planned_units": task.planned_units,
            "unit_type": task.unit_type,
            "effective_minutes": task.effective_minutes,
            "sequence": task.sequence,
            "status": task.status.value,
            "version": task.version,
        }

    async def _student(self, user_id: uuid.UUID) -> Student:
        student = await self._session.scalar(select(Student).where(Student.user_id == user_id))
        if student is None:
            raise AppError(404, "STUDENT_PROFILE_NOT_FOUND", "student profile has not been created")
        return student

    async def _active_plan(self, student_id: uuid.UUID, lock: bool = False) -> WeeklyPlan:
        query = select(WeeklyPlan).where(
            WeeklyPlan.student_id == student_id, WeeklyPlan.status == PlanStatus.ACTIVE
        )
        if lock:
            query = query.with_for_update()
        plan = await self._session.scalar(query)
        if plan is None:
            raise AppError(404, "ACTIVE_PLAN_NOT_FOUND", "student has no active plan")
        return plan
