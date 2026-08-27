from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.dependencies import get_auth_service, get_authenticated_user, get_current_user
from app.api.schemas import (
    DeletionResponse,
    LogoutRequest,
    MessageResponse,
    RefreshRequest,
    SmsCodeRequest,
    SmsLoginRequest,
    TokenResponse,
)
from app.application.auth import AuthService, CurrentUser

router = APIRouter(tags=["auth"])


@router.post("/auth/sms-codes", status_code=status.HTTP_202_ACCEPTED)
async def request_sms_code(
    body: SmsCodeRequest,
    request: Request,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> MessageResponse:
    client_ip = request.client.host if request.client else "unknown"
    await service.request_sms_code(body.phone, client_ip, body.purpose)
    return MessageResponse(status="accepted")


@router.post("/auth/sessions")
async def create_session(
    body: SmsLoginRequest,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> TokenResponse:
    return TokenResponse.model_validate(
        await service.login_with_sms(body.phone, body.code), from_attributes=True
    )


@router.post("/auth/token/refresh")
async def refresh_session(
    body: RefreshRequest,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> TokenResponse:
    return TokenResponse.model_validate(
        await service.refresh(body.refresh_token), from_attributes=True
    )


@router.delete("/auth/sessions/current", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    body: LogoutRequest,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> Response:
    await service.logout(body.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/account", status_code=status.HTTP_202_ACCEPTED)
async def request_account_deletion(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> DeletionResponse:
    purge_after = await service.request_account_deletion(current_user.user_id)
    return DeletionResponse(status="deletion_pending", purge_after=purge_after)


@router.post("/account/deletion/cancel")
async def cancel_account_deletion(
    current_user: Annotated[CurrentUser, Depends(get_authenticated_user)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> MessageResponse:
    await service.cancel_account_deletion(current_user.user_id)
    return MessageResponse(status="active")
