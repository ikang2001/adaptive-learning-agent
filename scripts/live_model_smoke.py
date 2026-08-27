from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pydantic import SecretStr

from app.config import Settings
from app.harness.contracts import RuntimeState
from app.infrastructure.adapters.model_gateway import QwenModelGateway


def load_local_qwen_config() -> tuple[str, str, str]:
    path = Path("实施文档/env.txt")
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return (
        values["VISIONLAB_QWEN_API_KEY"],
        values["VISIONLAB_QWEN_MODEL"],
        values["VISIONLAB_QWEN_EMBEDDING_BASE_URL"],
    )


async def main() -> None:
    api_key, model_name, base_url = load_local_qwen_config()
    settings = Settings(
        app_env="local",
        use_fake_model=False,
        qwen_api_key=SecretStr(api_key),
        qwen_base_url=base_url,
    )
    gateway = QwenModelGateway(settings, model_name)
    state = RuntimeState("live-smoke", "synthetic-student", "FEEDBACK_DIAGNOSIS")
    state.observations.append({"reason_codes": ["TIME_OVERRUN"]})
    result = await gateway.decide(
        state,
        [
            {
                "type": "function",
                "function": {
                    "name": "search_recent_attempts",
                    "description": "read recent synthetic attempts",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )
    print(
        json.dumps(
            {
                "model": result.model_name,
                "action": "tool_call" if result.action.tool_call else "decision",
                "tool": result.action.tool_call.name if result.action.tool_call else None,
                "latency_ms": result.latency_ms,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
