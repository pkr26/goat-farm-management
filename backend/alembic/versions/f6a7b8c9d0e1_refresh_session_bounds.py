"""bound refresh-session families, rotation history and expiry cleanup

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-08-08 18:05:00.000000+00:00

Refresh sessions are ephemeral security state. The one-time compaction logs
out only families/rotations beyond the new documented defaults; it cannot
delete farm or audit data. New signed family claims preserve replay-triggered
family revocation after an old consumed-session row is compacted.
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # First retain the ten newest families for each user, then the 1,024
    # newest rotations in each retained family. This establishes the ceiling
    # that request-time one-row/one-family eviction preserves thereafter.
    op.execute(
        text(
            """
            WITH ranked_families AS (
                SELECT
                    user_id,
                    family_id,
                    row_number() OVER (
                        PARTITION BY user_id
                        ORDER BY max(created_at) DESC, family_id
                    ) AS family_rank
                FROM refresh_sessions
                GROUP BY user_id, family_id
            ), evicted AS (
                SELECT user_id, family_id
                FROM ranked_families
                WHERE family_rank > 10
            )
            DELETE FROM refresh_sessions AS session
            USING evicted
            WHERE session.user_id = evicted.user_id
              AND session.family_id = evicted.family_id
            """
        )
    )
    op.execute(
        text(
            """
            WITH ranked_sessions AS (
                SELECT
                    id,
                    row_number() OVER (
                        PARTITION BY family_id
                        ORDER BY created_at DESC, id DESC
                    ) AS session_rank
                FROM refresh_sessions
            )
            DELETE FROM refresh_sessions AS session
            USING ranked_sessions
            WHERE session.id = ranked_sessions.id
              AND ranked_sessions.session_rank > 1024
            """
        )
    )

    op.drop_index("ix_refresh_sessions_family_id", table_name="refresh_sessions")
    op.drop_index("ix_refresh_sessions_user_id", table_name="refresh_sessions")
    op.create_index(
        "ix_refresh_sessions_expires_id",
        "refresh_sessions",
        ["expires_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_refresh_sessions_family_created_id",
        "refresh_sessions",
        ["family_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_refresh_sessions_user_created_id",
        "refresh_sessions",
        ["user_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_breeding_records_doe_date_id",
        "breeding_records",
        ["doe_id", "breeding_date", "id"],
        unique=False,
    )
    op.create_index(
        "ix_tasks_farm_animal_pending",
        "tasks",
        ["farm_id", "animal_id"],
        unique=False,
        postgresql_where=text("status = 'PENDING' AND animal_id IS NOT NULL"),
    )


def downgrade() -> None:
    # Compacted, expired/evicted authentication sessions are intentionally not
    # resurrected. Downgrade restores the previous index shape only.
    op.drop_index("ix_tasks_farm_animal_pending", table_name="tasks")
    op.drop_index("ix_breeding_records_doe_date_id", table_name="breeding_records")
    op.drop_index("ix_refresh_sessions_user_created_id", table_name="refresh_sessions")
    op.drop_index("ix_refresh_sessions_family_created_id", table_name="refresh_sessions")
    op.drop_index("ix_refresh_sessions_expires_id", table_name="refresh_sessions")
    op.create_index(
        "ix_refresh_sessions_user_id",
        "refresh_sessions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_refresh_sessions_family_id",
        "refresh_sessions",
        ["family_id"],
        unique=False,
    )
