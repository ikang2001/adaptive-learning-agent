from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from app.evaluation.paths import default_evaluation_root, ensure_evaluation_directories
from app.evaluation.schemas import EvaluationReport


def main() -> None:
    parser = argparse.ArgumentParser(description="比较两次同口径 Agent Evaluation")
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output-root", type=Path, default=default_evaluation_root())
    args = parser.parse_args()
    baseline = EvaluationReport.model_validate_json(args.baseline.read_text(encoding="utf-8"))
    candidate = EvaluationReport.model_validate_json(args.candidate.read_text(encoding="utf-8"))
    same_fake_family = baseline.gateway.startswith("fake") and candidate.gateway.startswith("fake")
    comparable = (
        baseline.dataset_hash == candidate.dataset_hash
        and baseline.case_count == candidate.case_count
        and (
            (baseline.gateway == candidate.gateway and baseline.model_name == candidate.model_name)
            or same_fake_family
        )
    )
    comparison = {
        "schema_version": "evaluation_comparison_v1",
        "created_at": datetime.now(UTC).isoformat(),
        "comparable": comparable,
        "baseline_run": baseline.run_id,
        "candidate_run": candidate.run_id,
        "dataset_hash": candidate.dataset_hash,
        "metric_delta": _metric_deltas(baseline, candidate),
        "tool_call_reduction": _reduction(
            baseline.metrics.average_tool_calls_per_agent_run,
            candidate.metrics.average_tool_calls_per_agent_run,
        ),
        "model_call_reduction": _reduction(
            baseline.metrics.average_model_calls_per_agent_run,
            candidate.metrics.average_model_calls_per_agent_run,
        ),
        "token_reduction": _reduction(
            baseline.metrics.average_tokens_per_agent_run,
            candidate.metrics.average_tokens_per_agent_run,
        ),
        "warning": (
            "Fake 前后对比只证明确定性 Harness 行为变化，不代表真实模型收益。"
            if comparable and same_fake_family
            else None
            if comparable
            else "数据集、Gateway、模型或 Case 数不一致，不得把差值表述为优化收益。"
        ),
    }
    ensure_evaluation_directories(args.output_root)
    output = args.output_root / "对比报告" / f"{baseline.run_id}_对比_{candidate.run_id}.json"
    output.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(comparison, output.with_suffix(".md"))
    print(json.dumps({"path": str(output), **comparison}, ensure_ascii=False))


def _metric_deltas(
    baseline: EvaluationReport, candidate: EvaluationReport
) -> dict[str, float | None]:
    values: dict[str, float | None] = {}
    for name in (
        "anomaly_routing_accuracy",
        "agent_decision_accuracy",
        "tool_selection_accuracy",
        "task_success_rate",
        "guardrail_block_recall",
        "high_risk_mutation_violation_rate",
        "termination_success_rate",
    ):
        before = getattr(baseline.metrics, name).value
        after = getattr(candidate.metrics, name).value
        values[name] = None if before is None or after is None else round(after - before, 6)
    return values


def _reduction(before: float | None, after: float | None) -> float | None:
    if before is None or after is None or before <= 0:
        return None
    return round((before - after) / before, 6)


def _write_markdown(comparison: dict[str, object], path: Path) -> None:
    deltas = comparison["metric_delta"]
    assert isinstance(deltas, dict)
    lines = [
        "# Agent Evaluation 前后对比",
        "",
        f"- Baseline：`{comparison['baseline_run']}`",
        f"- Candidate：`{comparison['candidate_run']}`",
        f"- Dataset SHA256：`{comparison['dataset_hash']}`",
        f"- 可比：{comparison['comparable']}",
        "",
        "## 指标变化",
        "",
    ]
    lines.extend(
        f"- `{name}`：{value if value is not None else 'N/A'}" for name, value in deltas.items()
    )
    lines.extend(
        [
            f"- Tool Call 降幅：{comparison['tool_call_reduction']}",
            f"- Model Call 降幅：{comparison['model_call_reduction']}",
            f"- Token 降幅：{comparison['token_reduction']}",
            "",
            f"> {comparison['warning'] or '同口径报告，可用于工程收益判断。'}",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
