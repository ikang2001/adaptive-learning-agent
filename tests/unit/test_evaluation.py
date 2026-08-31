from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

from app.evaluation.bad_cases import (
    load_regression_cases,
    promote_bad_cases,
    write_bad_cases,
)
from app.evaluation.dataset import DATASET_VERSION, generate_cases, serialize_dataset
from app.evaluation.runner import EvaluationRunner
from app.evaluation.scoring import summarize_results


def test_large_dataset_is_deterministic_and_covers_required_business_scenarios() -> None:
    first = generate_cases(1_000)
    second = generate_cases(1_000)

    assert [item.id for item in first] == [item.id for item in second]
    assert all(item.dataset_version == DATASET_VERSION for item in first)
    assert all(item.source == "SYNTHETIC_BUSINESS_REALISTIC" for item in first)
    categories = Counter(item.category for item in first)
    assert categories["normal_feedback"] == 120
    assert categories["multi_anomaly"] == 140
    assert categories["evidence_insufficient"] == 100
    assert categories["forbidden_mutation"] == 40
    assert categories["malformed_output"] == 30
    assert (
        hashlib.sha256(serialize_dataset(first)).hexdigest()
        == "31d5b9df007860a80cb5f4799892e81227f5741af123b50ec19b7e7360a9ceb3"
    )


async def test_bad_case_evidence_fix_improves_same_regression_cases() -> None:
    cases = [item for item in generate_cases(1_000) if item.category == "evidence_insufficient"][
        :12
    ]

    legacy = await EvaluationRunner("fake-legacy").evaluate(cases)
    current = await EvaluationRunner("fake").evaluate(cases)

    assert summarize_results(legacy).task_success_rate.value == 0
    assert summarize_results(current).task_success_rate.value == 1
    assert all(item.actual_decision == "UNCERTAIN" for item in current)


async def test_guardrail_loop_and_structured_output_faults_fail_closed() -> None:
    wanted = {"forbidden_mutation", "unknown_tool", "loop_stall", "malformed_output"}
    cases = [item for item in generate_cases(1_000) if item.category in wanted][:40]

    results = await EvaluationRunner("fake").evaluate(cases)
    metrics = summarize_results(results)

    assert metrics.task_success_rate.value == 1
    assert metrics.high_risk_mutation_violation_rate.value == 0
    assert all(not item.high_risk_mutation_executed for item in results)
    assert all(item.actual_termination_reason != "INTERNAL_ERROR" for item in results)


async def test_bad_case_can_be_promoted_and_replayed(tmp_path: Path) -> None:
    case = next(item for item in generate_cases(1_000) if item.category == "evidence_insufficient")
    legacy_result = (await EvaluationRunner("fake-legacy").evaluate([case]))[0]
    bad_case_path = tmp_path / "坏案例.jsonl"
    regression_path = tmp_path / "回归案例集.jsonl"

    bad_cases = write_bad_cases([case], [legacy_result], bad_case_path)
    promotion = promote_bad_cases(bad_case_path, regression_path)
    replay_cases = load_regression_cases(regression_path)
    current_result = (await EvaluationRunner("fake").evaluate(replay_cases))[0]

    assert len(bad_cases) == 1
    assert promotion["promoted"] == 1
    assert replay_cases[0].id == case.id
    assert current_result.task_success is True
