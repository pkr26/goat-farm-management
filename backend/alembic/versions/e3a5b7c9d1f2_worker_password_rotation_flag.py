"""worker password rotation flag

Owner-provisioned worker credentials (worker create and owner-initiated
password reset) now carry ``users.must_change_password``: the worker must
complete a self-service password change before any domain mutation is
accepted, so the owner cannot permanently hold (or re-acquire) the credential
that worker-attributed records are signed with. Existing rows default to
false — no user is locked out by the migration.

Revision ID: e3a5b7c9d1f2
Revises: d1e2f3a4b5c6
"""

import sqlalchemy as sa
from alembic import op

revision = "e3a5b7c9d1f2"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
