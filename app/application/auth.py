from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.enums import Role, UserStatus
from app.errors import AppError
from app.infrastructure.adapters.otp import OtpStore
from app.infrastructure.adapters.security import PhoneProtector, normalize_phone
from app.infrastructure.adapters.sms import SmsProvider
from app.infrastructure.db.models import (
    AccountDeletion,
    RefreshSession,
    User,
    UserRole,
)


@dataclass(frozen=True, slots=True)
class AuthTokens:
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int


@dataclass(frozen=True, slots=True)
class CurrentUser:
    user_id: uuid.UUID
    roles: frozenset[Role]
    status: UserStatus = UserStatus.ACTIVE


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        otp_store: OtpStore,
        sms_provider: SmsProvider,
        phone_protector: PhoneProtector,
        settings: Settings,
    ) -> None:
        self._session = session
        self._otp_store = otp_store
        self._sms_provider = sms_provider
        self._phone_protector = phone_protector
        self._settings = settings

    async def request_sms_code(self, raw_phone: str, client_ip: str, purpose: str) -> None:
        phone = normalize_phone(raw_phone)
        code = await self._otp_store.issue(phone, client_ip, purpose)
        await self._sms_provider.send_code(phone, code, purpose)

    async def login_with_sms(self, raw_phone: str, code: str) -> AuthTokens:
        phone = normalize_phone(raw_phone)
        await self._otp_store.verify(phone, code, "LOGIN")
        user = await self._find_or_create_user(phone)
        if user.status is not UserStatus.ACTIVE:
            raise AppError(403, "ACCOUNT_UNAVAILABLE", "account is not active")
        roles = await self._get_roles(user.id)
        return await self._issue_tokens(user.id, roles)

    async def refresh(self, refresh_token: str) -> AuthTokens:
        token_hash = self._token_hash(refresh_token)
        result = await self._session.execute(
            select(RefreshSession).where(RefreshSession.token_hash == token_hash).with_for_update()
        )
        current = result.scalar_one_or_none()
        if current is None or current.expires_at <= datetime.now(UTC):
            raise AppError(401, "INVALID_REFRESH_TOKEN", "refresh token is invalid or expired")
        if current.revoked_at is not None:
            await self._revoke_all_sessions(current.user_id)
            await self._session.commit()
            raise AppError(401, "REFRESH_TOKEN_REUSED", "refresh token reuse detected")
        roles = await self._get_roles(current.user_id)
        tokens, replacement = self._build_tokens(current.user_id, roles)
        current.revoked_at = datetime.now(UTC)
        current.replaced_by_id = replacement.id
        self._session.add(replacement)
        await self._session.commit()
        return tokens

    async def logout(self, refresh_token: str) -> None:
        token_hash = self._token_hash(refresh_token)
        result = await self._session.execute(
            select(RefreshSession).where(RefreshSession.token_hash == token_hash)
        )
        session = result.scalar_one_or_none()
        if session and session.revoked_at is None:
            session.revoked_at = datetime.now(UTC)
            await self._session.commit()

    async def request_account_deletion(self, user_id: uuid.UUID) -> datetime:
        user = await self._session.get(User, user_id, with_for_update=True)
        if user is None:
            raise AppError(404, "USER_NOT_FOUND", "user does not exist")
        now = datetime.now(UTC)
        purge_after = now + timedelta(days=30)
        existing = await self._session.scalar(
            select(AccountDeletion).where(AccountDeletion.user_id == user_id)
        )
        if existing is None:
            self._session.add(
                AccountDeletion(user_id=user_id, requested_at=now, purge_after=purge_after)
            )
        else:
            existing.requested_at = now
            existing.purge_after = purge_after
            existing.cancelled_at = None
        user.status = UserStatus.DELETION_PENDING
        await self._revoke_all_sessions(user_id)
        await self._session.commit()
        return purge_after

    async def cancel_account_deletion(self, user_id: uuid.UUID) -> None:
        user = await self._session.get(User, user_id, with_for_update=True)
        deletion_request = await self._session.scalar(
            select(AccountDeletion).where(AccountDeletion.user_id == user_id)
        )
        if user is None or deletion_request is None or deletion_request.purged_at is not None:
            raise AppError(409, "DELETION_NOT_PENDING", "account deletion is not pending")
        deletion_request.cancelled_at = datetime.now(UTC)
        user.status = UserStatus.ACTIVE
        await self._session.commit()

    async def _find_or_create_user(self, phone: str) -> User:
        lookup_hash = self._phone_protector.lookup_hash(phone)
        user = await self._session.scalar(select(User).where(User.phone_lookup_hash == lookup_hash))
        if user is not None:
            return user
        user = User(
            phone_lookup_hash=lookup_hash,
            phone_ciphertext=self._phone_protector.encrypt(phone),
            status=UserStatus.ACTIVE,
        )
        self._session.add(user)
        await self._session.flush()
        self._session.add(UserRole(user_id=user.id, role=Role.STUDENT))
        await self._session.commit()
        return user

    async def _get_roles(self, user_id: uuid.UUID) -> frozenset[Role]:
        result = await self._session.scalars(
            select(UserRole.role).where(UserRole.user_id == user_id)
        )
        return frozenset(result.all())

    async def _issue_tokens(self, user_id: uuid.UUID, roles: frozenset[Role]) -> AuthTokens:
        tokens, refresh_session = self._build_tokens(user_id, roles)
        self._session.add(refresh_session)
        await self._session.commit()
        return tokens

    def _build_tokens(
        self, user_id: uuid.UUID, roles: frozenset[Role]
    ) -> tuple[AuthTokens, RefreshSession]:
        now = datetime.now(UTC)
        access_expires = now + timedelta(minutes=self._settings.access_token_minutes)
        access = jwt.encode(
            {
                "sub": str(user_id),
                "roles": sorted(role.value for role in roles),
                "iss": self._settings.jwt_issuer,
                "iat": now,
                "exp": access_expires,
                "type": "access",
            },
            self._settings.jwt_secret.get_secret_value(),
            algorithm="HS256",
        )
        refresh = secrets.token_urlsafe(48)
        refresh_session = RefreshSession(
            user_id=user_id,
            token_hash=self._token_hash(refresh),
            expires_at=now + timedelta(days=self._settings.refresh_token_days),
        )
        return (
            AuthTokens(
                access_token=access,
                refresh_token=refresh,
                token_type="bearer",
                expires_in=self._settings.access_token_minutes * 60,
            ),
            refresh_session,
        )

    async def _revoke_all_sessions(self, user_id: uuid.UUID) -> None:
        await self._session.execute(delete(RefreshSession).where(RefreshSession.user_id == user_id))

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()


def decode_access_token(token: str, settings: Settings) -> CurrentUser:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "exp", "iss", "type"]},
        )
    except jwt.PyJWTError as exc:
        raise AppError(401, "INVALID_ACCESS_TOKEN", "access token is invalid or expired") from exc
    if payload.get("type") != "access":
        raise AppError(401, "INVALID_ACCESS_TOKEN", "token type is not access")
    try:
        user_id = uuid.UUID(payload["sub"])
        roles = frozenset(Role(value) for value in payload.get("roles", []))
    except (ValueError, TypeError) as exc:
        raise AppError(401, "INVALID_ACCESS_TOKEN", "token claims are invalid") from exc
    return CurrentUser(user_id=user_id, roles=roles)
