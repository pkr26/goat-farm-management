"""TOTP recovery codes (owner lockout protection)

ITEM 7 (2026-09-21 playbook): the TOTP second factor had one unrecoverable
failure mode — losing every enrolled device locked the owner out permanently,
because the product deliberately has no email-based account recovery. Ten
single-use Argon2-hashed recovery codes are minted at enrollment confirmation
(or an explicit password + TOTP-proof regeneration), shown exactly once, and
redeemable at the login TOTP challenge. A redemption is a security event the
operator can alert on.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c3d5e7f9a1b3"
down_revision = "b1c3d5e7f9a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "totp_recovery_codes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code_hash", sa.String(length=255), nullable=False),
        sa.Column("used_at", postgresql.TIMESTAMP(), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # No separate user_id single index: the (user_id, used_at, id)
    # composite below is full and already serves user_id-leading scans.
    op.create_index(
        "ix_totp_recovery_codes_user_used",
        "totp_recovery_codes",
        ["user_id", "used_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_totp_recovery_codes_user_used", table_name="totp_recovery_codes")
    op.drop_table("totp_recovery_codes")
