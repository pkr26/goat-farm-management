"""screening_images.bucket varchar(30) -> varchar(20) (2026-09-28 audit)

Revision ID: e8f9a0b1c2d3
Revises: b5c6d7e8f9a0
Create Date: 2026-09-28 00:00:00.000000+00:00

Every other bucket column in the schema is varchar(20); this one carried a
historic varchar(30). The contents are pinned by
``ck_screening_images_bucket_vocabulary`` to the Bucket enum, whose longest
value is 15 chars (PREGNANCY_EARLY), so the narrowing can never truncate a
live value — PostgreSQL's own verifying scan is the backstop, no data
preflight needed. ``alembic check`` compares column types, so the model's
String(20) needs the column to match; the alternative (keeping 30 in the
model) would have left the one odd width undocumented drift bait.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e8f9a0b1c2d3"
down_revision: str | Sequence[str] | None = "b5c6d7e8f9a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "screening_images",
        "bucket",
        existing_type=sa.String(length=30),
        type_=sa.String(length=20),
        postgresql_using="bucket::varchar(20)",
    )


def downgrade() -> None:
    op.alter_column(
        "screening_images",
        "bucket",
        existing_type=sa.String(length=20),
        type_=sa.String(length=30),
        postgresql_using="bucket::varchar(30)",
    )
