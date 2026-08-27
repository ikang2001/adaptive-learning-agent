from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.jobs import JobService
from app.domain.enums import AttemptContext, ErrorType, TaskStatus, TaskType
from app.domain.learning import (
    AnomalyDecision,
    AnomalyDetector,
    EfficiencyEstimator,
    FeedbackSignals,
    MasteryObservation,
    MasteryStrategy,
)
from app.errors import AppError
from app.infrastructure.db.models import (
    BackgroundJob,
    EfficiencyProfile,
    Feedback,
    PlanTask,
    PlanTaskKnowledge,
    PlanTaskQuestion,
    Question,
    QuestionAttempt,
    QuestionKnowledge,
    Student,
    StudentKnowledgeState,
    WeeklyPlan,
)


@dataclass(frozen=True, slots=True)
class PracticeQuestionData:
    id: uuid.UUID
    code: str
    content: str
    question_type: str
    difficulty: str
    score: int


@dataclass(frozen=True, slots=True)
class PracticeTaskData:
    id: uuid.UUID
    task_type: str
    estimated_min_minutes: int
    estimated_max_minutes: int
    status: str
    questions: tuple[PracticeQuestionData, ...]


@dataclass(frozen=True, slots=True)
class FeedbackResult:
    feedback_id: uuid.UUID
    anomaly: AnomalyDecision
    agent_job: BackgroundJob | None


@dataclass(frozen=True, slots=True)
class LearningFeedbackResult:
    feedback: Feedback
    anomaly: AnomalyDecision
    agent_job: BackgroundJob | None


def _int_detail(detail: dict[str, object], key: str) -> int:
    value = detail.get(key)
    return value if isinstance(value, int) else 0


