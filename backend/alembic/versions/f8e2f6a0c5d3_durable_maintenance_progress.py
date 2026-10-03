"""Keep bounded maintenance rotations fair across worker restarts.

Revision ID: f8e2f6a0c5d3
Revises: f7d1e5f9b4c2

The two initial checkpoints claim no completed work. A checkpoint transaction
advances only after independently committed, idempotent farm work. A crash can
repeat a page, but cannot advance past work that was never attempted. Historical
farm IDs remain valid positions even after a farm is deleted, so no farm FK is
appropriate here.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f8e2f6a0c5d3"
down_revision: str | Sequence[str] | None = "f7d1e5f9b4c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    progress = op.create_table(
        "maintenance_progress",
        sa.Column("job_name", sa.String(64), primary_key=True),
        sa.Column("after_farm_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_hour", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("after_farm_id >= 0", name="ck_maintenance_progress_cursor_nonnegative"),
        sa.CheckConstraint(
            "job_name IN ('cadence', 'notification_alerts')", name="ck_maintenance_progress_job"
        ),
    )
    op.bulk_insert(progress, [{"job_name": "cadence"}, {"job_name": "notification_alerts"}])


def downgrade() -> None:
    # This table is a retry checkpoint, never a business/delivery receipt.
    # With workers stopped for the coordinated rollback, losing the position
    # only repeats idempotent work; domain facts and outbox claims remain intact.
    op.drop_table("maintenance_progress")
