from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.evaluation.schemas import EvaluationCase, EvaluationCaseResult


def write_bad_cases(
    cases: list[EvaluationCase],
    results: list[EvaluationCaseResult],
    output_path: Path,
) -> list[dict[str, Any]]:
    case_map = {case.id: case for case in cases}
    bad_cases = [
        _bad_case(case_map[result.case_id], result)
        for result in results
        if not result.task_success
        or result.decision_correct is False
        or result.tool_selection_correct is False
        or result.high_risk_mutation_executed
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in bad_cases),
        encoding="utf-8",
    )
    return bad_cases


def write_bad_case_markdown(bad_cases: list[dict[str, Any]], path: Path) -> None:
    counts: dict[str, int] = {}
    for item in bad_cases:
        label = str(item["primary_failure"])
        counts[label] = counts.get(label, 0) + 1
    lines = [
        "# Agent Evaluation Bad Case 报告",
        "",
        f"- Bad Case 数量：{len(bad_cases)}",
        f"- 生成时间：{datetime.now(UTC).isoformat()}",
        "- 原始内容：同目录 JSONL；本报告不包含用户 PII。",
        "",
        "## 失败分布",
        "",
    ]
    lines.extend(f"- `{label}`：{count}" for label, count in sorted(counts.items()))
    lines.extend(["", "## 优先处理样例", ""])
    for item in bad_cases[:30]:
        result = item["result"]
        lines.extend(
            [
                f"### {item['case']['id']} · {item['primary_failure']}",
                "",
                f"- 分类：{item['case']['category']} / {item['case']['scenario']}",
                f"- 期望决策：{result['expected_decision']}",
                f"- 实际决策：{result['actual_decision']}",
                f"- 期望 Tool：{result['expected_tool_sequences']}",
                f"- 实际 Tool：{result['actual_tools']}",
                f"- 终止原因：{result['actual_termination_reason']}",
                f"- 优化建议：{item['suggested_optimization']}",
                f"- 重放命令：`{item['replay_command']}`",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def promote_bad_cases(bad_case_path: Path, regression_path: Path) -> dict[str, int]:
    incoming = [
        json.loads(line)
        for line in bad_case_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    existing = (
        [
            json.loads(line)
            for line in regression_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if regression_path.exists()
        else []
    )
    fingerprints = {item["fingerprint"] for item in existing}
    promoted = 0
    for item in incoming:
        if item["fingerprint"] in fingerprints:
            continue
        item["promoted_at"] = datetime.now(UTC).isoformat()
        item["promotion_status"] = "REGRESSION"
        existing.append(item)
        fingerprints.add(item["fingerprint"])
        promoted += 1
    regression_path.parent.mkdir(parents=True, exist_ok=True)
    regression_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in existing),
        encoding="utf-8",
    )
    return {"incoming": len(incoming), "promoted": promoted, "total": len(existing)}


def load_regression_cases(regression_path: Path) -> list[EvaluationCase]:
    records = [
        json.loads(line)
        for line in regression_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    loaded = [
        EvaluationCase.model_validate_json(json.dumps(item["case"], ensure_ascii=False))
        for item in records
    ]
    cases: dict[str, EvaluationCase] = {}
    for case in loaded:
        cases.setdefault(case.id, case)
    return list(cases.values())


def _bad_case(case: EvaluationCase, result: EvaluationCaseResult) -> dict[str, Any]:
    primary = result.failure_labels[0] if result.failure_labels else "UNKNOWN_FAILURE"
    fingerprint_source = json.dumps(
        {
            "category": case.category,
            "scenario": case.scenario.value,
            "knowledge_code": case.knowledge_code,
            "task_type": case.task_type,
            "expected_decision": case.expected_decision,
            "actual_decision": result.actual_decision,
            "actual_tools": result.actual_tools,
            "termination": result.actual_termination_reason,
            "failures": result.failure_labels,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    fingerprint = hashlib.sha256(fingerprint_source.encode()).hexdigest()
    return {
        "schema_version": "bad_case_v1",
        "fingerprint": fingerprint,
        "primary_failure": primary,
        "failure_labels": result.failure_labels,
        "triage_owner": _triage_owner(primary),
        "suggested_optimization": _suggestion(primary),
        "case": case.model_dump(mode="json"),
        "result": result.model_dump(mode="json"),
        "replay_command": (
            "python scripts/run_evaluation.py --case-id "
            f"{case.id} --gateway fake --output-root D:\\CodexTemp\\千人千案评测业务数据"
        ),
        "created_at": datetime.now(UTC).isoformat(),
    }


def _triage_owner(label: str) -> str:
    if label in {"DECISION_MISMATCH", "INSUFFICIENT_EVIDENCE_HANDLING"}:
        return "PROMPT_ROUTER_EVIDENCE"
    if label in {"TOOL_SELECTION_MISMATCH", "TOOL_VALIDATION_FAILURE"}:
        return "TOOL_SCHEMA"
    if label in {"GUARDRAIL_MISS", "HIGH_RISK_MUTATION_EXECUTED"}:
        return "POLICY_GUARD"
    if label in {"LOOP_TERMINATION_MISMATCH", "TERMINATION_MISMATCH"}:
        return "LOOP_RUNTIME"
    return "HARNESS_RUNTIME"


def _suggestion(label: str) -> str:
    suggestions = {
        "DECISION_MISMATCH": "检查 Reason Code 到决策的约束、Prompt 示例与模型路由。",
        "INSUFFICIENT_EVIDENCE_HANDLING": (
            "提高 Evidence 最小数量、归属与版本校验，并强制 UNCERTAIN。"
        ),
        "TOOL_SELECTION_MISMATCH": "收紧 Tool 描述、参数 Schema 和按场景暴露的 Tool 集合。",
        "TOOL_VALIDATION_FAILURE": "补充 Pydantic 参数约束与模型输出 Repair 样例。",
        "GUARDRAIL_MISS": "增加风险元数据、权限绑定和 Guardrail 回归 Case。",
        "HIGH_RISK_MUTATION_EXECUTED": "立即阻断直接写 Tool，并检查服务端权限与 Ledger。",
        "LOOP_TERMINATION_MISMATCH": "调整重复动作、无新证据和 A/B 振荡窗口。",
        "TERMINATION_MISMATCH": "核对错误分类、Retry Matrix 与终止原因映射。",
        "ANOMALY_ROUTING_MISMATCH": "修正确定性异常阈值，避免不必要的模型调用。",
    }
    return suggestions.get(label, "通过只读 Replay 复现并补充最小回归 Case。")
