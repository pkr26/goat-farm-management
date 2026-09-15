"""task provenance, title localization keys, and the WATER duty category

Revision ID: b7c1d5e9f3a2
Revises: f2a3b4c5d6e7
Create Date: 2026-09-14 00:00:00.000000+00:00

Audit remediation wave (backend-core workstream):

- ``tasks.created_at`` / ``tasks.created_by_id`` — creation attribution for
  every duty (manual duties record their creator; generated rows keep NULL
  creator). ``created_at`` follows the schema-wide naive-UTC convention, so
  its server default is ``timezone('UTC', now())`` (see b2c3d4e5f6a8),
  backfilled for legacy rows and then NOT NULL.
- ``tasks.title_key`` / ``tasks.title_args`` — the localization contract for
  server-generated duties: a stable snake_case key plus structured args the
  client renders in the worker's language; ``title`` stays the English
  fallback. ``title_args`` is jsonb default ``{}`` like roles.permissions.
- ``purchase_batches.created_at`` / ``created_by_id`` — the same provenance
  pair for procurement batches (the service already accepted a creator id
  and simply never stored it).
- ``ck_tasks_category`` widened with WATER (the daily trough round, added by
  the cadence sweep), following f1e2d3c4b5a6's NOT VALID -> VALIDATE swap.

FKs are created NOT VALID and validated in the same revision (short metadata
lock, then SHARE UPDATE EXCLUSIVE); every existing row's new FK column is
NULL, so validation cannot fail on data.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b7c1d5e9f3a2"
down_revision: str | Sequence[str] | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Keep in step with app.models (enums.py member order); the rendered bytes
# are pinned by tests/test_domain_check_constraints.py.
TASK_CATEGORIES_LEGACY = (
    "VACCINE",
    "DEWORMING",
    "ULTRASOUND",
    "KIDDING_DUE",
    "WEANING",
    "BUCKET_MOVE",
    "QUARANTINE",
    "FEED",
    "CLEANING",
    "OTHER",
    "KIDDING_WATCH",
    "BIRTHING_KIT",
    "HEALTH_CHECK",
    "HEAT_WATCH",
    "HOOF_TRIMMING",
    "SPRAYING",
    "DISINFECTION",
    "WEIGHING",
    "REBREED",
    "BUCK_ROTATION",
    "INSURANCE",
)
TASK_CATEGORIES_NEW = (*TASK_CATEGORIES_LEGACY, "WATER")


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _swap_constraint(name: str, table: str, column: str, values: tuple[str, ...]) -> None:
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(
        name,
        table,
        f"{column} IN ({_in_list(values)})",
        postgresql_not_valid=True,
    )
    op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")


def _create_and_validate_fk(name: str, source: str, column: str) -> None:
    # NOT VALID keeps the initial metadata lock short; explicit validation is
    # still fail-closed and runs inside this migration's transaction.
    op.create_foreign_key(name, source, "users", [column], ["id"], postgresql_not_valid=True)
    op.execute(sa.text(f'ALTER TABLE "{source}" VALIDATE CONSTRAINT "{name}"'))


def upgrade() -> None:
    op.add_column("tasks", sa.Column("title_key", sa.String(length=60), nullable=True))
    op.add_column(
        "tasks",
        sa.Column(
            "title_args",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    # Nullable + no default first (no table rewrite), backfill, then the
    # UTC-safe server default and NOT NULL.
    op.add_column("tasks", sa.Column("created_at", sa.DateTime(), nullable=True))
    op.execute("UPDATE tasks SET created_at = timezone('UTC', now()) WHERE created_at IS NULL")
    op.alter_column(
        "tasks",
        "created_at",
        nullable=False,
        server_default=sa.text("timezone('UTC', now())"),
    )
    op.add_column("tasks", sa.Column("created_by_id", sa.Integer(), nullable=True))
    _create_and_validate_fk("tasks_created_by_id_fkey", "tasks", "created_by_id")

    op.add_column("purchase_batches", sa.Column("created_at", sa.DateTime(), nullable=True))
    op.execute(
        "UPDATE purchase_batches SET created_at = timezone('UTC', now()) WHERE created_at IS NULL"
    )
    op.alter_column(
        "purchase_batches",
        "created_at",
        nullable=False,
        server_default=sa.text("timezone('UTC', now())"),
    )
    op.add_column("purchase_batches", sa.Column("created_by_id", sa.Integer(), nullable=True))
    _create_and_validate_fk(
        "purchase_batches_created_by_id_fkey", "purchase_batches", "created_by_id"
    )

    _swap_constraint("ck_tasks_category", "tasks", "category", TASK_CATEGORIES_NEW)


def downgrade() -> None:
    # The application release that writes WATER duties is gone with this
    # downgrade, so surviving rows normalize to the closest legacy value
    # before the tighter CHECK is restored (the f1e2d3c4b5a6 pattern).
    op.execute("UPDATE tasks SET category = 'FEED' WHERE category = 'WATER'")
    _swap_constraint("ck_tasks_category", "tasks", "category", TASK_CATEGORIES_LEGACY)

    op.drop_constraint(
        "purchase_batches_created_by_id_fkey", "purchase_batches", type_="foreignkey"
    )
    op.drop_column("purchase_batches", "created_by_id")
    op.drop_column("purchase_batches", "created_at")
    op.drop_constraint("tasks_created_by_id_fkey", "tasks", type_="foreignkey")
    op.drop_column("tasks", "created_by_id")
    op.drop_column("tasks", "created_at")
    op.drop_column("tasks", "title_args")
    op.drop_column("tasks", "title_key")
