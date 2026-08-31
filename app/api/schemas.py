from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class SmsCodeRequest(BaseModel):
    phone: str = Field(min_length=8, max_length=32)
    purpose: str = Field(default="LOGIN", pattern="^LOGIN$")


class SmsLoginRequest(BaseModel):
    phone: str = Field(min_length=8, max_length=32)
    code: str = Field(pattern=r"^\d{6}$")


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=256)


class LogoutRequest(RefreshRequest):
    pass


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int


class DeletionResponse(BaseModel):
    status: str
    purge_after: datetime


class MessageResponse(BaseModel):
    status: str


class StudentProfileUpsert(BaseModel):
    target_school_id: uuid.UUID
    exam_date: date
    expected_version: int | None = Field(default=None, ge=1)


class StudentProfileResponse(BaseModel):
    id: uuid.UUID
    target_school_id: uuid.UUID | None
    exam_subject: str
    exam_date: date | None
    current_stage: str
    version: int


class AvailabilityItem(BaseModel):
    date: date
    available_minutes: int = Field(ge=0, le=1440)


class AvailabilityReplace(BaseModel):
    days: list[AvailabilityItem] = Field(min_length=1, max_length=31)


class AvailabilityTemplateItem(BaseModel):
    weekday: int = Field(ge=0, le=6)
    available_minutes: int = Field(ge=0, le=1440)


class AvailabilityTemplateReplace(BaseModel):
    days: list[AvailabilityTemplateItem] = Field(min_length=7, max_length=7)


class SchoolResponse(BaseModel):
    id: uuid.UUID
    code: str
    school_name: str
    major: str
    subject_code: str
    subject_name: str
    syllabus_version: str


class KnowledgeNodeResponse(BaseModel):
    id: uuid.UUID
    code: str
    parent_id: uuid.UUID | None
    level: int
    name: str
    description: str
    tree_version: str


class JobResponse(BaseModel):
    id: uuid.UUID
    job_type: str
    status: str
    result: dict[str, object] | None
    error_code: str | None
    created_at: datetime
    finished_at: datetime | None
    attempt_count: int
    max_attempts: int
    next_retry_at: datetime | None
    dead_lettered_at: datetime | None


class PlanCreateRequest(BaseModel):
    start_date: date


class PlanTaskResponse(BaseModel):
    id: uuid.UUID
    task_date: date
    task_type: str
    target_count: int
    estimated_min_minutes: int
    estimated_max_minutes: int
    priority: float
    status: str
    reason: str
    sequence: int
    title: str
    description: str
    knowledge_id: uuid.UUID | None = None
    resource_section_id: uuid.UUID | None
    resource_title: str | None = None
    resource_section_title: str | None = None
    suggested_scope: str | None
    planned_units: int | None
    unit_type: str | None
    system_suggested_minutes: int
    student_estimated_minutes: int | None
    effective_minutes: int
    origin: str
    is_personal: bool
    has_capacity_warning: bool
    version: int


class PlanResponse(BaseModel):
    id: uuid.UUID
    start_date: date
    end_date: date
    revision: int
    status: str
    planner_version: str
    tasks: list[PlanTaskResponse]
    timezone: str
    version: int


class PlanTaskPatchItem(BaseModel):
    operation: str = Field(pattern="^(CREATE|UPDATE|DELETE)$")
    task_id: uuid.UUID | None = None
    expected_version: int | None = Field(default=None, ge=1)
    task_date: date | None = None
    task_type: str | None = None
    title: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=4000)
    knowledge_id: uuid.UUID | None = None
    resource_section_id: uuid.UUID | None = None
    suggested_scope: str | None = Field(default=None, max_length=512)
    planned_units: int | None = Field(default=None, ge=1, le=10000)
    unit_type: str | None = Field(default=None, max_length=32)
    student_estimated_minutes: int | None = Field(default=None, ge=1, le=1440)
    sequence: int | None = Field(default=None, ge=1)
    reason: str | None = Field(default=None, max_length=2000)


