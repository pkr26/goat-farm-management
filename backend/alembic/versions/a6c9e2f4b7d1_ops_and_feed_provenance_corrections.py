"""repair legacy loss attribution, chronology locking, and feed provenance

Revision ID: a6c9e2f4b7d1
Revises: e3f4a5b6c7d9
Create Date: 2026-08-09 23:30:00.000000+00:00

``b9e1c2d3f4a5`` assigned every pre-audit pregnancy loss to the user who
created the breeding service, even though that user may not have observed or
recorded the later loss.  Its fixed legacy note is an explicit provenance
marker, so clear only those fabricated actors.  This truth-preserving repair
is intentionally not reversed on downgrade.

The same historical revision used ``FOR KEY SHARE`` while reading a kidding
date from the kid-mortality trigger.  PostgreSQL permits a concurrent update
of that non-key date under KEY SHARE, allowing both transactions to validate
different snapshots.  Replace the function with a conflicting row lock.

Finally, retain exact feed-inventory, quantity, and unit-price provenance on
new FEED_PURCHASE ledger rows so later audited corrections can update stock
and money together without inverting a cent-rounded total. Historical rows
stay nullable because the missing inputs cannot be inferred safely.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a6c9e2f4b7d1"
down_revision: str | Sequence[str] | None = "e3f4a5b6c7d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_LEGACY_LOSS_NOTE = "Legacy pregnancy-loss row; original date and cause were not captured."


def _replace_mortality_chronology_function(lock_clause: str) -> None:
    if lock_clause not in {"FOR UPDATE", "FOR KEY SHARE"}:
        raise ValueError(f"unsupported row-lock clause: {lock_clause}")
    op.execute(
        sa.text(
            f"""
            CREATE OR REPLACE FUNCTION enforce_kid_mortality_chronology()
            RETURNS trigger AS $$
            DECLARE
              delivered_on date;
            BEGIN
              IF NEW.mortality_reported_at IS NOT NULL THEN
                SELECT date INTO delivered_on
                FROM kidding_records
                WHERE id = NEW.kidding_record_id
                {lock_clause};
                IF delivered_on IS NOT NULL AND NEW.mortality_reported_at < delivered_on THEN
                  RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'kid mortality cannot predate kidding';
                END IF;
              END IF;
              RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )


def upgrade() -> None:
    op.add_column("transactions", sa.Column("feed_inventory_id", sa.Integer(), nullable=True))
    op.add_column(
        "transactions",
        sa.Column("feed_quantity_kg", sa.Numeric(15, 3), nullable=True),
    )
    op.add_column(
        "transactions",
        sa.Column("feed_unit_price_per_kg", sa.Numeric(14, 2), nullable=True),
    )
    op.create_unique_constraint(
        "uq_feed_inventory_farm_id_id",
        "feed_inventory",
        ["farm_id", "id"],
    )
    op.create_foreign_key(
        "fk_transactions_farm_feed_inventory",
        "transactions",
        "feed_inventory",
        ["farm_id", "feed_inventory_id"],
        ["farm_id", "id"],
    )
    op.create_index(
        "ix_transactions_feed_inventory_id",
        "transactions",
        ["feed_inventory_id"],
        unique=False,
    )
    op.create_check_constraint(
        "ck_transactions_feed_purchase_provenance",
        "transactions",
        "(feed_inventory_id IS NULL AND feed_quantity_kg IS NULL "
        "AND feed_unit_price_per_kg IS NULL) OR "
        "(source_type = 'FEED_PURCHASE' AND feed_inventory_id IS NOT NULL "
        "AND feed_quantity_kg IS NOT NULL "
        "AND feed_unit_price_per_kg IS NOT NULL "
        "AND feed_quantity_kg BETWEEN 0.001 AND 1000000 "
        "AND feed_unit_price_per_kg BETWEEN 0 AND 1000000000)",
    )

    # The exact note was written only by b9's legacy backfill. Requiring the
    # UNKNOWN cause and creator equality further narrows the repair so a later,
    # genuinely attributed loss is never cleared merely for similar prose.
    op.execute(
        sa.text(
            """
            UPDATE breeding_records
            SET loss_recorded_by_id = NULL
            WHERE outcome = 'ABORTED'
              AND loss_cause = 'UNKNOWN'
              AND loss_notes = :legacy_note
              AND loss_recorded_by_id = created_by_id
            """
        ).bindparams(legacy_note=_LEGACY_LOSS_NOTE)
    )

    _replace_mortality_chronology_function("FOR UPDATE")


def downgrade() -> None:
    _replace_mortality_chronology_function("FOR KEY SHARE")

    # Do not re-invent an observer for repaired legacy pregnancy losses.
    op.drop_constraint(
        "ck_transactions_feed_purchase_provenance",
        "transactions",
        type_="check",
    )
    op.drop_index("ix_transactions_feed_inventory_id", table_name="transactions")
    op.drop_constraint(
        "fk_transactions_farm_feed_inventory",
        "transactions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_feed_inventory_farm_id_id",
        "feed_inventory",
        type_="unique",
    )
    op.drop_column("transactions", "feed_unit_price_per_kg")
    op.drop_column("transactions", "feed_quantity_kg")
    op.drop_column("transactions", "feed_inventory_id")
