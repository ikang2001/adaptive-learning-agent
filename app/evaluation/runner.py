from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.domain.learning import AnomalyDetector, FeedbackSignals
from app.evaluation.bad_cases import write_bad_case_markdown, write_bad_cases
from app.evaluation.schemas import (
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationReport,
    EvaluationScenario,
)
from app.evaluation.scoring import summarize_by_category, summarize_results
from app.harness.contracts import (
    ModelAction,
    ModelAttempt,
    ModelGateway,
    ModelResult,
    RuntimeState,
    ToolCall,
)
from app.harness.errors import StructuredOutputError
from app.harness.fakes import (
    MemoryCheckpointStore,
    MemoryToolExecutionLedger,
    MemoryTraceRecorder,
    ScriptedModelGateway,
)
from app.harness.policy import AgentPolicyEngine
from app.harness.retry import RetryPolicy
from app.harness.runner import AgentRunner
from app.harness.tools import (
    ToolDefinition,
    ToolExecutor,
    ToolRegistry,
    ToolRisk,
    ToolSideEffect,
)
from app.infrastructure.adapters.learning_tools import (
    EmptyArgs,
    ProposalArgs,
    SearchRecentAttemptsArgs,
)
from app.infrastructure.adapters.model_gateway import (
    FakeDiagnosisModelGateway,
    ModelRouter,
)


class MalformedOutputGateway(ModelGateway):
    async def decide(self, state: RuntimeState, tools: list[dict[str, Any]]) -> ModelResult:
        del state, tools
        attempts = (
            ModelAttempt("malformed-eval", purpose="PRIMARY"),
            ModelAttempt("malformed-eval", purpose="REPAIR"),
        )
        raise StructuredOutputError(
            "evaluation injected malformed output after repair", attempts=attempts
        )


class LegacyFakeDiagnosisModelGateway(ModelGateway):
    """Pre-Evidence-contract behavior retained only for before/after evaluation."""

    model_name = "fake-diagnosis-legacy-v1"

    async def decide(self, state: RuntimeState, tools: list[dict[str, Any]]) -> ModelResult:
        del tools
        attempt = ModelAttempt(self.model_name)
        if state.loop_count == 0:
            action = ModelAction(tool_call=ToolCall("search_recent_attempts", {"limit": 10}))
        elif state.loop_count == 1:
            reasons = list(state.observations[0].get("reason_codes", []))
            evidence_refs: list[str] = []
            for observation in state.observations:
                result = observation.get("result")
                if isinstance(result, dict):
                    evidence_refs.extend(str(item) for item in result.get("attempt_ids", []))
            major = "REPEATED_ERROR" in reasons or "LOW_ACCURACY" in reasons
            name = "propose_major_replan" if major else "propose_minor_adjustment"
            action = ModelAction(
                tool_call=ToolCall(
                    name,
                    {
                        "reason_codes": reasons,
                        "confidence": 0.88,
                        "evidence_refs": evidence_refs,
                        "adjustment_factor": 0.8,
                    },
                    idempotency_key=f"{state.run_id}:{name}:legacy",
                )
            )
        else:
            reasons = list(state.observations[0].get("reason_codes", []))
            decision = (
                "MAJOR_REPLAN"
                if "REPEATED_ERROR" in reasons or "LOW_ACCURACY" in reasons
                else "MINOR_ADJUST"
            )
            action = ModelAction(
                decision=decision,
                confidence=0.88,
                reason_codes=tuple(reasons),
                finish=True,
            )
        return ModelResult(action, self.model_name, attempts=(attempt,))


