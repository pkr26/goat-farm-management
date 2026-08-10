"""Exact ledger, finished feed stock, and task audit history.

Revision ID: c8f1d3a5e709
Revises: a4d9e6f2b701
Create Date: 2026-08-08 19:15:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c8f1d3a5e709"
down_revision: str | Sequence[str] | None = "a4d9e6f2b701"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_MONEY_COLUMNS = (
    ("transactions", "amount", False),
    ("animals", "purchase_price", True),
    ("animals", "sale_price", True),
    ("health_events", "cost", True),
    ("purchase_batches", "total_price", True),
    ("feed_inventory", "last_purchase_price_per_kg", True),
)
_MAX_NUMERIC_14_2 = "999999999999.99"


def _sample_ids(table: str, column: str, predicate: str) -> list[int]:
    """Return a deterministic sample so an operator can repair and retry."""
    rows = op.get_bind().execute(
        sa.text(
            f'SELECT id FROM "{table}" '
            f'WHERE "{column}" IS NOT NULL AND ({predicate}) ORDER BY id LIMIT 10'
        )
    )
    return [int(row.id) for row in rows]


def _preflight_money(table: str, column: str) -> None:
    special_ids = _sample_ids(
        table,
        column,
        f"\"{column}\"::text IN ('NaN', 'Infinity', '-Infinity')",
    )
    if special_ids:
        raise RuntimeError(
            f"Refusing exact-money migration: {table}.{column} contains "
            f"non-finite values at row ids {special_ids}; correct them explicitly before retrying"
        )

    overflow_ids = _sample_ids(
        table,
        column,
        f'abs("{column}"::numeric) > {_MAX_NUMERIC_14_2}',
    )
    if overflow_ids:
        raise RuntimeError(
            f"Refusing exact-money migration: {table}.{column} exceeds "
            f"numeric(14,2) at row ids {overflow_ids}; correct them explicitly before retrying"
        )

    # float8 -> numeric canonicalizes ordinary binary representation noise.
    # Any remaining difference is meaningful legacy sub-cent information and
    # must not be silently rounded into a different ledger value.
    subcent_ids = _sample_ids(
        table,
        column,
        f'"{column}"::numeric <> round("{column}"::numeric, 2)',
    )
    if subcent_ids:
        raise RuntimeError(
            f"Refusing exact-money migration: {table}.{column} contains "
            f"sub-cent values at row ids {subcent_ids}; correct them explicitly before retrying"
        )


def upgrade() -> None:
    # This one-time type conversion is the only point at which the original
    # float value is still available. A later corrective revision could report
    # that rounding occurred but could never recover what was discarded, so
    # fail before any DDL/data mutation and leave the whole revision retryable.
    for table, column, _nullable in _MONEY_COLUMNS:
        _preflight_money(table, column)

    op.add_column(
        "farms",
        sa.Column(
            "timezone",
            sa.String(length=64),
            server_default="Asia/Kolkata",
            nullable=False,
        ),
    )
    op.drop_constraint("ck_transactions_amount_nonneg", "transactions", type_="check")
    for table, column, nullable in _MONEY_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.Float(),
            type_=sa.Numeric(14, 2),
            existing_nullable=nullable,
            postgresql_using=f"round({column}::numeric, 2)",
        )
    op.create_check_constraint(
        "ck_transactions_amount_bounded",
        "transactions",
        "amount >= 0 AND amount <= 1000000000",
    )

    op.add_column(
        "transactions",
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.add_column("transactions", sa.Column("source_type", sa.String(length=40), nullable=True))
    op.add_column("transactions", sa.Column("source_id", sa.Integer(), nullable=True))
    op.add_column("transactions", sa.Column("correction_of_id", sa.Integer(), nullable=True))
    op.add_column("transactions", sa.Column("voided_at", sa.DateTime(), nullable=True))
    op.add_column("transactions", sa.Column("voided_by_id", sa.Integer(), nullable=True))
    op.add_column("transactions", sa.Column("void_reason", sa.String(length=255), nullable=True))
    op.create_foreign_key(
        "fk_transactions_correction_of_id",
        "transactions",
        "transactions",
        ["correction_of_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_transactions_voided_by_id",
        "transactions",
        "users",
        ["voided_by_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_transactions_correction_of_id",
        "transactions",
        ["correction_of_id"],
        unique=False,
    )
    op.create_index(
        "uq_transactions_active_source",
        "transactions",
        ["farm_id", "source_type", "source_id"],
        unique=True,
        postgresql_where=sa.text(
            "source_type IS NOT NULL AND source_id IS NOT NULL AND voided_at IS NULL"
        ),
    )

    op.create_table(
        "feed_finished_stock",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("farm_id", sa.Integer(), nullable=False),
        sa.Column("recipe_code", sa.String(length=30), nullable=False),
        sa.Column("qty_on_hand", sa.Float(), nullable=False, server_default="0"),
        sa.CheckConstraint("qty_on_hand >= 0", name="ck_finished_feed_qty_nonneg"),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.ForeignKeyConstraint(["recipe_code"], ["feed_recipes.code"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("farm_id", "recipe_code", name="uq_finished_feed_farm_recipe"),
    )
    op.create_index(
        op.f("ix_feed_finished_stock_farm_id"),
        "feed_finished_stock",
        ["farm_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_feed_finished_stock_recipe_code"),
        "feed_finished_stock",
        ["recipe_code"],
        unique=False,
    )

    op.add_column("tasks", sa.Column("skipped_at", sa.DateTime(), nullable=True))
    op.add_column("tasks", sa.Column("skip_reason", sa.String(length=255), nullable=True))
    op.add_column("tasks", sa.Column("recurring_series_id", sa.String(length=36), nullable=True))
    op.execute(
        "UPDATE tasks SET recurring_series_id = 'legacy-' || id::text "
        "WHERE recur_days IS NOT NULL AND recurring_series_id IS NULL"
    )
    op.execute(
        "UPDATE tasks SET skipped_at = COALESCE(completed_at, timezone('UTC', now())), "
        "skip_reason = COALESCE(skip_reason, 'Skipped before audit timestamps were enabled') "
        "WHERE status = 'SKIPPED' AND skipped_at IS NULL"
    )
    op.create_index(
        op.f("ix_tasks_recurring_series_id"),
        "tasks",
        ["recurring_series_id"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_task_recurring_series_due",
        "tasks",
        ["farm_id", "recurring_series_id", "due_date"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_task_recurring_series_due", "tasks", type_="unique")
    op.drop_index(op.f("ix_tasks_recurring_series_id"), table_name="tasks")
    op.drop_column("tasks", "recurring_series_id")
    op.drop_column("tasks", "skip_reason")
    op.drop_column("tasks", "skipped_at")

    op.drop_index(op.f("ix_feed_finished_stock_recipe_code"), table_name="feed_finished_stock")
    op.drop_index(op.f("ix_feed_finished_stock_farm_id"), table_name="feed_finished_stock")
    op.drop_table("feed_finished_stock")

    op.drop_index("uq_transactions_active_source", table_name="transactions")
    op.drop_index("ix_transactions_correction_of_id", table_name="transactions")
    op.drop_constraint("fk_transactions_voided_by_id", "transactions", type_="foreignkey")
    op.drop_constraint("fk_transactions_correction_of_id", "transactions", type_="foreignkey")
    for column in (
        "void_reason",
        "voided_by_id",
        "voided_at",
        "correction_of_id",
        "source_id",
        "source_type",
        "created_at",
    ):
        op.drop_column("transactions", column)

    op.drop_constraint("ck_transactions_amount_bounded", "transactions", type_="check")
    for table, column, nullable in _MONEY_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.Numeric(14, 2),
            type_=sa.Float(),
            existing_nullable=nullable,
            postgresql_using=f"{column}::double precision",
        )
    op.create_check_constraint("ck_transactions_amount_nonneg", "transactions", "amount >= 0")
    op.drop_column("farms", "timezone")
