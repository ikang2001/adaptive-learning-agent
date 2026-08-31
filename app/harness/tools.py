from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

import httpx
import structlog
from opentelemetry import trace
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import OperationalError

from app.errors import AppError
from app.harness.contracts import RuntimeState, ToolCall, ToolExecutionResult
from app.harness.errors import (
    ToolBusinessError,
    ToolError,
    ToolOutcomeUnknownError,
    ToolPermissionError,
    ToolRateLimitError,
    ToolTimeoutError,
    ToolTransientError,
    ToolUpstreamError,
    ToolValidationError,
    UnknownToolError,
)
from app.harness.policy import AgentPolicyEngine
from app.harness.retry import RetryPolicy, Sleep, default_sleep
from app.harness.termination import action_fingerprint

PolicyGuard = AgentPolicyEngine
logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
ReconcileHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]]


class ToolRisk(StrEnum):
    READ = "READ"
    PROPOSAL = "PROPOSAL"


class ToolSideEffect(StrEnum):
    NONE = "NONE"
    IDEMPOTENT_WRITE = "IDEMPOTENT_WRITE"
    NON_IDEMPOTENT_WRITE = "NON_IDEMPOTENT_WRITE"


class ToolLedgerStatus(StrEnum):
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ToolAvailabilityContext:
    environment: str = "local"
    roles: frozenset[str] = frozenset()
    feature_flags: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    risk: ToolRisk = ToolRisk.READ
    version: str = "v1"
    timeout_seconds: float = 10.0
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    args_model: type[BaseModel] | None = None
    side_effect_level: ToolSideEffect = ToolSideEffect.NONE
    idempotency_required: bool = False
    required_permissions: frozenset[str] = frozenset()
    requires_confirmation: bool = False
    reconcile_handler: ReconcileHandler | None = None
    enabled_environments: frozenset[str] = frozenset()
    feature_flag: str | None = None
    minimum_role: str | None = None
    terminal_decision: str | None = None

    def as_model_tool(self) -> dict[str, Any]:
        raw_schema = self.args_model.model_json_schema() if self.args_model else self.input_schema
        schema = _compact_model_schema(raw_schema)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }

    def is_enabled(self, context: ToolAvailabilityContext) -> bool:
        if self.enabled_environments and context.environment not in self.enabled_environments:
            return False
        if self.feature_flag and self.feature_flag not in context.feature_flags:
            return False
        return not self.minimum_role or self.minimum_role in context.roles


@dataclass(frozen=True, slots=True)
class ToolLedgerEntry:
    record_id: uuid.UUID
    status: ToolLedgerStatus
    is_new: bool
    result: dict[str, Any] | None = None


