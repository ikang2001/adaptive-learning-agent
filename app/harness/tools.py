from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.harness.contracts import ToolCall

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class ToolRisk(StrEnum):
    READ = "READ"
    PROPOSAL = "PROPOSAL"


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    risk: ToolRisk = ToolRisk.READ
    version: str = "v1"
    timeout_seconds: float = 10.0
    retry_count: int = 2

    def as_model_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._tools:
            raise ValueError(f"duplicate tool: {definition.name}")
        if definition.risk is ToolRisk.PROPOSAL and not definition.name.startswith("propose_"):
            raise ValueError("proposal tools must start with propose_")
        self._tools[definition.name] = definition

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ValueError(f"unknown tool: {name}") from exc

    def model_tools(self) -> list[dict[str, Any]]:
        return [definition.as_model_tool() for definition in self._tools.values()]


class PolicyGuard:
    forbidden_verbs = ("update_", "commit_", "delete_", "publish_")

    def validate(self, definition: ToolDefinition, call: ToolCall) -> None:
        if call.name.startswith(self.forbidden_verbs):
            raise PermissionError(f"direct state mutation tool is forbidden: {call.name}")
        if definition.risk is ToolRisk.PROPOSAL and not call.idempotency_key:
            raise PermissionError("proposal tools require an idempotency key")


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, guard: PolicyGuard) -> None:
        self._registry = registry
        self._guard = guard

    async def execute(self, call: ToolCall) -> dict[str, Any]:
        definition = self._registry.get(call.name)
        self._guard.validate(definition, call)
        attempts = 1 if definition.risk is ToolRisk.PROPOSAL else definition.retry_count + 1
        last_error: TimeoutError | None = None
        for _ in range(attempts):
            try:
                async with asyncio.timeout(definition.timeout_seconds):
                    arguments = dict(call.arguments)
                    if call.idempotency_key:
                        arguments["_idempotency_key"] = call.idempotency_key
                    return await definition.handler(arguments)
            except TimeoutError as exc:
                last_error = exc
        raise TimeoutError(f"tool timed out: {call.name}") from last_error


def action_fingerprint(call: ToolCall) -> str:
    canonical = json.dumps(
        {"name": call.name, "arguments": call.arguments},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
