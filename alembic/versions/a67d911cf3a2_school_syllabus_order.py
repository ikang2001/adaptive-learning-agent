"""store target-school syllabus order

Revision ID: a67d911cf3a2
Revises: ed14e801e0dc
Create Date: 2026-08-27 18:42:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a67d911cf3a2"
down_revision: str | None = "ed14e801e0dc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "school_knowledge_stats",
        sa.Column("syllabus_order", sa.Integer(), server_default=sa.text("9999"), nullable=False),
    )
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    stats.school_profile_id,
                    stats.knowledge_id,
                    row_number() OVER (
                        PARTITION BY stats.school_profile_id
                        ORDER BY CASE nodes.code
                            WHEN 'SYS' THEN 1
                            WHEN 'MODEL' THEN 2
                            WHEN 'TRANSFER' THEN 3
                            WHEN 'BLOCK' THEN 4
                            WHEN 'TIME' THEN 5
                            WHEN 'STABILITY' THEN 6
                            WHEN 'ROUTH' THEN 7
                            WHEN 'STEADY' THEN 8
                            WHEN 'ROOT' THEN 9
                            WHEN 'ROOT_RULE' THEN 10
                            WHEN 'BREAK' THEN 11
                            WHEN 'ANGLE' THEN 12
                            WHEN 'FREQ' THEN 13
                            WHEN 'BODE' THEN 14
                            WHEN 'NYQUIST' THEN 15
                            WHEN 'COMP' THEN 16
                            WHEN 'LEAD' THEN 17
                            WHEN 'STATE' THEN 18
                            ELSE 9999
                        END,
                        nodes.code
                    ) AS position
                FROM school_knowledge_stats AS stats
                JOIN knowledge_nodes AS nodes ON nodes.id = stats.knowledge_id
            )
            UPDATE school_knowledge_stats AS stats
            SET syllabus_order = ranked.position
            FROM ranked
            WHERE stats.school_profile_id = ranked.school_profile_id
              AND stats.knowledge_id = ranked.knowledge_id
            """
        )
    )
    op.create_index(
        "ix_school_knowledge_stats_school_syllabus_order",
        "school_knowledge_stats",
        ["school_profile_id", "syllabus_order"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_school_knowledge_stats_school_syllabus_order",
        table_name="school_knowledge_stats",
    )
    op.drop_column("school_knowledge_stats", "syllabus_order")