class ToolExecutionLedger(Protocol):
    async def begin(
        self,
        state: RuntimeState,
        definition: ToolDefinition,
        call: ToolCall,
        args_digest: str,
    ) -> ToolLedgerEntry: ...

    async def mark_started(self, state: RuntimeState, record_id: uuid.UUID) -> None: ...

    async def succeed(
        self, state: RuntimeState, record_id: uuid.UUID, result: dict[str, Any]
    ) -> None: ...

    async def fail(self, state: RuntimeState, record_id: uuid.UUID, error_code: str) -> None: ...

    async def unknown(self, state: RuntimeState, record_id: uuid.UUID, error_code: str) -> None: ...


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._tools:
            raise ValueError(f"duplicate tool: {definition.name}")
        if definition.risk is ToolRisk.PROPOSAL and not definition.name.startswith("propose_"):
            raise ValueError("proposal tools must start with propose_")
        if definition.risk is ToolRisk.PROPOSAL:
            if definition.side_effect_level is ToolSideEffect.NONE:
                raise ValueError("proposal tools must declare a side effect level")
            if not definition.idempotency_required:
                raise ValueError("proposal tools must require idempotency")
        if definition.side_effect_level is ToolSideEffect.NON_IDEMPOTENT_WRITE:
            raise ValueError("non-idempotent write tools are not supported by this harness")
        if definition.terminal_decision and definition.risk is not ToolRisk.PROPOSAL:
            raise ValueError("only proposal tools may define a terminal decision")
        self._tools[definition.name] = definition

    def get(self, name: str, context: ToolAvailabilityContext | None = None) -> ToolDefinition:
        try:
            definition = self._tools[name]
        except KeyError as exc:
            raise UnknownToolError(f"unknown tool: {name}") from exc
        if context is not None and not definition.is_enabled(context):
            raise UnknownToolError(f"tool is not enabled in this context: {name}")
        return definition

    def model_tools(self, context: ToolAvailabilityContext | None = None) -> list[dict[str, Any]]:
        selected: Iterable[ToolDefinition] = self._tools.values()
        if context is not None:
            selected = (item for item in selected if item.is_enabled(context))
        return [definition.as_model_tool() for definition in selected]

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._tools.values())


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        guard: AgentPolicyEngine,
        *,
        ledger: ToolExecutionLedger | None = None,
        availability: ToolAvailabilityContext | None = None,
        permissions: frozenset[str] = frozenset(),
        sleep: Sleep = default_sleep,
    ) -> None:
        self._registry = registry
        self._guard = guard
        self._ledger = ledger
        self._availability = availability or ToolAvailabilityContext()
        self._permissions = permissions
        self._sleep = sleep

    async def execute(self, state: RuntimeState, call: ToolCall) -> ToolExecutionResult:
        definition = self._registry.get(call.name, self._availability)
        arguments = self._validated_arguments(definition, call)
        if definition.risk is ToolRisk.PROPOSAL:
            arguments["evidence_refs"] = self._runtime_evidence_refs(state)
        with tracer.start_as_current_span(
            "policy.validate",
            attributes={"tool.name": call.name, "tool.risk": definition.risk.value},
        ):
            self._guard.validate(state, definition, call, self._permissions)
        ledger_entry = await self._begin_ledger(state, definition, call, arguments)
        if ledger_entry is not None and not ledger_entry.is_new:
            if (
                ledger_entry.status is ToolLedgerStatus.SUCCEEDED
                and ledger_entry.result is not None
            ):
                logger.info(
                    "agent_tool_replayed",
                    run_id=state.run_id,
                    tool_name=call.name,
                    status=ledger_entry.status.value,
                )
                return ToolExecutionResult(ledger_entry.result, replayed=True)
            reconciled = await self._reconcile(definition, arguments, call)
            if reconciled is not None:
                await self._ledger_succeed(state, ledger_entry.record_id, reconciled)
                return ToolExecutionResult(reconciled, replayed=True)
            if (
                definition.side_effect_level is ToolSideEffect.IDEMPOTENT_WRITE
                and definition.reconcile_handler is not None
                and self._ledger is not None
            ):
                await self._ledger.mark_started(state, ledger_entry.record_id)
            else:
                raise ToolOutcomeUnknownError(
                    f"existing tool execution is {ledger_entry.status.value}: {call.name}"
                )

        record_id = ledger_entry.record_id if ledger_entry else None
        attempts = self._attempt_limit(definition, call)
        retry_count = 0
        retry_started = time.monotonic()
        for attempt in range(1, attempts + 1):
            if attempt > 1 and record_id is not None and self._ledger is not None:
                await self._ledger.mark_started(state, record_id)
            try:
                async with asyncio.timeout(definition.timeout_seconds):
                    handler_arguments = dict(arguments)
                    if call.idempotency_key:
                        handler_arguments["_idempotency_key"] = call.idempotency_key
                    result = await definition.handler(handler_arguments)
                if not isinstance(result, dict):
                    raise ToolBusinessError("tool handler must return an object")
                if record_id is not None:
                    await self._ledger_succeed(state, record_id, result)
                return ToolExecutionResult(result, retry_count=retry_count)
            except Exception as exc:
                mapped = self._map_error(exc)
                can_retry = isinstance(mapped, ToolTransientError) and attempt < attempts
                if record_id is not None and isinstance(mapped, ToolTransientError):
                    assert self._ledger is not None
                    await self._ledger.unknown(state, record_id, mapped.code)
                    reconciled = await self._reconcile(definition, arguments, call)
                    if reconciled is not None:
                        await self._ledger_succeed(state, record_id, reconciled)
                        return ToolExecutionResult(
                            reconciled, retry_count=retry_count, replayed=True
                        )
                if not can_retry:
                    if record_id is not None and not isinstance(mapped, ToolTransientError):
                        assert self._ledger is not None
                        await self._ledger.fail(state, record_id, mapped.code)
                    raise mapped from exc
                retry_count += 1
                delay = definition.retry_policy.delay(retry_count)
                elapsed = time.monotonic() - retry_started
                if elapsed + delay > definition.retry_policy.retry_budget_seconds:
                    raise mapped from exc
                logger.warning(
                    "agent_tool_retry",
                    run_id=state.run_id,
                    tool_name=call.name,
                    error_code=mapped.code,
                    retry_attempt=retry_count,
                    retry_delay_seconds=round(delay, 3),
                )
                await self._sleep(delay)
        raise ToolBusinessError(f"tool did not produce a result: {call.name}")

    @staticmethod
    def _validated_arguments(definition: ToolDefinition, call: ToolCall) -> dict[str, Any]:
        if definition.args_model is None:
            if not isinstance(call.arguments, dict):
                raise ToolValidationError("tool arguments must be an object")
            return dict(call.arguments)
        try:
            raw_arguments = dict(call.arguments)
            if definition.risk is ToolRisk.PROPOSAL:
                raw_arguments.pop("evidence_refs", None)
            payload = definition.args_model.model_validate(raw_arguments)
        except ValidationError as exc:
            raise ToolValidationError(
                "tool arguments failed validation", detail={"errors": exc.errors()}
            ) from exc
        return payload.model_dump(mode="python")

    @staticmethod
    def _runtime_evidence_refs(state: RuntimeState) -> list[str]:
        refs: list[str] = []
        for observation in state.observations:
            task_id = observation.get("task_id")
            if task_id:
                refs.append(str(task_id))
            result = observation.get("result")
            if isinstance(result, dict):
                refs.extend(str(item) for item in result.get("attempt_ids", []))
        return list(dict.fromkeys(refs))

    async def _begin_ledger(
        self,
        state: RuntimeState,
        definition: ToolDefinition,
        call: ToolCall,
        arguments: dict[str, Any],
    ) -> ToolLedgerEntry | None:
        if definition.side_effect_level is ToolSideEffect.NONE or self._ledger is None:
            return None
        if not call.idempotency_key:
            raise ToolPermissionError("side-effect tool requires an idempotency key")
        return await self._ledger.begin(state, definition, call, canonical_digest(arguments))

    async def _reconcile(
        self,
        definition: ToolDefinition,
        arguments: dict[str, Any],
        call: ToolCall,
    ) -> dict[str, Any] | None:
        if definition.reconcile_handler is None:
            return None
        values = dict(arguments)
        if call.idempotency_key:
            values["_idempotency_key"] = call.idempotency_key
        return await definition.reconcile_handler(values)

    async def _ledger_succeed(
        self, state: RuntimeState, record_id: uuid.UUID, result: dict[str, Any]
    ) -> None:
        if self._ledger is not None:
            await self._ledger.succeed(state, record_id, result)

    @staticmethod
    def _attempt_limit(definition: ToolDefinition, call: ToolCall) -> int:
        if definition.side_effect_level is ToolSideEffect.NON_IDEMPOTENT_WRITE:
            return 1
        if definition.side_effect_level is ToolSideEffect.IDEMPOTENT_WRITE:
            return definition.retry_policy.max_attempts if call.idempotency_key else 1
        return definition.retry_policy.max_attempts

    @staticmethod
    def _map_error(exc: Exception) -> ToolError:
        if isinstance(exc, ToolError):
            return exc
        if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
            return ToolTimeoutError("tool timed out")
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            if status == 429:
                return ToolRateLimitError("tool upstream rate limited the request")
            if status >= 500:
                return ToolUpstreamError("tool upstream returned a server error")
            return ToolBusinessError(f"tool upstream rejected the request with {status}")
        if isinstance(exc, (httpx.NetworkError, OperationalError)):
            return ToolUpstreamError("tool dependency is temporarily unavailable")
        if isinstance(exc, PermissionError):
            return ToolPermissionError(str(exc))
        if isinstance(exc, AppError):
            return ToolBusinessError(exc.detail, detail={"code": exc.code})
        return ToolBusinessError(f"tool handler failed: {type(exc).__name__}")


def canonical_digest(value: object) -> str:
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


def _compact_model_schema(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _compact_model_schema(item)
            for key, item in value.items()
            if key not in {"title", "examples"}
        }
    if isinstance(value, list):
        return [_compact_model_schema(item) for item in value]
    return value


__all__ = [
    "AgentPolicyEngine",
    "PolicyGuard",
    "ToolAvailabilityContext",
    "ToolDefinition",
    "ToolExecutionLedger",
    "ToolExecutor",
    "ToolLedgerEntry",
    "ToolLedgerStatus",
    "ToolRegistry",
    "ToolRisk",
    "ToolSideEffect",
    "action_fingerprint",
]