class PlanTasksPatchRequest(BaseModel):
    expected_plan_version: int = Field(ge=1)
    allow_over_budget: bool = False
    changes: list[PlanTaskPatchItem] = Field(min_length=1, max_length=200)


class PlanChangeResponse(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID | None
    change_sequence: int
    actor: str
    operation: str
    before: dict[str, object] | None
    after: dict[str, object] | None
    reason: str
    occurred_at: datetime


class TodayTaskResponse(PlanTaskResponse):
    is_overdue: bool
    feedback_version: int | None = None


class TaskFeedbackUpsertRequest(BaseModel):
    expected_version: int | None = Field(default=None, ge=1)
    completion_ratio: float = Field(ge=0, le=1)
    actual_duration_seconds: int = Field(gt=0, le=86400)
    perceived_difficulty: int | None = Field(default=None, ge=1, le=5)
    free_text: str | None = Field(default=None, max_length=4000)
    progress_marker: str | None = Field(default=None, max_length=512)
    mastery_self_score: int | None = Field(default=None, ge=1, le=5)
    completed_units: int | None = Field(default=None, ge=0, le=10000)
    correct_units: int | None = Field(default=None, ge=0, le=10000)
    looked_at_solution: bool | None = None
    summary_text: str | None = Field(default=None, max_length=10000)


class TaskFeedbackResponse(BaseModel):
    feedback_id: uuid.UUID
    feedback_version: int
    requires_agent: bool
    reason_codes: list[str]
    agent_job_id: uuid.UUID | None


class KnowledgeUnlockResponse(BaseModel):
    knowledge_id: uuid.UUID
    knowledge_code: str
    knowledge_name: str
    status: str
    learning_task_total: int
    learning_task_completed: int
    true_exam_total: int
    true_exam_completed: int
    true_exam_unlocked: bool
    specialized_unlocked: bool
    version: int


class WeakKnowledgePointResponse(BaseModel):
    knowledge_id: uuid.UUID
    knowledge_name: str
    attempts: int
    accuracy: float
    true_exam_total: int
    true_exam_completed: int


class SpecializedScopeResponse(BaseModel):
    chapter_id: uuid.UUID
    chapter_order: int
    chapter_code: str
    chapter_name: str
    strengthened: bool
    true_exam_total: int
    true_exam_completed: int
    specialized_unlocked: bool
    weak_points: list[WeakKnowledgePointResponse]


class StrengtheningConfirmRequest(BaseModel):
    expected_version: int = Field(ge=1)


class StrengtheningConfirmResponse(BaseModel):
    knowledge_id: uuid.UUID
    status: str
    true_exam_unlocked: bool


class FullMockUnlockResponse(BaseModel):
    status: str
    full_mock_unlocked: bool


class ChapterSessionResponse(BaseModel):
    id: uuid.UUID
    knowledge_id: uuid.UUID
    question_snapshot_version: str
    status: str
    total_questions: int
    completed_questions: int
    completed_at: datetime | None


class ChapterSessionQuestionResponse(BaseModel):
    id: uuid.UUID
    sequence: int
    code: str
    content: str
    question_type: str
    difficulty: str
    score: int
    completed_at: datetime | None


class ChapterSessionDetailResponse(ChapterSessionResponse):
    questions: list[ChapterSessionQuestionResponse]


class SchoolChangePreviewRequest(BaseModel):
    target_school_id: uuid.UUID


class SchoolChangePreviewResponse(BaseModel):
    id: uuid.UUID
    from_school_id: uuid.UUID | None
    to_school_id: uuid.UUID
    status: str
    preview: dict[str, object]
    expires_at: datetime


class SchoolChangeApplyRequest(BaseModel):
    preview_id: uuid.UUID


class ResourceImportResponse(BaseModel):
    id: uuid.UUID
    resource_version_id: uuid.UUID
    status: str
    progress: float
    error_code: str | None
    result: dict[str, object] | None
    created_at: datetime


class LearningResourceResponse(BaseModel):
    id: uuid.UUID
    title: str
    resource_type: str
    status: str
    description: str
    version: int
    published_at: datetime | None


class ResourceMappingResponse(BaseModel):
    knowledge_id: uuid.UUID
    knowledge_name: str
    confidence: float
    confirmed: bool


class ResourceSectionReviewResponse(BaseModel):
    id: uuid.UUID
    title: str
    section_path: str
    level: int
    sequence: int
    page_start: int | None
    page_end: int | None
    version: int
    mappings: list[ResourceMappingResponse]


class PublishedResourceSectionResponse(BaseModel):
    id: uuid.UUID
    title: str
    resource_id: uuid.UUID
    resource_title: str
    resource_type: str
    knowledge_id: uuid.UUID
    page_start: int | None
    page_end: int | None
    suggested_units: int | None
    unit_type: str | None


class ResourceSectionUpdateRequest(BaseModel):
    expected_version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=512)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    knowledge_ids: list[uuid.UUID] = Field(min_length=1, max_length=10)


class ResourcePublishRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


class PracticeQuestionResponse(BaseModel):
    id: uuid.UUID
    code: str
    content: str
    question_type: str
    difficulty: str
    score: int


class PracticeTaskResponse(BaseModel):
    id: uuid.UUID
    task_type: str
    estimated_min_minutes: int
    estimated_max_minutes: int
    status: str
    questions: list[PracticeQuestionResponse]


class AttemptCreateRequest(BaseModel):
    task_id: uuid.UUID | None = None
    actual_duration_seconds: int = Field(gt=0, le=86400)
    score_ratio: float = Field(ge=0, le=1)
    looked_at_solution: bool = False
    self_difficulty: int | None = Field(default=None, ge=1, le=5)
    error_note: str | None = Field(default=None, max_length=2000)


class AttemptResponse(BaseModel):
    id: uuid.UUID
    question_id: uuid.UUID
    plan_task_id: uuid.UUID | None
    score_ratio: float
    actual_duration_seconds: int
    created_at: datetime


class FeedbackCreateRequest(BaseModel):
    completion_ratio: float = Field(ge=0, le=1)
    actual_duration_seconds: int = Field(gt=0, le=86400)
    completed_count: int = Field(ge=0, le=1000)
    correct_count: int = Field(ge=0, le=1000)
    looked_at_solution: bool = False
    perceived_difficulty: int | None = Field(default=None, ge=1, le=5)
    free_text: str | None = Field(default=None, max_length=4000)


class FeedbackResponse(BaseModel):
    feedback_id: uuid.UUID
    requires_agent: bool
    reason_codes: list[str]
    agent_job_id: uuid.UUID | None


class AgentStepResponse(BaseModel):
    step_number: int
    action: dict[str, object]
    model_name: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


class ToolInvocationResponse(BaseModel):
    id: uuid.UUID
    tool_name: str
    tool_version: str
    status: str
    latency_ms: int
    created_at: datetime
    retry_count: int = 0
    error_code: str | None = None
    replayed: bool = False


class ProposalResponse(BaseModel):
    id: uuid.UUID
    proposal_type: str
    status: str
    payload: dict[str, object]
    reason_codes: list[str]
    confidence: float
    evidence_refs: list[str]
    evidence_snapshot: list[dict[str, object]]
    approval_expires_at: datetime | None = None
    reviewer_user_id: uuid.UUID | None = None
    review_reason: str | None = None
    applied_at: datetime | None = None
    apply_error_code: str | None = None


