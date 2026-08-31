from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import (
    AgentRunStatus,
    AttemptContext,
    ChangeActor,
    Difficulty,
    ErrorType,
    ExamStatus,
    JobStatus,
    KnowledgeLearningStatus,
    MockExamType,
    PlanStatus,
    ProposalStatus,
    ProposalType,
    QuestionQuality,
    ResourceImportStatus,
    ResourceStatus,
    ResourceType,
    Role,
    SourceType,
    StudentStage,
    TargetSchoolChangeStatus,
    TaskOrigin,
    TaskStatus,
    TaskType,
    UserStatus,
)
from app.infrastructure.db.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    VersionMixin,
)


class User(UUIDPrimaryKeyMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "users"

    phone_lookup_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    phone_ciphertext: Mapped[str] = mapped_column(Text)
    status: Mapped[UserStatus] = mapped_column(Enum(UserStatus), default=UserStatus.ACTIVE)


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[Role] = mapped_column(Enum(Role), primary_key=True)


class RefreshSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "refresh_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class AccountDeletion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "account_deletions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    purge_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SchoolProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "school_profiles"

    code: Mapped[str] = mapped_column(String(64), unique=True)
    school_name: Mapped[str] = mapped_column(String(128))
    major: Mapped[str] = mapped_column(String(128))
    subject_code: Mapped[str] = mapped_column(String(32))
    subject_name: Mapped[str] = mapped_column(String(128))
    syllabus_version: Mapped[str] = mapped_column(String(32))
    source_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")


class Student(UUIDPrimaryKeyMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "students"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    target_school_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("school_profiles.id")
    )
    exam_subject: Mapped[str] = mapped_column(String(128), default="自动控制原理")
    exam_date: Mapped[date | None] = mapped_column(Date)
    current_stage: Mapped[StudentStage] = mapped_column(
        Enum(StudentStage), default=StudentStage.FOUNDATION
    )


class StudentAvailability(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "student_availability"
    __table_args__ = (
        UniqueConstraint("student_id", "available_date", name="uq_availability_student_date"),
        CheckConstraint("available_minutes BETWEEN 0 AND 1440", name="ck_available_minutes"),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    available_date: Mapped[date] = mapped_column(Date)
    available_minutes: Mapped[int] = mapped_column(Integer)


class StudentAvailabilityTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "student_availability_templates"
    __table_args__ = (
        UniqueConstraint("student_id", "weekday", name="uq_availability_template_weekday"),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_availability_template_weekday"),
        CheckConstraint("available_minutes BETWEEN 0 AND 1440", name="ck_template_minutes"),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    weekday: Mapped[int] = mapped_column(Integer)
    available_minutes: Mapped[int] = mapped_column(Integer)


class KnowledgeNode(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_nodes"

    code: Mapped[str] = mapped_column(String(64), unique=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_nodes.id")
    )
    level: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    tree_version: Mapped[str] = mapped_column(String(32))


class KnowledgePrerequisite(Base):
    __tablename__ = "knowledge_prerequisites"

    knowledge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), primary_key=True
    )
    prerequisite_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), primary_key=True
    )


class StudentKnowledgeProgress(UUIDPrimaryKeyMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "student_knowledge_progress"
    __table_args__ = (
        UniqueConstraint("student_id", "knowledge_id", name="uq_student_learning_progress"),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    knowledge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_nodes.id"), index=True
    )
    status: Mapped[KnowledgeLearningStatus] = mapped_column(
        Enum(KnowledgeLearningStatus), default=KnowledgeLearningStatus.NOT_STARTED
    )
    strengthened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    strengthened_by_user: Mapped[bool] = mapped_column(Boolean, default=False)
    true_exam_snapshot_version: Mapped[str | None] = mapped_column(String(64))
    true_exam_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    specialized_unlocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LearningResource(UUIDPrimaryKeyMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "learning_resources"

    title: Mapped[str] = mapped_column(String(256))
    resource_type: Mapped[ResourceType] = mapped_column(Enum(ResourceType), index=True)
    status: Mapped[ResourceStatus] = mapped_column(
        Enum(ResourceStatus), default=ResourceStatus.DRAFT, index=True
    )
    school_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("school_profiles.id", ondelete="SET NULL"), index=True
    )
    description: Mapped[str] = mapped_column(Text, default="")
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResourceVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "resource_versions"
    __table_args__ = (
        UniqueConstraint("resource_id", "version_number", name="uq_resource_version"),
        UniqueConstraint("content_hash", name="uq_resource_content_hash"),
    )

    resource_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("learning_resources.id", ondelete="CASCADE"), index=True
    )
    version_number: Mapped[int] = mapped_column(Integer)
    original_filename: Mapped[str] = mapped_column(String(512))
    media_type: Mapped[str] = mapped_column(String(128))
    content_hash: Mapped[str] = mapped_column(String(64))
    storage_path: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer)
    parser_version: Mapped[str] = mapped_column(String(32), default="resource_parser_v1")


class ResourceSection(UUIDPrimaryKeyMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "resource_sections"
    __table_args__ = (
        UniqueConstraint("resource_version_id", "section_path", name="uq_resource_section_path"),
    )

    resource_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resource_versions.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resource_sections.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(512))
    section_path: Mapped[str] = mapped_column(String(1024))
    level: Mapped[int] = mapped_column(Integer)
    sequence: Mapped[int] = mapped_column(Integer)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    suggested_units: Mapped[int | None] = mapped_column(Integer)
    unit_type: Mapped[str | None] = mapped_column(String(32))


class ResourceChunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "resource_chunks"
    __table_args__ = (
        UniqueConstraint("resource_version_id", "sequence", name="uq_resource_chunk_sequence"),
    )

    resource_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resource_versions.id", ondelete="CASCADE"), index=True
    )
    section_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resource_sections.id", ondelete="SET NULL"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    extraction_method: Mapped[str] = mapped_column(String(32))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))


class ResourceKnowledgeMapping(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "resource_knowledge_mappings"
    __table_args__ = (
        UniqueConstraint("section_id", "knowledge_id", name="uq_resource_knowledge_mapping"),
    )

    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resource_sections.id", ondelete="CASCADE"), index=True
    )
    knowledge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_nodes.id"), index=True
    )
    confidence: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32))
    reviewer_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)


class ResourceImportRun(UUIDPrimaryKeyMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "resource_import_runs"
    __table_args__ = (UniqueConstraint("resource_version_id", name="uq_resource_import_version"),)

    resource_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resource_versions.id", ondelete="CASCADE"), index=True
    )
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), index=True
    )
    status: Mapped[ResourceImportStatus] = mapped_column(
        Enum(ResourceImportStatus), default=ResourceImportStatus.QUEUED, index=True
    )
    progress: Mapped[float] = mapped_column(Float, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResourceReviewDecision(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "resource_review_decisions"

    resource_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("learning_resources.id", ondelete="CASCADE"), index=True
    )
    reviewer_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text)


class SchoolKnowledgeStat(Base):
    __tablename__ = "school_knowledge_stats"

    school_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("school_profiles.id", ondelete="CASCADE"), primary_key=True
    )
    knowledge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), primary_key=True
    )
    count: Mapped[int] = mapped_column(Integer)
    total_questions: Mapped[int] = mapped_column(Integer)
    normalized_weight: Mapped[float] = mapped_column(Float)
    syllabus_order: Mapped[int] = mapped_column(Integer, default=9999)
    years_covered: Mapped[int] = mapped_column(Integer)
    last_seen_year: Mapped[int | None] = mapped_column(Integer)
    trend: Mapped[str] = mapped_column(String(32), default="STABLE")
    source_refs: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)


