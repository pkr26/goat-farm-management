"""bound the screening retry budget per image

Revision ID: b9c0d1e2f3a4
Revises: f7a9c1e3b5d7
Create Date: 2026-09-18 15:40:00.000000+00:00

Every claim of a ``screening_images`` row now increments a persisted attempt
counter.  Rows that reach the pipeline's attempt cap are never re-claimed, so
a deterministic failure (a degenerate crop box, a provider contract the
rotation cannot serve, an object deleted out from under the row by a bucket
lifecycle rule) terminates instead of retrying hourly forever — each retry
re-downloaded up to 25 MB and re-billed gate/specialist calls.  Existing rows
start at zero attempts, preserving their current retry eligibility.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b9c0d1e2f3a4"
down_revision: str | Sequence[str] | None = "f7a9c1e3b5d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "screening_images",
        sa.Column("screening_attempts", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("screening_images", "screening_attempts")
