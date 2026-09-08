"""tenant-bounded ordered indexes for the ledger and health-event feeds

Revision ID: e8b0d2f4a6c1
Revises: bd201c1cdc1b
Create Date: 2026-09-08 12:00:00.000000+00:00

GET /api/finance and GET /api/health/events page their farm-wide feeds with
``WHERE farm_id = :farm ORDER BY date DESC, id DESC LIMIT/OFFSET`` (the
ledger additionally narrows by a month range). Both tables carried only
single-column indexes, so PostgreSQL satisfied each page by sorting the
farm's entire multi-year history instead of walking a tenant-bounded ordered
index. These composites follow the feeding-ledger indexes from
d8e9f0a1b2c3/e5f6a7b8c9d0: built CONCURRENTLY (no blocking of writes, no
maintenance window), each preceded by removal of a same-named *invalid*
remnant a killed build may have left, and offline ``--sql`` generation fails
closed on such remnants instead of silently keeping them.
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import context, op

revision: str = "e8b0d2f4a6c1"
down_revision: str | Sequence[str] | None = "bd201c1cdc1b"
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
        _drop_invalid_index("ix_transactions_farm_date_id")
        op.execute(
            text(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                "ix_transactions_farm_date_id "
                "ON transactions (farm_id, date DESC, id DESC)"
            )
        )
        _drop_invalid_index("ix_health_events_farm_date_id")
        op.execute(
            text(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                "ix_health_events_farm_date_id "
                "ON health_events (farm_id, date DESC, id DESC)"
            )
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(text("DROP INDEX CONCURRENTLY IF EXISTS ix_health_events_farm_date_id"))
        op.execute(text("DROP INDEX CONCURRENTLY IF EXISTS ix_transactions_farm_date_id"))
