"""screening join and cascade-reverse indexes (2026-09-28 audit, D6)

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-09-28 00:00:00.000000+00:00

The review list, image detail and dataset export all join
``screening_findings`` to its run, but no index served ``(farm_id,
run_id)``; and the retention job's cascade-reverse probes need
``(farm_id, crop_id)`` on both ``screening_runs`` and
``screening_findings``. Built CONCURRENTLY, following e5f6a7b8c9d0:
PostgreSQL cannot build these inside Alembic's transaction, and a killed
concurrent build leaves a same-named *invalid* index that IF NOT EXISTS
would silently keep, so each build removes that remnant first.
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import context, op

revision: str = "a4b5c6d7e8f9"
down_revision: str | Sequence[str] | None = "f3a4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_INDEXES = (
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_screening_findings_farm_run
    ON screening_findings (farm_id, run_id)
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_screening_runs_farm_crop
    ON screening_runs (farm_id, crop_id)
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_screening_findings_farm_crop
    ON screening_findings (farm_id, crop_id)
    """,
)

NEW_INDEX_NAMES = (
    "ix_screening_findings_farm_run",
    "ix_screening_runs_farm_crop",
    "ix_screening_findings_farm_crop",
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


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for index_name in reversed(NEW_INDEX_NAMES):
            op.execute(text(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}"))