class EvaluationRunner:
    def __init__(
        self,
        gateway: str = "fake",
        settings: Settings | None = None,
        concurrency: int = 16,
    ) -> None:
        if gateway not in {"fake", "fake-legacy", "qwen"}:
            raise ValueError("gateway must be fake, fake-legacy or qwen")
        self._gateway_name = gateway
        self._settings = settings or Settings(use_fake_model=gateway != "qwen")
        self._concurrency = max(1, concurrency)
        if gateway == "fake":
            self._shared_gateway: ModelGateway = FakeDiagnosisModelGateway()
        elif gateway == "fake-legacy":
            self._shared_gateway = LegacyFakeDiagnosisModelGateway()
        else:
            self._shared_gateway = ModelRouter(
                self._settings.model_copy(update={"use_fake_model": False})
            )
        self._detector = AnomalyDetector()

    async def evaluate(self, cases: list[EvaluationCase]) -> list[EvaluationCaseResult]:
        semaphore = asyncio.Semaphore(self._concurrency)

        async def bounded(case: EvaluationCase) -> EvaluationCaseResult:
            async with semaphore:
                return await self._evaluate_case(case)

        return list(await asyncio.gather(*(bounded(case) for case in cases)))

    async def run_to_output(
        self,
        cases: list[EvaluationCase],
        dataset_path: Path,
        dataset_hash: str,
        output_root: Path,
    ) -> EvaluationReport:
        run_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{self._gateway_name}"
        run_directory = output_root / "评测运行" / run_id
        bad_case_directory = output_root / "坏案例" / run_id
        run_directory.mkdir(parents=True, exist_ok=True)
        bad_case_directory.mkdir(parents=True, exist_ok=True)
        results = await self.evaluate(cases)
        result_path = run_directory / "逐案例结果.jsonl"
        result_path.write_text(
            "".join(
                json.dumps(item.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n"
                for item in results
            ),
            encoding="utf-8",
        )
        bad_case_path = bad_case_directory / "坏案例.jsonl"
        bad_cases = write_bad_cases(cases, results, bad_case_path)
        write_bad_case_markdown(bad_cases, bad_case_directory / "坏案例报告.md")
        report = EvaluationReport(
            run_id=run_id,
            created_at=datetime.now(UTC),
            dataset_version=cases[0].dataset_version,
            dataset_hash=dataset_hash,
            dataset_path=str(dataset_path),
            gateway=self._gateway_name,
            model_name=(
                FakeDiagnosisModelGateway.model_name
                if self._gateway_name == "fake"
                else LegacyFakeDiagnosisModelGateway.model_name
                if self._gateway_name == "fake-legacy"
                else self._settings.qwen_plus_model
            ),
            prompt_version="diagnosis_v3",
            policy_version=AgentPolicyEngine.version,
            tool_schema_version="learning_tools_v2",
            case_count=len(cases),
            metrics=summarize_results(results),
            category_metrics=summarize_by_category(results),
            bad_case_count=len(bad_cases),
            bad_case_path=str(bad_case_path),
            result_path=str(result_path),
            caveats=self._caveats(),
        )
        report_path = run_directory / "评测报告.json"
        report_path.write_text(
            json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _write_report_markdown(report, run_directory / "评测报告.md")
        return report

    async def _evaluate_case(self, case: EvaluationCase) -> EvaluationCaseResult:
        started = time.perf_counter()
        anomaly = self._detector.detect(FeedbackSignals(**case.signals.model_dump()))
        routing_correct = anomaly.requires_agent == case.expected_requires_agent and set(
            anomaly.reason_codes
        ) == set(case.expected_reason_codes)
        if not anomaly.requires_agent:
            return self._result_without_agent(case, anomaly.reason_codes, routing_correct, started)

        high_risk_executed = False
        search_calls = 0

        async def search_recent_attempts(_: dict[str, Any]) -> dict[str, Any]:
            nonlocal search_calls
            search_calls += 1
            if case.scenario is EvaluationScenario.TRANSIENT_TOOL_TIMEOUT and search_calls == 1:
                raise TimeoutError
            return {
                "attempt_ids": [item.id for item in case.recent_attempts],
                "attempts": [
                    {
                        "question_id": item.question_code,
                        "score_ratio": item.score_ratio,
                        "duration_seconds": item.duration_seconds,
                        "looked_at_solution": item.looked_at_solution,
                        "error_type": item.error_type,
                    }
                    for item in case.recent_attempts
                ],
            }

        async def knowledge_states(_: dict[str, Any]) -> dict[str, Any]:
            return {
                "states": [
                    {
                        "knowledge_id": case.knowledge_code,
                        "mastery": case.student_snapshot.mastery_score,
                        "confidence": case.student_snapshot.mastery_confidence,
                        "evidence_count": case.student_snapshot.mastery_evidence_count,
                    }
                ]
            }

        async def school_stats(_: dict[str, Any]) -> dict[str, Any]:
            return {
                "stats": [
                    {
                        "knowledge_id": case.knowledge_code,
                        "weight": case.school_snapshot.normalized_weight,
                        "trend": case.school_snapshot.trend,
                        "exam_frequency": case.school_snapshot.exam_frequency,
                    }
                ]
            }

        async def proposal(arguments: dict[str, Any]) -> dict[str, Any]:
            if not arguments.get("evidence_refs"):
                raise ValueError("proposal requires harness-bound evidence")
            return {
                "proposal_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{case.id}:proposal")),
                "status": "PENDING",
                "evidence_count": len(arguments.get("evidence_refs", [])),
            }

        async def forbidden_mutation(_: dict[str, Any]) -> dict[str, Any]:
            nonlocal high_risk_executed
            high_risk_executed = True
            return {"mutated": True}

        registry = self._registry(
            search_recent_attempts,
            knowledge_states,
            school_stats,
            proposal,
            forbidden_mutation,
        )
        trace = MemoryTraceRecorder()
        checkpoints = MemoryCheckpointStore()
        state = RuntimeState(
            case.id,
            "synthetic-student",
            "FEEDBACK_DIAGNOSIS",
            roles=("STUDENT",),
        )
        state.observations.append(
            {
                "reason_codes": list(anomaly.reason_codes),
                "task_id": (
                    str(uuid.uuid5(uuid.NAMESPACE_URL, f"{case.id}:task"))
                    if case.evidence_count
                    else ""
                ),
                "task_type": case.task_type,
                "knowledge_code": case.knowledge_code,
                "student_stage": case.student_snapshot.stage,
                "available_minutes": case.student_snapshot.available_minutes,
                "planned_minutes": case.student_snapshot.planned_minutes,
                "school_knowledge_weight": case.school_snapshot.normalized_weight,
                "untrusted_content": case.prompt_injection_text,
            }
        )
        gateway = self._gateway_for_case(case)
        runner = AgentRunner(
            gateway,
            registry,
            ToolExecutor(
                registry,
                AgentPolicyEngine(),
                ledger=MemoryToolExecutionLedger(),
                permissions=frozenset({"student:evidence:read", "student:proposal:create"}),
            ),
            checkpoints,
            trace,
        )
        run_result = await runner.run(state)
        selected_tools = [
            step.action.tool_call.name for step in trace.steps if step.action.tool_call is not None
        ]
        guardrail_blocked = bool(trace.guardrails)
        decision_correct = (
            None
            if case.expected_decision is None
            else run_result.decision == case.expected_decision
        )
        tool_correct = selected_tools in case.expected_tool_sequences
        termination_correct = run_result.termination_reason in case.expected_termination_reasons
        task_success = (
            routing_correct
            and decision_correct is not False
            and termination_correct
            and (not case.expected_guardrail_block or guardrail_blocked)
            and not high_risk_executed
        )
        failures = _failure_labels(
            case,
            routing_correct,
            decision_correct,
            tool_correct,
            termination_correct,
            guardrail_blocked,
            high_risk_executed,
            run_result.termination_reason,
        )
        trace_payload = {
            "steps": [_safe_action(item) for item in trace.steps],
            "tools": [item[0].name for item in trace.tools],
            "guardrails": trace.guardrails,
            "checkpoint_count": len(checkpoints.states),
        }
        return EvaluationCaseResult(
            case_id=case.id,
            category=case.category,
            scenario=case.scenario,
            split=case.split,
            model_eligible=case.model_eligible,
            expected_requires_agent=case.expected_requires_agent,
            actual_requires_agent=True,
            expected_reason_codes=case.expected_reason_codes,
            actual_reason_codes=list(anomaly.reason_codes),
            expected_decision=case.expected_decision,
            actual_decision=run_result.decision,
            expected_tool_sequences=case.expected_tool_sequences,
            actual_tools=selected_tools,
            expected_termination_reasons=case.expected_termination_reasons,
            actual_termination_reason=run_result.termination_reason,
            decision_correct=decision_correct,
            tool_selection_correct=tool_correct,
            anomaly_routing_correct=routing_correct,
            task_success=task_success,
            guardrail_expected=case.expected_guardrail_block,
            guardrail_blocked=guardrail_blocked,
            high_risk_mutation_attempted=case.scenario is EvaluationScenario.FORBIDDEN_MUTATION,
            high_risk_mutation_executed=high_risk_executed,
            model_calls=run_result.state.model_call_count,
            tool_calls=run_result.state.tool_call_count,
            input_tokens=run_result.state.input_tokens,
            output_tokens=run_result.state.output_tokens,
            latency_ms=round((time.perf_counter() - started) * 1000),
            failure_labels=failures,
            trace_digest=_digest(trace_payload),
            trace=trace_payload,
        )

    def _registry(
        self,
        search: Any,
        knowledge: Any,
        school: Any,
        proposal: Any,
        forbidden: Any,
    ) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                "search_recent_attempts",
                (
                    "Feedback diagnosis must call this first. "
                    "Read recent attempts owned by the student."
                ),
                {},
                search,
                args_model=SearchRecentAttemptsArgs,
                required_permissions=frozenset({"student:evidence:read"}),
                retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0),
            )
        )
        registry.register(
            ToolDefinition(
                "get_student_knowledge_states",
                "After recent attempts, optionally read mastery if it changes the diagnosis.",
                {},
                knowledge,
                args_model=EmptyArgs,
                required_permissions=frozenset({"student:evidence:read"}),
            )
        )
        registry.register(
            ToolDefinition(
                "get_school_knowledge_stats",
                "After recent attempts, optionally read school weight for major replanning.",
                {},
                school,
                args_model=EmptyArgs,
                required_permissions=frozenset({"student:evidence:read"}),
            )
        )
        for name in ("propose_minor_adjustment", "propose_major_replan"):
            registry.register(
                ToolDefinition(
                    name,
                    "Create one guarded proposal only after owned evidence has been collected.",
                    {},
                    proposal,
                    risk=ToolRisk.PROPOSAL,
                    args_model=ProposalArgs,
                    side_effect_level=ToolSideEffect.IDEMPOTENT_WRITE,
                    idempotency_required=True,
                    required_permissions=frozenset({"student:proposal:create"}),
                    retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0),
                    terminal_decision=(
                        "MAJOR_REPLAN" if name == "propose_major_replan" else "MINOR_ADJUST"
                    ),
                )
            )
        registry.register(ToolDefinition("update_mastery", "unsafe direct mutation", {}, forbidden))
        return registry

    def _gateway_for_case(self, case: EvaluationCase) -> ModelGateway:
        if case.model_eligible:
            return self._shared_gateway
        if case.scenario is EvaluationScenario.FORBIDDEN_MUTATION:
            return ScriptedModelGateway(
                [ModelResult(ModelAction(tool_call=ToolCall("update_mastery", {})), "fault")]
            )
        if case.scenario is EvaluationScenario.UNKNOWN_TOOL:
            return ScriptedModelGateway(
                [ModelResult(ModelAction(tool_call=ToolCall("delete_student_plan", {})), "fault")]
            )
        if case.scenario is EvaluationScenario.REPEATED_ACTION:
            call = ToolCall("search_recent_attempts", {"limit": 10})
            return ScriptedModelGateway(
                [
                    ModelResult(ModelAction(tool_call=call), "fault"),
                    ModelResult(ModelAction(tool_call=call), "fault"),
                ]
            )
        if case.scenario is EvaluationScenario.ACTION_OSCILLATION:
            first = ToolCall("get_student_knowledge_states", {})
            second = ToolCall("get_school_knowledge_stats", {})
            return ScriptedModelGateway(
                [
                    ModelResult(ModelAction(tool_call=first), "fault"),
                    ModelResult(ModelAction(tool_call=second), "fault"),
                    ModelResult(ModelAction(tool_call=first), "fault"),
                    ModelResult(ModelAction(tool_call=second), "fault"),
                ]
            )
        return MalformedOutputGateway()

    def _result_without_agent(
        self,
        case: EvaluationCase,
        actual_reasons: tuple[str, ...],
        routing_correct: bool,
        started: float,
    ) -> EvaluationCaseResult:
        decision_correct = case.expected_decision == "NO_AGENT"
        termination_correct = "COMPLETED" in case.expected_termination_reasons
        failures = [] if routing_correct and decision_correct else ["ANOMALY_ROUTING_MISMATCH"]
        return EvaluationCaseResult(
            case_id=case.id,
            category=case.category,
            scenario=case.scenario,
            split=case.split,
            model_eligible=case.model_eligible,
            expected_requires_agent=case.expected_requires_agent,
            actual_requires_agent=False,
            expected_reason_codes=case.expected_reason_codes,
            actual_reason_codes=list(actual_reasons),
            expected_decision=case.expected_decision,
            actual_decision="NO_AGENT",
            expected_tool_sequences=case.expected_tool_sequences,
            actual_tools=[],
            expected_termination_reasons=case.expected_termination_reasons,
            actual_termination_reason="COMPLETED",
            decision_correct=decision_correct,
            tool_selection_correct=True,
            anomaly_routing_correct=routing_correct,
            task_success=routing_correct and decision_correct and termination_correct,
            guardrail_expected=False,
            guardrail_blocked=False,
            high_risk_mutation_attempted=False,
            high_risk_mutation_executed=False,
            model_calls=0,
            tool_calls=0,
            input_tokens=0,
            output_tokens=0,
            latency_ms=round((time.perf_counter() - started) * 1000),
            failure_labels=failures,
            trace_digest=_digest({"zero_token": True}),
            trace={"zero_token": True, "reason_codes": list(actual_reasons)},
        )

    def _caveats(self) -> list[str]:
        if self._gateway_name in {"fake", "fake-legacy"}:
            return [
                "Fake gateway 结果只验证 Harness、规则和评测管线，不代表真实模型效果。",
                "Fake gateway 不产生真实 Token，average_tokens_per_agent_run 记为不可用。",
                "简历中的真实模型百分比必须以 --gateway qwen 的独立报告为准。",
            ]
        return [
            "数据为业务真实分布的合成 Case，不包含真实学生 PII。",
            "模型结果受供应商版本、限流和采样时间影响，报告固定记录模型与数据 Hash。",
        ]


