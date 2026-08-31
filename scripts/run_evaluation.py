from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
from collections import defaultdict
from pathlib import Path

import structlog
from pydantic import SecretStr

from app.config import Settings
from app.evaluation.bad_cases import load_regression_cases
from app.evaluation.dataset import DEFAULT_CASE_COUNT, generate_cases, load_dataset, write_dataset
from app.evaluation.paths import default_evaluation_root, ensure_evaluation_directories
from app.evaluation.runner import EvaluationRunner
from app.evaluation.schemas import EvaluationCase, EvaluationReport


def main() -> None:
    parser = argparse.ArgumentParser(description="运行 Agent Evaluation 并生成 Bad Case")
    parser.add_argument("--gateway", choices=["fake", "fake-legacy", "qwen"], default="fake")
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--output-root", type=Path, default=default_evaluation_root())
    parser.add_argument("--count", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--sample-per-category", type=int)
    parser.add_argument("--case-id")
    parser.add_argument("--regression-path", type=Path)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--confirm-real-model", action="store_true")
    parser.add_argument("--qwen-config-file", type=Path, default=Path("实施文档/env.txt"))
    parser.add_argument("--enforce-thresholds", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--thresholds",
        type=Path,
        default=Path("benchmarks/evaluation_thresholds.json"),
    )
    args = parser.parse_args()
    if not args.verbose:
        structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR))
    ensure_evaluation_directories(args.output_root)
    dataset_path = args.dataset or args.output_root / "数据集" / "学习诊断评测集_v2.jsonl"
    if not dataset_path.exists():
        write_dataset(generate_cases(args.count), dataset_path)
    cases, dataset_hash = load_dataset(dataset_path)
    if args.regression_path:
        cases = load_regression_cases(args.regression_path)
        dataset_path = args.regression_path
        dataset_hash = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    if args.case_id:
        cases = [case for case in cases if case.id == args.case_id]
        if not cases:
            raise SystemExit(f"case not found: {args.case_id}")
    if args.sample_per_category is not None:
        cases = _stratified_sample(cases, args.sample_per_category)
    if args.limit is not None:
        cases = cases[: args.limit]
    settings = Settings(use_fake_model=args.gateway != "qwen")
    if args.gateway == "qwen":
        if not args.confirm_real_model:
            raise SystemExit("real model evaluation requires --confirm-real-model")
        if not settings.qwen_api_key.get_secret_value() and args.qwen_config_file.exists():
            settings = _load_local_qwen_settings(settings, args.qwen_config_file)
        if not settings.qwen_api_key.get_secret_value():
            raise SystemExit("QWEN_API_KEY is not configured")
    report = asyncio.run(
        EvaluationRunner(args.gateway, settings, args.concurrency).run_to_output(
            cases, dataset_path, dataset_hash, args.output_root
        )
    )
    print(json.dumps(_summary(report), ensure_ascii=False))
    if args.enforce_thresholds:
        _enforce_thresholds(report, args.thresholds, require_full_size=args.regression_path is None)


def _summary(report: EvaluationReport) -> dict[str, object]:
    metrics = report.metrics
    return {
        "run_id": report.run_id,
        "gateway": report.gateway,
        "cases": report.case_count,
        "decision_accuracy": metrics.agent_decision_accuracy.value,
        "tool_selection_accuracy": metrics.tool_selection_accuracy.value,
        "task_success_rate": metrics.task_success_rate.value,
        "high_risk_mutation_violation_rate": metrics.high_risk_mutation_violation_rate.value,
        "bad_cases": report.bad_case_count,
        "result_path": report.result_path,
    }


def _stratified_sample(cases: list[EvaluationCase], per_category: int) -> list[EvaluationCase]:
    if per_category < 1:
        raise SystemExit("--sample-per-category must be positive")
    grouped: dict[str, list[EvaluationCase]] = defaultdict(list)
    for case in cases:
        grouped[case.category].append(case)
    return [case for category in sorted(grouped) for case in grouped[category][:per_category]]


def _load_local_qwen_settings(settings: Settings, path: Path) -> Settings:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    api_key = values.get("VISIONLAB_QWEN_API_KEY", "")
    model = values.get("VISIONLAB_QWEN_MODEL", settings.qwen_plus_model)
    base_url = values.get("VISIONLAB_QWEN_EMBEDDING_BASE_URL", settings.qwen_base_url)
    return settings.model_copy(
        update={
            "qwen_api_key": SecretStr(api_key),
            "qwen_plus_model": model,
            "qwen_flash_model": model,
            "qwen_base_url": base_url,
            "use_fake_model": False,
        }
    )


def _enforce_thresholds(
    report: EvaluationReport, threshold_path: Path, *, require_full_size: bool
) -> None:
    metrics = report.metrics
    configured = json.loads(threshold_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    minimum_cases = int(configured["minimum_full_case_count"])
    if require_full_size and report.case_count < minimum_cases:
        failures.append(f"case_count={report.case_count}<{minimum_cases}")
    for name, requirement in configured["metrics"].items():
        value = getattr(metrics, name).value
        threshold = float(requirement["value"])
        operator = str(requirement["operator"])
        if value is None:
            if "violation" not in name:
                failures.append(f"{name}=N/A")
        elif operator == "<=" and value > threshold:
            failures.append(f"{name}={value:.4f}>{threshold:.4f}")
        elif operator == ">=" and value < threshold:
            failures.append(f"{name}={value:.4f}<{threshold:.4f}")
    if failures:
        raise SystemExit("evaluation quality gate failed: " + ", ".join(failures))


if __name__ == "__main__":
    main()
