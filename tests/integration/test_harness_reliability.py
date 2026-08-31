from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.domain.enums import AgentRunStatus, JobStatus, UserStatus
from app.harness.contracts import RuntimePhase, RuntimeState, ToolCall
from app.harness.errors import StaleWorkerError
from app.harness.lease import RunLeaseManager
from app.harness.tools import (
    ToolDefinition,
    ToolLedgerStatus,
    ToolRisk,
    ToolSideEffect,
)
from app.infrastructure.adapters.harness_store import DatabaseCheckpointStore
from app.infrastructure.adapters.tool_ledger import DatabaseToolExecutionLedger
from app.infrastructure.db.models import AgentRun, BackgroundJob, Student, User
from app.infrastructure.db.session import engine, session_factory

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION") != "1",
        reason="set RUN_INTEGRATION=1 with PostgreSQL and Redis running",
    ),
]


@pytest.fixture(autouse=True)
async def reset_engine() -> None:
    await engine.dispose()


async def _create_run() -> tuple[uuid.UUID, uuid.UUID]:
    suffix = uuid.uuid4().hex
    async with session_factory() as session:
        user = User(
            phone_lookup_hash=suffix,
            phone_ciphertext="integration-fixture",
            status=UserStatus.ACTIVE,
        )
        session.add(user)
        await session.flush()
        student = Student(user_id=user.id)
        session.add(student)
        await session.flush()
        job = BackgroundJob(
            user_id=user.id,
            job_type="AGENT_DIAGNOSIS",
            status=JobStatus.RUNNING,
            payload={"student_id": str(student.id)},
            idempotency_key=f"integration-{suffix}",
            available_at=datetime.now(UTC),
            started_at=datetime.now(UTC),
        )
        session.add(job)
        await session.flush()
        run = AgentRun(
            job_id=job.id,
            student_id=student.id,
            goal="FEEDBACK_DIAGNOSIS",
            status=AgentRunStatus.RUNNING,
            model_version="fake",
            prompt_version="diagnosis_v3",
            policy_version="agent_policy_v2",
        )
        session.add(run)
        await session.commit()
        return run.id, student.id


async def test_checkpoint_resume_and_fencing_reject_stale_worker() -> None:
    run_id, student_id = await _create_run()
    first = RunLeaseManager(session_factory, run_id, "worker-a", lease_seconds=30)
    lease_a = await first.acquire()
    state = RuntimeState(
        str(run_id), str(student_id), "FEEDBACK_DIAGNOSIS", fencing_token=lease_a.fencing_token
    )
    state.loop_count = 1
    state.phase = RuntimePhase.READY
    store_a = DatabaseCheckpointStore(session_factory, run_id, lease_a.fencing_token)
    await store_a.save(state)

    async with session_factory() as session:
        run = await session.get(AgentRun, run_id, with_for_update=True)
        assert run is not None
        run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    second = RunLeaseManager(session_factory, run_id, "worker-b", lease_seconds=30)
    lease_b = await second.acquire()
    with pytest.raises(StaleWorkerError):
        await store_a.save(state)

    store_b = DatabaseCheckpointStore(session_factory, run_id, lease_b.fencing_token)
    restored = await store_b.load_latest()
    assert restored is not None
    assert restored.loop_count == 1
    assert restored.resumed is True
    restored.fencing_token = lease_b.fencing_token
    restored.tool_call_count = 2
    await store_b.save(restored)
    loaded_again = await store_b.load_latest()
    assert loaded_again is not None
    assert loaded_again.tool_call_count == 2
    await second.release()


async def test_database_tool_ledger_returns_completed_side_effect() -> None:
    run_id, student_id = await _create_run()
    lease_manager = RunLeaseManager(session_factory, run_id, "ledger-worker", lease_seconds=30)
    lease = await lease_manager.acquire()
    ledger = DatabaseToolExecutionLedger(session_factory, run_id, lease.fencing_token)
    state = RuntimeState(
        str(run_id), str(student_id), "FEEDBACK_DIAGNOSIS", fencing_token=lease.fencing_token
    )

    async def proposal(_: dict[str, Any]) -> dict[str, Any]:
        return {"proposal_id": "p1"}

    definition = ToolDefinition(
        "propose_minor_adjustment",
        "proposal",
        {},
        proposal,
        risk=ToolRisk.PROPOSAL,
        side_effect_level=ToolSideEffect.IDEMPOTENT_WRITE,
        idempotency_required=True,
    )
    call = ToolCall(definition.name, {}, "stable-key")
    first = await ledger.begin(state, definition, call, "digest")
    assert first.is_new is True
    await ledger.succeed(state, first.record_id, {"proposal_id": "p1"})
    replay = await ledger.begin(state, definition, call, "digest")

    assert replay.is_new is False
    assert replay.status is ToolLedgerStatus.SUCCEEDED
    assert replay.result == {"proposal_id": "p1"}
    await lease_manager.release()