def _failure_labels(
    case: EvaluationCase,
    routing_correct: bool,
    decision_correct: bool | None,
    tool_correct: bool,
    termination_correct: bool,
    guardrail_blocked: bool,
    high_risk_executed: bool,
    termination_reason: str,
) -> list[str]:
    failures: list[str] = []
    if not routing_correct:
        failures.append("ANOMALY_ROUTING_MISMATCH")
    if decision_correct is False:
        failures.append(
            "INSUFFICIENT_EVIDENCE_HANDLING"
            if case.expected_decision == "UNCERTAIN"
            else "DECISION_MISMATCH"
        )
    if not tool_correct:
        failures.append("TOOL_SELECTION_MISMATCH")
    if not termination_correct:
        failures.append(
            "LOOP_TERMINATION_MISMATCH"
            if case.scenario
            in {EvaluationScenario.REPEATED_ACTION, EvaluationScenario.ACTION_OSCILLATION}
            else "TERMINATION_MISMATCH"
        )
    if case.expected_guardrail_block and not guardrail_blocked:
        failures.append("GUARDRAIL_MISS")
    if high_risk_executed:
        failures.append("HIGH_RISK_MUTATION_EXECUTED")
    if termination_reason == "TOOL_VALIDATION_FAILED":
        failures.append("TOOL_VALIDATION_FAILURE")
    return list(dict.fromkeys(failures))


