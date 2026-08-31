from __future__ import annotations

import hashlib
import json
from typing import Any

from app.harness.contracts import RuntimeState, StallReason, ToolCall


def canonical_fingerprint(value: object) -> str:
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


def action_fingerprint(call: ToolCall) -> str:
    return canonical_fingerprint({"name": call.name, "arguments": call.arguments})


def observation_fingerprint(observation: dict[str, Any]) -> str:
    return canonical_fingerprint(observation.get("result", observation))


class StallDetector:
    history_limit = 8

    def record_action(self, state: RuntimeState, call: ToolCall) -> StallReason | None:
        fingerprint = action_fingerprint(call)
        state.last_action_fingerprints.append(fingerprint)
        state.last_action_fingerprints[:] = state.last_action_fingerprints[-self.history_limit :]
        actions = state.last_action_fingerprints
        if len(actions) >= 2 and actions[-1] == actions[-2]:
            return StallReason.REPEATED_ACTION
        if len(actions) >= 4 and actions[-4] == actions[-2] and actions[-3] == actions[-1]:
            if actions[-1] != actions[-2]:
                return StallReason.ACTION_OSCILLATION
        return None

    def record_observation(
        self, state: RuntimeState, observation: dict[str, Any]
    ) -> StallReason | None:
        fingerprint = observation_fingerprint(observation)
        state.last_observation_fingerprints.append(fingerprint)
        state.last_observation_fingerprints[:] = state.last_observation_fingerprints[
            -self.history_limit :
        ]
        evidence = state.last_observation_fingerprints
        if len(evidence) >= 2 and evidence[-1] == evidence[-2]:
            return StallReason.NO_NEW_EVIDENCE
        return None
