"""backfill kidding_records.parity for legacy NULL rows

Revision ID: e0f4a8b2c6d5
Revises: d9e3f7a1b5c4
Create Date: 2026-09-14 00:00:00.000000+00:00

a7b8c9d0e1f2 introduced the server-derived litter index and left legacy rows
NULL ("rather than being back-filled from possibly incomplete history").
Reporting meanwhile learned to treat NULL as unknown, which is the one answer
a farm cannot act on. Recompute parity deterministically: per doe, order her
kidding records by (date, id) and number them from 1. Where two kiddings
share one recorded date, the lower id (insertion order) keeps the earlier
parity — matching the per-animal chronology ordering used everywhere else.

One UPDATE over a tenant-bounded history table; lock_timeout bounds the
queue wait. The column stays nullable (genuinely unknowable future shapes
may still land NULL), but no legacy row keeps the unknown marker.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e0f4a8b2c6d5"
down_revision: str | Sequence[str] | None = "d9e3f7a1b5c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    op.execute(
        sa.text(
            """
            UPDATE kidding_records kr
            SET parity = numbered.parity
            FROM (
                SELECT id,
                       row_number() OVER (PARTITION BY doe_id ORDER BY date, id) AS parity
                FROM kidding_records
            ) numbered
            WHERE kr.id = numbered.id
              AND kr.parity IS NULL
            """
        )
    )


def downgrade() -> None:
    # The recomputed values are indistinguishable from service-derived ones;
    # a downgrade cannot restore the unknown marker without destroying real
    # data, so it deliberately leaves the column populated.
    pass
