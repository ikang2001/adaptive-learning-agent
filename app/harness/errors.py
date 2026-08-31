from __future__ import annotations

from typing import Any

from app.harness.contracts import ModelAttempt, TerminationReason


class AgentHarnessError(Exception):
    code = "AGENT_HARNESS_ERROR"
    termination_reason = TerminationReason.INTERNAL_ERROR


class ToolError(AgentHarnessError):
    code = "TOOL_ERROR"

    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.detail = detail or {}


class ToolValidationError(ToolError):
    code = "TOOL_VALIDATION_FAILED"
    termination_reason = TerminationReason.TOOL_VALIDATION_FAILED


class UnknownToolError(ToolValidationError):
    code = "UNKNOWN_TOOL"
    termination_reason = TerminationReason.UNKNOWN_TOOL


class ToolPermissionError(ToolError):
    code = "TOOL_PERMISSION_DENIED"
    termination_reason = TerminationReason.TOOL_PERMISSION_DENIED


class ToolBusinessError(ToolError):
    code = "TOOL_BUSINESS_ERROR"
    termination_reason = TerminationReason.TOOL_FAILED


class ToolTransientError(ToolError):
    code = "TOOL_TRANSIENT_ERROR"
    termination_reason = TerminationReason.TOOL_FAILED


class ToolRateLimitError(ToolTransientError):
    code = "TOOL_RATE_LIMITED"


class ToolTimeoutError(ToolTransientError):
    code = "TOOL_TIMEOUT"


class ToolUpstreamError(ToolTransientError):
    code = "TOOL_UPSTREAM_ERROR"


class ToolOutcomeUnknownError(ToolError):
    code = "TOOL_OUTCOME_UNKNOWN"
    termination_reason = TerminationReason.TOOL_OUTCOME_UNKNOWN


class StructuredOutputError(AgentHarnessError):
    code = "STRUCTURED_OUTPUT_ERROR"
    termination_reason = TerminationReason.STRUCTURED_OUTPUT_ERROR

    def __init__(
        self,
        message: str,
        *,
        raw_message: dict[str, Any] | None = None,
        attempts: tuple[ModelAttempt, ...] = (),
    ) -> None:
        super().__init__(message)
        self.raw_message = raw_message
        self.attempts = attempts


class BudgetExceededError(AgentHarnessError):
    def __init__(self, reason: TerminationReason) -> None:
        super().__init__(reason.value)
        self.termination_reason = reason
        self.code = reason.value


class ModelUnavailableError(AgentHarnessError):
    code = "MODEL_UNAVAILABLE"
    termination_reason = TerminationReason.MODEL_UNAVAILABLE

    def __init__(self, message: str, *, attempts: tuple[ModelAttempt, ...] = ()) -> None:
        super().__init__(message)
        self.attempts = attempts


class RunCancelledError(AgentHarnessError):
    code = "RUN_CANCELLED"
    termination_reason = TerminationReason.CANCELLED


class StaleWorkerError(AgentHarnessError):
    code = "STALE_WORKER"
    termination_reason = TerminationReason.STALE_WORKER


class LeaseUnavailableError(AgentHarnessError):
    code = "LEASE_UNAVAILABLE"
    termination_reason = TerminationReason.STALE_WORKER


class CheckpointValidationError(AgentHarnessError):
    code = "CHECKPOINT_INVALID"
    termination_reason = TerminationReason.CHECKPOINT_INVALID


class AgentRunExecutionError(AgentHarnessError):
    code = "AGENT_RUN_FAILED"

    def __init__(self, termination_reason: TerminationReason, error_code: str | None) -> None:
        super().__init__(f"agent run failed: {termination_reason.value}")
        self.termination_reason = termination_reason
        self.error_code = error_code
