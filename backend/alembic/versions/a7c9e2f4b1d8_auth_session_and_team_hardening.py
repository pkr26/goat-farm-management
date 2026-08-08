"""auth session and team hardening

Revision ID: a7c9e2f4b1d8
Revises: f5b1a09c8d7e
Create Date: 2026-08-08 17:30:00.000000+00:00

Adds access-token revocation versioning, fail-closed worker-account
provenance, and refresh-rotation successor tracking. Role deletion becomes
RESTRICT instead of CASCADE so a concurrent role assignment can never delete
the worker's membership as a side effect.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7c9e2f4b1d8"
down_revision: str | Sequence[str] | None = "f5b1a09c8d7e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "farm_memberships",
        sa.Column(
            "account_provisioned_by_farm",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.add_column(
        "refresh_sessions", sa.Column("replacement_jti", sa.String(length=64), nullable=True)
    )

    op.drop_constraint(
        op.f("farm_memberships_role_id_fkey"), "farm_memberships", type_="foreignkey"
    )
    op.create_foreign_key(
        op.f("farm_memberships_role_id_fkey"),
        "farm_memberships",
        "roles",
        ["role_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("farm_memberships_role_id_fkey"), "farm_memberships", type_="foreignkey"
    )
    op.create_foreign_key(
        op.f("farm_memberships_role_id_fkey"),
        "farm_memberships",
        "roles",
        ["role_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_column("refresh_sessions", "replacement_jti")
    op.drop_column("farm_memberships", "account_provisioned_by_farm")
    op.drop_column("users", "token_version")
