"""Bind PIN refresh families to the authenticating farm and membership.

Revision ID: f5b9d3e7a2c0
Revises: f4a8c2e6d1b9

Existing session origins cannot be recovered from historical rows. They stay
NULL and the scv=2 authentication contract requires reauthentication at
cutover. Do not label them PASSWORD: that would elevate a legacy PIN token.
Downgrading loses this binding and requires an application rollback together
with revocation of all sessions; it is not a safe rolling downgrade.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f5b9d3e7a2c0"
down_revision: str | Sequence[str] | None = "f4a8c2e6d1b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("refresh_sessions", sa.Column("session_origin", sa.String(8), nullable=True))
    op.add_column("refresh_sessions", sa.Column("farm_id", sa.Integer(), nullable=True))
    op.add_column("refresh_sessions", sa.Column("membership_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_refresh_sessions_farm_id_farms",
        "refresh_sessions",
        "farms",
        ["farm_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_refresh_sessions_farm_membership",
        "refresh_sessions",
        "farm_memberships",
        ["farm_id", "membership_id"],
        ["farm_id", "id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        "ck_refresh_sessions_origin_scope",
        "refresh_sessions",
        (
            "session_origin IS NULL OR (session_origin = 'PASSWORD' AND farm_id IS "
            "NULL AND membership_id IS NULL) OR (session_origin = 'PIN' AND farm_id "
            "IS NOT NULL AND membership_id IS NOT NULL)"
        ),
    )


def downgrade() -> None:
    op.drop_constraint("ck_refresh_sessions_origin_scope", "refresh_sessions", type_="check")
    op.drop_constraint(
        "fk_refresh_sessions_farm_membership", "refresh_sessions", type_="foreignkey"
    )
    op.drop_constraint("fk_refresh_sessions_farm_id_farms", "refresh_sessions", type_="foreignkey")
    op.drop_column("refresh_sessions", "membership_id")
    op.drop_column("refresh_sessions", "farm_id")
    op.drop_column("refresh_sessions", "session_origin")
