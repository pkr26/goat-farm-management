"""Durable single-use guard for MFA challenge tokens (2026-10-01 audit, 01-3)

Revision ID: f4a8c2e6d1b9
Revises: e7b9d1f3a5c2
Create Date: 2026-10-02 00:00:00.000000+00:00

The consumed-jti replay cache for TOTP challenge tokens lived in process
memory, so a restart inside the 300 s challenge TTL allowed exactly one
replay of an already-consumed token (accepted tradeoff, 2026-09-28 audit
S5). Decided 2026-10-02 per RFC 6238's verifier-MUST-detect-replay
requirement: the single-use marker moves to this table, claimed with
INSERT ... ON CONFLICT DO NOTHING inside the login-success transaction, so
consumption is durable across restarts and correct under concurrent replay
(the unique jti arbitrates exactly one winner; the loser's insert blocks
until the winner commits and then sees the conflict).

The table starts empty on upgrade — no in-memory state can be migrated, and
none needs to be: any challenge issued before the deploy is either inside
its TTL (consumption is checked fresh per exchange) or already expired.
Downgrade drops the table and returns to the per-process guard.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f4a8c2e6d1b9"
down_revision: str | Sequence[str] | None = "e7b9d1f3a5c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "consumed_mfa_challenges",
        sa.Column("jti", sa.String(length=64), primary_key=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column(
            "consumed_at",
            sa.DateTime(),
            server_default=sa.text("timezone('UTC', now())"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("consumed_mfa_challenges")
