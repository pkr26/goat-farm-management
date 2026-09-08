"""close the tenant-FK gap on weight_records and bucket_moves

Revision ID: b6d8f0a2c4e6
Revises: e8b0d2f4a6c1
Create Date: 2026-09-08 12:10:00.000000+00:00

f7d8c9b0a1e2 backfilled kid_entries.farm_id and added (farm_id, animal_id)
-> animals(farm_id, id) composites across every tenant-scoped relationship —
except the two per-animal history tables, which kept only their single-column
animal_id FK and therefore could still record a measurement or lifecycle move
against another farm's animal through any out-of-band writer. This revision
finishes the program with the identical recipe:

- add farm_id nullable, backfill from the parent animal, refuse (naming row
  ids) if any row still lacks a farm — that refusal is the orphan/cross-tenant
  preflight, because a farm_id derived from the row's own animal_id cannot be
  wrong unless the row has no resolvable parent;
- set NOT NULL (brief ACCESS EXCLUSIVE lock bounded by lock_timeout; both
  tables are wide but the rewrite only fills the new int column);
- farms FK + plain farm_id index;
- composite FK created NOT VALID (short metadata lock) then VALIDATE
  CONSTRAINT, which takes only a SHARE UPDATE EXCLUSIVE lock and refuses
  fail-closed on any surviving mismatch.

No maintenance window: every step is online-safe for a hot table.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b6d8f0a2c4e6"
down_revision: str | Sequence[str] | None = "e8b0d2f4a6c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HISTORY_TABLES = ("weight_records", "bucket_moves")


def _fail_on_orphans(table: str) -> None:
    """Abort before NOT NULL if any row could not resolve its parent animal."""
    # Mirrors the kid_entries backfill refusal in f7d8c9b0a1e2, upgraded to
    # name up to 30 offending ids so an operator can repair them directly.
    op.execute(
        sa.text(
            f"""
            DO $$ BEGIN
              IF EXISTS (SELECT 1 FROM {table} WHERE farm_id IS NULL) THEN
                RAISE EXCEPTION '{table} rows without a parent animal exist: %',
                  (SELECT string_agg(id::text, ', ') FROM (
                     SELECT id FROM {table} WHERE farm_id IS NULL
                     ORDER BY id LIMIT 30
                   ) orphaned);
              END IF;
            END $$;
            """
        )
    )


def _create_and_validate_fk(
    name: str,
    source: str,
    referent: str,
    local_columns: tuple[str, ...],
    remote_columns: tuple[str, ...],
) -> None:
    # NOT VALID keeps the initial metadata lock short; explicit validation is
    # still fail-closed and runs inside this migration's transaction.
    op.create_foreign_key(
        name,
        source,
        referent,
        list(local_columns),
        list(remote_columns),
        postgresql_not_valid=True,
    )
    op.execute(sa.text(f'ALTER TABLE "{source}" VALIDATE CONSTRAINT "{name}"'))


def upgrade() -> None:
    for table in _HISTORY_TABLES:
        op.add_column(table, sa.Column("farm_id", sa.Integer(), nullable=True))
        op.execute(
            f"UPDATE {table} child SET farm_id = parent.farm_id "
            "FROM animals parent WHERE parent.id = child.animal_id"
        )
        _fail_on_orphans(table)
        op.alter_column(table, "farm_id", nullable=False)
        op.create_foreign_key(f"{table}_farm_id_fkey", table, "farms", ["farm_id"], ["id"])
        op.create_index(f"ix_{table}_farm_id", table, ["farm_id"], unique=False)

    for table in _HISTORY_TABLES:
        _create_and_validate_fk(
            f"fk_{table}_farm_animal",
            table,
            "animals",
            ("farm_id", "animal_id"),
            ("farm_id", "id"),
        )


def downgrade() -> None:
    """Schema-only: drops the tenant guards, keeps all measurement rows."""
    for table in reversed(_HISTORY_TABLES):
        op.drop_constraint(f"fk_{table}_farm_animal", table, type_="foreignkey")

    for table in reversed(_HISTORY_TABLES):
        op.drop_index(f"ix_{table}_farm_id", table_name=table)
        op.drop_constraint(f"{table}_farm_id_fkey", table, type_="foreignkey")
        op.drop_column(table, "farm_id")
