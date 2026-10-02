"""Server-side parity for out-of-band writers (2026-10-01 audit, 04-2 + 04-Info)

Revision ID: e7b9d1f3a5c2
Revises: c1d3e5f7a9b4
Create Date: 2026-10-01 00:00:00.000000+00:00

Two small parity gaps where the ORM's Python-side guarantees had no database
counterpart for writers that bypass SQLAlchemy:

* ``screening_images.updated_at`` is the worker pipeline's lease/retry
  marker and the one ``updated_at`` column that both feeds staleness logic
  and receives direct SQL UPDATEs in production (the pipeline's Core
  sweeps). SQLAlchemy's column ``onupdate`` already refreshes it on ORM
  flushes AND Core ``update()`` statements, but a raw-SQL or future
  migration UPDATE would leave it frozen. A BEFORE UPDATE trigger closes
  that hole. The trigger bumps the column ONLY when the statement did not
  set it itself (``NEW.updated_at IS NOT DISTINCT FROM OLD.updated_at``),
  so the app layer's explicit values — ORM ``onupdate`` and the pipeline's
  lease touch — keep deciding the timestamp exactly as before.

  animals/farms deliberately get NO trigger: their ``updated_at`` is
  display/audit metadata, no production writer targets them with raw SQL,
  and concurrency uses explicit ``revision`` columns elsewhere. The
  ORM-layer-only refresh contract is documented at those models instead.

* ``notification_recipients`` opt-in booleans carry Python defaults but
  (except ``movement_restriction``) had no server default, so a direct-SQL
  INSERT had to supply five of the six flags or fail NOT NULL. Backfill
  ``DEFAULT false`` to mirror the ORM defaults — no row changes: every
  existing row already holds a value.

Downgrade drops the trigger/function and removes the backfilled defaults
(reverting to the a8c0e2f4b6d8-era asymmetry). It never rewrites data.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e7b9d1f3a5c2"
down_revision: str | Sequence[str] | None = "c1d3e5f7a9b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RECIPIENT_OPT_INS = (
    "daily_digest",
    "screening_flags",
    "kidding_watch",
    "overdue_critical",
    "feed_reorder",
    "verified",
)


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE FUNCTION refresh_screening_images_updated_at()
            RETURNS trigger AS $$
            BEGIN
              -- Only fire when the statement left updated_at untouched, so
              -- ORM/Core onupdate values (and any explicit writer-set value)
              -- are preserved verbatim; raw SQL and migration UPDATEs get
              -- the same UTC wall clock the server_default uses.
              IF NEW.updated_at IS NOT DISTINCT FROM OLD.updated_at THEN
                NEW.updated_at := timezone('UTC', now());
              END IF;
              RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_screening_images_updated_at_refresh
            BEFORE UPDATE ON screening_images
            FOR EACH ROW EXECUTE FUNCTION refresh_screening_images_updated_at()
            """
        )
    )
    for column in _RECIPIENT_OPT_INS:
        op.alter_column(
            "notification_recipients",
            column,
            server_default=sa.false(),
            existing_type=sa.Boolean(),
            existing_nullable=False,
        )


def downgrade() -> None:
    for column in reversed(_RECIPIENT_OPT_INS):
        op.alter_column(
            "notification_recipients",
            column,
            server_default=None,
            existing_type=sa.Boolean(),
            existing_nullable=False,
        )
    op.execute("DROP TRIGGER IF EXISTS trg_screening_images_updated_at_refresh ON screening_images")
    op.execute("DROP FUNCTION IF EXISTS refresh_screening_images_updated_at()")
