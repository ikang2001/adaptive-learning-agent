from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from math import floor

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.jobs import JobService
from app.config import Settings
from app.domain.enums import (
    AttemptContext,
    Difficulty,
    ErrorType,
    ExamStatus,
    JobStatus,
    MockExamType,
    QuestionQuality,
    SourceType,
    StudentStage,
)
from app.domain.exams import ExamQuestion, ExamSpec, ExamValidation, MockExamValidator
from app.domain.learning import MasteryObservation, MasteryStrategy
from app.errors import AppError
from app.infrastructure.adapters.question_generator import QuestionCandidateGenerator
from app.infrastructure.db.models import (
    BackgroundJob,
    ExamProfile,
    GeneratedQuestionCandidate,
    KnowledgeNode,
    MockExam,
    MockExamAttempt,
    MockExamQuestion,
    Question,
    QuestionAttempt,
    QuestionKnowledge,
    ReviewDecision,
    SchoolKnowledgeStat,
    Student,
    StudentKnowledgeState,
    TrueExamProfile,
)


@dataclass(frozen=True, slots=True)
class MockSubmissionItem:
    question_id: uuid.UUID
    score_ratio: float
    duration_seconds: int
    looked_at_solution: bool
    error_note: str | None


class MockExamGenerationService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._validator = MockExamValidator()

    async def execute_job(self, job: BackgroundJob) -> MockExam:
        if job.user_id is None:
            raise AppError(422, "JOB_HAS_NO_OWNER", "mock job requires an owner")
        student = await self._session.scalar(select(Student).where(Student.user_id == job.user_id))
        if student is None or student.target_school_id is None:
            raise AppError(
                422, "STUDENT_PROFILE_INCOMPLETE", "student and target school are required"
            )
        profile = await self._session.scalar(
            select(ExamProfile)
            .where(ExamProfile.school_profile_id == student.target_school_id)
            .order_by(ExamProfile.created_at.desc())
        )
        if profile is None:
            raise AppError(422, "EXAM_PROFILE_NOT_FOUND", "target school has no exam profile")
        mock = await self._load_or_create_mock(job, student, profile)
        await self._session.execute(
            delete(MockExamQuestion).where(MockExamQuestion.mock_exam_id == mock.id)
        )
        desired_knowledge = await self._desired_knowledge_counts(student, profile, mock)
        desired_difficulty = self._count_distribution(
            profile.difficulty_distribution, profile.question_count
        )
        selected = await self._select_questions(
            student, desired_knowledge, desired_difficulty, profile.question_count
        )
        validation = self._validate_selection(
            profile, desired_knowledge, desired_difficulty, selected
        )
        if not validation.valid:
            missing_pairs = self._missing_pairs(desired_knowledge, desired_difficulty, selected)
            await self._create_review_candidates(
                mock,
                missing_pairs or [(next(iter(desired_knowledge)), next(iter(desired_difficulty)))],
            )
            mock.status = ExamStatus.WAITING_FOR_REVIEW
            mock.validation_result = {"valid": False, "errors": list(validation.errors)}
            job.status = JobStatus.WAITING_FOR_REVIEW
            job.result = {"mock_exam_id": str(mock.id), "status": mock.status.value}
            return mock
        for sequence, (question, _, _) in enumerate(selected, start=1):
            self._session.add(
                MockExamQuestion(
                    mock_exam_id=mock.id,
                    question_id=question.id,
                    sequence=sequence,
                    score=profile.total_score // profile.question_count,
                )
            )
        mock.status = ExamStatus.PUBLISHED
        mock.validation_result = {"valid": True, "errors": []}
        job.result = {"mock_exam_id": str(mock.id), "status": mock.status.value}
        return mock

    async def _load_or_create_mock(
        self, job: BackgroundJob, student: Student, profile: ExamProfile
    ) -> MockExam:
        mock_id = job.payload.get("mock_exam_id")
        if mock_id:
            mock = await self._session.get(MockExam, uuid.UUID(str(mock_id)), with_for_update=True)
            if mock is None or mock.student_id != student.id:
                raise AppError(404, "MOCK_EXAM_NOT_FOUND", "mock exam does not exist")
            mock.job_id = job.id
            return mock
        mock_type = MockExamType(str(job.payload["mock_type"]))
        target = job.payload.get("target_knowledge_id")
        if mock_type is MockExamType.SPECIALIZED and not target:
            raise AppError(422, "TARGET_KNOWLEDGE_REQUIRED", "specialized mock requires a target")
        mock = MockExam(
            student_id=student.id,
            exam_profile_id=profile.id,
            job_id=job.id,
            mock_type=mock_type,
            status=ExamStatus.DRAFT,
            target_knowledge_id=uuid.UUID(str(target)) if target else None,
            total_score=profile.total_score,
            duration_minutes=profile.duration_minutes,
            strategy_version="mock_v1",
        )
        self._session.add(mock)
        await self._session.flush()
        return mock

    async def _desired_knowledge_counts(
        self, student: Student, profile: ExamProfile, mock: MockExam
    ) -> dict[str, int]:
        base = self._count_distribution(profile.knowledge_distribution, profile.question_count)
        if mock.mock_type is MockExamType.SPECIALIZED:
            target = await self._session.get(KnowledgeNode, mock.target_knowledge_id)
            school_nodes = list(
                (
                    await self._session.scalars(
                        select(KnowledgeNode)
                        .join(
                            SchoolKnowledgeStat,
                            SchoolKnowledgeStat.knowledge_id == KnowledgeNode.id,
                        )
                        .where(SchoolKnowledgeStat.school_profile_id == profile.school_profile_id)
                    )
                ).all()
            )
            if (
                target is None
                or target.parent_id is not None
                or all(node.id != target.id for node in school_nodes)
            ):
                raise AppError(422, "SPECIALIZED_SCOPE_MUST_BE_CHAPTER", "target must be a chapter")
            children_by_parent: dict[uuid.UUID, list[KnowledgeNode]] = {}
            for node in school_nodes:
                if node.parent_id:
                    children_by_parent.setdefault(node.parent_id, []).append(node)
            scope_nodes = self._chapter_nodes(target, children_by_parent)
            profile_signals = {
                row.knowledge_id: (row.accuracy, row.attempt_count)
                for row in (
                    await self._session.scalars(
                        select(TrueExamProfile).where(
                            TrueExamProfile.student_id == student.id,
                            TrueExamProfile.school_profile_id == profile.school_profile_id,
                            TrueExamProfile.knowledge_id.in_([node.id for node in scope_nodes]),
                        )
                    )
                ).all()
            }
            ranked = sorted(
                scope_nodes,
                key=lambda node: (
                    profile_signals.get(node.id, (1.0, 0))[1] == 0,
                    profile_signals.get(node.id, (1.0, 0))[0],
                    -profile_signals.get(node.id, (1.0, 0))[1],
                ),
            )
            target_count = round(profile.question_count * 0.6)
            signals = [(node.code, *profile_signals.get(node.id, (1.0, 0))) for node in ranked]
            result = self._specialized_target_counts(signals, target_count)
            scope_codes = {node.code for node in scope_nodes}
            outside = [
                code
                for code, count in base.items()
                if code not in scope_codes
                for _ in range(count)
            ]
            outside_count = profile.question_count - target_count
            fallback = outside or [node.code for node in ranked]
            for index in range(outside_count):
                code = fallback[index % len(fallback)]
                result[code] = result.get(code, 0) + 1
            return result
        states = {
            str(row.knowledge_id): row.mastery_score
            for row in (
                await self._session.scalars(
                    select(StudentKnowledgeState).where(
                        StudentKnowledgeState.student_id == student.id
                    )
                )
            ).all()
        }
        nodes = {
            row.code: row.id
            for row in (
                await self._session.scalars(
                    select(KnowledgeNode).where(KnowledgeNode.code.in_(list(base)))
                )
            ).all()
        }
        if not nodes:
            return base
        weakest = min(nodes, key=lambda code: states.get(str(nodes[code]), 0.5))
        strongest = max(nodes, key=lambda code: states.get(str(nodes[code]), 0.5))
        if weakest != strongest and states.get(str(nodes[weakest]), 0.5) < 0.6:
            base[weakest] = min(base[weakest] + 1, 2)
            base[strongest] = max(0, base[strongest] - 1)
        return base

    async def _select_questions(
        self,
        student: Student,
        knowledge_counts: dict[str, int],
        difficulty_counts: dict[str, int],
        question_count: int,
    ) -> list[tuple[Question, KnowledgeNode, Difficulty]]:
        attempted = set(
            (
                await self._session.scalars(
                    select(QuestionAttempt.question_id).where(
                        QuestionAttempt.student_id == student.id
                    )
                )
            ).all()
        )
        result = await self._session.execute(
            select(Question, KnowledgeNode)
            .join(QuestionKnowledge, QuestionKnowledge.question_id == Question.id)
            .join(KnowledgeNode, KnowledgeNode.id == QuestionKnowledge.knowledge_id)
            .where(Question.quality_status == QuestionQuality.VALID)
            .order_by(Question.source_type, Question.code)
        )
        rows = list(result.tuples().all())
        selected: list[tuple[Question, KnowledgeNode, Difficulty]] = []
        used: set[uuid.UUID] = set()
        knowledge_slots = [code for code, count in knowledge_counts.items() for _ in range(count)]
        difficulty_slots = self._expand_balanced_counts(difficulty_counts)
        for code, difficulty in zip(knowledge_slots, difficulty_slots, strict=True):
            candidate = self._best_candidate(rows, code, difficulty, attempted | used)
            if candidate is None:
                continue
            question, node = candidate
            selected.append((question, node, question.difficulty))
            used.add(question.id)
        return selected[:question_count]

    @staticmethod
    def _best_candidate(
        rows: list[tuple[Question, KnowledgeNode]],
        knowledge_code: str,
        difficulty: str,
        excluded: set[uuid.UUID],
    ) -> tuple[Question, KnowledgeNode] | None:
        candidates = [
            row
            for row in rows
            if row[1].code == knowledge_code
            and row[0].difficulty.value == difficulty
            and row[0].id not in excluded
        ]
        candidates.sort(
            key=lambda row: (
                row[0].source_type is SourceType.GENERATED,
                row[0].code,
            )
        )
        return candidates[0] if candidates else None

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

    @staticmethod
    def _specialized_target_counts(
        signals: list[tuple[str, float, int]], total: int
    ) -> dict[str, int]:
        weights = [
            1.0 + (1.0 - accuracy) * 3 if attempts else 1.0 for _, accuracy, attempts in signals
        ]
        weight_total = sum(weights)
        raw_counts = [total * weight / weight_total for weight in weights]
        counts = [floor(value) for value in raw_counts]
        remainder = total - sum(counts)
        remainder_order = sorted(
            range(len(signals)),
            key=lambda index: (raw_counts[index] - counts[index], -index),
            reverse=True,
        )
        for index in remainder_order[:remainder]:
            counts[index] += 1
        return {
            code: count for (code, _, _), count in zip(signals, counts, strict=True) if count > 0
        }

    @staticmethod
    def _expand_balanced_counts(counts: dict[str, int]) -> list[str]:
        remaining = dict(counts)
        values: list[str] = []
        while any(count > 0 for count in remaining.values()):
            for key in remaining:
                if remaining[key] <= 0:
                    continue
                values.append(key)
                remaining[key] -= 1
        return values

    def _validate_selection(
        self,
        profile: ExamProfile,
        knowledge_counts: dict[str, int],
        difficulty_counts: dict[str, int],
        selected: list[tuple[Question, KnowledgeNode, Difficulty]],
    ) -> ExamValidation:
        count = profile.question_count
        spec = ExamSpec(
            total_score=profile.total_score,
            question_count=count,
            difficulty_distribution={
                Difficulty(key): value / count for key, value in difficulty_counts.items()
            },
            knowledge_distribution={key: value / count for key, value in knowledge_counts.items()},
            distribution_tolerance=0.001,
        )
        score = profile.total_score // count
        questions = [
            ExamQuestion(
                str(question.id),
                score,
                difficulty,
                node.code,
                bool(question.answer),
                question.quality_status is QuestionQuality.VALID,
            )
            for question, node, difficulty in selected
        ]
        return self._validator.validate(spec, questions)

    async def _create_review_candidates(
        self,
        mock: MockExam,
        missing_pairs: list[tuple[str, str]],
    ) -> None:
        existing = await self._session.scalar(
            select(GeneratedQuestionCandidate).where(
                GeneratedQuestionCandidate.mock_exam_id == mock.id,
                GeneratedQuestionCandidate.quality_status == QuestionQuality.REVIEW_REQUIRED,
            )
        )
        if existing is not None:
            return
        nodes = {
            node.code: node
            for node in (
                await self._session.scalars(
                    select(KnowledgeNode).where(
                        KnowledgeNode.code.in_([code for code, _ in missing_pairs])
                    )
                )
            ).all()
        }
        generator = QuestionCandidateGenerator(self._settings)
        for index, (code, difficulty) in enumerate(missing_pairs):
            seed = f"{mock.id.hex[:8]}-{index + 1}"
            content = await generator.generate(nodes[code].name, difficulty, seed)
            self._session.add(
                GeneratedQuestionCandidate(
                    requested_by_student_id=mock.student_id,
                    mock_exam_id=mock.id,
                    content=content.content,
                    answer=content.answer,
                    solution=content.solution,
                    metadata_json={
                        "knowledge_id": str(nodes[code].id),
                        "knowledge_code": code,
                        "difficulty": difficulty,
                        "question_type": "GENERATED_CALCULATION",
                        "score": 15,
                        "estimated_duration_minutes": 18,
                    },
                    generator_model=content.model_name,
                    prompt_version="question_generation_v1",
                    generation_seed=seed,
                    validation_result={
                        "schema_valid": True,
                        "answer_present": bool(content.answer),
                        "requires_human_review": True,
                    },
                    quality_status=QuestionQuality.REVIEW_REQUIRED,
                )
            )

    def _missing_pairs(
        self,
        knowledge_counts: dict[str, int],
        difficulty_counts: dict[str, int],
        selected: list[tuple[Question, KnowledgeNode, Difficulty]],
    ) -> list[tuple[str, str]]:
        knowledge_slots = [code for code, count in knowledge_counts.items() for _ in range(count)]
        difficulty_slots = self._expand_balanced_counts(difficulty_counts)
        expected = Counter(zip(knowledge_slots, difficulty_slots, strict=True))
        actual = Counter((node.code, difficulty.value) for _, node, difficulty in selected)
        missing: list[tuple[str, str]] = []
        for pair, count in (expected - actual).items():
            missing.extend([pair] * count)
        return missing

    @staticmethod
    def _count_distribution(distribution: dict[str, float], total: int) -> dict[str, int]:
        counts = {key: round(value * total) for key, value in distribution.items()}
        difference = total - sum(counts.values())
        if difference:
            largest = max(distribution, key=distribution.__getitem__)
            counts[largest] += difference
        return counts


