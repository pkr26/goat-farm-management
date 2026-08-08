"""refresh_sessions — server-side refresh-token revocation

Revision ID: b3e91c47a2f5
Revises: 91a712b0367b
Create Date: 2026-08-07 16:30:00.000000+00:00

Audit findings HIGH 0-1 / MEDIUM 0-2 / MEDIUM 1-1: refresh tokens were
stateless JWTs with no server-side record, so logout, owner-initiated worker
password resets, and rotation itself terminated nothing, and a stolen
pre-rotation token kept working for the full 14-day TTL. Persist one row per
issued refresh token (jti, rotation family, expiry, consumed/revoked marks)
so /refresh can consume-and-rotate, replay of a rotated jti can revoke the
whole family, and reset/logout/deactivation can kill sessions outright.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b3e91c47a2f5"
down_revision: str | Sequence[str] | None = "91a712b0367b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "refresh_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("family_id", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_refresh_sessions_user_id"), "refresh_sessions", ["user_id"])
    op.create_index(op.f("ix_refresh_sessions_jti"), "refresh_sessions", ["jti"], unique=True)
    op.create_index(op.f("ix_refresh_sessions_family_id"), "refresh_sessions", ["family_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_refresh_sessions_family_id"), table_name="refresh_sessions")
    op.drop_index(op.f("ix_refresh_sessions_jti"), table_name="refresh_sessions")
    op.drop_index(op.f("ix_refresh_sessions_user_id"), table_name="refresh_sessions")
    op.drop_table("refresh_sessions")
