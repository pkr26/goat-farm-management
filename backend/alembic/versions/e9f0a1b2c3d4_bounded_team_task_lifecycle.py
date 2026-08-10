"""bound team/account lifecycle work with retained assignment anchors

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-08-08 20:05:00.000000+00:00

Worker deactivation and account/role deletion previously rewrote every Task
ever assigned to the target inside one request transaction.  A long or hostile
history could therefore make access revocation time out.  Every personal task
now also carries its role fallback, inactive membership rows can remain as
non-authorizing FK/audit anchors, and custom roles are soft-deleted.  Historical
assignments require no synchronous fan-out.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import text

from alembic import context, op

revision: str = "e9f0a1b2c3d4"
down_revision: str | Sequence[str] | None = "d8e9f0a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("roles", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.drop_constraint("uq_role_name_per_farm", "roles", type_="unique")
    op.create_index(
        "uq_roles_farm_active_name",
        "roles",
        ["farm_id", "name"],
        unique=True,
        postgresql_where=text("deleted_at IS NULL"),
    )

    # NOT VALID deliberately avoids an unbounded rewrite/scan of a tenant's
    # historical task table in the release transaction. PostgreSQL enforces
    # the invariant for every new/updated PENDING row immediately. The bounded
    # post-readiness repair worker fills legacy personal fallbacks in finite
    # SKIP LOCKED batches; task visibility also resolves the retained
    # membership's role dynamically until that repair reaches a row.
    op.create_check_constraint(
        "ck_tasks_user_assignment_has_role",
        "tasks",
        "status <> 'PENDING' OR assigned_user_id IS NULL OR assigned_role_id IS NOT NULL",
        postgresql_not_valid=True,
    )


def downgrade() -> None:
    if context.is_offline_mode():
        # Offline (--sql) generation cannot inspect data; render the same
        # tombstone guard as an in-script check that fails the apply instead.
        op.execute(
            "DO $$ BEGIN "
            "IF EXISTS (SELECT 1 FROM roles WHERE deleted_at IS NOT NULL) THEN "
            "RAISE EXCEPTION 'Cannot downgrade role tombstones while deleted "
            "roles exist; restore or permanently reconcile them first'; "
            "END IF; END $$"
        )
    else:
        deleted_role = (
            op.get_bind()
            .execute(text("SELECT id FROM roles WHERE deleted_at IS NOT NULL ORDER BY id LIMIT 1"))
            .scalar_one_or_none()
        )
        if deleted_role is not None:
            raise RuntimeError(
                "Cannot downgrade role tombstones while deleted roles exist; "
                f"restore or permanently reconcile role id {deleted_role} first"
            )

    op.drop_constraint("ck_tasks_user_assignment_has_role", "tasks", type_="check")
    op.drop_index("uq_roles_farm_active_name", table_name="roles")
    op.create_unique_constraint("uq_role_name_per_farm", "roles", ["farm_id", "name"])
    op.drop_column("roles", "deleted_at")
