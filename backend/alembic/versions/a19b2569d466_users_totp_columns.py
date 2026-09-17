"""users: TOTP second-factor columns (HUM-1 follow-up, 2026-09-16)

Adds the three nullable columns backing optional TOTP two-factor
authentication: the AES-GCM encrypted shared secret (never plaintext; the
key is derived from the JWT signing key, so a database dump alone cannot
recover second-factor material), the enrollment state (NULL / PENDING /
ACTIVE — PENDING is enroll-started-but-unconfirmed and is not demanded at
login), and the replay high-water step (a verified code never re-validates).

All columns are nullable with no backfill: every existing account simply
reports "not enrolled". CHECK constraints keep secret and state paired and
the state inside its vocabulary.

Revision ID: a19b2569d466
Revises: e0f4a8b2c6d5
Create Date: 2026-09-16 00:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a19b2569d466"
down_revision: str | None = "e0f4a8b2c6d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("totp_secret_enc", sa.LargeBinary(), nullable=True))
    op.add_column(
        "users",
        sa.Column("totp_state", sa.String(length=7), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("totp_last_step", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_users_totp_state",
        "users",
        "totp_state IS NULL OR totp_state IN ('PENDING', 'ACTIVE')",
    )
    op.create_check_constraint(
        "ck_users_totp_secret_pairs_with_state",
        "users",
        "(totp_secret_enc IS NULL) = (totp_state IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_totp_secret_pairs_with_state", "users", type_="check")
    op.drop_constraint("ck_users_totp_state", "users", type_="check")
    op.drop_column("users", "totp_last_step")
    op.drop_column("users", "totp_state")
    op.drop_column("users", "totp_secret_enc")
