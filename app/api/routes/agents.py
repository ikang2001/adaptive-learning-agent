from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header

from app.api.dependencies import (
    get_agent_query_service,
    get_current_user,
    get_proposal_service,
)
from app.api.schemas import (
    AgentRunResponse,
    AgentStepResponse,
    ProposalDecisionResponse,
    ProposalResponse,
    ToolInvocationResponse,
)
from app.application.agent_runs import AgentQueryService, ProposalService
from app.application.auth import CurrentUser
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
        tool_call_count=run.tool_call_count,
        termination_reason=run.termination_reason,
        steps=[AgentStepResponse.model_validate(item, from_attributes=True) for item in steps],
        tools=[ToolInvocationResponse.model_validate(item, from_attributes=True) for item in tools],
        proposals=[
            ProposalResponse.model_validate(item, from_attributes=True) for item in proposals
        ],
    )


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(
    proposal_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[ProposalService, Depends(get_proposal_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ProposalDecisionResponse:
    return await _decide(proposal_id, current_user, service, idempotency_key, True)


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(
    proposal_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[ProposalService, Depends(get_proposal_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ProposalDecisionResponse:
    return await _decide(proposal_id, current_user, service, idempotency_key, False)


async def _decide(
    proposal_id: uuid.UUID,
    current_user: CurrentUser,
    service: ProposalService,
    idempotency_key: str | None,
    approve: bool,
) -> ProposalDecisionResponse:
    if not idempotency_key:
        raise AppError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
    proposal, job = await service.decide(
        current_user.user_id, proposal_id, approve, idempotency_key
    )
    return ProposalDecisionResponse(
        proposal=ProposalResponse.model_validate(proposal, from_attributes=True),
        job_id=job.id if job else None,
    )
