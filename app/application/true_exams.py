from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import AttemptContext, ErrorType, StudentStage
from app.domain.learning import MasteryObservation, MasteryStrategy
from app.errors import AppError
from app.infrastructure.db.models import (
    Question,
    QuestionAttempt,
    QuestionKnowledge,
    Student,
    StudentKnowledgeState,
    TrueExam,
    TrueExamAttempt,
    TrueExamProfile,
    TrueExamQuestion,
)


@dataclass(frozen=True, slots=True)
class ExamQuestionResult:
    question_id: uuid.UUID
    score_ratio: float
    duration_seconds: int
    looked_at_solution: bool
    error_note: str | None


class TrueExamService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._mastery = MasteryStrategy()

    async def list_for_user(self, user_id: uuid.UUID) -> list[TrueExam]:
        student = await self._student(user_id)
        if student.target_school_id is None:
            raise AppError(422, "TARGET_SCHOOL_REQUIRED", "target school has not been selected")
        return list(
            (
                await self._session.scalars(
                    select(TrueExam)
                    .where(TrueExam.school_profile_id == student.target_school_id)
                    .order_by(TrueExam.year)
                )
            ).all()
        )

    async def detail_for_user(
        self, user_id: uuid.UUID, exam_id: uuid.UUID
    ) -> tuple[TrueExam, list[tuple[TrueExamQuestion, Question]]]:
        student = await self._student(user_id)
        exam = await self._session.get(TrueExam, exam_id)
        if exam is None or exam.school_profile_id != student.target_school_id:
            raise AppError(404, "TRUE_EXAM_NOT_FOUND", "true exam does not exist")
        result = await self._session.execute(
            select(TrueExamQuestion, Question)
            .join(Question, Question.id == TrueExamQuestion.question_id)
            .where(TrueExamQuestion.true_exam_id == exam.id)
            .order_by(TrueExamQuestion.sequence)
        )
        return exam, list(result.tuples().all())

    async def submit(
        self,
        user_id: uuid.UUID,
        exam_id: uuid.UUID,
        results: list[ExamQuestionResult],
        idempotency_key: str,
    ) -> TrueExamAttempt:
        student = await self._student(user_id)
        existing = await self._session.scalar(
            select(TrueExamAttempt).where(
                TrueExamAttempt.student_id == student.id,
                TrueExamAttempt.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.true_exam_id != exam_id:
                raise AppError(
                    409, "IDEMPOTENCY_CONFLICT", "exam attempt key has different content"
                )
            return existing
        exam = await self._session.get(TrueExam, exam_id)
        if exam is None or exam.school_profile_id != student.target_school_id:
            raise AppError(404, "TRUE_EXAM_NOT_FOUND", "true exam does not exist")
        assigned_ids = set(
            (
                await self._session.scalars(
                    select(TrueExamQuestion.question_id).where(
                        TrueExamQuestion.true_exam_id == exam_id
                    )
                )
            ).all()
        )
        submitted_ids = {item.question_id for item in results}
        if len(submitted_ids) != len(results) or submitted_ids != assigned_ids:
            raise AppError(
                422, "INCOMPLETE_EXAM_SUBMISSION", "submit each exam question exactly once"
            )
        question_map = {
            item.id: item
            for item in (
                await self._session.scalars(select(Question).where(Question.id.in_(submitted_ids)))
            ).all()
        }
        score = sum(
            Decimal(str(item.score_ratio)) * question_map[item.question_id].score
            for item in results
        )
        overall = TrueExamAttempt(
            student_id=student.id,
            true_exam_id=exam.id,
            score=score,
            duration_seconds=sum(item.duration_seconds for item in results),
            idempotency_key=idempotency_key,
        )
        self._session.add(overall)
        await self._session.flush()
        for item in results:
            await self._record_question(
                student, exam, overall, question_map[item.question_id], item
            )
        if student.current_stage in {StudentStage.FOUNDATION, StudentStage.STRENGTHEN}:
            student.current_stage = StudentStage.TRUE_EXAM
            student.version += 1
        await self._session.commit()
        return overall

    async def profile(self, user_id: uuid.UUID) -> list[TrueExamProfile]:
        student = await self._student(user_id)
        return list(
            (
                await self._session.scalars(
                    select(TrueExamProfile)
                    .where(TrueExamProfile.student_id == student.id)
                    .order_by(TrueExamProfile.accuracy)
                )
            ).all()
        )

    async def _record_question(
        self,
        student: Student,
        exam: TrueExam,
        overall: TrueExamAttempt,
        question: Question,
        result: ExamQuestionResult,
    ) -> None:
        attempt = QuestionAttempt(
            student_id=student.id,
            question_id=question.id,
            plan_task_id=None,
            true_exam_attempt_id=overall.id,
            context=AttemptContext.TRUE_EXAM,
            finished_at=datetime.now(UTC),
            actual_duration_seconds=result.duration_seconds,
            score_ratio=result.score_ratio,
            looked_at_solution=result.looked_at_solution,
            student_error_note=result.error_note,
            agent_error_type=ErrorType.UNKNOWN if result.score_ratio < 0.6 else None,
            idempotency_key=f"true:{overall.id}:{question.id}",
        )
        self._session.add(attempt)
        knowledge_ids = list(
            (
                await self._session.scalars(
                    select(QuestionKnowledge.knowledge_id).where(
                        QuestionKnowledge.question_id == question.id
                    )
                )
            ).all()
        )
        for knowledge_id in knowledge_ids:
            await self._update_true_profile(
                student.id, exam.school_profile_id, knowledge_id, result
            )
            await self._update_mastery(student.id, knowledge_id, question, result)

    async def _update_true_profile(
        self,
        student_id: uuid.UUID,
        school_id: uuid.UUID,
        knowledge_id: uuid.UUID,
        result: ExamQuestionResult,
    ) -> None:
        profile = await self._session.scalar(
            select(TrueExamProfile)
            .where(
                TrueExamProfile.student_id == student_id,
                TrueExamProfile.school_profile_id == school_id,
                TrueExamProfile.knowledge_id == knowledge_id,
            )
            .with_for_update()
        )
        if profile is None:
            profile = TrueExamProfile(
                student_id=student_id,
                school_profile_id=school_id,
                knowledge_id=knowledge_id,
                attempt_count=0,
                accuracy=0,
                average_score_ratio=0,
                average_duration_seconds=0,
            )
            self._session.add(profile)
        count = profile.attempt_count
        profile.attempt_count = count + 1
        profile.accuracy = (profile.accuracy * count + float(result.score_ratio >= 0.6)) / (
            count + 1
        )
        profile.average_score_ratio = (profile.average_score_ratio * count + result.score_ratio) / (
            count + 1
        )
        profile.average_duration_seconds = round(
            (profile.average_duration_seconds * count + result.duration_seconds) / (count + 1)
        )

    async def _update_mastery(
        self,
        student_id: uuid.UUID,
        knowledge_id: uuid.UUID,
        question: Question,
        result: ExamQuestionResult,
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
        updated = self._mastery.update(
            state.mastery_score,
            state.evidence_count,
            MasteryObservation(result.score_ratio, question.difficulty, result.looked_at_solution),
        )
        state.mastery_score = updated.score
        state.confidence = updated.confidence
        state.evidence_count = updated.evidence_count
        state.last_practiced_at = datetime.now(UTC)
        state.model_version = self._mastery.version
        state.version += 1

    async def _student(self, user_id: uuid.UUID) -> Student:
        student = await self._session.scalar(select(Student).where(Student.user_id == user_id))
        if student is None:
            raise AppError(404, "STUDENT_PROFILE_NOT_FOUND", "student profile has not been created")
        return student
