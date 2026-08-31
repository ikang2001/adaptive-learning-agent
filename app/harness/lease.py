from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import AgentRunStatus
from app.harness.contracts import RuntimeState
from app.harness.errors import (
    LeaseUnavailableError,
    RunCancelledError,
    StaleWorkerError,
)
from app.infrastructure.db.models import AgentRun


@dataclass(frozen=True, slots=True)
class RunLease:
    run_id: uuid.UUID
    owner: str
    fencing_token: int
    expires_at: datetime


class RunLeaseManager:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        run_id: uuid.UUID,
        owner: str,
        *,
        lease_seconds: int = 45,
        heartbeat_seconds: int = 10,
    ) -> None:
        self._factory = factory
        self._run_id = run_id
        self._owner = owner
        self._lease_seconds = lease_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._lease: RunLease | None = None

    @property
    def lease(self) -> RunLease:
        if self._lease is None:
            raise RuntimeError("run lease has not been acquired")
        return self._lease

    async def acquire(self) -> RunLease:
        now = datetime.now(UTC)
        async with self._factory() as session:
            run = await session.scalar(
                select(AgentRun).where(AgentRun.id == self._run_id).with_for_update()
            )
            if run is None:
                raise RuntimeError("agent run does not exist")
            if run.status in {AgentRunStatus.CANCEL_REQUESTED, AgentRunStatus.CANCELLED}:
                raise RunCancelledError("agent run was cancelled before lease acquisition")
            if (
                run.lease_owner
                and run.lease_owner != self._owner
                and run.lease_expires_at
                and run.lease_expires_at > now
            ):
                raise LeaseUnavailableError("agent run lease is held by another worker")
            run.fencing_token += 1
            run.lease_owner = self._owner
            run.heartbeat_at = now
            run.lease_expires_at = now + timedelta(seconds=self._lease_seconds)
            run.status = AgentRunStatus.RUNNING
            await session.commit()
            self._lease = RunLease(run.id, self._owner, run.fencing_token, run.lease_expires_at)
            return self._lease

    async def renew(self) -> None:
        lease = self.lease
        now = datetime.now(UTC)
        async with self._factory() as session:
            run = await session.scalar(
                select(AgentRun).where(AgentRun.id == self._run_id).with_for_update()
            )
            if run is None:
                raise StaleWorkerError("agent run disappeared")
            _assert_run_fence(run, lease.fencing_token, lease.owner, now)
            if run.status is AgentRunStatus.CANCEL_REQUESTED:
                raise RunCancelledError("agent run cancellation was requested")
            run.heartbeat_at = now
            run.lease_expires_at = now + timedelta(seconds=self._lease_seconds)
            await session.commit()
            self._lease = RunLease(run.id, lease.owner, lease.fencing_token, run.lease_expires_at)

    async def release(self) -> None:
        if self._lease is None:
            return
        lease = self._lease
        async with self._factory() as session:
            run = await session.get(AgentRun, self._run_id, with_for_update=True)
            if (
                run is not None
                and run.fencing_token == lease.fencing_token
                and run.lease_owner == lease.owner
            ):
                run.lease_owner = None
                run.lease_expires_at = None
                await session.commit()
        self._lease = None

    async def assert_active(self, state: RuntimeState) -> None:
        lease = self.lease
        if state.fencing_token != lease.fencing_token:
            raise StaleWorkerError("runtime state has a stale fencing token")
        async with self._factory() as session:
            run = await session.get(AgentRun, self._run_id)
            if run is None:
                raise StaleWorkerError("agent run disappeared")
            if run.status is AgentRunStatus.CANCEL_REQUESTED:
                raise RunCancelledError("agent run cancellation was requested")
            _assert_run_fence(run, lease.fencing_token, lease.owner, datetime.now(UTC))

    @asynccontextmanager
    async def hold(self) -> AsyncIterator[RunLease]:
        lease = await self.acquire()
        stop = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(stop))
        try:
            yield lease
        finally:
            stop.set()
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, RunCancelledError, StaleWorkerError):
                await heartbeat
            await self.release()

    async def _heartbeat(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._heartbeat_seconds)
            except TimeoutError:
                await self.renew()


async def assert_fence(
    session: AsyncSession,
    run_id: uuid.UUID,
    fencing_token: int,
    owner: str | None = None,
) -> AgentRun:
    run = await session.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
    if run is None:
        raise StaleWorkerError("agent run disappeared")
    _assert_run_fence(run, fencing_token, owner, datetime.now(UTC))
    return run


def _assert_run_fence(run: AgentRun, fencing_token: int, owner: str | None, now: datetime) -> None:
    if run.fencing_token != fencing_token:
        raise StaleWorkerError("agent run fencing token is stale")
    if owner is not None and run.lease_owner != owner:
        raise StaleWorkerError("agent run lease owner changed")
    if run.lease_expires_at is None or run.lease_expires_at <= now:
        raise StaleWorkerError("agent run lease expired")
