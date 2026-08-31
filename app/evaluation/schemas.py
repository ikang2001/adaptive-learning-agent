from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EvaluationScenario(StrEnum):
    STANDARD = "STANDARD"
    EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"
    PROMPT_INJECTION = "PROMPT_INJECTION"
    TRANSIENT_TOOL_TIMEOUT = "TRANSIENT_TOOL_TIMEOUT"
    FORBIDDEN_MUTATION = "FORBIDDEN_MUTATION"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    REPEATED_ACTION = "REPEATED_ACTION"
    ACTION_OSCILLATION = "ACTION_OSCILLATION"
    MALFORMED_MODEL_OUTPUT = "MALFORMED_MODEL_OUTPUT"


class EvaluationSignals(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    completion_ratio: float = Field(ge=0, le=1)
    actual_duration_seconds: int = Field(gt=0)
    expected_p75_seconds: int = Field(gt=0)
    recent_accuracy: float = Field(ge=0, le=1)
    recent_attempt_count: int = Field(ge=0)
    same_error_streak: int = Field(ge=0)
    consecutive_low_completion_days: int = Field(ge=0)


class EvaluationAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    question_code: str
    score_ratio: float = Field(ge=0, le=1)
    duration_seconds: int = Field(gt=0)
    looked_at_solution: bool
    error_type: str | None


class EvaluationStudentSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    stage: str
    mastery_score: float = Field(ge=0, le=1)
    mastery_confidence: float = Field(ge=0, le=1)
    mastery_evidence_count: int = Field(ge=0)
    available_minutes: int = Field(ge=0, le=1_440)
    planned_minutes: int = Field(ge=0, le=1_440)


class EvaluationSchoolSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    normalized_weight: float = Field(ge=0, le=1)
    exam_frequency: int = Field(ge=0)
    trend: str


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    dataset_version: str
    source: str = "SYNTHETIC_BUSINESS_REALISTIC"
    split: str
    category: str
    scenario: EvaluationScenario
    description: str
    school_code: str
    knowledge_code: str
    task_type: str
    signals: EvaluationSignals
    evidence_count: int = Field(ge=0, le=20)
    recent_attempts: list[EvaluationAttempt]
    student_snapshot: EvaluationStudentSnapshot
    school_snapshot: EvaluationSchoolSnapshot
    prompt_injection_text: str | None = None
    expected_requires_agent: bool
    expected_reason_codes: list[str]
    expected_decision: str | None
    expected_tool_sequences: list[list[str]]
    expected_termination_reasons: list[str]
    expected_guardrail_block: bool = False
    model_eligible: bool = True
    tags: list[str] = Field(default_factory=list)


class EvaluationCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    category: str
    scenario: EvaluationScenario
    split: str
    model_eligible: bool
    expected_requires_agent: bool
    actual_requires_agent: bool
    expected_reason_codes: list[str]
    actual_reason_codes: list[str]
    expected_decision: str | None
    actual_decision: str | None
    expected_tool_sequences: list[list[str]]
    actual_tools: list[str]
    expected_termination_reasons: list[str]
    actual_termination_reason: str
    decision_correct: bool | None
    tool_selection_correct: bool | None
    anomaly_routing_correct: bool
    task_success: bool
    guardrail_expected: bool
    guardrail_blocked: bool
    high_risk_mutation_attempted: bool
    high_risk_mutation_executed: bool
    model_calls: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    latency_ms: int
    failure_labels: list[str]
    trace_digest: str
    trace: dict[str, Any]


class MetricValue(BaseModel):
    value: float | None
    numerator: int
    denominator: int
    confidence_low: float | None = None
    confidence_high: float | None = None


class EvaluationMetrics(BaseModel):
    anomaly_routing_accuracy: MetricValue
    agent_decision_accuracy: MetricValue
    tool_selection_accuracy: MetricValue
    task_success_rate: MetricValue
    guardrail_block_recall: MetricValue
    high_risk_mutation_violation_rate: MetricValue
    termination_success_rate: MetricValue
    average_tool_calls_per_agent_run: float | None
    average_model_calls_per_agent_run: float | None
    average_tokens_per_agent_run: float | None
    p95_latency_ms: float | None


class EvaluationReport(BaseModel):
    schema_version: str = "evaluation_report_v1"
    run_id: str
    created_at: datetime
    dataset_version: str
    dataset_hash: str
    dataset_path: str
    gateway: str
    model_name: str
    prompt_version: str
    policy_version: str
    tool_schema_version: str
    case_count: int
    metrics: EvaluationMetrics
    category_metrics: dict[str, EvaluationMetrics]
    bad_case_count: int
    bad_case_path: str
    result_path: str
    caveats: list[str]
