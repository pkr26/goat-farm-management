"""make farm-local preset role codes unique

Revision ID: d5e7f9a1b3c4
Revises: c3d4e5f6a7b1
Create Date: 2026-08-18 00:00:00.000000+00:00

``Role.code`` is the stable key used to assign every generated duty. Display
names and permissions are editable, but the database previously allowed two
rows in one farm to carry the same non-null code; the service then selected an
arbitrary ``first()`` row. Preserve every referenced role id while repairing
legacy duplicates: the active, lowest-id row keeps the code and the others
become ordinary custom roles (``code = NULL``).

Non-null codes outside the finite server-owned preset catalog had the same
problem in another form: the API treated them as undeletable presets although
no task category could route to them. They are normalized to custom roles and
a CHECK prevents new pseudo-presets.

The unique partial index is built concurrently. A killed build can leave an
invalid same-named relation, so a retry removes that remnant before rebuilding.
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import context, op

revision: str = "d5e7f9a1b3c4"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "uq_roles_farm_preset_code"
CHECK_NAME = "ck_roles_preset_code"
PRESET_CODES = ("CLEANER", "CLEANER_MANAGER", "FEEDER", "MOVER", "VET")
PRESET_CODES_SQL = ", ".join(f"'{code}'" for code in PRESET_CODES)


def _drop_invalid_index() -> None:
    if context.is_offline_mode():
        # Offline generation cannot inspect pg_index. Refuse to let
        # ``IF NOT EXISTS`` silently retain an interrupted invalid build.
        op.execute(
            text(
                "DO $$ BEGIN IF EXISTS ("
                "SELECT 1 FROM pg_class AS c "
                "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
                "LEFT JOIN pg_index AS i ON i.indexrelid = c.oid "
                f"WHERE n.nspname = current_schema() AND c.relname = '{INDEX_NAME}' "
                "AND (i.indisvalid IS NULL "
                "OR NOT (i.indisvalid AND i.indisready AND i.indislive))"
                f") THEN RAISE EXCEPTION 'Schema object {INDEX_NAME} exists but is "
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
    if not (row.indisvalid and row.indisready and row.indislive):
        op.execute(text(f'DROP INDEX CONCURRENTLY "{INDEX_NAME}"'))


def upgrade() -> None:
    # A non-null code outside the finite server-owned preset catalog behaves
    # like an undeletable pseudo-preset in the API. It has never been a valid
    # routing identity; normalize it to an ordinary custom role without
    # changing the row id or any FK references.
    op.execute(
        text(
            f"UPDATE roles SET code = NULL "
            f"WHERE code IS NOT NULL AND code NOT IN ({PRESET_CODES_SQL})"
        )
    )
    # Rank first by live/tombstoned state, then by immutable id. Updating only
    # ``code`` preserves memberships, tasks and every historical FK reference
    # to a duplicate role row.
    op.execute(
        text(
            """
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY farm_id, code
                           ORDER BY (deleted_at IS NULL) DESC, id
                       ) AS ordinal
                FROM roles
                WHERE code IS NOT NULL
            )
            UPDATE roles AS role
            SET code = NULL
            FROM ranked
            WHERE role.id = ranked.id
              AND ranked.ordinal > 1
            """
        )
    )
    with op.get_context().autocommit_block():
        _drop_invalid_index()
        op.execute(
            text(
                f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME} "
                "ON roles (farm_id, code) WHERE code IS NOT NULL"
            )
        )
    op.create_check_constraint(
        CHECK_NAME,
        "roles",
        f"code IS NULL OR code IN ({PRESET_CODES_SQL})",
        postgresql_not_valid=True,
    )
    op.execute(f"ALTER TABLE roles VALIDATE CONSTRAINT {CHECK_NAME}")


def downgrade() -> None:
    # Duplicate normalization is intentionally retained: reconstructing which
    # row used to carry the same routing identity would invent data.
    with op.get_context().autocommit_block():
        op.execute(text(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}"))
    op.drop_constraint(CHECK_NAME, "roles", type_="check")