class PracticeService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._mastery = MasteryStrategy()
        self._efficiency = EfficiencyEstimator()
        self._anomaly = AnomalyDetector()

    async def today(self, user_id: uuid.UUID, target_date: date) -> list[PracticeTaskData]:
        student = await self._student_for_user(user_id)
        rows = (
            await self._session.execute(
                select(PlanTask, Question)
                .join(WeeklyPlan, WeeklyPlan.id == PlanTask.plan_id)
                .join(PlanTaskQuestion, PlanTaskQuestion.task_id == PlanTask.id)
                .join(Question, Question.id == PlanTaskQuestion.question_id)
                .where(
                    WeeklyPlan.student_id == student.id,
                    PlanTask.task_date == target_date,
                    PlanTask.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS]),
                )
                .order_by(PlanTask.sequence, PlanTaskQuestion.sequence)
            )
        ).all()
        grouped: dict[uuid.UUID, tuple[PlanTask, list[PracticeQuestionData]]] = {}
        for task, question in rows:
            grouped.setdefault(task.id, (task, []))[1].append(
                PracticeQuestionData(
                    id=question.id,
                    code=question.code,
                    content=question.content,
                    question_type=question.question_type,
                    difficulty=question.difficulty.value,
                    score=question.score,
                )
            )
        return [
            PracticeTaskData(
                id=task.id,
                task_type=task.task_type.value,
                estimated_min_minutes=task.estimated_min_minutes,
                estimated_max_minutes=task.estimated_max_minutes,
                status=task.status.value,
                questions=tuple(questions),
            )
            for task, questions in grouped.values()
        ]

    async def record_attempt(
        self,
        user_id: uuid.UUID,
        question_id: uuid.UUID,
        task_id: uuid.UUID | None,
        duration_seconds: int,
        score_ratio: float,
        looked_at_solution: bool,
        self_difficulty: int | None,
        error_note: str | None,
        idempotency_key: str,
    ) -> QuestionAttempt:
        student = await self._student_for_user(user_id)
        existing = await self._session.scalar(
            select(QuestionAttempt).where(
                QuestionAttempt.student_id == student.id,
                QuestionAttempt.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.question_id != question_id:
                raise AppError(409, "IDEMPOTENCY_CONFLICT", "attempt key has different content")
            return existing
        question = await self._session.get(Question, question_id)
        if question is None:
            raise AppError(404, "QUESTION_NOT_FOUND", "question does not exist")
        if task_id is not None:
            await self._validate_task_ownership(student.id, task_id, question_id)
        attempt = QuestionAttempt(
            student_id=student.id,
            question_id=question_id,
            plan_task_id=task_id,
            context=AttemptContext.PRACTICE,
            started_at=None,
            finished_at=datetime.now(UTC),
            actual_duration_seconds=duration_seconds,
            score_ratio=score_ratio,
            looked_at_solution=looked_at_solution,
            self_difficulty=self_difficulty,
            student_error_note=error_note,
            agent_error_type=ErrorType.UNKNOWN if score_ratio < 1 else None,
            idempotency_key=idempotency_key,
        )
        self._session.add(attempt)
        await self._session.flush()
        knowledge_ids = list(
            (
                await self._session.scalars(
                    select(QuestionKnowledge.knowledge_id).where(
                        QuestionKnowledge.question_id == question_id
                    )
                )
            ).all()
        )
        for knowledge_id in knowledge_ids:
            await self._update_mastery(student.id, knowledge_id, question, attempt)
        await self._session.commit()
        return attempt

    async def submit_feedback(
        self,
        user_id: uuid.UUID,
        task_id: uuid.UUID,
        completion_ratio: float,
        actual_duration_seconds: int,
        completed_count: int,
        correct_count: int,
        looked_at_solution: bool,
        perceived_difficulty: int | None,
        free_text: str | None,
        idempotency_key: str,
    ) -> FeedbackResult:
        student = await self._student_for_user(user_id)
        existing = await self._session.scalar(
            select(Feedback).where(
                Feedback.student_id == student.id,
                Feedback.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.task_id != task_id:
                raise AppError(409, "IDEMPOTENCY_CONFLICT", "feedback key has different content")
            return FeedbackResult(existing.id, AnomalyDecision(False, ()), None)
        task = await self._owned_task(student.id, task_id)
        accuracy = correct_count / completed_count if completed_count else 0.0
        recent_attempts = await self._recent_attempts(student.id, task_id)
        same_error_streak = self._same_error_streak(recent_attempts)
        low_completion_days = await self._low_completion_days(student.id)
        decision = self._anomaly.detect(
            FeedbackSignals(
                completion_ratio=completion_ratio,
                actual_duration_seconds=actual_duration_seconds,
                expected_p75_seconds=task.estimated_max_minutes * 60,
                recent_accuracy=accuracy,
                recent_attempt_count=completed_count,
                same_error_streak=same_error_streak,
                consecutive_low_completion_days=low_completion_days,
            )
        )
        feedback = Feedback(
            student_id=student.id,
            task_id=task.id,
            completion_ratio=completion_ratio,
            actual_duration_seconds=actual_duration_seconds,
            completed_count=completed_count,
            correct_count=correct_count,
            looked_at_solution=looked_at_solution,
            perceived_difficulty=perceived_difficulty,
            free_text=free_text,
            idempotency_key=idempotency_key,
        )
        self._session.add(feedback)
        await self._session.flush()
        task.status = TaskStatus.COMPLETED if completion_ratio >= 1 else TaskStatus.IN_PROGRESS
        await self._update_efficiency(student.id, task, actual_duration_seconds, completed_count)
        agent_job = None
        if decision.requires_agent:
            agent_job = await JobService(self._session).create(
                user_id,
                "AGENT_DIAGNOSIS",
                {
                    "student_id": str(student.id),
                    "task_id": str(task.id),
                    "reason_codes": list(decision.reason_codes),
                },
                f"feedback-agent:{feedback.id}",
                commit=False,
            )
        await self._session.commit()
        return FeedbackResult(feedback.id, decision, agent_job)

    async def upsert_learning_feedback(
        self,
        user_id: uuid.UUID,
        task_id: uuid.UUID,
        expected_version: int | None,
        completion_ratio: float,
        actual_duration_seconds: int,
        perceived_difficulty: int | None,
        free_text: str | None,
        progress_marker: str | None,
        mastery_self_score: int | None,
        detail: dict[str, object],
        idempotency_key: str,
    ) -> LearningFeedbackResult:
        student = await self._student_for_user(user_id)
        task = await self._owned_task(student.id, task_id)
        if task.task_type not in {
            TaskType.COURSE_LEARNING,
            TaskType.HANDOUT_PRACTICE,
            TaskType.ERROR_REVIEW,
            TaskType.KNOWLEDGE_SUMMARY,
        }:
            raise AppError(422, "NOT_A_LEARNING_TASK", "use question feedback for exam tasks")
        feedback = await self._session.scalar(
            select(Feedback)
            .where(Feedback.student_id == student.id, Feedback.task_id == task.id)
            .with_for_update()
        )
        if feedback is None:
            if expected_version is not None:
                raise AppError(409, "VERSION_CONFLICT", "feedback does not exist yet")
            feedback = Feedback(
                student_id=student.id,
                task_id=task.id,
                completion_ratio=completion_ratio,
                actual_duration_seconds=actual_duration_seconds,
                completed_count=_int_detail(detail, "completed_units"),
                correct_count=_int_detail(detail, "correct_units"),
                looked_at_solution=bool(detail.get("looked_at_solution", False)),
                perceived_difficulty=perceived_difficulty,
                free_text=free_text,
                idempotency_key=idempotency_key,
                feedback_version=1,
                detail=detail,
                mastery_self_score=mastery_self_score,
                progress_marker=progress_marker,
            )
            self._session.add(feedback)
            await self._session.flush()
        else:
            if expected_version != feedback.feedback_version:
                raise AppError(
                    409,
                    "VERSION_CONFLICT",
                    "feedback changed; reload before saving",
                    {"current_version": feedback.feedback_version},
                )
            feedback.completion_ratio = completion_ratio
            feedback.actual_duration_seconds = actual_duration_seconds
            feedback.perceived_difficulty = perceived_difficulty
            feedback.free_text = free_text
            feedback.progress_marker = progress_marker
            feedback.mastery_self_score = mastery_self_score
            feedback.detail = detail
            feedback.completed_count = _int_detail(detail, "completed_units")
            feedback.correct_count = _int_detail(detail, "correct_units")
            feedback.looked_at_solution = bool(detail.get("looked_at_solution", False))
            feedback.feedback_version += 1
        task.status = TaskStatus.COMPLETED if completion_ratio >= 1 else TaskStatus.IN_PROGRESS
        task.version += 1
        await self._update_efficiency_for_learning_task(
            student.id, task, actual_duration_seconds, detail
        )
        decision = self._learning_anomaly(task, completion_ratio, actual_duration_seconds, detail)
        agent_job = None
        if decision.requires_agent:
            agent_job = await JobService(self._session).create(
                user_id,
                "AGENT_DIAGNOSIS",
                {
                    "student_id": str(student.id),
                    "task_id": str(task.id),
                    "reason_codes": list(decision.reason_codes),
                    "task_type": task.task_type.value,
                },
                f"learning-feedback-agent:{feedback.id}:{feedback.feedback_version}",
                commit=False,
            )
        await self._session.commit()
        return LearningFeedbackResult(feedback, decision, agent_job)

    async def _update_efficiency_for_learning_task(
        self,
        student_id: uuid.UUID,
        task: PlanTask,
        actual_duration_seconds: int,
        detail: dict[str, object],
    ) -> None:
        units = _int_detail(detail, "completed_units")
        normalized = max(1, round(actual_duration_seconds / max(1, units)))
        knowledge_id = await self._session.scalar(
            select(PlanTaskKnowledge.knowledge_id).where(PlanTaskKnowledge.task_id == task.id)
        )
        profile = await self._session.scalar(
            select(EfficiencyProfile)
            .where(
                EfficiencyProfile.student_id == student_id,
                EfficiencyProfile.task_type == task.task_type,
                EfficiencyProfile.knowledge_id == knowledge_id,
            )
            .with_for_update()
        )
        if profile is None:
            profile = EfficiencyProfile(
                student_id=student_id,
                task_type=task.task_type,
                knowledge_id=knowledge_id,
                sample_count=0,
                recent_samples_seconds=[],
                p50_duration_seconds=normalized,
                p75_duration_seconds=normalized,
                average_duration_seconds=normalized,
                confidence=0.25,
            )
            self._session.add(profile)
        samples = [*profile.recent_samples_seconds, normalized][-20:]
        estimate = self._efficiency.estimate(task.task_type, samples)
        profile.recent_samples_seconds = samples
        profile.sample_count = estimate.sample_count
        profile.p50_duration_seconds = estimate.p50_seconds
        profile.p75_duration_seconds = estimate.p75_seconds
        profile.average_duration_seconds = round(sum(samples) / len(samples))
        profile.confidence = estimate.confidence

    @staticmethod
    def _learning_anomaly(
        task: PlanTask,
        completion_ratio: float,
        actual_duration_seconds: int,
        detail: dict[str, object],
    ) -> AnomalyDecision:
        reasons: list[str] = []
        expected = max(task.effective_minutes * 60, 1)
        if actual_duration_seconds > expected * 1.5:
            reasons.append("TIME_OVERRUN")
        completed = _int_detail(detail, "completed_units")
        correct = _int_detail(detail, "correct_units")
        if task.task_type is TaskType.HANDOUT_PRACTICE and completed >= 5:
            if correct / completed < 0.4:
                reasons.append("LOW_ACCURACY")
        if completion_ratio < 0.6:
            reasons.append("LOW_COMPLETION")
        return AnomalyDecision(bool(reasons), tuple(reasons))

    async def _update_mastery(
        self,
        student_id: uuid.UUID,
        knowledge_id: uuid.UUID,
        question: Question,
        attempt: QuestionAttempt,
    ) -> None:
        state = await self._session.scalar(
            select(StudentKnowledgeState)
            .where(
                StudentKnowledgeState.student_id == student_id,
                StudentKnowledgeState.knowledge_id == knowledge_id,
            )
            .with_for_update()
        )
        if state is None:
            state = StudentKnowledgeState(
                student_id=student_id,
                knowledge_id=knowledge_id,
                mastery_score=0.5,
                confidence=0.25,
                evidence_count=0,
                error_streak=0,
                correct_streak=0,
                model_version=self._mastery.version,
                version=0,
            )
            self._session.add(state)
        result = self._mastery.update(
            state.mastery_score,
            state.evidence_count,
            MasteryObservation(
                attempt.score_ratio, question.difficulty, attempt.looked_at_solution
            ),
        )
        state.mastery_score = result.score
        state.confidence = result.confidence
        state.evidence_count = result.evidence_count
        state.last_practiced_at = attempt.finished_at
        state.error_streak = state.error_streak + 1 if attempt.score_ratio < 0.6 else 0
        state.correct_streak = state.correct_streak + 1 if attempt.score_ratio >= 0.8 else 0
        state.model_version = self._mastery.version
        state.version += 1

    async def _update_efficiency(
        self,
        student_id: uuid.UUID,
        task: PlanTask,
        actual_duration_seconds: int,
        completed_count: int,
    ) -> None:
        if completed_count <= 0:
            return
        per_item = max(1, round(actual_duration_seconds / completed_count))
        profile = await self._session.scalar(
            select(EfficiencyProfile)
            .where(
                EfficiencyProfile.student_id == student_id,
                EfficiencyProfile.task_type == task.task_type,
                EfficiencyProfile.knowledge_id.is_(None),
            )
            .with_for_update()
        )
        if profile is None:
            profile = EfficiencyProfile(
                student_id=student_id,
                task_type=task.task_type,
                knowledge_id=None,
                sample_count=0,
                recent_samples_seconds=[],
                p50_duration_seconds=per_item,
                p75_duration_seconds=per_item,
                average_duration_seconds=per_item,
                confidence=0.25,
            )
            self._session.add(profile)
        samples = [*profile.recent_samples_seconds, per_item][-20:]
        estimate = self._efficiency.estimate(task.task_type, samples)
        profile.recent_samples_seconds = samples
        profile.sample_count = estimate.sample_count
        profile.p50_duration_seconds = estimate.p50_seconds
        profile.p75_duration_seconds = estimate.p75_seconds
        profile.average_duration_seconds = round(sum(samples) / len(samples))
        profile.confidence = estimate.confidence

    async def _validate_task_ownership(
        self, student_id: uuid.UUID, task_id: uuid.UUID, question_id: uuid.UUID
    ) -> None:
        owned = await self._session.scalar(
            select(PlanTaskQuestion.question_id)
            .join(PlanTask, PlanTask.id == PlanTaskQuestion.task_id)
            .join(WeeklyPlan, WeeklyPlan.id == PlanTask.plan_id)
            .where(
                WeeklyPlan.student_id == student_id,
                PlanTaskQuestion.task_id == task_id,
                PlanTaskQuestion.question_id == question_id,
            )
        )
        if owned is None:
            raise AppError(403, "QUESTION_NOT_ASSIGNED", "question is not assigned to this task")

    async def _owned_task(self, student_id: uuid.UUID, task_id: uuid.UUID) -> PlanTask:
        task = await self._session.scalar(
            select(PlanTask)
            .join(WeeklyPlan, WeeklyPlan.id == PlanTask.plan_id)
            .where(PlanTask.id == task_id, WeeklyPlan.student_id == student_id)
            .with_for_update()
        )
        if task is None:
            raise AppError(404, "PLAN_TASK_NOT_FOUND", "plan task does not exist")
        return task

    async def _student_for_user(self, user_id: uuid.UUID) -> Student:
        student = await self._session.scalar(select(Student).where(Student.user_id == user_id))
        if student is None:
            raise AppError(404, "STUDENT_PROFILE_NOT_FOUND", "student profile has not been created")
        return student

    async def _recent_attempts(
        self, student_id: uuid.UUID, task_id: uuid.UUID
    ) -> list[QuestionAttempt]:
        return list(
            (
                await self._session.scalars(
                    select(QuestionAttempt)
                    .where(
                        QuestionAttempt.student_id == student_id,
                        QuestionAttempt.plan_task_id == task_id,
                    )
                    .order_by(QuestionAttempt.created_at.desc())
                    .limit(10)
                )
            ).all()
        )

    async def _low_completion_days(self, student_id: uuid.UUID) -> int:
        recent = list(
            (
                await self._session.scalars(
                    select(Feedback)
                    .where(Feedback.student_id == student_id)
                    .order_by(Feedback.created_at.desc())
                    .limit(2)
                )
            ).all()
        )
        return sum(1 for item in recent if item.completion_ratio < 0.6)

    @staticmethod
    def _same_error_streak(attempts: list[QuestionAttempt]) -> int:
        streak = 0
        for attempt in attempts:
            if attempt.score_ratio >= 0.6:
                break
            streak += 1
        return streak