class AgentRunResponse(BaseModel):
    id: uuid.UUID
    goal: str
    status: str
    model_version: str
    prompt_version: str
    policy_version: str
    loop_count: int
    model_call_count: int
    tool_call_count: int
    input_tokens: int
    output_tokens: int
    resumed_count: int
    termination_reason: str | None
    steps: list[AgentStepResponse]
    tools: list[ToolInvocationResponse]
    proposals: list[ProposalResponse]


class ProposalDecisionResponse(BaseModel):
    proposal: ProposalResponse
    job_id: uuid.UUID | None


class ProposalDecisionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


class AgentCancelResponse(BaseModel):
    id: uuid.UUID
    status: str
    cancel_requested_at: datetime | None


class ShadowEvaluationResponse(BaseModel):
    id: uuid.UUID
    source_run_id: uuid.UUID
    job_id: uuid.UUID | None
    status: str
    baseline_model: str
    baseline_prompt_version: str
    baseline_decision: str | None
    baseline_confidence: float | None
    candidate_model: str
    candidate_prompt_version: str
    candidate_decision: str | None
    candidate_confidence: float | None
    comparison: dict[str, object] | None
    error_code: str | None


class TrueExamResponse(BaseModel):
    id: uuid.UUID
    year: int
    title: str
    total_score: int
    duration_minutes: int


class TrueExamQuestionResponse(BaseModel):
    id: uuid.UUID
    sequence: int
    code: str
    content: str
    question_type: str
    difficulty: str
    score: int


class TrueExamDetailResponse(TrueExamResponse):
    questions: list[TrueExamQuestionResponse]


class ExamQuestionResultRequest(BaseModel):
    question_id: uuid.UUID
    score_ratio: float = Field(ge=0, le=1)
    duration_seconds: int = Field(gt=0, le=21600)
    looked_at_solution: bool = False
    error_note: str | None = Field(default=None, max_length=2000)


class ChapterSessionSubmitRequest(BaseModel):
    results: list[ExamQuestionResultRequest] = Field(min_length=1, max_length=500)


class TrueExamSubmitRequest(BaseModel):
    results: list[ExamQuestionResultRequest] = Field(min_length=1, max_length=100)


class TrueExamAttemptResponse(BaseModel):
    id: uuid.UUID
    true_exam_id: uuid.UUID
    score: Decimal
    duration_seconds: int
    created_at: datetime


class TrueExamProfileResponse(BaseModel):
    knowledge_id: uuid.UUID
    attempt_count: int
    accuracy: float
    average_score_ratio: float
    average_duration_seconds: int


class MockExamCreateRequest(BaseModel):
    mock_type: str = Field(pattern="^(SPECIALIZED|FULL)$")
    target_knowledge_id: uuid.UUID | None = None


class MockQuestionResponse(BaseModel):
    id: uuid.UUID
    sequence: int
    score: int
    code: str
    content: str
    question_type: str
    difficulty: str


class MockExamResponse(BaseModel):
    id: uuid.UUID
    mock_type: str
    status: str
    total_score: int
    duration_minutes: int
    target_knowledge_id: uuid.UUID | None
    strategy_version: str
    validation_result: dict[str, object] | None
    questions: list[MockQuestionResponse]


class MockExamSubmitRequest(BaseModel):
    results: list[ExamQuestionResultRequest] = Field(min_length=1, max_length=100)


class MockExamAttemptResponse(BaseModel):
    id: uuid.UUID
    mock_exam_id: uuid.UUID
    score: Decimal
    duration_seconds: int
    created_at: datetime


class GeneratedQuestionResponse(BaseModel):
    id: uuid.UUID
    mock_exam_id: uuid.UUID | None
    content: str
    answer: str
    solution: str
    metadata_json: dict[str, object]
    generator_model: str
    prompt_version: str
    validation_result: dict[str, object]
    quality_status: str
    created_at: datetime


class ReviewDecisionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


class ReviewDecisionResponse(BaseModel):
    candidate: GeneratedQuestionResponse
    resume_job_id: uuid.UUID | None
