"""make normalized screening-content de-duplication concurrency-safe

Revision ID: f7a9c1e3b5d7
Revises: b7e8f9a0c1d2
Create Date: 2026-09-18 13:20:00.000000+00:00

``screening_images`` needs one row for every accepted upload, so a unique
digest on that table would reject useful audit rows.  This companion table
instead reserves a single canonical image per farm-local normalized digest.
The database uniqueness constraint is portable across PostgreSQL and SQLite
and closes the window where two workers normalize identical new keys before
either image becomes terminal.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f7a9c1e3b5d7"
down_revision: str | Sequence[str] | None = "b7e8f9a0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite's CURRENT_TIMESTAMP is UTC; PostgreSQL needs the explicit
    # conversion because this schema stores naive UTC timestamps.
    created_at_default = (
        "timezone('UTC', now())"
        if op.get_bind().dialect.name == "postgresql"
        else "CURRENT_TIMESTAMP"
    )
    op.create_table(
        "screening_content_claims",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("farm_id", sa.Integer(), nullable=False),
        sa.Column("image_id", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text(created_at_default),
            nullable=False,
        ),
        sa.CheckConstraint("length(sha256) = 64", name="ck_screening_content_claims_sha256_length"),
        sa.ForeignKeyConstraint(
            ["farm_id", "image_id"],
            ["screening_images.farm_id", "screening_images.id"],
            name="fk_screening_content_claims_image",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("farm_id", "sha256", name="uq_screening_content_claims_farm_sha256"),
        sa.UniqueConstraint("farm_id", "image_id", name="uq_screening_content_claims_farm_image"),
    )

    # Preserve the canonical owner for rows screened before this revision.
    # The correlated lower-id predicate is standard SQL (no PostgreSQL-only
    # DISTINCT ON / window function) and gives every existing duplicate set a
    # stable owner without modifying its audit rows.
    op.execute(
        """
        INSERT INTO screening_content_claims (farm_id, image_id, sha256)
        SELECT image.farm_id, image.id, image.sha256
        FROM screening_images AS image
        WHERE image.sha256 IS NOT NULL
          AND length(image.sha256) = 64
          AND NOT EXISTS (
              SELECT 1
              FROM screening_images AS earlier
              WHERE earlier.farm_id = image.farm_id
                AND earlier.sha256 = image.sha256
                AND earlier.id < image.id
          )
        """
    )


def downgrade() -> None:
    op.drop_table("screening_content_claims")
