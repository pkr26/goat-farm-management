"""goat-only: drop the buffalo-dairy farm type, milk records and milk ledger

Revision ID: bd201c1cdc1b
Revises: a1b2c3d4e5f6
Create Date: 2026-09-08 00:00:00.000000+00:00

The product is simplified to goat-only. This migration:

- deletes every BUFFALO_DAIRY farm and its dependent rows (the farm-scoped
  tables are emptied child-first);
- drops the ``milk_records`` table and the milk provenance columns/CHECK on
  ``transactions``, and the MILK transaction category;
- drops the ``farm_type`` column (and its CHECKs / composite uniques) from
  ``farms``, ``bucket_definitions``, ``feed_recipes`` and
  ``vaccine_templates``, replacing the composite uniques with single-column
  ones and deleting the dairy reference rows;
- tightens the goat gestation CHECKs on ``breeding_records`` from the
  cross-species 350-day bound to the goat 200-day recording band.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "bd201c1cdc1b"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Child-first order (reverse of metadata dependency order) for the farm-scoped
# tables whose rows must go before the dairy farms themselves can be deleted.
_FARM_CHILD_TABLES = (
    "milk_records",
    "kid_entries",
    "tasks",
    "movement_restriction_actions",
    "kidding_records",
    "transactions",
    "health_events",
    "breeding_records",
    "farm_memberships",
    "animals",
    "simulation_scenarios",
    "roles",
    "purchase_batches",
    "planner_plans",
    "idempotency_records",
    "feeding_records",
    "feed_inventory",
    "feed_finished_stock",
    "bucket_feed_settings",
)


def upgrade() -> None:
    # 0. Grandchild rows keyed by animal_id only: at this revision
    # bucket_moves and weight_records carry no farm_id (their tenant
    # composite FKs arrive in e8b0d2f4a6c1), so they cannot join the
    # farm_id sweep below — but their animals FK blocks that sweep's
    # DELETE FROM animals unless they go first. No-op on a fresh database.
    for table in ("bucket_moves", "weight_records"):
        op.execute(
            f"DELETE FROM {table} WHERE animal_id IN "
            "(SELECT id FROM animals WHERE farm_id IN "
            "(SELECT id FROM farms WHERE farm_type = 'BUFFALO_DAIRY'))"
        )
    # 1. Remove buffalo-dairy tenants entirely (no-op on a fresh database).
    for table in _FARM_CHILD_TABLES:
        op.execute(
            f"DELETE FROM {table} WHERE farm_id IN "
            "(SELECT id FROM farms WHERE farm_type = 'BUFFALO_DAIRY')"
        )
    op.execute("DELETE FROM farms WHERE farm_type = 'BUFFALO_DAIRY'")

    # 2. Milk production records and milk-sale ledger provenance.
    op.drop_table("milk_records")
    op.execute("DELETE FROM transactions WHERE category = 'MILK'")
    op.drop_constraint("ck_transactions_milk_provenance", "transactions", type_="check")
    for column in (
        "milk_litres",
        "milk_unit_price_per_litre",
        "milk_fat_pct",
        "milk_price_per_kg_fat",
    ):
        op.drop_column("transactions", column)
    op.drop_constraint("ck_transactions_category", "transactions", type_="check")
    op.create_check_constraint(
        "ck_transactions_category",
        "transactions",
        "category IN ('ANIMAL_SALE', 'ANIMAL_PURCHASE', 'FEED', 'MEDICINE', "
        "'VET', 'LABOUR', 'EQUIPMENT', 'MANURE', 'OTHER')",
    )

    # 3. Reference tables: goat-only rows, single-column uniques.
    op.execute("DELETE FROM bucket_definitions WHERE farm_type = 'BUFFALO_DAIRY'")
    op.drop_constraint("uq_bucket_definitions_type_code", "bucket_definitions", type_="unique")
    op.drop_constraint("ck_bucket_definitions_farm_type", "bucket_definitions", type_="check")
    op.drop_column("bucket_definitions", "farm_type")
    op.create_unique_constraint("uq_bucket_definitions_code", "bucket_definitions", ["code"])

    # Recipes are referenced by their lines (recipe_id, no cascade, no
    # farm_id to sweep by) and by per-farm finished stock (recipe_code).
    # The dairy-era seed inserted recipes WITH lines on every boot, so the
    # recipe DELETE below aborts with a raw FK violation on any deployed
    # dairy-era database unless the lines go first — the same child-first
    # pattern b3d7f1a5c9e2's downgrade already uses (2026-09-20 audit P1-6).
    op.execute(
        "DELETE FROM feed_finished_stock WHERE recipe_code IN "
        "(SELECT code FROM feed_recipes WHERE farm_type = 'BUFFALO_DAIRY')"
    )
    op.execute(
        "DELETE FROM feed_recipe_lines WHERE recipe_id IN "
        "(SELECT id FROM feed_recipes WHERE farm_type = 'BUFFALO_DAIRY')"
    )
    op.execute("DELETE FROM feed_recipes WHERE farm_type = 'BUFFALO_DAIRY'")
    op.drop_constraint("ck_feed_recipes_farm_type", "feed_recipes", type_="check")
    op.drop_column("feed_recipes", "farm_type")

    op.execute("DELETE FROM vaccine_templates WHERE farm_type = 'BUFFALO_DAIRY'")
    op.drop_constraint("uq_vaccine_templates_type_name", "vaccine_templates", type_="unique")
    op.drop_constraint("ck_vaccine_templates_farm_type", "vaccine_templates", type_="check")
    op.drop_column("vaccine_templates", "farm_type")
    op.create_unique_constraint("uq_vaccine_templates_name", "vaccine_templates", ["name"])

    # 4. The farm itself carries no species any more.
    op.drop_constraint("ck_farms_farm_type", "farms", type_="check")
    op.drop_column("farms", "farm_type")

    # 5. Dairy parlour preset role codes leave the server-owned vocabulary.
    op.drop_constraint("ck_roles_preset_code", "roles", type_="check")
    op.create_check_constraint(
        "ck_roles_preset_code",
        "roles",
        "code IS NULL OR code IN "
        "('ACCOUNTANT', 'BUYER', 'CLEANER', 'CLEANER_MANAGER', 'FEEDER', "
        "'MANAGER', 'MOVER', 'VET', 'VIEWER')",
    )

    # 6. Goat gestation recording band on breeding records.
    op.drop_constraint("ck_breeding_loss_within_max_gestation", "breeding_records", type_="check")
    op.create_check_constraint(
        "ck_breeding_loss_within_max_gestation",
        "breeding_records",
        "loss_date IS NULL OR loss_cause = 'ANIMAL_STATUS_CHANGE' "
        "OR loss_date <= breeding_date + 200",
    )
    op.drop_constraint(
        "ck_breeding_records_result_within_max_gestation", "breeding_records", type_="check"
    )
    op.create_check_constraint(
        "ck_breeding_records_result_within_max_gestation",
        "breeding_records",
        "ultrasound_result_date IS NULL OR ultrasound_result_date <= breeding_date + 200",
    )


def downgrade() -> None:
    """Schema-only roundtrip: re-add the farm_type layer and milk ledger.

    Deleted buffalo-dairy tenants and their rows are NOT restored — the
    dairy seed data (buckets, recipes, vaccines, parlour roles) would be
    recreated by downgrading past b3d7f1a5c9e2 and re-upgrading, and the
    farm_type columns come back with the 'GOAT' default for every
    surviving farm.
    """
    op.drop_constraint(
        "ck_breeding_records_result_within_max_gestation", "breeding_records", type_="check"
    )
    op.create_check_constraint(
        "ck_breeding_records_result_within_max_gestation",
        "breeding_records",
        "ultrasound_result_date IS NULL OR ultrasound_result_date <= breeding_date + 350",
    )
    op.drop_constraint("ck_breeding_loss_within_max_gestation", "breeding_records", type_="check")
    op.create_check_constraint(
        "ck_breeding_loss_within_max_gestation",
        "breeding_records",
        "loss_date IS NULL OR loss_cause = 'ANIMAL_STATUS_CHANGE' "
        "OR loss_date <= breeding_date + 350",
    )

    op.add_column(
        "farms",
        sa.Column("farm_type", sa.String(length=20), nullable=False, server_default="GOAT"),
    )
    op.create_check_constraint(
        "ck_farms_farm_type", "farms", "farm_type IN ('GOAT', 'BUFFALO_DAIRY')"
    )

    op.drop_constraint("uq_vaccine_templates_name", "vaccine_templates", type_="unique")
    op.add_column(
        "vaccine_templates",
        sa.Column("farm_type", sa.String(length=20), nullable=False, server_default="GOAT"),
    )
    op.create_check_constraint(
        "ck_vaccine_templates_farm_type",
        "vaccine_templates",
        "farm_type IN ('GOAT', 'BUFFALO_DAIRY')",
    )
    op.create_unique_constraint(
        "uq_vaccine_templates_type_name", "vaccine_templates", ["farm_type", "name"]
    )

    op.add_column(
        "feed_recipes",
        sa.Column("farm_type", sa.String(length=20), nullable=False, server_default="GOAT"),
    )
    op.create_check_constraint(
        "ck_feed_recipes_farm_type", "feed_recipes", "farm_type IN ('GOAT', 'BUFFALO_DAIRY')"
    )

    op.drop_constraint("uq_bucket_definitions_code", "bucket_definitions", type_="unique")
    op.add_column(
        "bucket_definitions",
        sa.Column("farm_type", sa.String(length=20), nullable=False, server_default="GOAT"),
    )
    op.create_check_constraint(
        "ck_bucket_definitions_farm_type",
        "bucket_definitions",
        "farm_type IN ('GOAT', 'BUFFALO_DAIRY')",
    )
    op.create_unique_constraint(
        "uq_bucket_definitions_type_code", "bucket_definitions", ["farm_type", "code"]
    )

    op.drop_constraint("ck_transactions_category", "transactions", type_="check")
    op.create_check_constraint(
        "ck_transactions_category",
        "transactions",
        "category IN ('ANIMAL_SALE', 'ANIMAL_PURCHASE', 'FEED', 'MEDICINE', "
        "'VET', 'LABOUR', 'EQUIPMENT', 'MILK', 'MANURE', 'OTHER')",
    )
    op.add_column("transactions", sa.Column("milk_litres", sa.Numeric(12, 3), nullable=True))
    op.add_column(
        "transactions", sa.Column("milk_unit_price_per_litre", sa.Numeric(14, 2), nullable=True)
    )
    op.add_column("transactions", sa.Column("milk_fat_pct", sa.Numeric(4, 2), nullable=True))
    op.add_column(
        "transactions", sa.Column("milk_price_per_kg_fat", sa.Numeric(14, 2), nullable=True)
    )
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
    op.create_table(
        "milk_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("farm_id", sa.Integer(), nullable=False),
        sa.Column("animal_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("shift", sa.String(length=10), nullable=False),
        sa.Column("litres", sa.Numeric(10, 3, asdecimal=False), nullable=False),
        sa.Column("fat_pct", sa.Numeric(4, 2, asdecimal=False), nullable=True),
        sa.Column("notes", sa.String(length=255), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        # correction-audit columns added by c5a8e1f3b7d2: recreating the
        # table at its end-of-chain shape keeps older migrations' downgrades
        # (which drop these columns) valid during a full downgrade walk.
        sa.Column("original_litres", sa.Numeric(10, 3, asdecimal=False), nullable=True),
        sa.Column("original_fat_pct", sa.Numeric(4, 2, asdecimal=False), nullable=True),
        sa.Column("original_notes", sa.Text(), nullable=True),
        sa.Column("original_recorded_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["original_recorded_by_id"],
            ["users.id"],
            name="fk_milk_records_original_recorded_by",
        ),
        sa.Column("corrected_at", sa.DateTime(), nullable=True),
        sa.Column("correction_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.ForeignKeyConstraint(["animal_id"], ["animals.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["farm_id", "animal_id"],
            ["animals.farm_id", "animals.id"],
            name="fk_milk_records_farm_animal",
        ),
        sa.UniqueConstraint("animal_id", "date", "shift", name="uq_milk_records_animal_day_shift"),
        sa.UniqueConstraint("farm_id", "id", name="uq_milk_records_farm_id_id"),
        sa.CheckConstraint(
            "shift IN ('MORNING', 'AFTERNOON', 'NIGHT')", name="ck_milk_records_shift"
        ),
        sa.CheckConstraint(
            "litres > 0 AND litres <= 100 AND litres::text NOT IN ('NaN', 'Infinity', '-Infinity')",
            name="ck_milk_records_litres",
        ),
        sa.CheckConstraint(
            "fat_pct IS NULL OR (fat_pct BETWEEN 3 AND 12 AND "
            "fat_pct::text NOT IN ('NaN', 'Infinity', '-Infinity'))",
            name="ck_milk_records_fat_pct",
        ),
    )
    op.create_index("ix_milk_records_farm_id", "milk_records", ["farm_id"])
    op.create_index("ix_milk_records_animal_id", "milk_records", ["animal_id"])
    op.create_index("ix_milk_records_farm_date_id", "milk_records", ["farm_id", "date", "id"])
    op.create_index(
        "ix_milk_records_farm_animal_date_id",
        "milk_records",
        ["farm_id", "animal_id", "date", "id"],
    )

    op.drop_constraint("ck_roles_preset_code", "roles", type_="check")
    op.create_check_constraint(
        "ck_roles_preset_code",
        "roles",
        "code IS NULL OR code IN "
        "('ACCOUNTANT', 'BUYER', 'CALF_ATTENDANT', 'CLEANER', 'CLEANER_MANAGER', "
        "'FEEDER', 'MANAGER', 'MILKER', 'MILK_QC', 'MOVER', 'VET', 'VIEWER')",
    )
