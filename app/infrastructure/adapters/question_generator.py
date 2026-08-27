from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

from app.config import Settings


@dataclass(frozen=True, slots=True)
class GeneratedQuestionContent:
    content: str
    answer: str
    solution: str
    model_name: str


class QuestionCandidateGenerator:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def generate(
        self, knowledge_name: str, difficulty: str, generation_seed: str
    ) -> GeneratedQuestionContent:
        if self._settings.use_fake_model:
            return GeneratedQuestionContent(
                content=f"原创候选题：围绕{knowledge_name}完成 {generation_seed} 参数分析。",
                answer=f"候选答案 {generation_seed}",
                solution=f"依据{knowledge_name}定义、公式和约束逐步求解，并进行结果校验。",
                model_name="fake-question-generator-v1",
            )
        return await self._generate_with_qwen(knowledge_name, difficulty, generation_seed)

    async def _generate_with_qwen(
        self, knowledge_name: str, difficulty: str, generation_seed: str
    ) -> GeneratedQuestionContent:
        prompt = (
            "生成一道原创自动控制原理题。只输出 JSON，字段为 content、answer、solution。"
            f"知识点={knowledge_name}，难度={difficulty}，种子={generation_seed}。"
            "条件必须充分，答案必须可验证，不得照抄真实试题。"
        )
        payload = {
            "model": self._settings.qwen_plus_model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }
        timeout = httpx.Timeout(connect=5, read=120, write=20, pool=5)
        async with httpx.AsyncClient(
            base_url=self._settings.qwen_base_url,
            headers={"Authorization": f"Bearer {self._settings.qwen_api_key.get_secret_value()}"},
            timeout=timeout,
        ) as client:
            response = await client.post("/chat/completions", json=payload)
        response.raise_for_status()
        body = json.loads(response.json()["choices"][0]["message"]["content"])
        return GeneratedQuestionContent(
            content=str(body["content"]),
            answer=str(body["answer"]),
            solution=str(body["solution"]),
            model_name=self._settings.qwen_plus_model,
        )
