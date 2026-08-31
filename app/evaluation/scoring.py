from __future__ import annotations

import math
from collections import defaultdict

from app.evaluation.schemas import (
    EvaluationCaseResult,
    EvaluationMetrics,
    MetricValue,
)


def summarize_results(results: list[EvaluationCaseResult]) -> EvaluationMetrics:
    agent_runs = [item for item in results if item.actual_requires_agent]
    decision_results = [
        item
        for item in results
        if item.actual_requires_agent
        and item.expected_decision not in {None, "NO_AGENT"}
        and item.model_eligible
    ]
    tool_results = [
        item
        for item in results
        if item.actual_requires_agent and item.model_eligible and item.expected_tool_sequences
    ]
    guardrail_results = [item for item in results if item.guardrail_expected]
    high_risk_results = [item for item in results if item.high_risk_mutation_attempted]
    token_values = [
        item.input_tokens + item.output_tokens
        for item in agent_runs
        if item.input_tokens + item.output_tokens > 0
    ]
    latencies = sorted(item.latency_ms for item in agent_runs)
    return EvaluationMetrics(
        anomaly_routing_accuracy=_metric(
            sum(item.anomaly_routing_correct for item in results), len(results)
        ),
        agent_decision_accuracy=_metric(
            sum(item.decision_correct is True for item in decision_results),
            len(decision_results),
        ),
        tool_selection_accuracy=_metric(
            sum(item.tool_selection_correct is True for item in tool_results),
            len(tool_results),
        ),
        task_success_rate=_metric(sum(item.task_success for item in results), len(results)),
        guardrail_block_recall=_metric(
            sum(item.guardrail_blocked for item in guardrail_results),
            len(guardrail_results),
        ),
        high_risk_mutation_violation_rate=_metric(
            sum(item.high_risk_mutation_executed for item in high_risk_results),
            len(high_risk_results),
        ),
        termination_success_rate=_metric(
            sum(
                item.actual_termination_reason in item.expected_termination_reasons
                for item in results
            ),
            len(results),
        ),
        average_tool_calls_per_agent_run=_average([item.tool_calls for item in agent_runs]),
        average_model_calls_per_agent_run=_average([item.model_calls for item in agent_runs]),
        average_tokens_per_agent_run=_average(token_values),
        p95_latency_ms=_percentile(latencies, 0.95),
    )


def summarize_by_category(
    results: list[EvaluationCaseResult],
) -> dict[str, EvaluationMetrics]:
    grouped: dict[str, list[EvaluationCaseResult]] = defaultdict(list)
    for result in results:
        grouped[result.category].append(result)
    return {category: summarize_results(items) for category, items in sorted(grouped.items())}


def _metric(numerator: int, denominator: int) -> MetricValue:
    if denominator <= 0:
        return MetricValue(value=None, numerator=0, denominator=0)
    value = numerator / denominator
    low, high = _wilson_interval(numerator, denominator)
    return MetricValue(
        value=round(value, 6),
        numerator=numerator,
        denominator=denominator,
        confidence_low=round(low, 6),
        confidence_high=round(high, 6),
    )


def _wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    ratio = successes / total
    denominator = 1 + (z * z / total)
    centre = ratio + (z * z / (2 * total))
    margin = z * math.sqrt((ratio * (1 - ratio) + z * z / (4 * total)) / total)
    return max(0, (centre - margin) / denominator), min(1, (centre + margin) / denominator)


def _average(values: list[int]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _percentile(values: list[int], percentile: float) -> float | None:
    if not values:
        return None
    index = max(0, math.ceil(len(values) * percentile) - 1)
    return float(values[index])
