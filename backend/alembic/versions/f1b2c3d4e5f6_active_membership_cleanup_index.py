"""online index for bounded tombstoned-account membership cleanup

Revision ID: f1b2c3d4e5f6
Revises: f0a1b2c3d4e5
Create Date: 2026-08-08 21:20:00.000000+00:00

Account deletion now tombstones the authoritative User row in constant work.
The application deactivates retained membership audit anchors afterward in
finite SKIP LOCKED batches.  This partial index keeps that convergence path
bounded to active rows and supports deterministic per-user/id batching.
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "f1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f0a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MEMBERSHIP_INDEX = "ix_farm_memberships_active_user_id_id"
DELETED_USER_INDEX = "ix_users_deleted_id"


def _drop_invalid_index(index_name: str) -> None:
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
        _drop_invalid_index(MEMBERSHIP_INDEX)
        _drop_invalid_index(DELETED_USER_INDEX)
        op.execute(
            text(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                f"{MEMBERSHIP_INDEX} ON farm_memberships (user_id, id) "
                "WHERE is_active IS TRUE"
            )
        )
        op.execute(
            text(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                f"{DELETED_USER_INDEX} ON users (id) "
                "WHERE deleted_at IS NOT NULL"
            )
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(text(f"DROP INDEX CONCURRENTLY IF EXISTS {MEMBERSHIP_INDEX}"))
        op.execute(text(f"DROP INDEX CONCURRENTLY IF EXISTS {DELETED_USER_INDEX}"))
