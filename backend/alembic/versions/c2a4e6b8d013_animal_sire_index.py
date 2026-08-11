"""index animals.sire_id alongside the existing dam_id index

Revision ID: c2a4e6b8d013
Revises: b7c8d9e0f1a2
Create Date: 2026-08-11 09:10:00.000000+00:00

``animals.dam_id`` has carried an index since d8f2b6a41e90; ``sire_id`` never
did, even though both are ``ON DELETE SET NULL`` parents matched by the same
``dam_id = :id OR sire_id = :id`` offspring lookup on the animal profile. The
missing half forced a sequential scan of the farm's entire lifetime herd —
including SOLD and DEAD rows, which are never removed — on every profile view
and again on the offspring count, and made the SET NULL cascade of deleting a
sire scan the same table.

Built CONCURRENTLY, following e5f6a7b8c9d0: PostgreSQL cannot build it inside
Alembic's transaction, and a killed concurrent build leaves a same-named
*invalid* index that IF NOT EXISTS would silently keep.
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import context, op

revision: str = "c2a4e6b8d013"
down_revision: str | Sequence[str] | None = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_animals_sire_id"


def _drop_invalid_index(index_name: str) -> None:
    """Remove a killed CONCURRENTLY remnant before an idempotent rebuild."""
    if context.is_offline_mode():
        # Offline (--sql) generation cannot inspect pg_index, and DROP INDEX
        # CONCURRENTLY cannot be decided in-script. Fail the apply with the
        # same diagnosis the online path acts on. The name is a static constant.
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
        _drop_invalid_index(INDEX_NAME)
        op.execute(
            text(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME} ON animals (sire_id)")
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(text(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}"))
