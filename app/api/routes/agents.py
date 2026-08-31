from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, status

from app.api.dependencies import (
    get_agent_query_service,
    get_current_user,
    get_proposal_service,
    get_shadow_evaluation_service,
)
from app.api.schemas import (
    AgentCancelResponse,
    AgentRunResponse,
    AgentStepResponse,
    ProposalDecisionRequest,
    ProposalDecisionResponse,
    ProposalResponse,
    ShadowEvaluationResponse,
    ToolInvocationResponse,
)
from app.application.agent_runs import AgentQueryService, ProposalService
from app.application.auth import CurrentUser
from app.application.shadow_evaluations import ShadowEvaluationService
from app.errors import AppError

router = APIRouter(tags=["agent"])


@router.get("/agent-runs/{run_id}")
async def get_agent_run(
    run_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[AgentQueryService, Depends(get_agent_query_service)],
) -> AgentRunResponse:
    run, steps, tools, proposals = await service.owned_run(current_user.user_id, run_id)
    return AgentRunResponse(
        id=run.id,
        goal=run.goal,
        status=run.status,
        model_version=run.model_version,
        prompt_version=run.prompt_version,
        policy_version=run.policy_version,
        loop_count=run.loop_count,
        model_call_count=run.model_call_count,
        tool_call_count=run.tool_call_count,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        resumed_count=run.resumed_count,
        termination_reason=run.termination_reason,
        steps=[AgentStepResponse.model_validate(item, from_attributes=True) for item in steps],
        tools=[ToolInvocationResponse.model_validate(item, from_attributes=True) for item in tools],
        proposals=[
            ProposalResponse.model_validate(item, from_attributes=True) for item in proposals
        ],
    )


@router.get("/agent-runs/{run_id}/replay")
async def replay_agent_run(
    run_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[AgentQueryService, Depends(get_agent_query_service)],
) -> dict[str, Any]:
    return await service.replay(current_user.user_id, run_id)


@router.post("/agent-runs/{run_id}/cancel")
async def cancel_agent_run(
    run_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[AgentQueryService, Depends(get_agent_query_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AgentCancelResponse:
    if not idempotency_key:
        raise AppError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
    run = await service.request_cancel(current_user.user_id, run_id)
    return AgentCancelResponse.model_validate(run, from_attributes=True)


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(
    proposal_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[ProposalService, Depends(get_proposal_service)],
    request: Annotated[ProposalDecisionRequest | None, Body()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ProposalDecisionResponse:
    return await _decide(
        proposal_id,
        current_user,
        service,
        request or ProposalDecisionRequest(),
        idempotency_key,
        True,
    )


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(
    proposal_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[ProposalService, Depends(get_proposal_service)],
    request: Annotated[ProposalDecisionRequest | None, Body()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ProposalDecisionResponse:
    return await _decide(
        proposal_id,
        current_user,
        service,
        request or ProposalDecisionRequest(),
        idempotency_key,
        False,
    )


@router.post("/agent-runs/{run_id}/shadow-evaluations", status_code=status.HTTP_202_ACCEPTED)
async def create_shadow_evaluation(
    run_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[ShadowEvaluationService, Depends(get_shadow_evaluation_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object]:
    if not idempotency_key:
        raise AppError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
    evaluation, job = await service.request(current_user.user_id, run_id, idempotency_key)
    return {
        "evaluation": ShadowEvaluationResponse.model_validate(
            evaluation, from_attributes=True
        ).model_dump(mode="json"),
        "job_id": job.id,
    }


@router.get("/shadow-evaluations/{evaluation_id}")
async def get_shadow_evaluation(
    evaluation_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[ShadowEvaluationService, Depends(get_shadow_evaluation_service)],
) -> ShadowEvaluationResponse:
    evaluation = await service.get_owned(current_user.user_id, evaluation_id)
    return ShadowEvaluationResponse.model_validate(evaluation, from_attributes=True)


async def _decide(
    proposal_id: uuid.UUID,
    current_user: CurrentUser,
    service: ProposalService,
    request: ProposalDecisionRequest,
    idempotency_key: str | None,
    approve: bool,
) -> ProposalDecisionResponse:
    if not idempotency_key:
        raise AppError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
    proposal, job = await service.decide(
        current_user.user_id,
        proposal_id,
        approve,
        idempotency_key,
        request.reason,
    )
    return ProposalDecisionResponse(
        proposal=ProposalResponse.model_validate(proposal, from_attributes=True),
        job_id=job.id if job else None,
    )
