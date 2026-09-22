"""Worker-tablet PIN authentication columns

ITEM 2 Phase 1 (2026-09-21 playbook): shared-tablet quick sign-in for farm
workers. ``farm_memberships`` gains an optional Argon2id ``pin_hash`` plus its
``pin_updated_at`` audit timestamp. Everything else about PIN login lives in
application code — the roster, the throttled ``/api/auth/worker-login``
exchange, and the owner-managed provisioning/reset endpoints.

Plain nullable ADD COLUMNs; no rewrite, no data migration.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "d4e6f8a0b2c4"
down_revision = "c3d5e7f9a1b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "farm_memberships",
        sa.Column("pin_hash", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "farm_memberships",
        sa.Column("pin_updated_at", postgresql.TIMESTAMP(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("farm_memberships", "pin_updated_at")
    op.drop_column("farm_memberships", "pin_hash")
