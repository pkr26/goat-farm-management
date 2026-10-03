"""Persist one-shot clinical alerts and cross-day per-recipient delivery claims.

Revision ID: f7d1e5f9b4c2
Revises: f6c0e4f8b3d1

No historical alerts or recipient claims are fabricated. Existing log rows
retain their original daily dedupe boundary. New outbox-linked rows retain
their recipient claim across midnight, so deferred delivery cannot resend a
recipient already accepted or ambiguously attempted by a paid provider.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f7d1e5f9b4c2"
down_revision: str | Sequence[str] | None = "f6c0e4f8b3d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "farm_id", sa.Integer(), sa.ForeignKey("farms.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("alert_class", sa.String(30), nullable=False),
        sa.Column("event_key", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("timezone('UTC', now())"),
        ),
        sa.Column(
            "due_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("timezone('UTC', now())"),
        ),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "farm_id", "alert_class", "event_key", name="uq_notification_outbox_event"
        ),
        sa.UniqueConstraint("farm_id", "id", name="uq_notification_outbox_farm_id_id"),
        sa.CheckConstraint(
            "alert_class IN ('SCREENING_FLAG', 'MOVEMENT_RESTRICTION')",
            name="ck_notification_outbox_alert_class",
        ),
        sa.CheckConstraint(
            "btrim(event_key) <> '' AND btrim(message) <> ''", name="ck_notification_outbox_content"
        ),
    )
    op.create_index(
        "ix_notification_outbox_due",
        "notification_outbox",
        ["due_at", "id"],
        postgresql_where=sa.text("completed_at IS NULL"),
    )
    op.add_column("notification_log", sa.Column("outbox_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_notification_log_farm_id_outbox_id",
        "notification_log",
        "notification_outbox",
        ["farm_id", "outbox_id"],
        ["farm_id", "id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_notification_log_outbox_recipient", "notification_log", ["outbox_id", "recipient_id"]
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM notification_outbox) THEN\n"
        "        RAISE EXCEPTION 'Notification downgrade would remove deferred "
        "alerts/delivery claims. Archive and reconcile before coordinated "
        "rollback.';\n"
        "        END IF; END $$"
    )
    op.drop_constraint("uq_notification_log_outbox_recipient", "notification_log", type_="unique")
    op.drop_constraint(
        "fk_notification_log_farm_id_outbox_id", "notification_log", type_="foreignkey"
    )
    op.drop_column("notification_log", "outbox_id")
    op.drop_table("notification_outbox")
