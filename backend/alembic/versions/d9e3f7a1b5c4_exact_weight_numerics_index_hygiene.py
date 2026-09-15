"""exact weight numerics and index hygiene

Revision ID: d9e3f7a1b5c4
Revises: c8d2e6f0a4b3
Create Date: 2026-09-14 00:00:00.000000+00:00

Audit remediation wave (backend-core workstream):

- ``weight_records.weight_kg``, ``animals.birth_weight`` and
  ``kid_entries.birth_weight`` move from float8 to ``numeric(8, 2)`` — the
  same exact storage sale_weight_kg already has (a weight times a realized
  ₹/kg rate must not drift in binary). The f2c3d4e5f6a7 recipe: a classifying
  preflight refuses non-finite / out-of-range / sub-0.01 kg legacy values
  fail-closed (naming row ids) instead of silently rounding them, then
  ``ALTER COLUMN TYPE`` rewrites with ``round(col::numeric, 2)``. The NaN /
  Infinity CHECK guards stay: numeric still accepts 'NaN'.
- ``ix_kid_entries_farm_animal`` on (farm_id, animal_id) — the composite
  tenant FK fk_kid_entries_farm_animal had no covering index, so its reverse
  lookups and every tenant join scanned kid_entries.
- Dropped as subsumed (all CONCURRENTLY, c2a4e6b8d013 pattern):
  ``ix_weight_records_date`` (covered by ix_weight_records_recent_date_id_animal),
  ``ix_tasks_status`` and ``ix_tasks_due_date`` (every status/due_date query
  is farm-scoped through ix_tasks_farm_status_due or the PENDING partials).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import text

from alembic import context, op

revision: str = "d9e3f7a1b5c4"
down_revision: str | Sequence[str] | None = "c8d2e6f0a4b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WEIGHT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("weight_records", "weight_kg"),
    ("animals", "birth_weight"),
    ("kid_entries", "birth_weight"),
)
_NEW_INDEX = "ix_kid_entries_farm_animal"
_DROPPED_INDEXES = ("ix_weight_records_date", "ix_tasks_status", "ix_tasks_due_date")


def _preflight_weight(table: str, column: str) -> None:
    """One classifying scan per column; deterministic samples enable repair.

    CASE (unlike a bare OR chain) guarantees ordered, lazy evaluation, so the
    numeric casts in the later arms never execute on a non-finite value.
    PostgreSQL's float8 -> numeric cast removes ordinary binary artifacts; a
    remaining difference past 0.01 kg is real legacy information and is never
    silently rounded away.
    """
    classify = (
        "CASE "
        f"WHEN \"{column}\"::text IN ('NaN', 'Infinity', '-Infinity') THEN 'non-finite' "
        f"WHEN abs(\"{column}\"::numeric) > 999999.99 THEN 'overflow' "
        f'WHEN "{column}"::numeric <> round("{column}"::numeric, 2) THEN \'sub-0.01-kg\' '
        "END"
    )
    rows = (
        op.get_bind()
        .execute(
            sa.text(
                f'SELECT id, {classify} AS kind FROM "{table}" '
                f'WHERE "{column}" IS NOT NULL AND {classify} IS NOT NULL '
                "ORDER BY id LIMIT 30"
            )
        )
        .all()
    )
    if rows:
        problems: dict[str, list[int]] = {}
        for row in rows:
            problems.setdefault(str(row.kind), []).append(int(row.id))
        details = "; ".join(
            f"{kind} values at row ids {ids}" for kind, ids in sorted(problems.items())
        )
        raise RuntimeError(
            f"Refusing exact weight migration: {table}.{column} contains "
            f"{details}; correct them explicitly before retrying"
        )


def _drop_invalid_index(index_name: str) -> None:
    """Remove a killed CONCURRENTLY remnant before an idempotent rebuild."""
    if context.is_offline_mode():
        op.execute(
            text(
                "DO $$ BEGIN IF EXISTS ("
                "SELECT 1 FROM pg_class AS c "
                "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
                "LEFT JOIN pg_index AS i ON i.indexrelid = c.oid "
                f"WHERE n.nspname = current_schema() AND c.relname = '{index_name}' "
                "AND (i.indisvalid IS NULL "
                "OR NOT (i.indisvalid AND i.indisready AND i.indislive))"
                f") THEN RAISE EXCEPTION 'Schema object {index_name} exists but is "
                "not a valid ready index; drop it manually (DROP INDEX CONCURRENTLY) "
                "and re-apply'; END IF; END $$"
            )
        )
        return
    row = (
        op.get_bind()
        .execute(
            text(
                """
            SELECT c.relkind::text AS relkind,
                   i.indisvalid, i.indisready, i.indislive
            FROM pg_class AS c
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            LEFT JOIN pg_index AS i ON i.indexrelid = c.oid
            WHERE n.nspname = current_schema() AND c.relname = :index_name
            """
            ),
            {"index_name": index_name},
        )
        .one_or_none()
    )
    if row is None:
        return
    if row.relkind not in {"i", "I"} or row.indisvalid is None:
        raise RuntimeError(
            f"Schema object {index_name!r} exists but is not an index "
            f"(relkind={row.relkind!r}, indisvalid={row.indisvalid!r})"
        )
    if not (row.indisvalid and row.indisready and row.indislive):
        op.execute(text(f'DROP INDEX CONCURRENTLY "{index_name}"'))


def upgrade() -> None:
    # Offline (--sql) generation cannot inspect data; emit the caveat into
    # the generated script and keep the preflight for online runs.
    if context.is_offline_mode():
        op.execute(
            "-- WARNING: offline generation skipped the exact-weight preflight. "
            "Audit the weight columns for non-finite/overflow/sub-0.01-kg float "
            "values (or run 'alembic upgrade' online) before applying this script."
        )
    else:
        for table, column in _WEIGHT_COLUMNS:
            _preflight_weight(table, column)

    for table, column in _WEIGHT_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.Float(),
            type_=sa.Numeric(8, 2),
            # The preflight proves this cannot change a meaningful value;
            # round only supplies the target column's explicit scale.
            postgresql_using=f'round("{column}"::numeric, 2)',
        )

    with op.get_context().autocommit_block():
        _drop_invalid_index(_NEW_INDEX)
        op.execute(
            text(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_NEW_INDEX} "
                "ON kid_entries (farm_id, animal_id)"
            )
        )
        for index_name in _DROPPED_INDEXES:
            op.execute(text(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}"))


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            text(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_weight_records_date "
                "ON weight_records (date)"
            )
        )
        op.execute(
            text("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_tasks_status ON tasks (status)")
        )
        op.execute(
            text("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_tasks_due_date ON tasks (due_date)")
        )
        op.execute(text(f"DROP INDEX CONCURRENTLY IF EXISTS {_NEW_INDEX}"))

    for table, column in reversed(_WEIGHT_COLUMNS):
        op.alter_column(
            table,
            column,
            existing_type=sa.Numeric(8, 2),
            type_=sa.Float(),
            postgresql_using=f'"{column}"::double precision',
        )
