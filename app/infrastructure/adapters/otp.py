from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis

from app.errors import AppError


class OtpStore(Protocol):
    async def issue(self, phone: str, client_ip: str, purpose: str) -> str: ...

    async def verify(self, phone: str, code: str, purpose: str) -> None: ...


class RedisOtpStore:
    def __init__(self, redis: Redis, hmac_secret: str, fixed_code: str | None = None) -> None:
        self._redis = redis
        self._secret = hmac_secret.encode()
        self._fixed_code = fixed_code

    async def issue(self, phone: str, client_ip: str, purpose: str) -> str:
        await self._check_rate_limit(phone, client_ip)
        code = self._fixed_code or f"{secrets.randbelow(1_000_000):06d}"
        payload = f"{self._digest(phone, code, purpose)}:0"
        await self._redis.set(self._otp_key(phone, purpose), payload, ex=300)
        return code

    async def verify(self, phone: str, code: str, purpose: str) -> None:
        key = self._otp_key(phone, purpose)
        payload = await self._redis.get(key)
        if payload is None:
            raise AppError(401, "OTP_EXPIRED", "verification code is expired")
        digest, attempts_text = payload.decode().rsplit(":", 1)
        attempts = int(attempts_text)
        if attempts >= 5:
            await self._redis.delete(key)
            raise AppError(429, "OTP_ATTEMPTS_EXCEEDED", "too many verification attempts")
        if not hmac.compare_digest(digest, self._digest(phone, code, purpose)):
            ttl = max(await self._redis.ttl(key), 1)
            await self._redis.set(key, f"{digest}:{attempts + 1}", ex=ttl)
            raise AppError(401, "OTP_INVALID", "verification code is invalid")
        await self._redis.delete(key)

    async def _check_rate_limit(self, phone: str, client_ip: str) -> None:
        minute_key = f"otp:rate:minute:{phone}"
        if not await self._redis.set(minute_key, "1", ex=60, nx=True):
            raise AppError(429, "OTP_RATE_LIMITED", "wait before requesting another code")
        limits = (
            (f"otp:rate:hour:{phone}", 5, 3600),
            (f"otp:rate:day:{phone}", 10, 86400),
            (f"otp:rate:ip:{client_ip}", 20, 3600),
        )
        for key, maximum, ttl in limits:
            count = await self._redis.incr(key)
            if count == 1:
                await self._redis.expire(key, ttl)
            if count > maximum:
                raise AppError(429, "OTP_RATE_LIMITED", "verification request limit exceeded")

    def _digest(self, phone: str, code: str, purpose: str) -> str:
        message = f"{phone}:{purpose}:{code}".encode()
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    @staticmethod
    def _otp_key(phone: str, purpose: str) -> str:
        return f"otp:code:{purpose}:{phone}"


@dataclass(slots=True)
class _OtpRecord:
    digest: str
    expires_at: float
    attempts: int = 0


class InMemoryOtpStore:
    def __init__(self, hmac_secret: str, fixed_code: str = "246810") -> None:
        self._secret = hmac_secret.encode()
        self._fixed_code = fixed_code
        self._records: dict[tuple[str, str], _OtpRecord] = {}

    async def issue(self, phone: str, client_ip: str, purpose: str) -> str:
        digest = self._digest(phone, self._fixed_code, purpose)
        self._records[(phone, purpose)] = _OtpRecord(digest, time.monotonic() + 300)
        return self._fixed_code

    async def verify(self, phone: str, code: str, purpose: str) -> None:
        record = self._records.get((phone, purpose))
        if record is None or record.expires_at <= time.monotonic():
            raise AppError(401, "OTP_EXPIRED", "verification code is expired")
        if record.attempts >= 5:
            raise AppError(429, "OTP_ATTEMPTS_EXCEEDED", "too many verification attempts")
        if not hmac.compare_digest(record.digest, self._digest(phone, code, purpose)):
            record.attempts += 1
            raise AppError(401, "OTP_INVALID", "verification code is invalid")
        del self._records[(phone, purpose)]

    def _digest(self, phone: str, code: str, purpose: str) -> str:
        return hmac.new(
            self._secret, f"{phone}:{purpose}:{code}".encode(), hashlib.sha256
        ).hexdigest()