class Question(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "questions"
    __table_args__ = (
        CheckConstraint("score > 0", name="ck_question_score_positive"),
        CheckConstraint("estimated_duration_minutes > 0", name="ck_question_duration_positive"),
        Index("ix_questions_selection", "quality_status", "difficulty", "question_type"),
    )

    code: Mapped[str] = mapped_column(String(96), unique=True)
    source_type: Mapped[SourceType] = mapped_column(Enum(SourceType))
    school_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("school_profiles.id")
    )
    year: Mapped[int | None] = mapped_column(Integer)
    question_type: Mapped[str] = mapped_column(String(64), index=True)
    difficulty: Mapped[Difficulty] = mapped_column(Enum(Difficulty), index=True)
    score: Mapped[int] = mapped_column(Integer)
    estimated_duration_minutes: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    solution: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    quality_status: Mapped[QuestionQuality] = mapped_column(
        Enum(QuestionQuality), default=QuestionQuality.DRAFT
    )
    content_version: Mapped[str] = mapped_column(String(32), default="v1")


class QuestionKnowledge(Base):
    __tablename__ = "question_knowledge"

    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True
    )
    knowledge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), primary_key=True
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)


class MaterialChunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "material_chunks"

    document_id: Mapped[str] = mapped_column(String(96), index=True)
    source: Mapped[str] = mapped_column(String(128))
    chapter: Mapped[str] = mapped_column(String(128))
    page: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str] = mapped_column(String(128))
    text: Mapped[str] = mapped_column(Text)
    document_version: Mapped[str] = mapped_column(String(32))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))


class MaterialChunkKnowledge(Base):
    __tablename__ = "material_chunk_knowledge"

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("material_chunks.id", ondelete="CASCADE"), primary_key=True
    )
    knowledge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), primary_key=True
    )


class WeeklyPlan(UUIDPrimaryKeyMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "weekly_plans"
    __table_args__ = (
        UniqueConstraint("student_id", "start_date", "revision", name="uq_plan_revision"),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    parent_plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    planner_version: Mapped[str] = mapped_column(String(32), default="planner_v1")
    status: Mapped[PlanStatus] = mapped_column(Enum(PlanStatus), default=PlanStatus.DRAFT)
    generated_reason: Mapped[str] = mapped_column(Text, default="INITIAL")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    last_rolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlanTask(UUIDPrimaryKeyMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "plan_tasks"
    __table_args__ = (
        CheckConstraint("estimated_min_minutes >= 0", name="ck_task_min_duration"),
        CheckConstraint("estimated_max_minutes >= estimated_min_minutes", name="ck_task_duration"),
    )

    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("weekly_plans.id", ondelete="CASCADE"), index=True
    )
    task_date: Mapped[date] = mapped_column(Date, index=True)
    task_type: Mapped[TaskType] = mapped_column(Enum(TaskType))
    title: Mapped[str] = mapped_column(String(256), default="学习任务")
    description: Mapped[str] = mapped_column(Text, default="")
    resource_section_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resource_sections.id", ondelete="SET NULL"), index=True
    )
    suggested_scope: Mapped[str | None] = mapped_column(String(512))
    target_count: Mapped[int] = mapped_column(Integer)
    planned_units: Mapped[int | None] = mapped_column(Integer)
    unit_type: Mapped[str | None] = mapped_column(String(32))
    estimated_min_minutes: Mapped[int] = mapped_column(Integer)
    estimated_max_minutes: Mapped[int] = mapped_column(Integer)
    system_suggested_minutes: Mapped[int] = mapped_column(Integer, default=30)
    student_estimated_minutes: Mapped[int | None] = mapped_column(Integer)
    effective_minutes: Mapped[int] = mapped_column(Integer, default=30)
    priority: Mapped[float] = mapped_column(Float)
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus), default=TaskStatus.PENDING)
    origin: Mapped[TaskOrigin] = mapped_column(Enum(TaskOrigin), default=TaskOrigin.SYSTEM)
    is_personal: Mapped[bool] = mapped_column(Boolean, default=False)
    has_capacity_warning: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str] = mapped_column(Text)
    modified_reason: Mapped[str | None] = mapped_column(Text)
    sequence: Mapped[int] = mapped_column(Integer)
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class PlanTaskChangeEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "plan_task_change_events"
    __table_args__ = (
        UniqueConstraint("plan_id", "change_sequence", name="uq_plan_change_sequence"),
    )

    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("weekly_plans.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plan_tasks.id", ondelete="SET NULL"), index=True
    )
    change_sequence: Mapped[int] = mapped_column(Integer)
    actor: Mapped[ChangeActor] = mapped_column(Enum(ChangeActor))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    operation: Mapped[str] = mapped_column(String(32))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PlanTaskKnowledge(Base):
    __tablename__ = "plan_task_knowledge"

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plan_tasks.id", ondelete="CASCADE"), primary_key=True
    )
    knowledge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_nodes.id"), primary_key=True
    )


