"""Tenant-bounded ordered composite indexes backing the paginated feeds."""

from __future__ import annotations

from sqlalchemy import text

from app.db import Base, get_sessionmaker

# (index name, table, ordered column clause). The feeds page with
# ``WHERE farm_id = :farm ORDER BY date DESC, id DESC`` — the index must
# carry exactly that shape, or a farm's whole history is sorted per page.
EXPECTED_ORDERED_INDEXES = {
    "ix_transactions_farm_date_id": ("transactions", "(farm_id, date DESC, id DESC)"),
    "ix_health_events_farm_date_id": ("health_events", "(farm_id, date DESC, id DESC)"),
}


async def test_tenant_date_pagination_composites_exist_with_ordered_columns() -> None:
    async with get_sessionmaker()() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = current_schema() "
                    "AND indexname IN ('ix_transactions_farm_date_id', "
                    "'ix_health_events_farm_date_id')"
                )
            )
        ).all()
    assert len(rows) == len(EXPECTED_ORDERED_INDEXES)
    for index_name, indexdef in rows:
        expected_table, expected_columns = EXPECTED_ORDERED_INDEXES[index_name]
        assert indexdef.startswith(f"CREATE INDEX {index_name} ON public.{expected_table} ")
        assert expected_columns in indexdef


def test_models_declare_the_same_ordered_composites() -> None:
    """Mirror the database indexes in metadata so `alembic check` stays honest."""
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex

    declared = {
        index.name: index for table in Base.metadata.tables.values() for index in table.indexes
    }
    for index_name, (table_name, expected_columns) in EXPECTED_ORDERED_INDEXES.items():
        index = declared[index_name]
        assert index.table.name == table_name
        rendered = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
        assert f"ON {table_name} {expected_columns}" in rendered
