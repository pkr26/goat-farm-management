"""UTC server defaults on the remaining timestamp columns (D7 completion)

Revision ID: d9e0f2a4b6c8
Revises: c7d8e9f0a1b2
Create Date: 2026-09-29 00:00:00.000000+00:00

``b5c6d7e8f9a0`` finished the newest tables; eleven NOT NULL timestamp
columns on the founding tables still carry no server default, so a
direct-SQL insert must know the application's UTC convention while every
sibling table does not (2026-09-28 audit, D7; 2026-09-29 completion).
Metadata-only change: SET DEFAULT rewrites no rows.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d9e0f2a4b6c8"
down_revision: str | Sequence[str] | None = "c7d8e9f0a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column) — NOT NULL timestamps gaining the standardized UTC server
# default, the exact set information_schema reports without one at
# c7d8e9f0a1b2 (verified 2026-09-29).
SERVER_DEFAULT_COLUMNS = (
    ("animals", "created_at"),
    ("farm_memberships", "created_at"),
    ("farms", "created_at"),
    ("idempotency_records", "created_at"),
    ("planner_plans", "created_at"),
    ("planner_plans", "updated_at"),
    ("refresh_sessions", "created_at"),
    ("roles", "created_at"),
    ("simulation_scenarios", "created_at"),
    ("simulation_scenarios", "updated_at"),
    ("users", "created_at"),
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
