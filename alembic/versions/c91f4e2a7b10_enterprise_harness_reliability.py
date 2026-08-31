"""enterprise harness reliability

Revision ID: c91f4e2a7b10
Revises: a67d911cf3a2
Create Date: 2026-08-31 03:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c91f4e2a7b10"
down_revision: str | None = "a67d911cf3a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _add_enum_values()

    op.add_column(
        "background_jobs",
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "background_jobs",
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
    )
    op.add_column(
        "background_jobs", sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "background_jobs", sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "ix_background_jobs_next_retry_at", "background_jobs", ["next_retry_at"], unique=False
    )

    for name in ("model_call_count", "input_tokens", "output_tokens", "resumed_count"):
        op.add_column(
            "agent_runs", sa.Column(name, sa.Integer(), server_default="0", nullable=False)
        )
    op.add_column(
        "agent_runs", sa.Column("fencing_token", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "agent_runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "agent_runs", sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "agent_runs", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.add_column(
        "agent_steps",
        sa.Column("prompt_version", sa.String(32), server_default="unknown", nullable=False),
    )
    op.add_column(
        "agent_steps",
        sa.Column("policy_version", sa.String(32), server_default="unknown", nullable=False),
    )
    op.add_column(
        "agent_steps",
        sa.Column("action_type", sa.String(32), server_default="UNKNOWN", nullable=False),
    )
    op.add_column("agent_steps", sa.Column("decision", sa.String(64), nullable=True))
    op.add_column(
        "agent_steps", sa.Column("confidence", sa.Float(), server_default="0", nullable=False)
    )
    op.add_column(
        "agent_steps",
        sa.Column("reason_codes", sa.ARRAY(sa.String()), server_default="{}", nullable=False),
    )
    op.add_column("agent_steps", sa.Column("stall_reason", sa.String(64), nullable=True))

    op.create_table(
        "model_invocations",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("step_id", sa.UUID(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("model_name", sa.String(96), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("latency_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("response_action", sa.JSON(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["step_id"], ["agent_steps.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("step_id", "attempt_number", name="uq_model_invocation_attempt"),
    )
    op.create_index("ix_model_invocations_run_id", "model_invocations", ["run_id"])
    op.create_index("ix_model_invocations_step_id", "model_invocations", ["step_id"])

    op.create_unique_constraint(
        "uq_tool_invocation_step", "tool_invocations", ["run_id", "step_id"]
    )
    op.add_column(
        "tool_invocations",
        sa.Column("risk", sa.String(32), server_default="READ", nullable=False),
    )
    op.add_column(
        "tool_invocations",
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("tool_invocations", sa.Column("error_code", sa.String(64), nullable=True))
    op.add_column(
        "tool_invocations",
        sa.Column("replayed", sa.Boolean(), server_default=sa.false(), nullable=False),
    )

    op.add_column(
        "checkpoints",
        sa.Column("checkpoint_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "checkpoints",
        sa.Column("state_hash", sa.String(64), server_default="legacy", nullable=False),
    )
    op.add_column(
        "checkpoints",
        sa.Column("resume_safe", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "checkpoints",
        sa.Column("fencing_token", sa.Integer(), server_default="0", nullable=False),
    )

    op.create_table(
        "tool_execution_records",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("tool_name", sa.String(96), nullable=False),
        sa.Column("tool_version", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("args_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("result_digest", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "tool_name", "idempotency_key", name="uq_tool_execution_idempotency"
        ),
    )
    op.create_index("ix_tool_execution_records_run_id", "tool_execution_records", ["run_id"])

    op.create_table(
        "guardrail_events",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("step_id", sa.UUID(), nullable=True),
        sa.Column("tool_name", sa.String(96), nullable=True),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["step_id"], ["agent_steps.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_guardrail_events_run_id", "guardrail_events", ["run_id"])

    op.add_column(
        "proposals", sa.Column("approval_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "proposals",
        sa.Column("evidence_snapshot", sa.JSON(), server_default="[]", nullable=False),
    )
    op.add_column("proposals", sa.Column("reviewer_user_id", sa.UUID(), nullable=True))
    op.add_column("proposals", sa.Column("review_reason", sa.Text(), nullable=True))
    op.add_column("proposals", sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("proposals", sa.Column("apply_error_code", sa.String(64), nullable=True))
    op.create_foreign_key(
        "fk_proposals_reviewer_user",
        "proposals",
        "users",
        ["reviewer_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "shadow_evaluations",
        sa.Column("source_run_id", sa.UUID(), nullable=False),
        sa.Column("requested_by_user_id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(32), server_default="QUEUED", nullable=False),
        sa.Column("baseline_model", sa.String(96), nullable=False),
        sa.Column("baseline_prompt_version", sa.String(32), nullable=False),
        sa.Column("baseline_decision", sa.String(64), nullable=True),
        sa.Column("baseline_confidence", sa.Float(), nullable=True),
        sa.Column("candidate_model", sa.String(96), nullable=False),
        sa.Column("candidate_prompt_version", sa.String(32), nullable=False),
        sa.Column("candidate_decision", sa.String(64), nullable=True),
        sa.Column("candidate_confidence", sa.Float(), nullable=True),
        sa.Column("comparison", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["background_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id"),
    )
    op.create_index("ix_shadow_evaluations_source_run_id", "shadow_evaluations", ["source_run_id"])
    op.create_index(
        "ix_shadow_evaluations_requested_by_user_id",
        "shadow_evaluations",
        ["requested_by_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_shadow_evaluations_requested_by_user_id", table_name="shadow_evaluations")
    op.drop_index("ix_shadow_evaluations_source_run_id", table_name="shadow_evaluations")
    op.drop_table("shadow_evaluations")

    op.drop_constraint("fk_proposals_reviewer_user", "proposals", type_="foreignkey")
    for name in (
        "apply_error_code",
        "applied_at",
        "review_reason",
        "reviewer_user_id",
        "approval_expires_at",
        "evidence_snapshot",
    ):
        op.drop_column("proposals", name)

    op.drop_index("ix_guardrail_events_run_id", table_name="guardrail_events")
    op.drop_table("guardrail_events")
    op.drop_index("ix_tool_execution_records_run_id", table_name="tool_execution_records")
    op.drop_table("tool_execution_records")

    for name in ("fencing_token", "resume_safe", "state_hash", "checkpoint_version"):
        op.drop_column("checkpoints", name)

    for name in ("replayed", "error_code", "retry_count", "risk"):
        op.drop_column("tool_invocations", name)
    op.drop_constraint("uq_tool_invocation_step", "tool_invocations", type_="unique")

    op.drop_index("ix_model_invocations_step_id", table_name="model_invocations")
    op.drop_index("ix_model_invocations_run_id", table_name="model_invocations")
    op.drop_table("model_invocations")

    for name in (
        "stall_reason",
        "reason_codes",
        "confidence",
        "decision",
        "action_type",
        "policy_version",
        "prompt_version",
    ):
        op.drop_column("agent_steps", name)

    for name in (
        "cancelled_at",
        "cancel_requested_at",
        "heartbeat_at",
        "fencing_token",
        "resumed_count",
        "output_tokens",
        "input_tokens",
        "model_call_count",
    ):
        op.drop_column("agent_runs", name)

    op.drop_index("ix_background_jobs_next_retry_at", table_name="background_jobs")
    for name in ("dead_lettered_at", "next_retry_at", "max_attempts", "attempt_count"):
        op.drop_column("background_jobs", name)

    _restore_old_enums()


def _add_enum_values() -> None:
    for value in ("RETRY_WAIT", "DEAD_LETTER"):
        op.execute(f"ALTER TYPE jobstatus ADD VALUE IF NOT EXISTS '{value}'")
    for value in ("CANCEL_REQUESTED", "CANCELLED"):
        op.execute(f"ALTER TYPE agentrunstatus ADD VALUE IF NOT EXISTS '{value}'")
    for value in ("APPLYING", "APPLIED", "APPLY_FAILED"):
        op.execute(f"ALTER TYPE proposalstatus ADD VALUE IF NOT EXISTS '{value}'")


def _restore_old_enums() -> None:
    op.execute("UPDATE background_jobs SET status='FAILED' WHERE status::text IN ('RETRY_WAIT','DEAD_LETTER')")
    op.execute("UPDATE agent_runs SET status='FAILED' WHERE status::text IN ('CANCEL_REQUESTED','CANCELLED')")
    op.execute("UPDATE proposals SET status='APPROVED' WHERE status::text IN ('APPLYING','APPLIED','APPLY_FAILED')")
    _replace_enum(
        "background_jobs",
        "jobstatus",
        ("QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "WAITING_FOR_REVIEW"),
    )
    _replace_enum(
        "agent_runs",
        "agentrunstatus",
        ("PENDING", "RUNNING", "COMPLETED", "FAILED", "STALLED"),
    )
    _replace_enum(
        "proposals",
        "proposalstatus",
        (
            "PENDING",
            "AUTO_COMMITTED",
            "AWAITING_CONFIRMATION",
            "APPROVED",
            "REJECTED",
            "EXPIRED",
        ),
    )


def _replace_enum(table: str, enum_name: str, values: tuple[str, ...]) -> None:
    op.execute(f'ALTER TABLE "{table}" ALTER COLUMN status TYPE VARCHAR(32) USING status::text')
    op.execute(f"DROP TYPE {enum_name}")
    literals = ", ".join(f"'{value}'" for value in values)
    op.execute(f"CREATE TYPE {enum_name} AS ENUM ({literals})")
    op.execute(
        f'ALTER TABLE "{table}" ALTER COLUMN status TYPE {enum_name} '
        f"USING status::text::{enum_name}"
    )
