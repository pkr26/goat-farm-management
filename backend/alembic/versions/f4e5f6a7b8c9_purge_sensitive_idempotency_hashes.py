"""purge pre-HMAC sensitive idempotency fingerprints

Revision ID: f4e5f6a7b8c9
Revises: f3d4e5f6a7b8
Create Date: 2026-08-09 01:10:00.000000+00:00

Older worker-create request fingerprints were unkeyed hashes of request bodies
that contain passwords. Delete every such replay record before the keyed
fingerprint implementation is released; retaining them would preserve an
offline password oracle for anyone who can read the database or a backup.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "f3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM idempotency_records WHERE operation = 'team.workers.create'")


def downgrade() -> None:
    # Intentionally irreversible: deleted request/response material cannot be
    # reconstructed safely. A downgrade preserves the secure deletion.
    pass
