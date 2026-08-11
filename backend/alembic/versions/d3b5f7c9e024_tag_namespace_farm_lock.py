"""bound the tag-namespace triggers to one advisory lock per farm

Revision ID: d3b5f7c9e024
Revises: c2a4e6b8d013
Create Date: 2026-08-11 09:30:00.000000+00:00

``e3f4a5b6c7d9`` closed the animal/stillborn cross-table tag race with a
transaction-scoped advisory lock keyed on ``(farm_id, hashtext(tag))``, taken
per row. Transaction-scoped locks are held until COMMIT, so a transaction
inserting N animals held N distinct advisory locks — and the shared lock table
is a fixed-size arena sized by ``max_locks_per_transaction × max_connections``.
A handful of concurrent bulk purchases (``MAX_BATCH_COUNT`` is 1000, and
``POST /api/purchases`` inserts the whole batch in one transaction) exhausts it,
and PostgreSQL aborts unrelated sessions with "out of shared memory".

The lock only has to make the two tables' checks mutually exclusive; it never
needed tag granularity. Keying it on the farm alone makes it one lock entry per
farm per transaction no matter how many rows are written, and taking it in
SHARE mode on the high-volume animals path keeps concurrent animal inserts in
one farm running in parallel with each other. The rare stillborn-tag path takes
the same key in EXCLUSIVE mode, so the two paths still serialise against one
another and neither can commit without seeing the other's row.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d3b5f7c9e024"
down_revision: str | Sequence[str] | None = "c2a4e6b8d013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# EXCLUSIVE on the farm: rare (a stillborn kid entry carrying a tag), and must
# exclude every concurrent animal insert in the same farm.
_STILLBORN_FN_FARM_LOCK = """
CREATE OR REPLACE FUNCTION enforce_stillborn_tag_namespace()
RETURNS trigger AS $$
BEGIN
  IF NEW.tag IS NULL OR btrim(NEW.tag) = '' OR NEW.status <> 'STILLBORN' THEN
    RETURN NEW;
  END IF;
  PERFORM pg_advisory_xact_lock(NEW.farm_id);
  IF EXISTS (
    SELECT 1 FROM animals
    WHERE farm_id = NEW.farm_id AND tag_number = NEW.tag
  ) THEN
    RAISE EXCEPTION USING
      ERRCODE = '23505',
      CONSTRAINT = 'uq_stillborn_tag_farm_namespace',
      MESSAGE = 'stillborn tag conflicts with an animal tag in this farm';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

# SHARE on the farm: high volume (every animal insert, including 1000-row
# purchase batches). Share locks do not conflict with each other, so batches
# still run concurrently; they only wait behind a stillborn tag write.
_ANIMAL_FN_FARM_LOCK = """
CREATE OR REPLACE FUNCTION enforce_animal_tag_against_stillborns()
RETURNS trigger AS $$
BEGIN
  PERFORM pg_advisory_xact_lock_shared(NEW.farm_id);
  IF EXISTS (
    SELECT 1 FROM kid_entries
    WHERE farm_id = NEW.farm_id
      AND tag = NEW.tag_number
      AND status = 'STILLBORN'
  ) THEN
    RAISE EXCEPTION USING
      ERRCODE = '23505',
      CONSTRAINT = 'uq_stillborn_tag_farm_namespace',
      MESSAGE = 'animal tag conflicts with a stillborn tag in this farm';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

_STILLBORN_FN_PER_TAG_LOCK = _STILLBORN_FN_FARM_LOCK.replace(
    "PERFORM pg_advisory_xact_lock(NEW.farm_id);",
    "PERFORM pg_advisory_xact_lock(NEW.farm_id, hashtext(NEW.tag));",
)
_ANIMAL_FN_PER_TAG_LOCK = _ANIMAL_FN_FARM_LOCK.replace(
    "PERFORM pg_advisory_xact_lock_shared(NEW.farm_id);",
    "PERFORM pg_advisory_xact_lock(NEW.farm_id, hashtext(NEW.tag_number));",
)


def upgrade() -> None:
    # CREATE OR REPLACE keeps both triggers pointing at the same function
    # names, so no trigger has to be dropped and recreated.
    op.execute(sa.text(_STILLBORN_FN_FARM_LOCK))
    op.execute(sa.text(_ANIMAL_FN_FARM_LOCK))


def downgrade() -> None:
    op.execute(sa.text(_STILLBORN_FN_PER_TAG_LOCK))
    op.execute(sa.text(_ANIMAL_FN_PER_TAG_LOCK))
