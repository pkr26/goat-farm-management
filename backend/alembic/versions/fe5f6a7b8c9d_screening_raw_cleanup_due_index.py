"""Build the raw-cleanup due queue index without blocking image writes.

Revision ID: fe5f6a7b8c9d
Revises: fd4e5f6a7b8c

``screening_images`` is the live worker queue. A transactional CREATE INDEX
would hold a SHARE lock and block uploads/claims for the whole build, so this
separate revision uses PostgreSQL's concurrent path. Interrupted concurrent
builds can leave a same-named invalid catalog entry before Alembic stamps the
revision; retries remove that remnant and rebuild the exact definition. A
valid cross-table name collision fails closed instead of deleting an unrelated
schema object.
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import context, op

revision: str = "fe5f6a7b8c9d"
down_revision: str | Sequence[str] | None = "fd4e5f6a7b8c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_screening_images_raw_cleanup_due"


def _prepare_index_name() -> None:
    if context.is_offline_mode():
        # Offline SQL cannot prove an existing object's table or definition.
        # Refuse a name-only IF NOT EXISTS false green.
        op.execute(
            text(
                "DO $$ BEGIN IF EXISTS ("
                "SELECT 1 FROM pg_class AS c "
                "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
                f"WHERE n.nspname = current_schema() AND c.relname = '{INDEX_NAME}' "
                f") THEN RAISE EXCEPTION 'Schema object {INDEX_NAME} exists but is "
                "not verifiable during offline apply; verify and drop it manually "
                "(DROP INDEX CONCURRENTLY) before re-applying'; END IF; END $$"
            )
        )
        return
    row = (
        op.get_bind()
        .execute(
            text(
                """
                SELECT c.relkind::text AS relkind,
                       i.indisvalid, i.indisready, i.indislive,
                       indexed.relname::text AS indexed_table
                FROM pg_class AS c
                JOIN pg_namespace AS n ON n.oid = c.relnamespace
                LEFT JOIN pg_index AS i ON i.indexrelid = c.oid
                LEFT JOIN pg_class AS indexed ON indexed.oid = i.indrelid
                WHERE n.nspname = current_schema() AND c.relname = :index_name
                """
            ),
            {"index_name": INDEX_NAME},
        )
        .one_or_none()
    )
    if row is None:
        return
    if row.relkind not in {"i", "I"} or row.indisvalid is None:
        raise RuntimeError(
            f"Schema object {INDEX_NAME!r} exists but is not an index "
            f"(relkind={row.relkind!r}, indisvalid={row.indisvalid!r})"
        )
    usable = row.indisvalid and row.indisready and row.indislive
    if usable and row.indexed_table != "screening_images":
        raise RuntimeError(
            f"Valid index {INDEX_NAME!r} belongs to table {row.indexed_table!r}, "
            "not 'screening_images'; refusing to drop an unrelated schema object"
        )
    # Rebuild even a valid same-table object: IF NOT EXISTS compares only the
    # name and would otherwise stamp a wrong column order or predicate.
    op.execute(text(f'DROP INDEX CONCURRENTLY "{INDEX_NAME}"'))


def upgrade() -> None:
    with op.get_context().autocommit_block():
        _prepare_index_name()
        op.execute(
            text(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME} "
                "ON screening_images (raw_cleanup_next_attempt_at, id) "
                "WHERE raw_cleanup_completed_at IS NULL "
                "AND raw_cleanup_next_attempt_at IS NOT NULL"
            )
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(text(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}"))
