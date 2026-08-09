"""actor-scoped farm creation idempotency and task-check validation

Revision ID: f3d4e5f6a7b8
Revises: f2c3d4e5f6a7
Create Date: 2026-08-09 00:20:00.000000+00:00

Farm creation has no tenant yet, so its durable retry claim is scoped by the
authenticated actor, operation, and opaque key digest.  PostgreSQL 14 lacks
``UNIQUE NULLS NOT DISTINCT``; a partial unique index provides the same
collision semantics for the tightly constrained NULL-farm rows.

This revision also completes the bounded task-assignment transition begun in
e9f0a1b2c3d4.  It derives missing PENDING personal-task roles only from the
assignee's retained same-farm membership and active role, refuses unresolved
legacy rows, and then validates the already-enforced NOT VALID check.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "f2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CREATE_FARM_OPERATION = "auth.farms.create"


def _fail_on_invalid_actor_scopes() -> None:
    bind = op.get_bind()
    invalid = bind.execute(
        sa.text(
            """
            SELECT id, farm_id, operation
            FROM idempotency_records
            WHERE (farm_id IS NULL AND operation IS DISTINCT FROM :operation)
               OR (farm_id IS NOT NULL AND operation = :operation)
            ORDER BY id
            LIMIT 10
            """
        ),
        {"operation": _CREATE_FARM_OPERATION},
    ).all()
    if invalid:
        sample = [(int(row.id), row.farm_id, str(row.operation)) for row in invalid]
        raise RuntimeError(
            "Refusing actor-scoped idempotency migration: farm scope and operation "
            f"disagree at sample (id, farm_id, operation) rows {sample}"
        )

    duplicates = bind.execute(
        sa.text(
            """
            SELECT actor_id, array_agg(id ORDER BY id) AS ids
            FROM idempotency_records
            WHERE farm_id IS NULL
            GROUP BY actor_id, operation, key_digest
            HAVING count(*) > 1
            ORDER BY min(id)
            LIMIT 10
            """
        )
    ).all()
    if duplicates:
        sample = [(int(row.actor_id), [int(value) for value in row.ids]) for row in duplicates]
        raise RuntimeError(
            "Refusing actor-scoped idempotency migration: duplicate NULL-farm "
            f"actor/operation/key scopes exist at sample (actor_id, record_ids) {sample}"
        )


def _repair_and_validate_personal_task_roles() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE tasks AS task
            SET assigned_role_id = membership.role_id
            FROM farm_memberships AS membership
            JOIN roles AS role
              ON role.farm_id = membership.farm_id
             AND role.id = membership.role_id
             AND role.deleted_at IS NULL
            WHERE task.status = 'PENDING'
              AND task.assigned_user_id IS NOT NULL
              AND task.assigned_role_id IS NULL
              AND membership.farm_id = task.farm_id
              AND membership.user_id = task.assigned_user_id
            """
        )
    )
    unresolved_count = int(
        bind.execute(
            sa.text(
                """
                SELECT count(*)
                FROM tasks
                WHERE status = 'PENDING'
                  AND assigned_user_id IS NOT NULL
                  AND assigned_role_id IS NULL
                """
            )
        ).scalar_one()
    )
    if unresolved_count:
        sample_ids = [
            int(value)
            for value in bind.execute(
                sa.text(
                    """
                    SELECT id
                    FROM tasks
                    WHERE status = 'PENDING'
                      AND assigned_user_id IS NOT NULL
                      AND assigned_role_id IS NULL
                    ORDER BY id
                    LIMIT 10
                    """
                )
            ).scalars()
        ]
        raise RuntimeError(
            "Cannot validate ck_tasks_user_assignment_has_role: "
            f"{unresolved_count} PENDING personal task(s) have no role after "
            "same-farm retained-membership/active-role repair; "
            f"sample task ids {sample_ids}"
        )
    op.execute("ALTER TABLE tasks VALIDATE CONSTRAINT ck_tasks_user_assignment_has_role")


def upgrade() -> None:
    op.alter_column(
        "idempotency_records",
        "farm_id",
        existing_type=sa.Integer(),
        existing_nullable=False,
        nullable=True,
    )
    _fail_on_invalid_actor_scopes()
    op.create_check_constraint(
        "ck_idempotency_scope_kind",
        "idempotency_records",
        "(farm_id IS NULL AND operation = 'auth.farms.create') OR "
        "(farm_id IS NOT NULL AND operation <> 'auth.farms.create')",
        postgresql_not_valid=True,
    )
    op.execute("ALTER TABLE idempotency_records VALIDATE CONSTRAINT ck_idempotency_scope_kind")
    op.create_index(
        "uq_idempotency_actor_scope_key",
        "idempotency_records",
        ["actor_id", "operation", "key_digest"],
        unique=True,
        postgresql_where=sa.text("farm_id IS NULL"),
    )
    _repair_and_validate_personal_task_roles()
    # This revision must remain one atomic transaction, so this is an ordinary
    # index build. Deploy it in the same maintenance window as the preceding
    # task validation: CREATE INDEX takes a SHARE lock on tasks while building.
    op.create_index(
        "ix_tasks_pending_farm_role",
        "tasks",
        ["farm_id", "assigned_role_id"],
        unique=False,
        postgresql_where=sa.text("status = 'PENDING' AND assigned_role_id IS NOT NULL"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    actor_scoped_ids = [
        int(value)
        for value in bind.execute(
            sa.text(
                """
                SELECT id
                FROM idempotency_records
                WHERE farm_id IS NULL
                ORDER BY id
                LIMIT 10
                """
            )
        ).scalars()
    ]
    if actor_scoped_ids:
        raise RuntimeError(
            "Cannot downgrade actor-scoped idempotency while NULL-farm records "
            f"remain; purge or let them expire first. Sample record ids {actor_scoped_ids}"
        )

    op.drop_index("ix_tasks_pending_farm_role", table_name="tasks")
    op.drop_index(
        "uq_idempotency_actor_scope_key",
        table_name="idempotency_records",
    )
    op.drop_constraint(
        "ck_idempotency_scope_kind",
        "idempotency_records",
        type_="check",
    )
    op.alter_column(
        "idempotency_records",
        "farm_id",
        existing_type=sa.Integer(),
        existing_nullable=True,
        nullable=False,
    )
    # ck_tasks_user_assignment_has_role predates this revision.  Validation is
    # monotonic metadata, so downgrade intentionally leaves it validated.
