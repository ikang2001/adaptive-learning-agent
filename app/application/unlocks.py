from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import TypedDict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ErrorType, KnowledgeLearningStatus, QuestionQuality, StudentStage
from app.errors import AppError
from app.infrastructure.db.models import (
    ChapterTrueExamSession,
    ChapterTrueExamSessionQuestion,
    KnowledgeNode,
    PlanTask,
    PlanTaskKnowledge,
    Question,
    QuestionAttempt,
    QuestionKnowledge,
    SchoolKnowledgeStat,
    Student,
    StudentKnowledgeProgress,
    TrueExamProfile,
    WeeklyPlan,
)


class WeakKnowledgePointData(TypedDict):
    knowledge_id: uuid.UUID
    knowledge_name: str
    attempts: int
    accuracy: float
    true_exam_total: int
    true_exam_completed: int


class SpecializedScopeData(TypedDict):
    chapter_id: uuid.UUID
    chapter_order: int
    chapter_code: str
    chapter_name: str
    strengthened: bool
    true_exam_total: int
    true_exam_completed: int
    specialized_unlocked: bool
    weak_points: list[WeakKnowledgePointData]


class UnlockService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def progress(self, user_id: uuid.UUID) -> list[dict[str, object]]:
        student = await self._student(user_id)
        nodes = await self._school_nodes(student.target_school_id)
        rows: list[dict[str, object]] = []
        for node in nodes:
            progress = await self._progress(student.id, node.id)
            task_total, task_done = await self._learning_task_counts(student.id, node.id)
            true_total = await self._true_question_count(student.target_school_id, node.id)
            true_done = await self._true_completed_count(student.id, node.id)
            specialized_unlocked = (
                progress.status is KnowledgeLearningStatus.STRENGTHENED
                and true_total > 0
                and true_done >= true_total
            )
            if specialized_unlocked and progress.specialized_unlocked_at is None:
                progress.specialized_unlocked_at = datetime.now(UTC)
            rows.append(
                {
                    "knowledge_id": node.id,
                    "knowledge_code": node.code,
                    "knowledge_name": node.name,
                    "status": progress.status,
                    "learning_task_total": task_total,
                    "learning_task_completed": task_done,
                    "true_exam_total": true_total,
                    "true_exam_completed": true_done,
                    "true_exam_unlocked": progress.status
                    in {KnowledgeLearningStatus.STRENGTHENED, KnowledgeLearningStatus.LEARNING},
                    "specialized_unlocked": specialized_unlocked,
                    "version": progress.version,
                }
            )
        await self._session.commit()
        return rows

    async def specialized_scopes(self, user_id: uuid.UUID) -> list[SpecializedScopeData]:
        student = await self._student(user_id)
        nodes = await self._school_nodes(student.target_school_id)
        children_by_parent: dict[uuid.UUID, list[KnowledgeNode]] = {}
        for node in nodes:
            if node.parent_id:
                children_by_parent.setdefault(node.parent_id, []).append(node)
        scopes: list[SpecializedScopeData] = []
        chapters = [node for node in nodes if node.parent_id is None]
        for chapter_order, chapter in enumerate(chapters, start=1):
            scope_nodes = self._chapter_nodes(chapter, children_by_parent)
            weak_points: list[WeakKnowledgePointData] = []
            strengthened = True
            total_questions = 0
            completed_questions = 0
            for node in scope_nodes:
                progress = await self._progress(student.id, node.id)
                question_total = await self._true_question_count(student.target_school_id, node.id)
                question_done = await self._true_completed_count(student.id, node.id)
                profile = await self._session.scalar(
                    select(TrueExamProfile).where(
                        TrueExamProfile.student_id == student.id,
                        TrueExamProfile.school_profile_id == student.target_school_id,
                        TrueExamProfile.knowledge_id == node.id,
                    )
                )
                if question_total > 0:
                    total_questions += question_total
                    completed_questions += min(question_done, question_total)
                    strengthened = strengthened and (
                        progress.status is KnowledgeLearningStatus.STRENGTHENED
                    )
                if question_total > 0:
                    weak_points.append(
                        {
                            "knowledge_id": node.id,
                            "knowledge_name": node.name,
                            "attempts": profile.attempt_count if profile else 0,
                            "accuracy": profile.accuracy if profile else 1.0,
                            "true_exam_total": question_total,
                            "true_exam_completed": question_done,
                        }
                    )
            weak_points.sort(
                key=lambda item: (
                    item["attempts"] == 0,
                    item["accuracy"],
                    -item["attempts"],
                )
            )
            scopes.append(
                {
                    "chapter_id": chapter.id,
                    "chapter_order": chapter_order,
                    "chapter_code": chapter.code,
                    "chapter_name": chapter.name,
                    "strengthened": strengthened,
                    "true_exam_total": total_questions,
                    "true_exam_completed": completed_questions,
                    "specialized_unlocked": (
                        strengthened
                        and total_questions > 0
                        and completed_questions >= total_questions
                    ),
                    "weak_points": weak_points,
                }
            )
        return scopes

    async def assert_mock_access(
        self, user_id: uuid.UUID, mock_type: str, target_knowledge_id: uuid.UUID | None
    ) -> None:
        student = await self._student(user_id)
        if mock_type == "SPECIALIZED":
            if target_knowledge_id is None:
                raise AppError(
                    422, "TARGET_KNOWLEDGE_REQUIRED", "specialized mock requires a target"
                )
            nodes = await self._school_nodes(student.target_school_id)
            target = next((node for node in nodes if node.id == target_knowledge_id), None)
            if target is None or target.parent_id is not None:
                raise AppError(
                    422,
                    "SPECIALIZED_SCOPE_MUST_BE_CHAPTER",
                    "specialized target must be a syllabus chapter",
                )
            children_by_parent: dict[uuid.UUID, list[KnowledgeNode]] = {}
            for node in nodes:
                if node.parent_id:
                    children_by_parent.setdefault(node.parent_id, []).append(node)
            scope_nodes = self._chapter_nodes(target, children_by_parent)
            eligible = True
            has_questions = False
            for node in scope_nodes:
                progress = await self._progress(student.id, node.id)
                total = await self._true_question_count(student.target_school_id, node.id)
                completed = await self._true_completed_count(student.id, node.id)
                if total:
                    has_questions = True
                    eligible = eligible and (
                        progress.status is KnowledgeLearningStatus.STRENGTHENED
                        and completed >= total
                    )
            if not has_questions or not eligible:
                raise AppError(
                    409,
                    "SPECIALIZED_PRACTICE_LOCKED",
                    "complete all current true exam questions for this knowledge first",
                )
            return
        nodes = await self._school_nodes(student.target_school_id)
        for node in nodes:
            progress = await self._progress(student.id, node.id)
            if progress.status is not KnowledgeLearningStatus.STRENGTHENED:
                raise AppError(
                    409,
                    "FULL_MOCK_LOCKED",
                    "confirm every syllabus knowledge as strengthened first",
                )
        if student.current_stage not in {StudentStage.MOCK_EXAM, StudentStage.SPRINT}:
            raise AppError(
                409,
                "FULL_MOCK_CONFIRMATION_REQUIRED",
                "confirm the full mock stage transition first",
            )

    async def confirm_full_mock_unlock(self, user_id: uuid.UUID) -> dict[str, object]:
        student = await self._student(user_id)
        nodes = await self._school_nodes(student.target_school_id)
        if not nodes:
            raise AppError(422, "SYLLABUS_EMPTY", "target school syllabus is empty")
        for node in nodes:
            progress = await self._progress(student.id, node.id)
            if progress.status is not KnowledgeLearningStatus.STRENGTHENED:
                raise AppError(
                    409,
                    "FULL_MOCK_LOCKED",
                    "confirm every syllabus knowledge as strengthened first",
                )
        student.current_stage = StudentStage.MOCK_EXAM
        student.version += 1
        await self._session.commit()
        return {"status": student.current_stage.value, "full_mock_unlocked": True}

    async def confirm_strengthened(
        self, user_id: uuid.UUID, knowledge_id: uuid.UUID, expected_version: int | None
    ) -> dict[str, object]:
        student = await self._student(user_id)
        node = await self._session.get(KnowledgeNode, knowledge_id)
        if node is None:
            raise AppError(404, "KNOWLEDGE_NOT_FOUND", "knowledge node does not exist")
        total, completed = await self._learning_task_counts(student.id, knowledge_id)
        if total == 0 or completed < total:
            raise AppError(
                422, "LEARNING_TASKS_INCOMPLETE", "complete all learning tasks before confirming"
            )
        progress = await self._progress(student.id, knowledge_id, create=True)
        if expected_version is not None and progress.version != expected_version:
            raise AppError(409, "VERSION_CONFLICT", "knowledge progress changed")
        progress.status = KnowledgeLearningStatus.STRENGTHENED
        progress.strengthened_at = datetime.now(UTC)
        progress.strengthened_by_user = True
        progress.version += 1
        await self._session.commit()
        return {"knowledge_id": knowledge_id, "status": progress.status, "true_exam_unlocked": True}

    async def create_chapter_session(
        self, user_id: uuid.UUID, knowledge_id: uuid.UUID
    ) -> ChapterTrueExamSession:
        student = await self._student(user_id)
        progress = await self._progress(student.id, knowledge_id)
        if progress.status is not KnowledgeLearningStatus.STRENGTHENED:
            raise AppError(409, "TRUE_EXAM_LOCKED", "confirm this knowledge as strengthened first")
        if student.target_school_id is None:
            raise AppError(422, "TARGET_SCHOOL_REQUIRED", "target school has not been selected")
        snapshot = await self._question_snapshot(student.target_school_id, knowledge_id)
        if snapshot[1] == 0:
            raise AppError(
                404, "NO_CHAPTER_TRUE_EXAM", "no true exam question is available for this knowledge"
            )
        existing = await self._session.scalar(
            select(ChapterTrueExamSession).where(
                ChapterTrueExamSession.student_id == student.id,
                ChapterTrueExamSession.knowledge_id == knowledge_id,
                ChapterTrueExamSession.question_snapshot_version == snapshot[0],
            )
        )
        if existing is not None:
            return existing
        session = ChapterTrueExamSession(
            student_id=student.id,
            knowledge_id=knowledge_id,
            school_profile_id=student.target_school_id,
            question_snapshot_version=snapshot[0],
            status="ACTIVE",
            total_questions=snapshot[1],
            completed_questions=0,
            version=1,
        )
        self._session.add(session)
        await self._session.flush()
        for sequence, question_id in enumerate(snapshot[2], start=1):
            self._session.add(
                ChapterTrueExamSessionQuestion(
                    session_id=session.id,
                    question_id=question_id,
                    sequence=sequence,
                )
            )
        progress.true_exam_snapshot_version = snapshot[0]
        await self._session.commit()
        return session

    async def chapter_session_detail(
        self, user_id: uuid.UUID, session_id: uuid.UUID
    ) -> tuple[ChapterTrueExamSession, list[tuple[ChapterTrueExamSessionQuestion, Question]]]:
        student = await self._student(user_id)
        session = await self._session.get(ChapterTrueExamSession, session_id)
        if session is None or session.student_id != student.id:
            raise AppError(404, "CHAPTER_SESSION_NOT_FOUND", "chapter session does not exist")
        result = await self._session.execute(
            select(ChapterTrueExamSessionQuestion, Question)
            .join(Question, Question.id == ChapterTrueExamSessionQuestion.question_id)
            .where(ChapterTrueExamSessionQuestion.session_id == session.id)
            .order_by(ChapterTrueExamSessionQuestion.sequence)
        )
        return session, list(result.tuples().all())

    async def submit_chapter_session(
        self,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        results: list[dict[str, object]],
        idempotency_key: str,
    ) -> ChapterTrueExamSession:
        student = await self._student(user_id)
        session = await self._session.get(ChapterTrueExamSession, session_id, with_for_update=True)
        if session is None or session.student_id != student.id:
            raise AppError(404, "CHAPTER_SESSION_NOT_FOUND", "chapter session does not exist")
        links = list(
            (
                await self._session.scalars(
                    select(ChapterTrueExamSessionQuestion).where(
                        ChapterTrueExamSessionQuestion.session_id == session.id
                    )
                )
            ).all()
        )
        by_id = {str(link.question_id): link for link in links}
        if {str(item["question_id"]) for item in results} != set(by_id):
            raise AppError(422, "INCOMPLETE_EXAM_SUBMISSION", "submit every chapter question once")
        for item in results:
            question_id = uuid.UUID(str(item["question_id"]))
            existing = await self._session.scalar(
                select(QuestionAttempt).where(
                    QuestionAttempt.student_id == student.id,
                    QuestionAttempt.idempotency_key == f"{idempotency_key}:{question_id}",
                )
            )
            if existing is None:
                duration = item["duration_seconds"]
                score = item["score_ratio"]
                if not isinstance(duration, int) or not isinstance(score, (int, float)):
                    raise AppError(422, "INVALID_EXAM_RESULT", "invalid chapter exam result")
                self._session.add(
                    QuestionAttempt(
                        student_id=student.id,
                        question_id=question_id,
                        context="TRUE_EXAM",
                        chapter_true_exam_session_id=session.id,
                        finished_at=datetime.now(UTC),
                        actual_duration_seconds=duration,
                        score_ratio=float(score),
                        looked_at_solution=bool(item.get("looked_at_solution", False)),
                        student_error_note=(
                            str(item["error_note"]) if item.get("error_note") else None
                        ),
                        agent_error_type=ErrorType.UNKNOWN if float(score) < 0.6 else None,
                        idempotency_key=f"{idempotency_key}:{question_id}",
                    )
                )
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
                    await self._update_true_exam_profile(
                        student.id,
                        session.school_profile_id,
                        knowledge_id,
                        float(score),
                        duration,
                    )
            by_id[str(question_id)].completed_at = datetime.now(UTC)
        session.completed_questions = len(results)
        session.status = "COMPLETED"
        session.completed_at = datetime.now(UTC)
        session.version += 1
        progress = await self._progress(student.id, session.knowledge_id, create=True)
        progress.true_exam_completed_at = session.completed_at
        progress.specialized_unlocked_at = session.completed_at
        progress.version += 1
        await self._session.commit()
        return session

    async def _update_true_exam_profile(
        self,
        student_id: uuid.UUID,
        school_id: uuid.UUID,
        knowledge_id: uuid.UUID,
        score_ratio: float,
        duration_seconds: int,
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
        profile.accuracy = (profile.accuracy * count + float(score_ratio >= 0.6)) / (count + 1)
        profile.average_score_ratio = (profile.average_score_ratio * count + score_ratio) / (
            count + 1
        )
        profile.average_duration_seconds = round(
            (profile.average_duration_seconds * count + duration_seconds) / (count + 1)
        )

    @staticmethod
    def _chapter_nodes(
        chapter: KnowledgeNode, children_by_parent: dict[uuid.UUID, list[KnowledgeNode]]
    ) -> list[KnowledgeNode]:
        result = [chapter]
        pending = list(children_by_parent.get(chapter.id, []))
        while pending:
            node = pending.pop(0)
            result.append(node)
            pending.extend(children_by_parent.get(node.id, []))
        return result

    async def _question_snapshot(
        self, school_id: uuid.UUID, knowledge_id: uuid.UUID
    ) -> tuple[str, int, list[uuid.UUID]]:
        rows = list(
            (
                await self._session.scalars(
                    select(Question.id)
                    .join(QuestionKnowledge, QuestionKnowledge.question_id == Question.id)
                    .where(
                        Question.school_profile_id == school_id,
                        Question.source_type == "TRUE_EXAM",
                        Question.quality_status == QuestionQuality.VALID,
                        QuestionKnowledge.knowledge_id == knowledge_id,
                    )
                    .order_by(Question.year, Question.code)
                )
            ).all()
        )
        source = ":".join([str(school_id), str(knowledge_id), *(str(item) for item in rows)])
        version = hashlib.sha256(source.encode()).hexdigest()
        return version, len(rows), rows

    async def _learning_task_counts(
        self, student_id: uuid.UUID, knowledge_id: uuid.UUID
    ) -> tuple[int, int]:
        rows = list(
            (
                await self._session.scalars(
                    select(PlanTask)
                    .join(PlanTaskKnowledge, PlanTaskKnowledge.task_id == PlanTask.id)
                    .join(WeeklyPlan, WeeklyPlan.id == PlanTask.plan_id)
                    .where(
                        WeeklyPlan.student_id == student_id,
                        PlanTaskKnowledge.knowledge_id == knowledge_id,
                    )
                )
            ).all()
        )
        rows = [
            row
            for row in rows
            if row.task_type.value
            in {
                "COURSE_LEARNING",
                "HANDOUT_PRACTICE",
                "ERROR_REVIEW",
                "KNOWLEDGE_SUMMARY",
            }
            and row.status.value not in {"SUPERSEDED_LEGACY", "SKIPPED"}
        ]
        return len(rows), sum(row.status.value == "COMPLETED" for row in rows)

    async def _true_question_count(
        self, school_id: uuid.UUID | None, knowledge_id: uuid.UUID
    ) -> int:
        if school_id is None:
            return 0
        return int(
            await self._session.scalar(
                select(func.count(Question.id))
                .join(QuestionKnowledge, QuestionKnowledge.question_id == Question.id)
                .where(
                    Question.school_profile_id == school_id,
                    Question.source_type == "TRUE_EXAM",
                    Question.quality_status == QuestionQuality.VALID,
                    QuestionKnowledge.knowledge_id == knowledge_id,
                )
            )
            or 0
        )

    async def _true_completed_count(self, student_id: uuid.UUID, knowledge_id: uuid.UUID) -> int:
        return int(
            await self._session.scalar(
                select(func.count(QuestionAttempt.id.distinct()))
                .join(
                    QuestionKnowledge, QuestionKnowledge.question_id == QuestionAttempt.question_id
                )
                .where(
                    QuestionAttempt.student_id == student_id,
                    QuestionAttempt.context == "TRUE_EXAM",
                    QuestionKnowledge.knowledge_id == knowledge_id,
                )
            )
            or 0
        )

    async def _school_nodes(self, school_id: uuid.UUID | None) -> list[KnowledgeNode]:
        if school_id is None:
            return []
        return list(
            (
                await self._session.scalars(
                    select(KnowledgeNode)
                    .join(SchoolKnowledgeStat, SchoolKnowledgeStat.knowledge_id == KnowledgeNode.id)
                    .where(SchoolKnowledgeStat.school_profile_id == school_id)
                    .order_by(SchoolKnowledgeStat.syllabus_order, KnowledgeNode.code)
                )
            ).all()
        )

    async def _progress(
        self, student_id: uuid.UUID, knowledge_id: uuid.UUID, create: bool = False
    ) -> StudentKnowledgeProgress:
        progress = await self._session.scalar(
            select(StudentKnowledgeProgress)
            .where(
                StudentKnowledgeProgress.student_id == student_id,
                StudentKnowledgeProgress.knowledge_id == knowledge_id,
            )
            .with_for_update()
        )
        if progress is None and create:
            progress = StudentKnowledgeProgress(
                student_id=student_id,
                knowledge_id=knowledge_id,
                status=KnowledgeLearningStatus.NOT_STARTED,
                version=1,
            )
            self._session.add(progress)
            await self._session.flush()
        if progress is None:
            progress = StudentKnowledgeProgress(
                student_id=student_id,
                knowledge_id=knowledge_id,
                status=KnowledgeLearningStatus.NOT_STARTED,
                version=1,
            )
        return progress

    async def _student(self, user_id: uuid.UUID) -> Student:
        student = await self._session.scalar(select(Student).where(Student.user_id == user_id))
        if student is None:
            raise AppError(404, "STUDENT_PROFILE_NOT_FOUND", "student profile has not been created")
        return student
