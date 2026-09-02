"""planner plans

Revision ID: a1b2c3d4e5f6
Revises: f9b3c7d1e5a2
Create Date: 2026-09-02 00:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f9b3c7d1e5a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "planner_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("farm_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("start_year_month", sa.String(length=7), nullable=False),
        sa.Column("targets", sa.Text(), nullable=False),
        sa.Column("assumptions", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_planner_plans_revision_positive"),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["farm_id"],
            ["farms.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("farm_id", "name", name="uq_planner_plan_per_farm"),
    )
    op.create_index(op.f("ix_planner_plans_farm_id"), "planner_plans", ["farm_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_planner_plans_farm_id"), table_name="planner_plans")
    op.drop_table("planner_plans")
