"""Add the append-only transactional security-event ledger.

Revision ID: fa1b2c3d4e5f
Revises: f9a3b7c1d5e2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "fa1b2c3d4e5f"
down_revision: str | Sequence[str] | None = "f9a3b7c1d5e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "security_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("event", sa.String(120), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "targets",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("timezone('UTC', now())"),
        ),
        sa.CheckConstraint("btrim(event) <> ''", name="ck_security_events_event_nonblank"),
        sa.CheckConstraint("btrim(summary) <> ''", name="ck_security_events_summary_nonblank"),
    )
    op.create_index("ix_security_events_occurred_id", "security_events", ["occurred_at", "id"])
    op.create_index(
        "ix_security_events_event_occurred", "security_events", ["event", "occurred_at"]
    )
    op.execute(
        """
        CREATE FUNCTION reject_security_event_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'security_events is append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER security_events_append_only
        BEFORE UPDATE OR DELETE ON security_events
        FOR EACH ROW EXECUTE FUNCTION reject_security_event_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM security_events) THEN
                RAISE EXCEPTION 'security_events is nonempty; archive evidence before downgrade';
            END IF;
        END $$
        """
    )
    op.execute("DROP TRIGGER security_events_append_only ON security_events")
    op.execute("DROP FUNCTION reject_security_event_mutation()")
    op.drop_table("security_events")
