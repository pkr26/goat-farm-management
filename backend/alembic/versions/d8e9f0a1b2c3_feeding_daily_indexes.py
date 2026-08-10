"""cover bounded feeding-day history and exact allocation aggregates

Revision ID: d8e9f0a1b2c3
Revises: d7e8f9a0b1c2
Create Date: 2026-08-08 19:20:00.000000+00:00

The plan response keeps only the newest 200 ledger rows but also reports an
exact full-day count and exact per-allocation totals.  These online indexes
bound the ordered preview probe and let PostgreSQL aggregate the complete day
from a covering tenant/date index without fetching every heap row.
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import context, op

revision: str = "d8e9f0a1b2c3"
down_revision: str | Sequence[str] | None = "d7e8f9a0b1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_invalid_index(index_name: str) -> None:
    if context.is_offline_mode():
        # Offline (--sql) generation cannot inspect pg_index, and DROP INDEX
        # CONCURRENTLY cannot be decided in-script. IF NOT EXISTS below would
        # silently keep an *invalid* remnant, so fail the apply with the same
        # diagnosis the online path acts on. Index names are static constants.
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
    with op.get_context().autocommit_block():
        _drop_invalid_index("ix_feeding_records_farm_date_id")
        op.execute(
            text(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                "ix_feeding_records_farm_date_id "
                "ON feeding_records (farm_id, date, id)"
            )
        )
        _drop_invalid_index("ix_feeding_records_farm_date_allocation")
        op.execute(
            text(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                "ix_feeding_records_farm_date_allocation "
                "ON feeding_records (farm_id, date, bucket, recipe_code, shift) "
                "INCLUDE (qty_kg)"
            )
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            text("DROP INDEX CONCURRENTLY IF EXISTS ix_feeding_records_farm_date_allocation")
        )
        op.execute(text("DROP INDEX CONCURRENTLY IF EXISTS ix_feeding_records_farm_date_id"))
