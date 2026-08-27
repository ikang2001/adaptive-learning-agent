from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.config import Settings
from app.harness.contracts import ModelAction, ModelGateway, ModelResult, RuntimeState, ToolCall


class QwenModelGateway(ModelGateway):
    def __init__(self, settings: Settings, model_name: str) -> None:
        self._settings = settings
        self._model_name = model_name

    async def decide(self, state: RuntimeState, tools: list[dict[str, Any]]) -> ModelResult:
        started = time.monotonic()
        payload = {
            "model": self._model_name,
            "messages": self._messages(state),
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.1,
        }
        headers = {
            "Authorization": f"Bearer {self._settings.qwen_api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        timeout = httpx.Timeout(connect=5, read=120, write=20, pool=5)
        async with httpx.AsyncClient(
            base_url=self._settings.qwen_base_url,
            headers=headers,
            timeout=timeout,
        ) as client:
            response = await client.post("/chat/completions", json=payload)
        response.raise_for_status()
        body = response.json()
        choice = body["choices"][0]["message"]
        action = self._parse_action(choice)
        usage = body.get("usage", {})
        return ModelResult(
            action=action,
            model_name=self._model_name,
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            latency_ms=round((time.monotonic() - started) * 1000),
        )

    @staticmethod
    def _messages(state: RuntimeState) -> list[dict[str, str]]:
        system = (
            "你是学习诊断决策 Agent。只能使用给定只读工具与 propose_* 工具；"
            "禁止直接修改业务状态。证据不足时必须结束并返回 UNCERTAIN。"
            "最终输出 JSON：decision, confidence, reason_codes, finish=true。"
        )
        context = json.dumps(
            {
                "goal": state.goal,
                "loop_count": state.loop_count,
                "observations": state.observations,
            },
            ensure_ascii=False,
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": context}]

    @staticmethod
    def _parse_action(message: dict[str, Any]) -> ModelAction:
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            function = tool_calls[0]["function"]
            arguments = json.loads(function.get("arguments") or "{}")
            return ModelAction(
                tool_call=ToolCall(
                    name=function["name"],
                    arguments=arguments,
                    idempotency_key=tool_calls[0].get("id"),
                )
            )
        content = message.get("content") or "{}"
        parsed = json.loads(content)
        return ModelAction(
            decision=parsed.get("decision"),
            confidence=float(parsed.get("confidence", 0)),
            reason_codes=tuple(parsed.get("reason_codes", [])),
            finish=bool(parsed.get("finish", True)),
        )


class FakeDiagnosisModelGateway(ModelGateway):
    """Deterministic model used by tests and local business demos."""

    model_name = "fake-diagnosis-v1"

    async def decide(self, state: RuntimeState, tools: list[dict[str, Any]]) -> ModelResult:
        if state.loop_count == 0:
            return ModelResult(
                ModelAction(tool_call=ToolCall("search_recent_attempts", {"limit": 10})),
                self.model_name,
            )
        if state.loop_count == 1:
            reasons = self._reason_codes(state)
            major = "REPEATED_ERROR" in reasons or "LOW_ACCURACY" in reasons
            name = "propose_major_replan" if major else "propose_minor_adjustment"
            proposal = {
                "reason_codes": reasons,
                "confidence": 0.88,
                "evidence_refs": self._attempt_refs(state),
                "adjustment_factor": 0.8,
            }
            return ModelResult(
                ModelAction(
                    tool_call=ToolCall(
                        name,
                        proposal,
                        idempotency_key=f"{state.run_id}:{name}:v1",
                    )
                ),
                self.model_name,
            )
        reasons = self._reason_codes(state)
        decision = (
            "MAJOR_REPLAN"
            if "REPEATED_ERROR" in reasons or "LOW_ACCURACY" in reasons
            else "MINOR_ADJUST"
        )
        return ModelResult(
            ModelAction(
                decision=decision,
                confidence=0.88,
                reason_codes=tuple(reasons),
                finish=True,
            ),
            self.model_name,
        )

    @staticmethod
    def _reason_codes(state: RuntimeState) -> list[str]:
        initial = state.observations[0] if state.observations else {}
        return list(initial.get("reason_codes", []))

    @staticmethod
    def _attempt_refs(state: RuntimeState) -> list[str]:
        refs: list[str] = []
        for observation in state.observations:
            result = observation.get("result", {})
            if isinstance(result, dict):
                refs.extend(str(item) for item in result.get("attempt_ids", []))
        return refs


class ModelRouter(ModelGateway):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._fake = FakeDiagnosisModelGateway()
        self._plus = QwenModelGateway(settings, settings.qwen_plus_model)
        self._flash = QwenModelGateway(settings, settings.qwen_flash_model)

    async def decide(self, state: RuntimeState, tools: list[dict[str, Any]]) -> ModelResult:
        if self._settings.use_fake_model:
            return await self._fake.decide(state, tools)
        gateway = self._flash if state.goal in {"EXPLAIN", "SUMMARIZE"} else self._plus
        result = await gateway.decide(state, tools)
        if gateway is self._flash and result.action.confidence < 0.75:
            return await self._plus.decide(state, tools)
        return result