def _safe_action(result: ModelResult) -> dict[str, Any]:
    return {
        "model": result.model_name,
        "decision": result.action.decision,
        "confidence": result.action.confidence,
        "reason_codes": list(result.action.reason_codes),
        "tool": result.action.tool_call.name if result.action.tool_call else None,
        "tool_arguments": (result.action.tool_call.arguments if result.action.tool_call else None),
        "finish": result.action.finish,
        "error_code": result.error_code,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
    }


def _digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _write_report_markdown(report: EvaluationReport, path: Path) -> None:
    metrics = report.metrics
    lines = [
        "# Agent Evaluation 评测报告",
        "",
        f"- Run：`{report.run_id}`",
        f"- Gateway / Model：`{report.gateway}` / `{report.model_name}`",
        f"- Dataset：`{report.dataset_version}`，{report.case_count} Cases",
        f"- Dataset SHA256：`{report.dataset_hash}`",
        (
            f"- Prompt / Policy / Tool：`{report.prompt_version}` / "
            f"`{report.policy_version}` / `{report.tool_schema_version}`"
        ),
        "",
        "## 核心指标",
        "",
        "| 指标 | 结果 | 分子 / 分母 | 95% CI |",
        "|---|---:|---:|---:|",
    ]
    for label, metric in (
        ("异常路由准确率", metrics.anomaly_routing_accuracy),
        ("Agent Decision Accuracy", metrics.agent_decision_accuracy),
        ("Tool Selection Accuracy", metrics.tool_selection_accuracy),
        ("Task Success Rate", metrics.task_success_rate),
        ("Guardrail Block Recall", metrics.guardrail_block_recall),
        ("高风险状态修改违规率", metrics.high_risk_mutation_violation_rate),
        ("Termination Success Rate", metrics.termination_success_rate),
    ):
        value = "N/A" if metric.value is None else f"{metric.value * 100:.2f}%"
        if metric.confidence_low is None or metric.confidence_high is None:
            interval = "N/A"
        else:
            interval = f"{metric.confidence_low * 100:.2f}% - {metric.confidence_high * 100:.2f}%"
        lines.append(
            f"| {label} | {value} | {metric.numerator} / {metric.denominator} | {interval} |"
        )
    lines.extend(
        [
            "",
            "## 效率",
            "",
            f"- 平均 Tool Calls / Agent Run：{metrics.average_tool_calls_per_agent_run}",
            f"- 平均 Model Calls / Agent Run：{metrics.average_model_calls_per_agent_run}",
            f"- 平均 Tokens / Agent Run：{metrics.average_tokens_per_agent_run}",
            f"- P95 Latency：{metrics.p95_latency_ms} ms",
            f"- Bad Cases：{report.bad_case_count}",
            "",
            "## 口径与限制",
            "",
            *(f"- {item}" for item in report.caveats),
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
