"""online indexes for bounded herd and duty query paths

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-08 17:20:00.000000+00:00

The API now returns finite previews with exact totals, but latest-history probes
and ordered partial pages still need matching composite indexes at production
scale. PostgreSQL cannot build these inside Alembic's transaction. A killed
concurrent build can leave a same-named *invalid* index, so each build removes
that remnant before its restart-safe CONCURRENTLY + IF NOT EXISTS statement.
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import context, op

revision: str = "e5f6a7b8c9d0"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


NEW_INDEXES = (
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_bucket_moves_animal_moved_id_desc
    ON bucket_moves (animal_id, moved_at DESC, id DESC)
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_weight_records_animal_date_id_desc
    ON weight_records (animal_id, date DESC, id DESC) INCLUDE (weight_kg)
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_weight_records_recent_date_id_animal
    ON weight_records (date DESC, id DESC, animal_id)
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_animals_farm_active_bucket_tag_id
    ON animals (farm_id, current_bucket, tag_number, id)
    INCLUDE (name, sex, birth_weight, created_at)
    WHERE status = 'ACTIVE'
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_animals_farm_active_cull_tag_id
    ON animals (farm_id, tag_number, id) INCLUDE (name)
    WHERE status = 'ACTIVE' AND cull_candidate IS TRUE
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_animals_farm_status
    ON animals (farm_id, status)
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_breeding_records_farm_confirmed_doe_date_id
    ON breeding_records (farm_id, doe_id, breeding_date DESC, id DESC)
    WHERE outcome = 'CONFIRMED_PREGNANT'
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_breeding_records_farm_confirmed_due_id
    ON breeding_records (farm_id, expected_kidding_date, id) INCLUDE (doe_id)
    WHERE outcome = 'CONFIRMED_PREGNANT'
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_tasks_farm_pending_due_id
    ON tasks (farm_id, due_date, id) WHERE status = 'PENDING'
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_tasks_farm_pending_category_due_id
    ON tasks (farm_id, category, due_date, id) WHERE status = 'PENDING'
    """,
)

NEW_INDEX_NAMES = (
    "ix_bucket_moves_animal_moved_id_desc",
    "ix_weight_records_animal_date_id_desc",
    "ix_weight_records_recent_date_id_animal",
    "ix_animals_farm_active_bucket_tag_id",
    "ix_animals_farm_active_cull_tag_id",
    "ix_animals_farm_status",
    "ix_breeding_records_farm_confirmed_doe_date_id",
    "ix_breeding_records_farm_confirmed_due_id",
    "ix_tasks_farm_pending_due_id",
    "ix_tasks_farm_pending_category_due_id",
)


def _drop_invalid_index(index_name: str) -> None:
    """Remove a killed CONCURRENTLY remnant before an idempotent rebuild."""
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
        for index_name, statement in zip(NEW_INDEX_NAMES, NEW_INDEXES, strict=True):
            _drop_invalid_index(index_name)
            op.execute(text(statement))
        # Superseded by the ordered covering active-herd index above.
        op.execute(text("DROP INDEX CONCURRENTLY IF EXISTS ix_animals_farm_active"))


def downgrade() -> None:
    with op.get_context().autocommit_block():
        _drop_invalid_index("ix_animals_farm_active")
        op.execute(
            text(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_animals_farm_active "
                "ON animals (farm_id) WHERE status = 'ACTIVE'"
            )
        )
        for index_name in reversed(NEW_INDEX_NAMES):
            op.execute(text(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}"))
