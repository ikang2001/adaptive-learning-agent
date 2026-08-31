from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.harness.contracts import RuntimeState, ToolCall
from app.harness.errors import ToolPermissionError

if TYPE_CHECKING:
    from app.harness.tools import ToolDefinition


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    reason_code: str


class AgentPolicyEngine:
    version = "agent_policy_v2"
    forbidden_prefixes = ("update_", "commit_", "delete_", "publish_")

    def validate(
        self,
        state: RuntimeState,
        definition: ToolDefinition,
        call: ToolCall,
        permissions: frozenset[str] = frozenset(),
    ) -> PolicyDecision:
        del state
        if call.name.startswith(self.forbidden_prefixes):
            raise ToolPermissionError(
                f"direct state mutation tool is forbidden: {call.name}",
                detail={"reason_code": "DIRECT_MUTATION_FORBIDDEN"},
            )
        if definition.idempotency_required and not call.idempotency_key:
            raise ToolPermissionError(
                "tool requires an idempotency key",
                detail={"reason_code": "IDEMPOTENCY_KEY_REQUIRED"},
            )
        missing = definition.required_permissions - permissions
        if missing:
            raise ToolPermissionError(
                "tool permission requirements are not satisfied",
                detail={"reason_code": "MISSING_PERMISSION", "missing": sorted(missing)},
            )
        return PolicyDecision(True, "ALLOWED")


PolicyGuard = AgentPolicyEngine
