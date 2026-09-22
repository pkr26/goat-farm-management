"""Notification recipients and delivery log (ITEM 4, 2026-09-21 playbook)

Daily digest + same-day alerts over SMS (MSG91 first; console provider for
dev). ``notification_recipients`` holds membership-scoped phone numbers with
per-alert-class opt-ins; ``notification_log`` is the append-only audit trail
and the (farm, recipient, class, payload, local-day) dedupe ledger.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "e5a7c9d1b3f5"
down_revision = "d4e6f8a0b2c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_recipients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "farm_id",
            sa.Integer(),
            sa.ForeignKey("farms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "membership_id",
            sa.Integer(),
            sa.ForeignKey("farm_memberships.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column("daily_digest", sa.Boolean(), nullable=False),
        sa.Column("screening_flags", sa.Boolean(), nullable=False),
        sa.Column("kidding_watch", sa.Boolean(), nullable=False),
        sa.Column("overdue_critical", sa.Boolean(), nullable=False),
        sa.Column("feed_reorder", sa.Boolean(), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "farm_id", "membership_id", name="uq_notification_recipients_membership"
        ),
    )
    # No separate farm_id single: the (farm_id, daily_digest) composite below
    # serves the enabled-digest scan and farm_id-leading reads.
    op.create_index(
        "ix_notification_recipients_farm_enabled",
        "notification_recipients",
        ["farm_id", "daily_digest"],
    )

    op.create_table(
        "notification_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "farm_id",
            sa.Integer(),
            sa.ForeignKey("farms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recipient_id",
            sa.Integer(),
            sa.ForeignKey("notification_recipients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alert_class", sa.String(length=30), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("local_date", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("provider_message_id", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "farm_id",
            "recipient_id",
            "alert_class",
            "payload_hash",
            "local_date",
            name="uq_notification_log_day_dedupe",
        ),
    )
    op.create_index(
        "ix_notification_log_farm_created",
        "notification_log",
        ["farm_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_notification_log_farm_created", table_name="notification_log")
    op.drop_table("notification_log")
    op.drop_index("ix_notification_recipients_farm_enabled", table_name="notification_recipients")
    op.drop_table("notification_recipients")
