"""merge independent screening and finance integrity revisions

Revision ID: b7e8f9a0c1d2
Revises: a6d4e2f9c8b7, c4d8e1f9a2b7
Create Date: 2026-09-17 16:35:00.000000+00:00

The screening upload hardening and insurance/planner corrections touch
independent tables, so they can safely share the prior head and converge here.
Keeping an explicit merge revision ensures ``alembic upgrade head`` remains a
single, deterministic deployment path.
"""

from collections.abc import Sequence

revision: str = "b7e8f9a0c1d2"
down_revision: str | Sequence[str] | None = ("a6d4e2f9c8b7", "c4d8e1f9a2b7")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
