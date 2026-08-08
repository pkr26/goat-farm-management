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


def upgrade() -> None:
    op.add_column(
        "farms",
        sa.Column(
            "timezone",
            sa.String(length=64),
            server_default="Asia/Kolkata",
            nullable=False,
        ),
    )
    # PostgreSQL floating-point columns can contain non-finite legacy values.
    # They cannot become money; preserve the row but neutralize a required
    # transaction amount and clear optional price fields before the cast.
    op.execute(
        "UPDATE transactions SET amount = 0 WHERE amount::text IN ('NaN', 'Infinity', '-Infinity')"
    )
    for table, column, nullable in _MONEY_COLUMNS[1:]:
        if nullable:
            op.execute(
                f"UPDATE {table} SET {column} = NULL "
                f"WHERE {column}::text IN ('NaN', 'Infinity', '-Infinity')"
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
        "UPDATE tasks SET skipped_at = COALESCE(completed_at, CURRENT_TIMESTAMP), "
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
