from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import PlanStatus, QuestionQuality, TaskStatus, TaskType
from app.domain.planning import (
    AvailabilityDay,
    CandidateQuestion,
    KnowledgePriority,
    LearningCandidate,
    PlanDraft,
    PlanningStrategy,
)
from app.errors import AppError
from app.infrastructure.db.models import (
    BackgroundJob,
    ErrorProfile,
    PlanTask,
    PlanTaskKnowledge,
    PlanTaskQuestion,
    Question,
    QuestionAttempt,
    QuestionKnowledge,
    SchoolKnowledgeStat,
    Student,
    StudentAvailability,
    StudentAvailabilityTemplate,
    StudentKnowledgeState,
    WeeklyPlan,
)


class PlanGenerationService:
    def __init__(self, session: AsyncSession, strategy: PlanningStrategy | None = None) -> None:
        self._session = session
        self._strategy = strategy or PlanningStrategy()

    async def execute_job(self, job: BackgroundJob) -> WeeklyPlan:
        if job.user_id is None:
            raise AppError(422, "JOB_HAS_NO_OWNER", "plan job requires an owner")
        student = await self._session.scalar(select(Student).where(Student.user_id == job.user_id))
        if student is None or student.target_school_id is None:
            raise AppError(
                422, "STUDENT_PROFILE_INCOMPLETE", "student and target school are required"
            )
        start_date = date.fromisoformat(str(job.payload["start_date"]))
        from app.application.learning_plans import LearningPlanService

        plan = await LearningPlanService(self._session).generate_or_roll(job.user_id, start_date)
        job.result = {"plan_id": str(plan.id), "revision": plan.revision}
        return plan

    async def _load_availability(
        self, student_id: uuid.UUID, start_date: date
    ) -> list[AvailabilityDay]:
        end_date = start_date + timedelta(days=6)
        rows = (
            await self._session.scalars(
                select(StudentAvailability)
                .where(
                    StudentAvailability.student_id == student_id,
                    StudentAvailability.available_date.between(start_date, end_date),
                )
                .order_by(StudentAvailability.available_date)
            )
        ).all()
        configured = {row.available_date: row.available_minutes for row in rows}
        template_rows = (
            await self._session.scalars(
                select(StudentAvailabilityTemplate).where(
                    StudentAvailabilityTemplate.student_id == student_id
                )
            )
        ).all()
        template = {row.weekday: row.available_minutes for row in template_rows}
        result: list[AvailabilityDay] = []
        for offset in range(7):
            day = start_date + timedelta(days=offset)
            minutes = configured.get(day, template.get(day.weekday(), 120))
            result.append(AvailabilityDay(day, minutes))
        return result

    async def _load_priorities(self, student: Student, today: date) -> list[KnowledgePriority]:
        stats = (
            await self._session.scalars(
                select(SchoolKnowledgeStat).where(
                    SchoolKnowledgeStat.school_profile_id == student.target_school_id
                )
            )
        ).all()
        states = {
            row.knowledge_id: row
            for row in (
                await self._session.scalars(
                    select(StudentKnowledgeState).where(
                        StudentKnowledgeState.student_id == student.id
                    )
                )
            ).all()
        }
        errors = {
            row.knowledge_id: row
            for row in (
                await self._session.scalars(
                    select(ErrorProfile).where(ErrorProfile.student_id == student.id)
                )
            ).all()
        }
        max_weight = max((row.normalized_weight for row in stats), default=1.0)
        priorities: list[KnowledgePriority] = []
        for stat in stats:
            state = states.get(stat.knowledge_id)
            error = errors.get(stat.knowledge_id)
            days_since_practice = 30
            if state and state.last_practiced_at:
                days_since_practice = max(0, (today - state.last_practiced_at.date()).days)
            priorities.append(
                KnowledgePriority(
                    knowledge_id=str(stat.knowledge_id),
                    school_weight=stat.normalized_weight / max_weight,
                    mastery_gap=1 - (state.mastery_score if state else 0.5),
                    error_recency=min(1.0, (error.recent_count / 3) if error else 0),
                    error_frequency=min(1.0, (error.occurrence_count / 10) if error else 0),
                    forgetting_risk=min(1.0, days_since_practice / 30),
                    stage_weight=0.5,
                )
            )
        return priorities

    async def _load_candidates(
        self, student: Student, priorities: list[KnowledgePriority]
    ) -> tuple[list[CandidateQuestion], dict[str, Question]]:
        attempted = set(
            (
                await self._session.scalars(
                    select(QuestionAttempt.question_id).where(
                        QuestionAttempt.student_id == student.id
                    )
                )
            ).all()
        )
        rows = (
            await self._session.execute(
                select(Question, QuestionKnowledge)
                .join(QuestionKnowledge, QuestionKnowledge.question_id == Question.id)
                .where(Question.quality_status == QuestionQuality.VALID)
            )
        ).all()
        priority_map = {item.knowledge_id: item for item in priorities}
        candidates: list[CandidateQuestion] = []
        question_map: dict[str, Question] = {}
        for question, relation in rows:
            if question.id in attempted:
                continue
            priority = priority_map.get(str(relation.knowledge_id))
            if priority is None:
                continue
            question_map[str(question.id)] = question
            candidates.append(
                CandidateQuestion(
                    question_id=str(question.id),
                    knowledge_id=str(relation.knowledge_id),
                    difficulty=question.difficulty,
                    question_type=question.question_type,
                    estimated_p50_minutes=question.estimated_duration_minutes,
                    estimated_p75_minutes=max(
                        question.estimated_duration_minutes + 1,
                        round(question.estimated_duration_minutes * 1.25),
                    ),
                    school_weight=priority.school_weight,
                    weakness=priority.mastery_gap,
                    difficulty_fit=1.0,
                    spacing_score=1.0,
                    diversity_score=1.0,
                )
            )
        return candidates, question_map

    async def _load_learning_candidates(
        self, student: Student, priorities: list[KnowledgePriority]
    ) -> list[LearningCandidate]:
        from app.infrastructure.db.models import (
            LearningResource,
            ResourceKnowledgeMapping,
            ResourceSection,
            ResourceVersion,
        )

        priority_map = {item.knowledge_id: item for item in priorities}
        rows = (
            await self._session.execute(
                select(ResourceSection, LearningResource, ResourceKnowledgeMapping)
                .join(
                    ResourceKnowledgeMapping,
                    ResourceKnowledgeMapping.section_id == ResourceSection.id,
                )
                .join(ResourceVersion, ResourceVersion.id == ResourceSection.resource_version_id)
                .join(
                    LearningResource,
                    LearningResource.id == ResourceVersion.resource_id,
                )
                .where(LearningResource.status == "PUBLISHED")
            )
        ).all()
        candidates: list[LearningCandidate] = []
        for section, resource, mapping in rows:
            knowledge_id = str(mapping.knowledge_id)
            priority = priority_map.get(knowledge_id)
            if priority is None:
                continue
            if resource.resource_type.value == "COURSE":
                task_type = TaskType.COURSE_LEARNING
                title = f"学习：{section.title}"
                description = "完成对应课程章节，记录看到的小节或视频进度。"
                units = section.suggested_units or 3
                unit_type = section.unit_type or "节"
                p50, p75 = 35, 50
            else:
                task_type = TaskType.HANDOUT_PRACTICE
                title = f"讲义：{section.title}"
                description = "完成对应辅导班讲义，记录题量、正确数和是否看过解析。"
                units = section.suggested_units or 10
                unit_type = section.unit_type or "题"
                p50, p75 = 40, 60
            candidates.append(
                LearningCandidate(
                    knowledge_id=knowledge_id,
                    task_type=task_type,
                    title=title,
                    description=description,
                    resource_section_id=str(section.id),
                    suggested_scope=(
                        f"第 {section.page_start}-{section.page_end} 页"
                        if section.page_start and section.page_end
                        else None
                    ),
                    planned_units=units,
                    unit_type=unit_type,
                    estimated_p50_minutes=p50,
                    estimated_p75_minutes=p75,
                    priority=priority.score,
                    task_identity=f"{knowledge_id}:{task_type.value}",
                )
            )
            candidates.append(
                LearningCandidate(
                    knowledge_id=knowledge_id,
                    task_type=TaskType.KNOWLEDGE_SUMMARY,
                    title=f"总结：{section.title}",
                    description="整理本知识点的定义、公式、解题步骤和仍然不确定的地方。",
                    resource_section_id=str(section.id),
                    suggested_scope="一页纸总结",
                    planned_units=1,
                    unit_type="份",
                    estimated_p50_minutes=20,
                    estimated_p75_minutes=30,
                    priority=max(0.1, priority.score - 0.05),
                    task_identity=f"{knowledge_id}:summary",
                )
            )
        return candidates

    async def _persist_plan(
        self,
        student_id: uuid.UUID,
        start_date: date,
        draft: PlanDraft,
        question_map: dict[str, Question],
    ) -> WeeklyPlan:
        end_date = start_date + timedelta(days=6)
        current = await self._session.scalar(
            select(WeeklyPlan)
            .where(WeeklyPlan.student_id == student_id, WeeklyPlan.status == PlanStatus.ACTIVE)
            .with_for_update()
        )
        revision = current.revision + 1 if current else 1
        if current:
            current.status = PlanStatus.SUPERSEDED
            await self._session.execute(
                update(PlanTask)
                .where(PlanTask.plan_id == current.id, PlanTask.status == TaskStatus.PENDING)
                .values(status=TaskStatus.SUPERSEDED)
            )
        plan = WeeklyPlan(
            student_id=student_id,
            start_date=start_date,
            end_date=end_date,
            revision=revision,
            parent_plan_id=current.id if current else None,
            planner_version=self._strategy.version,
            status=PlanStatus.ACTIVE,
            generated_reason="REPLAN" if current else "INITIAL",
        )
        self._session.add(plan)
        await self._session.flush()
        for sequence, draft_task in enumerate(draft.tasks, start=1):
            task = PlanTask(
                plan_id=plan.id,
                task_date=draft_task.day,
                task_type=draft_task.task_type,
                target_count=len(draft_task.question_ids),
                estimated_min_minutes=draft_task.estimated_min_minutes,
                estimated_max_minutes=draft_task.estimated_max_minutes,
                priority=draft_task.priority,
                status=TaskStatus.PENDING,
                reason=draft_task.reason,
                sequence=sequence,
            )
            self._session.add(task)
            await self._session.flush()
            self._session.add(
                PlanTaskKnowledge(task_id=task.id, knowledge_id=uuid.UUID(draft_task.knowledge_id))
            )
            for question_sequence, question_id in enumerate(draft_task.question_ids, start=1):
                self._session.add(
                    PlanTaskQuestion(
                        task_id=task.id,
                        question_id=question_map[question_id].id,
                        sequence=question_sequence,
                    )
                )
        return plan

    async def _persist_learning_plan(
        self, student_id: uuid.UUID, start_date: date, draft: PlanDraft
    ) -> WeeklyPlan:
        from app.domain.enums import TaskOrigin

        end_date = start_date + timedelta(days=6)
        current = await self._session.scalar(
            select(WeeklyPlan)
            .where(WeeklyPlan.student_id == student_id, WeeklyPlan.status == PlanStatus.ACTIVE)
            .with_for_update()
        )
        if current is not None:
            raise AppError(409, "ACTIVE_PLAN_EXISTS", "student already has an active learning plan")
        plan = WeeklyPlan(
            student_id=student_id,
            start_date=start_date,
            end_date=end_date,
            revision=1,
            planner_version=self._strategy.version,
            status=PlanStatus.ACTIVE,
            generated_reason="INITIAL_LEARNING_PLAN",
        )
        self._session.add(plan)
        await self._session.flush()
        for sequence, item in enumerate(draft.tasks, start=1):
            effective = item.system_suggested_minutes or item.estimated_max_minutes
            task = PlanTask(
                plan_id=plan.id,
                task_date=item.day,
                task_type=item.task_type,
                title=item.title,
                description=item.description,
                resource_section_id=(
                    uuid.UUID(item.resource_section_id) if item.resource_section_id else None
                ),
                suggested_scope=item.suggested_scope,
                target_count=0,
                planned_units=item.planned_units,
                unit_type=item.unit_type,
                estimated_min_minutes=item.estimated_min_minutes,
                estimated_max_minutes=item.estimated_max_minutes,
                system_suggested_minutes=effective,
                student_estimated_minutes=None,
                effective_minutes=effective,
                priority=item.priority,
                status=TaskStatus.PENDING,
                origin=item.origin if item.origin else TaskOrigin.SYSTEM,
                is_personal=item.is_personal,
                has_capacity_warning=False,
                reason=item.reason,
                sequence=sequence,
            )
            self._session.add(task)
            await self._session.flush()
            self._session.add(
                PlanTaskKnowledge(task_id=task.id, knowledge_id=uuid.UUID(item.knowledge_id))
            )
        return plan


class PlanQueryService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def current_for_user(self, user_id: uuid.UUID) -> tuple[WeeklyPlan, list[PlanTask]]:
        student = await self._session.scalar(select(Student).where(Student.user_id == user_id))
        if student is None:
            raise AppError(404, "STUDENT_PROFILE_NOT_FOUND", "student profile has not been created")
        plan = await self._session.scalar(
            select(WeeklyPlan).where(
                WeeklyPlan.student_id == student.id, WeeklyPlan.status == PlanStatus.ACTIVE
            )
        )
        if plan is None:
            raise AppError(404, "ACTIVE_PLAN_NOT_FOUND", "student has no active plan")
        tasks = list(
            (
                await self._session.scalars(
                    select(PlanTask)
                    .where(PlanTask.plan_id == plan.id)
                    .order_by(PlanTask.task_date, PlanTask.sequence)
                )
            ).all()
        )
        return plan, tasks
