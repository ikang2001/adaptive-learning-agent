from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 2.0
    retry_budget_seconds: float = 5.0
    jitter_ratio: float = 0.2

    def delay(self, retry_number: int, random_value: float | None = None) -> float:
        raw = min(self.max_delay_seconds, self.base_delay_seconds * (2 ** (retry_number - 1)))
        sample: float = float(random.random() if random_value is None else random_value)
        jitter = raw * self.jitter_ratio * ((sample * 2) - 1)
        result: float = float(raw + jitter)
        return result if result > 0 else 0.0


Sleep = Callable[[float], Awaitable[None]]


async def default_sleep(delay: float) -> None:
    await asyncio.sleep(delay)
