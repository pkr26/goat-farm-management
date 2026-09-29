"""UTC server defaults on the newest tables' timestamps (2026-09-28 audit, D7)

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-09-28 00:00:00.000000+00:00

The housekeeping wave (``f6b8d0e2a4c6``) standardized
``server_default=timezone('UTC', now())`` on six sibling tables, but the
newest tables — ``notification_recipients``, ``notification_log`` and
``totp_recovery_codes`` — carry NOT NULL timestamp columns with no server
default, so direct-SQL inserts must know the application convention. Add
the same server default to ``created_at`` on all three and to
``notification_recipients.updated_at`` (the only ``updated_at`` among
them). Metadata-only change: SET DEFAULT rewrites no rows.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b5c6d7e8f9a0"
down_revision: str | Sequence[str] | None = "a4b5c6d7e8f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column) — NOT NULL timestamps gaining the standardized UTC server
# default, mirroring f6b8d0e2a4c6's sa.text("timezone('UTC', now())").
SERVER_DEFAULT_COLUMNS = (
    ("notification_recipients", "created_at"),
    ("notification_recipients", "updated_at"),
    ("notification_log", "created_at"),
    ("totp_recovery_codes", "created_at"),
)


def upgrade() -> None:
    for table, column in SERVER_DEFAULT_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(),
            existing_nullable=False,
            server_default=sa.text("timezone('UTC', now())"),
        )


def downgrade() -> None:
    for table, column in SERVER_DEFAULT_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(),
            existing_nullable=False,
            server_default=None,
        )