class PlanTaskQuestion(Base):
    __tablename__ = "plan_task_questions"

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plan_tasks.id", ondelete="CASCADE"), primary_key=True
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(Integer)


class QuestionAttempt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "question_attempts"
    __table_args__ = (
        UniqueConstraint("student_id", "idempotency_key", name="uq_attempt_idempotency"),
        CheckConstraint("score_ratio BETWEEN 0 AND 1", name="ck_attempt_score_ratio"),
        CheckConstraint("actual_duration_seconds > 0", name="ck_attempt_duration"),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("questions.id"))
    plan_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plan_tasks.id")
    )
    true_exam_attempt_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    chapter_true_exam_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chapter_true_exam_sessions.id", ondelete="SET NULL"),
        index=True,
    )
    mock_exam_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    context: Mapped[AttemptContext] = mapped_column(Enum(AttemptContext))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_duration_seconds: Mapped[int] = mapped_column(Integer)
    score_ratio: Mapped[float] = mapped_column(Float)
    looked_at_solution: Mapped[bool] = mapped_column(Boolean, default=False)
    self_difficulty: Mapped[int | None] = mapped_column(Integer)
    student_error_note: Mapped[str | None] = mapped_column(Text)
    agent_error_type: Mapped[ErrorType | None] = mapped_column(Enum(ErrorType))
    idempotency_key: Mapped[str] = mapped_column(String(128))


class Feedback(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "feedback"
    __table_args__ = (
        UniqueConstraint("student_id", "task_id", name="uq_feedback_task"),
        UniqueConstraint("student_id", "idempotency_key", name="uq_feedback_idempotency"),
        CheckConstraint("completion_ratio BETWEEN 0 AND 1", name="ck_feedback_completion"),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("plan_tasks.id"))
    completion_ratio: Mapped[float] = mapped_column(Float)
    actual_duration_seconds: Mapped[int] = mapped_column(Integer)
    completed_count: Mapped[int] = mapped_column(Integer)
    correct_count: Mapped[int] = mapped_column(Integer)
    looked_at_solution: Mapped[bool] = mapped_column(Boolean, default=False)
    perceived_difficulty: Mapped[int | None] = mapped_column(Integer)
    free_text: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    feedback_version: Mapped[int] = mapped_column(Integer, default=1)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    mastery_self_score: Mapped[int | None] = mapped_column(Integer)
    progress_marker: Mapped[str | None] = mapped_column(String(512))


class StudentKnowledgeState(UUIDPrimaryKeyMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "student_knowledge_states"
    __table_args__ = (
        UniqueConstraint("student_id", "knowledge_id", name="uq_student_knowledge"),
        CheckConstraint("mastery_score BETWEEN 0 AND 1", name="ck_mastery_score"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_mastery_confidence"),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    knowledge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_nodes.id"), index=True
    )
    mastery_score: Mapped[float] = mapped_column(Float, default=0.5)
    confidence: Mapped[float] = mapped_column(Float, default=0.25)
    last_practiced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recent_accuracy: Mapped[float | None] = mapped_column(Float)
    recent_avg_duration: Mapped[float | None] = mapped_column(Float)
    error_streak: Mapped[int] = mapped_column(Integer, default=0)
    correct_streak: Mapped[int] = mapped_column(Integer, default=0)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    model_version: Mapped[str] = mapped_column(String(32), default="mastery_v1")


class ErrorProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "error_profiles"
    __table_args__ = (
        UniqueConstraint("student_id", "knowledge_id", "error_type", name="uq_error_profile"),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    knowledge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_nodes.id")
    )
    error_type: Mapped[ErrorType] = mapped_column(Enum(ErrorType))
    occurrence_count: Mapped[int] = mapped_column(Integer, default=0)
    recent_count: Mapped[int] = mapped_column(Integer, default=0)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evidence_attempt_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.25)


class EfficiencyProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "efficiency_profiles"
    __table_args__ = (
        UniqueConstraint(
            "student_id",
            "task_type",
            "knowledge_id",
            name="uq_efficiency_profile",
            postgresql_nulls_not_distinct=True,
        ),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    task_type: Mapped[TaskType] = mapped_column(Enum(TaskType))
    knowledge_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_nodes.id")
    )
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    recent_samples_seconds: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)
    p50_duration_seconds: Mapped[int] = mapped_column(Integer)
    p75_duration_seconds: Mapped[int] = mapped_column(Integer)
    average_duration_seconds: Mapped[int] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float, default=0.25)


class ExamProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "exam_profiles"

    school_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("school_profiles.id", ondelete="CASCADE"), index=True
    )
    total_score: Mapped[int] = mapped_column(Integer)
    duration_minutes: Mapped[int] = mapped_column(Integer)
    question_count: Mapped[int] = mapped_column(Integer)
    structure: Mapped[dict[str, Any]] = mapped_column(JSON)
    difficulty_distribution: Mapped[dict[str, float]] = mapped_column(JSON)
    knowledge_distribution: Mapped[dict[str, float]] = mapped_column(JSON)
    profile_version: Mapped[str] = mapped_column(String(32))


class TrueExam(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "true_exams"
    __table_args__ = (
        UniqueConstraint("school_profile_id", "year", name="uq_true_exam_school_year"),
    )

    school_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("school_profiles.id", ondelete="CASCADE")
    )
    year: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(128))
    total_score: Mapped[int] = mapped_column(Integer)
    duration_minutes: Mapped[int] = mapped_column(Integer)
    source_version: Mapped[str] = mapped_column(String(32))


class TrueExamQuestion(Base):
    __tablename__ = "true_exam_questions"

    true_exam_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("true_exams.id", ondelete="CASCADE"), primary_key=True
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(Integer)


class ChapterTrueExamSession(UUIDPrimaryKeyMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "chapter_true_exam_sessions"
    __table_args__ = (
        UniqueConstraint(
            "student_id",
            "knowledge_id",
            "question_snapshot_version",
            name="uq_chapter_exam_snapshot",
        ),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    knowledge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_nodes.id"), index=True
    )
    school_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("school_profiles.id"), index=True
    )
    question_snapshot_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    total_questions: Mapped[int] = mapped_column(Integer)
    completed_questions: Mapped[int] = mapped_column(Integer, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChapterTrueExamSessionQuestion(Base):
    __tablename__ = "chapter_true_exam_session_questions"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chapter_true_exam_sessions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TargetSchoolChangePreview(UUIDPrimaryKeyMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "target_school_change_previews"

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    from_school_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    to_school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("school_profiles.id"), index=True
    )
    status: Mapped[TargetSchoolChangeStatus] = mapped_column(
        Enum(TargetSchoolChangeStatus), default=TargetSchoolChangeStatus.PREVIEW
    )
    preview: Mapped[dict[str, Any]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TrueExamAttempt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "true_exam_attempts"
    __table_args__ = (
        UniqueConstraint("student_id", "idempotency_key", name="uq_true_exam_attempt_idempotency"),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    true_exam_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("true_exams.id"))
    score: Mapped[Decimal] = mapped_column(Numeric(7, 2))
    duration_seconds: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(128))


class TrueExamProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "true_exam_profiles"
    __table_args__ = (
        UniqueConstraint(
            "student_id", "school_profile_id", "knowledge_id", name="uq_true_exam_profile"
        ),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    school_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("school_profiles.id")
    )
    knowledge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_nodes.id")
    )
    attempt_count: Mapped[int] = mapped_column(Integer)
    accuracy: Mapped[float] = mapped_column(Float)
    average_score_ratio: Mapped[float] = mapped_column(Float)
    average_duration_seconds: Mapped[int] = mapped_column(Integer)


class MockExam(UUIDPrimaryKeyMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "mock_exams"

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    exam_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exam_profiles.id")
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    mock_type: Mapped[MockExamType] = mapped_column(Enum(MockExamType))
    status: Mapped[ExamStatus] = mapped_column(Enum(ExamStatus), default=ExamStatus.DRAFT)
    target_knowledge_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_nodes.id")
    )
    total_score: Mapped[int] = mapped_column(Integer)
    duration_minutes: Mapped[int] = mapped_column(Integer)
    strategy_version: Mapped[str] = mapped_column(String(32), default="mock_v1")
    validation_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class MockExamQuestion(Base):
    __tablename__ = "mock_exam_questions"

    mock_exam_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mock_exams.id", ondelete="CASCADE"), primary_key=True
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    score: Mapped[int] = mapped_column(Integer)


class MockExamAttempt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "mock_exam_attempts"
    __table_args__ = (
        UniqueConstraint("student_id", "idempotency_key", name="uq_mock_attempt_idempotency"),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    mock_exam_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("mock_exams.id"))
    score: Mapped[Decimal] = mapped_column(Numeric(7, 2))
    duration_seconds: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(128))


class GeneratedQuestionCandidate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "generated_question_candidates"

    requested_by_student_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="SET NULL")
    )
    mock_exam_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mock_exams.id", ondelete="SET NULL")
    )
    content: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    solution: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    generator_model: Mapped[str] = mapped_column(String(96))
    prompt_version: Mapped[str] = mapped_column(String(32))
    generation_seed: Mapped[str] = mapped_column(String(64))
    validation_result: Mapped[dict[str, Any]] = mapped_column(JSON)
    quality_status: Mapped[QuestionQuality] = mapped_column(
        Enum(QuestionQuality), default=QuestionQuality.REVIEW_REQUIRED
    )


class ReviewDecision(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "review_decisions"

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generated_question_candidates.id", ondelete="CASCADE"),
        index=True,
    )
    reviewer_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text)


class BackgroundJob(UUIDPrimaryKeyMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "background_jobs"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "job_type", "idempotency_key", name="uq_background_job_idempotency"
        ),
        Index("ix_background_jobs_dispatch", "status", "available_at"),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    job_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.QUEUED)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentRun(UUIDPrimaryKeyMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "agent_runs"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("background_jobs.id", ondelete="CASCADE"), unique=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    goal: Mapped[str] = mapped_column(String(128))
    status: Mapped[AgentRunStatus] = mapped_column(
        Enum(AgentRunStatus), default=AgentRunStatus.PENDING
    )
    model_version: Mapped[str] = mapped_column(String(96))
    prompt_version: Mapped[str] = mapped_column(String(32))
    policy_version: Mapped[str] = mapped_column(String(32))
    loop_count: Mapped[int] = mapped_column(Integer, default=0)
    model_call_count: Mapped[int] = mapped_column(Integer, default=0)
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    resumed_count: Mapped[int] = mapped_column(Integer, default=0)
    termination_reason: Mapped[str | None] = mapped_column(String(64))
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fencing_token: Mapped[int] = mapped_column(Integer, default=0)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentStep(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_steps"
    __table_args__ = (UniqueConstraint("run_id", "step_number", name="uq_agent_step_number"),)

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    step_number: Mapped[int] = mapped_column(Integer)
    action: Mapped[dict[str, Any]] = mapped_column(JSON)
    observation_digest: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str] = mapped_column(String(96))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    prompt_version: Mapped[str] = mapped_column(String(32), default="unknown")
    policy_version: Mapped[str] = mapped_column(String(32), default="unknown")
    action_type: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    decision: Mapped[str | None] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float, default=0)
    reason_codes: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    stall_reason: Mapped[str | None] = mapped_column(String(64))


