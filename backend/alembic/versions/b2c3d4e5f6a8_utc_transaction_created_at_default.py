"""store transactions.created_at defaults in UTC, not server local time

Revision ID: b2c3d4e5f6a8
Revises: a1b2c3d4e5f7
Create Date: 2026-08-09 09:45:00.000000+00:00

Every datetime column in this schema is ``TIMESTAMP WITHOUT TIME ZONE`` holding
naive **UTC** (``app/utils.py`` ``utcnow``), which is why ``b9e1c2d3f4a5`` uses
``timezone('UTC', now())`` for ``loss_recorded_at``.  ``c8f1d3a5e709`` instead
gave ``transactions.created_at`` a ``CURRENT_TIMESTAMP`` server default:
``CURRENT_TIMESTAMP`` is a ``timestamptz``, so assigning it to a naive column
converts through the session's ``TimeZone`` GUC and stores the database
server's *local* wall clock.  The default survived to head, so any writer that
omits the column (a manual INSERT, a data import, a future service) silently
mixes two clock conventions into one column.

Only the default is corrected here.  Rows that ``c8f1d3a5e709`` backfilled
cannot be repaired safely: the offset that applied when that revision ran is
not recorded anywhere, and rewriting timestamps by today's offset would corrupt
correct rows on a server whose ``TimeZone`` is already UTC.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b2c3d4e5f6a8"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE transactions ALTER COLUMN created_at SET DEFAULT timezone('UTC', now())"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE transactions ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP")
