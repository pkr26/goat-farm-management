"""store feed quantities as exact whole-gram numerics

Revision ID: f2c3d4e5f6a7
Revises: f1b2c3d4e5f6
Create Date: 2026-08-08 22:10:00.000000+00:00

The API has long normalized feed quantities to 0.001 kg, but the underlying
``double precision`` columns could still accumulate binary drift.  Refuse to
guess how to repair genuinely sub-gram or non-finite legacy data, then move
the stock ledger, dispensing history, and per-head feeding settings to exact
``numeric(15, 3)`` storage.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "f2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "f1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_QUANTITY_COLUMNS: tuple[tuple[str, str, bool], ...] = (
    ("feed_inventory", "qty_on_hand", False),
    ("feed_inventory", "reorder_level", True),
    ("feed_finished_stock", "qty_on_hand", False),
    ("feeding_records", "qty_kg", False),
    ("bucket_definitions", "daily_kg_per_head", False),
    ("bucket_feed_settings", "daily_kg_per_head", False),
)
_MAX_NUMERIC_15_3 = "999999999999.999"


def _preflight_quantity(table: str, column: str) -> None:
    """One classifying scan per column; deterministic samples enable repair.

    CASE (unlike a bare OR chain) guarantees ordered, lazy evaluation, so the
    numeric casts in the later arms never execute on a non-finite value —
    ``'Infinity'::numeric`` errors on PostgreSQL < 14.

    PostgreSQL's float8 -> numeric cast removes ordinary binary artifacts
    (for example 0.30000000000000004 becomes 0.3). A remaining difference is
    therefore real legacy sub-gram information; do not silently decide
    whether that information should be rounded up or down.
    """
    classify = (
        "CASE "
        f"WHEN \"{column}\"::text IN ('NaN', 'Infinity', '-Infinity') THEN 'non-finite' "
        f"WHEN abs(\"{column}\"::numeric) > {_MAX_NUMERIC_15_3} THEN 'overflow' "
        f"WHEN \"{column}\"::numeric <> round(\"{column}\"::numeric, 3) THEN 'sub-gram' "
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
            f"Refusing exact feed-quantity migration: {table}.{column} contains "
            f"{details}; correct them explicitly before retrying"
        )


def upgrade() -> None:
    # Offline (--sql) generation cannot inspect data; emit the caveat into
    # the generated script and keep the preflight for online runs.
    if context.is_offline_mode():
        op.execute(
            "-- WARNING: offline generation skipped the exact feed-quantity preflight. "
            "Audit quantity columns for non-finite/overflow/sub-gram float values "
            "(or run 'alembic upgrade' online) before applying this script."
        )
    else:
        for table, column, _nullable in _QUANTITY_COLUMNS:
            _preflight_quantity(table, column)

    for table, column, nullable in _QUANTITY_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.Float(),
            type_=sa.Numeric(15, 3),
            existing_nullable=nullable,
            # The preflight proves this cannot change a meaningful value;
            # round only supplies the target column's explicit scale.
            postgresql_using=f'round("{column}"::numeric, 3)',
        )


def downgrade() -> None:
    for table, column, nullable in reversed(_QUANTITY_COLUMNS):
        op.alter_column(
            table,
            column,
            existing_type=sa.Numeric(15, 3),
            type_=sa.Float(),
            existing_nullable=nullable,
            postgresql_using=f'"{column}"::double precision',
        )