class ModelInvocation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "model_invocations"
    __table_args__ = (
        UniqueConstraint("step_id", "attempt_number", name="uq_model_invocation_attempt"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    step_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_steps.id", ondelete="CASCADE"), index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer)
    purpose: Mapped[str] = mapped_column(String(32))
    model_name: Mapped[str] = mapped_column(String(96))
    status: Mapped[str] = mapped_column(String(32))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    response_action: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class ToolInvocation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tool_invocations"
    __table_args__ = (UniqueConstraint("run_id", "step_id", name="uq_tool_invocation_step"),)

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    step_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_steps.id"))
    tool_name: Mapped[str] = mapped_column(String(96))
    tool_version: Mapped[str] = mapped_column(String(32))
    args_digest: Mapped[str] = mapped_column(String(64))
    observation_digest: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32))
    latency_ms: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    risk: Mapped[str] = mapped_column(String(32), default="READ")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    replayed: Mapped[bool] = mapped_column(Boolean, default=False)


class Checkpoint(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "checkpoints"
    __table_args__ = (UniqueConstraint("run_id", "step_number", name="uq_checkpoint_step"),)

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    step_number: Mapped[int] = mapped_column(Integer)
    state: Mapped[dict[str, Any]] = mapped_column(JSON)
    checkpoint_version: Mapped[int] = mapped_column(Integer, default=2)
    state_hash: Mapped[str] = mapped_column(String(64))
    resume_safe: Mapped[bool] = mapped_column(Boolean, default=True)
    fencing_token: Mapped[int] = mapped_column(Integer, default=0)


class ToolExecutionRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tool_execution_records"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "tool_name", "idempotency_key", name="uq_tool_execution_idempotency"
        ),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    tool_name: Mapped[str] = mapped_column(String(96))
    tool_version: Mapped[str] = mapped_column(String(32))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    args_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    result_digest: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fencing_token: Mapped[int] = mapped_column(Integer)


class GuardrailEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "guardrail_events"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_steps.id", ondelete="SET NULL")
    )
    tool_name: Mapped[str | None] = mapped_column(String(96))
    policy_version: Mapped[str] = mapped_column(String(32))
    decision: Mapped[str] = mapped_column(String(32))
    reason_code: Mapped[str] = mapped_column(String(64))


class Proposal(UUIDPrimaryKeyMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "proposals"
    __table_args__ = (
        UniqueConstraint("run_id", "idempotency_key", name="uq_proposal_idempotency"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    proposal_type: Mapped[ProposalType] = mapped_column(Enum(ProposalType))
    status: Mapped[ProposalStatus] = mapped_column(
        Enum(ProposalStatus), default=ProposalStatus.PENDING
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    reason_codes: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    confidence: Mapped[float] = mapped_column(Float)
    evidence_refs: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    evidence_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    review_reason: Mapped[str | None] = mapped_column(Text)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    apply_error_code: Mapped[str | None] = mapped_column(String(64))


class ShadowEvaluation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "shadow_evaluations"

    source_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("background_jobs.id", ondelete="SET NULL"), unique=True
    )
    status: Mapped[str] = mapped_column(String(32), default="QUEUED")
    baseline_model: Mapped[str] = mapped_column(String(96))
    baseline_prompt_version: Mapped[str] = mapped_column(String(32))
    baseline_decision: Mapped[str | None] = mapped_column(String(64))
    baseline_confidence: Mapped[float | None] = mapped_column(Float)
    candidate_model: Mapped[str] = mapped_column(String(96))
    candidate_prompt_version: Mapped[str] = mapped_column(String(32))
    candidate_decision: Mapped[str | None] = mapped_column(String(64))
    candidate_confidence: Mapped[float | None] = mapped_column(Float)
    comparison: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DomainEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "domain_events"
    __table_args__ = (
        UniqueConstraint(
            "aggregate_type", "aggregate_id", "aggregate_version", name="uq_event_version"
        ),
        Index("ix_domain_events_outbox", "published_at", "occurred_at"),
    )

    aggregate_type: Mapped[str] = mapped_column(String(64))
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    aggregate_version: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(96))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    strategy_versions: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IdempotencyRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("user_id", "route", "idempotency_key", name="uq_idempotency_scope"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    route: Mapped[str] = mapped_column(String(128))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
