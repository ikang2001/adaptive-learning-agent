from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.harness.contracts import RuntimeState, ToolCall
from app.harness.errors import ToolValidationError
from app.harness.lease import assert_fence
from app.harness.tools import (
    ToolDefinition,
    ToolExecutionLedger,
    ToolLedgerEntry,
    ToolLedgerStatus,
)
from app.infrastructure.db.models import ToolExecutionRecord


class DatabaseToolExecutionLedger(ToolExecutionLedger):
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        run_id: uuid.UUID,
        fencing_token: int,
    ) -> None:
        self._factory = factory
        self._run_id = run_id
        self._fencing_token = fencing_token

    async def begin(
        self,
        state: RuntimeState,
        definition: ToolDefinition,
        call: ToolCall,
        args_digest: str,
    ) -> ToolLedgerEntry:
        del state
        if call.idempotency_key is None:
            raise ToolValidationError("ledger requires an idempotency key")
        async with self._factory() as session:
            await assert_fence(session, self._run_id, self._fencing_token)
            record = await session.scalar(
                select(ToolExecutionRecord)
                .where(
                    ToolExecutionRecord.run_id == self._run_id,
                    ToolExecutionRecord.tool_name == definition.name,
                    ToolExecutionRecord.idempotency_key == call.idempotency_key,
                )
                .with_for_update()
            )
            if record is not None:
                if record.args_digest != args_digest:
                    raise ToolValidationError(
                        "idempotency key was reused with different tool arguments"
                    )
                return ToolLedgerEntry(
                    record.id,
                    ToolLedgerStatus(record.status),
                    False,
                    record.result,
                )
            record = ToolExecutionRecord(
                run_id=self._run_id,
                tool_name=definition.name,
                tool_version=definition.version,
                idempotency_key=call.idempotency_key,
                args_digest=args_digest,
                status=ToolLedgerStatus.STARTED.value,
                result=None,
                result_digest=None,
                error_code=None,
                started_at=datetime.now(UTC),
                finished_at=None,
                fencing_token=self._fencing_token,
            )
            session.add(record)
            await session.commit()
            return ToolLedgerEntry(record.id, ToolLedgerStatus.STARTED, True)

    async def mark_started(self, state: RuntimeState, record_id: uuid.UUID) -> None:
        await self._update(state, record_id, ToolLedgerStatus.STARTED, None, None)

    async def succeed(
        self, state: RuntimeState, record_id: uuid.UUID, result: dict[str, object]
    ) -> None:
        await self._update(state, record_id, ToolLedgerStatus.SUCCEEDED, result, None)

    async def fail(self, state: RuntimeState, record_id: uuid.UUID, error_code: str) -> None:
        await self._update(state, record_id, ToolLedgerStatus.FAILED, None, error_code)

    async def unknown(self, state: RuntimeState, record_id: uuid.UUID, error_code: str) -> None:
        await self._update(state, record_id, ToolLedgerStatus.UNKNOWN, None, error_code)

    async def _update(
        self,
        state: RuntimeState,
        record_id: uuid.UUID,
        status: ToolLedgerStatus,
        result: dict[str, object] | None,
        error_code: str | None,
    ) -> None:
        del state
        async with self._factory() as session:
            await assert_fence(session, self._run_id, self._fencing_token)
            record = await session.get(ToolExecutionRecord, record_id, with_for_update=True)
            if record is None or record.run_id != self._run_id:
                raise RuntimeError("tool execution record does not exist")
            record.status = status.value
            record.result = result
            record.result_digest = self._digest(result) if result is not None else None
            record.error_code = error_code
            record.fencing_token = self._fencing_token
            if status is ToolLedgerStatus.STARTED:
                record.started_at = datetime.now(UTC)
                record.finished_at = None
            else:
                record.finished_at = datetime.now(UTC)
            await session.commit()

    @staticmethod
    def _digest(value: object) -> str:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()
