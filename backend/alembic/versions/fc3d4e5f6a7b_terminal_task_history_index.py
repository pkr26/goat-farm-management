"""Index terminal task history by its displayed completion instant.

Revision ID: fc3d4e5f6a7b
Revises: fb2c3d4e5f6a

The tasks table is a hot operational queue, so both directions are online.
A killed concurrent build can leave a same-named invalid index before Alembic
stamps this revision. A failed deploy can also leave a valid but incorrectly
defined manual/remnant index whose name would make ``IF NOT EXISTS`` silently
skip the required build. Online retries remove either same-table form before
rebuilding, while a valid cross-table name collision fails loudly.
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import context, op

revision: str = "fc3d4e5f6a7b"
down_revision: str | Sequence[str] | None = "fb2c3d4e5f6a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_tasks_farm_terminal_finished_id"


def _prepare_index_name() -> None:
    if context.is_offline_mode():
        # Offline generation cannot safely prove that an existing same-named
        # object has the expected table, keys, expression and predicate. Fail
        # closed instead of letting IF NOT EXISTS stamp a false-green schema.
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
    if usable and row.indexed_table != "tasks":
        raise RuntimeError(
            f"Valid index {INDEX_NAME!r} belongs to table {row.indexed_table!r}, "
            "not 'tasks'; refusing to drop an unrelated schema object"
        )
    # Even a valid same-table index can have the wrong columns/expression or
    # predicate. Rebuilding is online and is the only definition-independent
    # way to make a retry exact rather than trusting PostgreSQL's name-only
    # IF NOT EXISTS check.
    op.execute(text(f'DROP INDEX CONCURRENTLY "{INDEX_NAME}"'))


def upgrade() -> None:
    with op.get_context().autocommit_block():
        _prepare_index_name()
        op.execute(
            text(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME} ON tasks "
                "(farm_id, (CASE WHEN status = 'SKIPPED' THEN skipped_at "
                "ELSE completed_at END) DESC, id DESC) "
                "WHERE status IN ('DONE', 'VERIFIED', 'SKIPPED')"
            )
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(text(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}"))
