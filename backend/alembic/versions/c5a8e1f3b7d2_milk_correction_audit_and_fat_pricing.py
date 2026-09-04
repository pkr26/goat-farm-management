"""milk correction audit trail and fat-based milk pricing

Revision ID: c5a8e1f3b7d2
Revises: b3d7f1a5c9e2
Create Date: 2026-08-31 00:00:00.000000 +00:00

Two audit findings, both in the dairy ledger:

- Re-submitting a milking silently overwrote the previous reading with no
  audit trail. Finance corrects by voiding and replacing a row; milk cannot
  (the (animal, date, shift) unique constraint is what stops a corrected
  milking from being counted twice), so ``milk_records`` gains in-place
  correction audit columns instead: the first submitted reading is frozen
  in ``original_*`` and every correction stamps ``corrected_at`` and its
  ``correction_reason``. All are nullable — existing rows were recorded
  before corrections carried provenance and none of it can be inferred.
  ``corrected_at`` follows the codebase convention of naive-UTC ``timestamp``
  columns (``milk_records.created_at``, ``transactions.voided_at``) rather
  than ``timestamptz`` so one accessor/serializer serves every audit stamp.

- MILK income provenance carried litres and a flat ₹/litre only, while real
  dairy procurement pays on fat. ``transactions`` gains the optional pair
  ``milk_fat_pct`` / ``milk_price_per_kg_fat`` and the provenance CHECK is
  widened so either pricing basis may appear alone (existing ₹/litre rows
  remain valid; no pricing basis is still required at the DB layer — the
  API schema refuses incomplete provenance before a row is written).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c5a8e1f3b7d2"
down_revision: str | Sequence[str] | None = "b3d7f1a5c9e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "milk_records",
        sa.Column("original_litres", sa.Numeric(10, 3, asdecimal=False), nullable=True),
    )
    op.add_column(
        "milk_records",
        sa.Column("original_fat_pct", sa.Numeric(4, 2, asdecimal=False), nullable=True),
    )
    op.add_column("milk_records", sa.Column("original_notes", sa.Text(), nullable=True))
    op.add_column("milk_records", sa.Column("original_recorded_by_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_milk_records_original_recorded_by",
        "milk_records",
        "users",
        ["original_recorded_by_id"],
        ["id"],
    )
    op.add_column("milk_records", sa.Column("corrected_at", sa.DateTime(), nullable=True))
    op.add_column("milk_records", sa.Column("correction_reason", sa.Text(), nullable=True))

    op.add_column("transactions", sa.Column("milk_fat_pct", sa.Numeric(4, 2), nullable=True))
    op.add_column(
        "transactions", sa.Column("milk_price_per_kg_fat", sa.Numeric(14, 2), nullable=True)
    )
    op.drop_constraint("ck_transactions_milk_provenance", "transactions", type_="check")
    op.create_check_constraint(
        "ck_transactions_milk_provenance",
        "transactions",
        "(milk_litres IS NULL AND milk_unit_price_per_litre IS NULL "
        "AND milk_fat_pct IS NULL AND milk_price_per_kg_fat IS NULL) OR "
        "(category = 'MILK' AND type = 'INCOME' "
        "AND milk_litres BETWEEN 0.001 AND 1000000 "
        "AND (milk_unit_price_per_litre IS NULL "
        "OR milk_unit_price_per_litre BETWEEN 0 AND 1000000000) "
        "AND ((milk_fat_pct IS NULL AND milk_price_per_kg_fat IS NULL) OR "
        "(milk_fat_pct BETWEEN 0 AND 12 "
        "AND milk_price_per_kg_fat BETWEEN 0 AND 1000000000)))",
    )


def downgrade() -> None:
    # The widened CHECK is dropped with the columns it governs; restoring the
    # original narrower text keeps a downgrade->upgrade roundtrip identical.
    op.drop_constraint("ck_transactions_milk_provenance", "transactions", type_="check")
    op.create_check_constraint(
        "ck_transactions_milk_provenance",
        "transactions",
        "(milk_litres IS NULL AND milk_unit_price_per_litre IS NULL) OR "
        "(category = 'MILK' AND type = 'INCOME' "
        "AND milk_litres BETWEEN 0.001 AND 1000000 "
        "AND milk_unit_price_per_litre BETWEEN 0 AND 1000000000)",
    )
    op.drop_column("transactions", "milk_price_per_kg_fat")
    op.drop_column("transactions", "milk_fat_pct")

    op.drop_column("milk_records", "correction_reason")
    op.drop_column("milk_records", "corrected_at")
    op.drop_constraint(
        "fk_milk_records_original_recorded_by",
        "milk_records",
        type_="foreignkey",
    )
    op.drop_column("milk_records", "original_recorded_by_id")
    op.drop_column("milk_records", "original_notes")
    op.drop_column("milk_records", "original_fat_pct")
    op.drop_column("milk_records", "original_litres")
