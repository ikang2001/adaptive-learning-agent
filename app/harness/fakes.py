from __future__ import annotations

import copy
import uuid
from collections import deque

from app.harness.contracts import ModelResult, RuntimeState, ToolCall
from app.harness.tools import (
    ToolDefinition,
    ToolLedgerEntry,
    ToolLedgerStatus,
)


class ScriptedModelGateway:
    def __init__(self, results: list[ModelResult]) -> None:
        self._results = deque(results)

    async def decide(self, state: RuntimeState, tools: list[dict[str, object]]) -> ModelResult:
        if not self._results:
            raise RuntimeError("scripted model has no result")
        return self._results.popleft()


class MemoryCheckpointStore:
    def __init__(self) -> None:
        self.states: list[RuntimeState] = []

    async def save(self, state: RuntimeState) -> None:
        self.states.append(copy.deepcopy(state))

    async def load_latest(self) -> RuntimeState | None:
        return copy.deepcopy(self.states[-1]) if self.states else None


class MemoryTraceRecorder:
    def __init__(self) -> None:
        self.steps: list[ModelResult] = []
        self.tools: list[tuple[ToolCall, dict[str, object]]] = []
        self.guardrails: list[tuple[str, str]] = []

    async def record_step(self, state: RuntimeState, result: ModelResult) -> str:
        self.steps.append(result)
        return f"step-{state.loop_count}"

    async def record_tool(
        self,
        state: RuntimeState,
        step_id: str,
        tool_call: ToolCall,
        observation: dict[str, object],
        latency_ms: int,
        retry_count: int = 0,
        status: str = "SUCCEEDED",
        error_code: str | None = None,
        replayed: bool = False,
    ) -> None:
        del state, step_id, latency_ms, retry_count, status, error_code, replayed
        self.tools.append((tool_call, observation))

    async def record_guardrail(
        self,
        state: RuntimeState,
        step_id: str | None,
        tool_name: str | None,
        decision: str,
        reason_code: str,
    ) -> None:
        del state, step_id, tool_name
        self.guardrails.append((decision, reason_code))


class MemoryToolExecutionLedger:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str, str], ToolLedgerEntry] = {}

    async def begin(
        self,
        state: RuntimeState,
        definition: ToolDefinition,
        call: ToolCall,
        args_digest: str,
    ) -> ToolLedgerEntry:
        del args_digest
        assert call.idempotency_key is not None
        key = (state.run_id, definition.name, call.idempotency_key)
        existing = self.records.get(key)
        if existing is not None:
            return ToolLedgerEntry(existing.record_id, existing.status, False, existing.result)
        entry = ToolLedgerEntry(uuid.uuid4(), ToolLedgerStatus.STARTED, True)
        self.records[key] = entry
        return entry

    async def mark_started(self, state: RuntimeState, record_id: uuid.UUID) -> None:
        del state
        self._replace(record_id, ToolLedgerStatus.STARTED)

    async def succeed(
        self, state: RuntimeState, record_id: uuid.UUID, result: dict[str, object]
    ) -> None:
        del state
        self._replace(record_id, ToolLedgerStatus.SUCCEEDED, dict(result))

    async def fail(self, state: RuntimeState, record_id: uuid.UUID, error_code: str) -> None:
        del state, error_code
        self._replace(record_id, ToolLedgerStatus.FAILED)

    async def unknown(self, state: RuntimeState, record_id: uuid.UUID, error_code: str) -> None:
        del state, error_code
        self._replace(record_id, ToolLedgerStatus.UNKNOWN)

    def _replace(
        self,
        record_id: uuid.UUID,
        status: ToolLedgerStatus,
        result: dict[str, object] | None = None,
    ) -> None:
        for key, item in self.records.items():
            if item.record_id == record_id:
                self.records[key] = ToolLedgerEntry(record_id, status, False, result)
                return
        raise KeyError(record_id)
