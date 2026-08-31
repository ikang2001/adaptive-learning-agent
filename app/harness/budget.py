from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.harness.contracts import RuntimeState, TerminationReason
from app.harness.errors import BudgetExceededError


@dataclass(frozen=True, slots=True)
class RunBudget:
    max_steps: int = 8
    max_model_calls: int = 10
    max_tool_calls: int = 12
    max_input_tokens: int = 64_000
    max_output_tokens: int = 8_192
    max_total_tokens: int = 72_192
    max_runtime_seconds: int = 600
    max_repair_calls: int = 1


class BudgetManager:
    def __init__(self, budget: RunBudget | None = None) -> None:
        self.budget = budget or RunBudget()

    def check_before_model(self, state: RuntimeState) -> None:
        self._check_common(state)
        if state.loop_count >= self.budget.max_steps:
            raise BudgetExceededError(TerminationReason.STEP_BUDGET_EXCEEDED)
        if state.model_call_count >= self.budget.max_model_calls:
            raise BudgetExceededError(TerminationReason.MODEL_CALL_BUDGET_EXCEEDED)

    def check_before_tool(self, state: RuntimeState) -> None:
        self._check_common(state)
        if state.tool_call_count >= self.budget.max_tool_calls:
            raise BudgetExceededError(TerminationReason.TOOL_BUDGET_EXCEEDED)

    def check_after_model(self, state: RuntimeState) -> None:
        self._check_common(state)
        if state.model_call_count > self.budget.max_model_calls:
            raise BudgetExceededError(TerminationReason.MODEL_CALL_BUDGET_EXCEEDED)
        if state.repair_call_count > self.budget.max_repair_calls:
            raise BudgetExceededError(TerminationReason.REPAIR_BUDGET_EXCEEDED)

    def _check_common(self, state: RuntimeState) -> None:
        if (
            datetime.now(UTC) - state.started_at
        ).total_seconds() >= self.budget.max_runtime_seconds:
            raise BudgetExceededError(TerminationReason.TIME_BUDGET_EXCEEDED)
        if (
            state.input_tokens > self.budget.max_input_tokens
            or state.output_tokens > self.budget.max_output_tokens
            or state.total_tokens > self.budget.max_total_tokens
        ):
            raise BudgetExceededError(TerminationReason.TOKEN_BUDGET_EXCEEDED)
