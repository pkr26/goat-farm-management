"""Audited movement-restriction clearance.

Revision ID: d9e4b7c2a610
Revises: c8f1d3a5e709
Create Date: 2026-08-08 22:30:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d9e4b7c2a610"
down_revision: str | Sequence[str] | None = "c8f1d3a5e709"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("animals", sa.Column("restriction_cleared_at", sa.DateTime(), nullable=True))
    op.add_column("animals", sa.Column("restriction_cleared_by_id", sa.Integer(), nullable=True))
    op.add_column(
        "animals",
        sa.Column("restriction_clearance_reference", sa.String(length=255), nullable=True),
    )
    op.create_foreign_key(
        "fk_animals_restriction_cleared_by_id",
        "animals",
        "users",
        ["restriction_cleared_by_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_animals_restriction_cleared_by_id", "animals", type_="foreignkey")
    op.drop_column("animals", "restriction_clearance_reference")
    op.drop_column("animals", "restriction_cleared_by_id")
    op.drop_column("animals", "restriction_cleared_at")
