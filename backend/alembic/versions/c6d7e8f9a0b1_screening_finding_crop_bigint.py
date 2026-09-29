"""screening run/crop FK columns int4 -> bigint (2026-09-28 audit, D1 part 2)

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-09-28 00:00:00.000000+00:00

``e2f3a4b5c6d8`` widened the three ``image_id`` columns after the screening
fact-table PKs went bigint, but the columns pointing at the OTHER bigint
PKs stayed int4: ``screening_findings.run_id`` (references the bigint
``screening_runs.id``) and the two ``crop_id`` columns (reference the bigint
``screening_crops.id``). Once a run or crop id exceeds 2^31-1 — the ceiling
the PK widenings call "a live constraint" — every finding/crop-linked
insert dies with ``integer out of range``. Same fix, same reason, while the
tables are small. The composite tenant FKs stay valid across the retype
(bigint now references bigint), so no constraint rebuild is needed.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c6d7e8f9a0b1"
down_revision: str | Sequence[str] | None = "b5c6d7e8f9a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column) — FK column widened to bigint, mirroring the image_id
# widening in e2f3a4b5c6d8. Plain ALTER COLUMN: int4 -> int8 widens
# implicitly, and the tables are small enough that the brief lock is a
# non-event.
BIGINT_FK_COLUMNS = (
    ("screening_findings", "run_id"),
    ("screening_findings", "crop_id"),
    ("screening_runs", "crop_id"),
)


def upgrade() -> None:
    for table, column in BIGINT_FK_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            postgresql_using=f"{column}::bigint",
        )


def downgrade() -> None:
    for table, column in BIGINT_FK_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            postgresql_using=f"{column}::integer",
        )
