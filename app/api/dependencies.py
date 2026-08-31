from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.agent_runs import AgentQueryService, ProposalService
from app.application.auth import AuthService, CurrentUser, decode_access_token
from app.application.catalog import CatalogService
from app.application.jobs import JobService
from app.application.learning_plans import LearningPlanService
from app.application.mock_exams import GeneratedQuestionReviewService, MockExamService
from app.application.plans import PlanQueryService
from app.application.practice import PracticeService
from app.application.resources import ResourceService
from app.application.school_change import SchoolChangeService
from app.application.shadow_evaluations import ShadowEvaluationService
from app.application.students import StudentService
from app.application.true_exams import TrueExamService
from app.application.unlocks import UnlockService
from app.config import Settings, get_settings
from app.domain.enums import UserStatus
from app.errors import AppError
from app.infrastructure.adapters.otp import RedisOtpStore
from app.infrastructure.adapters.security import PhoneProtector
from app.infrastructure.adapters.sms import FixedSmsProvider, SmsProvider, UnconfiguredSmsProvider
from app.infrastructure.db.models import User, UserRole
from app.infrastructure.db.session import get_session
from app.infrastructure.redis import get_redis

bearer = HTTPBearer(auto_error=False)


def get_sms_provider(settings: Annotated[Settings, Depends(get_settings)]) -> SmsProvider:
    if settings.sms_provider == "fixed":
        return FixedSmsProvider()
    return UnconfiguredSmsProvider()


async def get_auth_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
    sms_provider: Annotated[SmsProvider, Depends(get_sms_provider)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncIterator[AuthService]:
    otp_store = RedisOtpStore(
        redis,
        settings.otp_hmac_secret.get_secret_value(),
        settings.fixed_sms_code.get_secret_value() if settings.sms_provider == "fixed" else None,
    )
    protector = PhoneProtector(
        settings.pii_hmac_secret.get_secret_value(),
        settings.pii_encryption_key.get_secret_value(),
    )
    yield AuthService(session, otp_store, sms_provider, protector, settings)


async def get_authenticated_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError(401, "AUTHENTICATION_REQUIRED", "bearer access token is required")
    token_user = decode_access_token(credentials.credentials, settings)
    user = await session.get(User, token_user.user_id)
    if user is None or user.status is UserStatus.DISABLED:
        raise AppError(401, "ACCOUNT_UNAVAILABLE", "account is unavailable")
    roles = frozenset(
        (await session.scalars(select(UserRole.role).where(UserRole.user_id == user.id))).all()
    )
    return CurrentUser(user_id=user.id, roles=roles, status=user.status)


async def get_current_user(
    user: Annotated[CurrentUser, Depends(get_authenticated_user)],
) -> CurrentUser:
    if user.status is not UserStatus.ACTIVE:
        raise AppError(403, "ACCOUNT_DELETION_PENDING", "account access is frozen")
    return user


def get_student_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StudentService:
    return StudentService(session)


def get_catalog_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CatalogService:
    return CatalogService(session)


def get_job_service(session: Annotated[AsyncSession, Depends(get_session)]) -> JobService:
    return JobService(session)


def get_plan_query_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PlanQueryService:
    return PlanQueryService(session)


def get_learning_plan_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LearningPlanService:
    return LearningPlanService(session)


def get_practice_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PracticeService:
    return PracticeService(session)


def get_agent_query_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentQueryService:
    return AgentQueryService(session)


def get_proposal_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProposalService:
    return ProposalService(session)


def get_shadow_evaluation_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ShadowEvaluationService:
    return ShadowEvaluationService(session, settings)


def get_true_exam_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TrueExamService:
    return TrueExamService(session)


def get_unlock_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UnlockService:
    return UnlockService(session)


def get_school_change_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SchoolChangeService:
    return SchoolChangeService(session)


def get_mock_exam_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MockExamService:
    return MockExamService(session)


def get_generated_question_review_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> GeneratedQuestionReviewService:
    return GeneratedQuestionReviewService(session)


def get_resource_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ResourceService:
    return ResourceService(session, settings)