class MockExamService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._mastery = MasteryStrategy()

    async def owned_exam(
        self, user_id: uuid.UUID, mock_id: uuid.UUID
    ) -> tuple[MockExam, list[tuple[MockExamQuestion, Question]]]:
        mock = await self._session.scalar(
            select(MockExam)
            .join(Student, Student.id == MockExam.student_id)
            .where(MockExam.id == mock_id, Student.user_id == user_id)
        )
        if mock is None:
            raise AppError(404, "MOCK_EXAM_NOT_FOUND", "mock exam does not exist")
        result = await self._session.execute(
            select(MockExamQuestion, Question)
            .join(Question, Question.id == MockExamQuestion.question_id)
            .where(MockExamQuestion.mock_exam_id == mock.id)
            .order_by(MockExamQuestion.sequence)
        )
        rows = list(result.tuples().all())
        return mock, rows

    async def submit(
        self,
        user_id: uuid.UUID,
        mock_id: uuid.UUID,
        results: list[MockSubmissionItem],
        idempotency_key: str,
    ) -> MockExamAttempt:
        mock, assigned = await self.owned_exam(user_id, mock_id)
        if mock.status not in {ExamStatus.PUBLISHED, ExamStatus.IN_PROGRESS}:
            raise AppError(409, "MOCK_EXAM_NOT_PUBLISHED", "mock exam cannot be submitted")
        existing = await self._session.scalar(
            select(MockExamAttempt).where(
                MockExamAttempt.student_id == mock.student_id,
                MockExamAttempt.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing
        assigned_map = {question.id: (link, question) for link, question in assigned}
        if {item.question_id for item in results} != set(assigned_map) or len(results) != len(
            assigned_map
        ):
            raise AppError(422, "INCOMPLETE_EXAM_SUBMISSION", "submit each mock question once")
        total = sum(
            Decimal(str(item.score_ratio)) * assigned_map[item.question_id][0].score
            for item in results
        )
        attempt = MockExamAttempt(
            student_id=mock.student_id,
            mock_exam_id=mock.id,
            score=total,
            duration_seconds=sum(item.duration_seconds for item in results),
            idempotency_key=idempotency_key,
        )
        self._session.add(attempt)
        await self._session.flush()
        for item in results:
            question = assigned_map[item.question_id][1]
            await self._record_question(mock, attempt, question, item)
        mock.status = ExamStatus.COMPLETED
        student = await self._session.get(Student, mock.student_id)
        if student:
            student.current_stage = StudentStage.MOCK_EXAM
            student.version += 1
        await self._session.commit()
        return attempt

    async def _record_question(
        self,
        mock: MockExam,
        overall: MockExamAttempt,
        question: Question,
        result: MockSubmissionItem,
    ) -> None:
        self._session.add(
            QuestionAttempt(
                student_id=mock.student_id,
                question_id=question.id,
                context=AttemptContext.MOCK_EXAM,
                mock_exam_id=mock.id,
                finished_at=datetime.now(UTC),
                actual_duration_seconds=result.duration_seconds,
                score_ratio=result.score_ratio,
                looked_at_solution=result.looked_at_solution,
                student_error_note=result.error_note,
                agent_error_type=ErrorType.UNKNOWN if result.score_ratio < 0.6 else None,
                idempotency_key=f"mock:{overall.id}:{question.id}",
            )
        )
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
            state = await self._session.scalar(
                select(StudentKnowledgeState)
                .where(
                    StudentKnowledgeState.student_id == mock.student_id,
                    StudentKnowledgeState.knowledge_id == knowledge_id,
                )
                .with_for_update()
            )
            if state is None:
                state = StudentKnowledgeState(
                    student_id=mock.student_id,
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
                MasteryObservation(
                    result.score_ratio, question.difficulty, result.looked_at_solution
                ),
            )
            state.mastery_score = updated.score
            state.confidence = updated.confidence
            state.evidence_count = updated.evidence_count
            state.last_practiced_at = datetime.now(UTC)
            state.version += 1


class GeneratedQuestionReviewService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def pending(self) -> list[GeneratedQuestionCandidate]:
        return list(
            (
                await self._session.scalars(
                    select(GeneratedQuestionCandidate)
                    .where(
                        GeneratedQuestionCandidate.quality_status == QuestionQuality.REVIEW_REQUIRED
                    )
                    .order_by(GeneratedQuestionCandidate.created_at)
                    .limit(100)
                )
            ).all()
        )

    async def decide(
        self,
        reviewer_id: uuid.UUID,
        candidate_id: uuid.UUID,
        approve: bool,
        reason: str | None,
        idempotency_key: str,
    ) -> tuple[GeneratedQuestionCandidate, BackgroundJob | None]:
        candidate = await self._session.get(
            GeneratedQuestionCandidate, candidate_id, with_for_update=True
        )
        if candidate is None:
            raise AppError(404, "GENERATED_QUESTION_NOT_FOUND", "candidate does not exist")
        if candidate.quality_status is not QuestionQuality.REVIEW_REQUIRED:
            return candidate, None
        if approve:
            await self._publish(candidate)
            candidate.quality_status = QuestionQuality.VALID
            decision = "APPROVED"
        else:
            candidate.quality_status = QuestionQuality.REJECTED
            decision = "REJECTED"
        self._session.add(
            ReviewDecision(
                candidate_id=candidate.id,
                reviewer_user_id=reviewer_id,
                decision=decision,
                reason=reason,
            )
        )
        resume_job = await self._resume_if_ready(candidate, idempotency_key)
        await self._session.commit()
        return candidate, resume_job

    async def _publish(self, candidate: GeneratedQuestionCandidate) -> None:
        metadata = candidate.metadata_json
        knowledge_id = uuid.UUID(str(metadata["knowledge_id"]))
        question = Question(
            code=f"GEN-{candidate.id}",
            source_type=SourceType.GENERATED,
            question_type=str(metadata["question_type"]),
            difficulty=Difficulty(str(metadata["difficulty"])),
            score=int(metadata["score"]),
            estimated_duration_minutes=int(metadata["estimated_duration_minutes"]),
            content=candidate.content,
            solution=candidate.solution,
            answer=candidate.answer,
            provenance={"candidate_id": str(candidate.id)},
            quality_status=QuestionQuality.VALID,
            content_version="generated_v1",
        )
        self._session.add(question)
        await self._session.flush()
        self._session.add(
            QuestionKnowledge(question_id=question.id, knowledge_id=knowledge_id, is_primary=True)
        )

    async def _resume_if_ready(
        self, candidate: GeneratedQuestionCandidate, idempotency_key: str
    ) -> BackgroundJob | None:
        if candidate.mock_exam_id is None:
            return None
        pending = await self._session.scalar(
            select(GeneratedQuestionCandidate.id).where(
                GeneratedQuestionCandidate.mock_exam_id == candidate.mock_exam_id,
                GeneratedQuestionCandidate.id != candidate.id,
                GeneratedQuestionCandidate.quality_status == QuestionQuality.REVIEW_REQUIRED,
            )
        )
        if pending is not None:
            return None
        mock = await self._session.get(MockExam, candidate.mock_exam_id)
        if mock is None:
            return None
        student = await self._session.get(Student, mock.student_id)
        if student is None:
            return None
        return await JobService(self._session).create(
            student.user_id,
            "GENERATE_MOCK",
            {"mock_exam_id": str(mock.id)},
            f"{idempotency_key}:resume",
            commit=False,
        )
