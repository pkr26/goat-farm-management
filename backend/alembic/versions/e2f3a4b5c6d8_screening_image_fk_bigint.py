"""screening image FK columns int4 -> bigint (2026-09-28 audit, D1)

Revision ID: e2f3a4b5c6d8
Revises: d7e9f1a3b5c7
Create Date: 2026-09-28 00:00:00.000000+00:00

``b9d1f3a5c7e9`` and ``f6b8d0e2a4c6`` widened the screening fact-table PKs
to bigint but left the referencing columns int4: once a ``screening_images``
id exceeds 2^31-1 — the ceiling those revisions call "a live constraint" —
every crop/run/claim insert dies with ``integer out of range``. The three
``image_id`` columns widen the same way the PKs did, while the tables are
small. The composite tenant FKs stay valid across the retype (bigint now
references bigint), so no constraint rebuild is needed.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e2f3a4b5c6d8"
down_revision: str | Sequence[str] | None = "d7e9f1a3b5c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table) — image_id FK column widened to bigint, mirroring the PK widening
# in b9d1f3a5c7e9. Plain ALTER COLUMN: int4 -> int8 widens implicitly, and
# the tables are small enough that the brief lock is a non-event.
BIGINT_IMAGE_FK_TABLES = (
    "screening_runs",
    "screening_crops",
    "screening_content_claims",
)


def upgrade() -> None:
    for table in BIGINT_IMAGE_FK_TABLES:
        op.alter_column(
            table,
            "image_id",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            postgresql_using="image_id::bigint",
        )


def downgrade() -> None:
    for table in BIGINT_IMAGE_FK_TABLES:
        op.alter_column(
            table,
            "image_id",
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            postgresql_using="image_id::integer",
        )
