from __future__ import annotations

from typing import Protocol


class SmsProvider(Protocol):
    async def send_code(self, phone: str, code: str, purpose: str) -> None: ...


class FixedSmsProvider:
    """Local/test provider. It intentionally does not log the phone or code."""

    async def send_code(self, phone: str, code: str, purpose: str) -> None:
        return None


class UnconfiguredSmsProvider:
    async def send_code(self, phone: str, code: str, purpose: str) -> None:
        raise RuntimeError("a production SMS provider adapter has not been configured")
