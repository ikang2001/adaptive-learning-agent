from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.harness.contracts import RuntimeState
from app.harness.fakes import (
    MemoryCheckpointStore,
    MemoryToolExecutionLedger,
    MemoryTraceRecorder,
)
from app.harness.runner import AgentRunner
from app.harness.tools import (
    PolicyGuard,
    ToolDefinition,
    ToolExecutor,
    ToolRegistry,
    ToolRisk,
    ToolSideEffect,
)
from app.infrastructure.adapters.model_gateway import FakeDiagnosisModelGateway


async def run_case(case: dict[str, Any]) -> tuple[bool, bool]:
    registry = ToolRegistry()

    async def attempts(_: dict[str, Any]) -> dict[str, Any]:
        return {"attempt_ids": [f"{case['id']}-attempt"], "attempts": []}

    async def proposal(_: dict[str, Any]) -> dict[str, Any]:
        return {"proposal_id": f"{case['id']}-proposal"}

    registry.register(
        ToolDefinition("search_recent_attempts", "read attempts", {"type": "object"}, attempts)
    )
    for name in ("propose_minor_adjustment", "propose_major_replan"):
        registry.register(
            ToolDefinition(
                name,
                "create proposal",
                {"type": "object"},
                proposal,
                risk=ToolRisk.PROPOSAL,
                side_effect_level=ToolSideEffect.IDEMPOTENT_WRITE,
                idempotency_required=True,
            )
        )
    runner = AgentRunner(
        FakeDiagnosisModelGateway(),
        registry,
        ToolExecutor(registry, PolicyGuard(), ledger=MemoryToolExecutionLedger()),
        MemoryCheckpointStore(),
        MemoryTraceRecorder(),
    )
    state = RuntimeState(case["id"], "student", "FEEDBACK_DIAGNOSIS")
    state.observations.append({"reason_codes": case["reasons"]})
    result = await runner.run(state)
    return result.decision == case["expected"], result.termination_reason == "COMPLETED"


async def run_all(cases: list[dict[str, Any]]) -> None:
    results = [await run_case(case) for case in cases]
    decision_accuracy = sum(decision for decision, _ in results) / len(results)
    termination_rate = sum(termination for _, termination in results) / len(results)
    print(
        json.dumps(
            {
                "cases": len(cases),
                "decision_accuracy": decision_accuracy,
                "termination_success_rate": termination_rate,
            }
        )
    )
    if decision_accuracy < 0.9 or termination_rate < 1:
        raise SystemExit(1)


def main() -> None:
    cases = json.loads(Path("benchmarks/agent_cases.json").read_text(encoding="utf-8"))
    asyncio.run(run_all(cases))


if __name__ == "__main__":
    main()
