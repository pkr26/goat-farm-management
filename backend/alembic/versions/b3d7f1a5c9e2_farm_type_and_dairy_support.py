"""farm type (goat / buffalo dairy), AI breeding, milk records

Revision ID: b3d7f1a5c9e2
Revises: e7f9a1b3c5d8
Create Date: 2026-08-30 00:00:00.000000 +00:00

Adds multi-species support alongside the existing goat model:

- ``farms.farm_type`` ('GOAT' | 'BUFFALO_DAIRY'); every pre-existing farm is a
  goat farm via the server default.
- Species-scoped reference data: ``bucket_definitions`` and
  ``vaccine_templates`` are keyed per farm type (the ten lifecycle stage codes
  are shared, each species labels them its own way), ``feed_recipes`` carries
  a farm-type namespace.
- AI breeding: ``breeding_records.method`` gains AI / AI_SEXED, ``buck_id``
  becomes nullable and a ``semen_sire_name`` column names the semen bull.
- Dairy operations: ``milk_records`` (per-animal, per-shift production) and
  optional milk sale provenance columns on ``transactions``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b3d7f1a5c9e2"
down_revision: str | Sequence[str] | None = "e7f9a1b3c5d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "farms",
        sa.Column("farm_type", sa.String(length=20), nullable=False, server_default="GOAT"),
    )
    op.create_check_constraint(
        "ck_farms_farm_type", "farms", "farm_type IN ('GOAT', 'BUFFALO_DAIRY')"
    )

    op.add_column(
        "bucket_definitions",
        sa.Column("farm_type", sa.String(length=20), nullable=False, server_default="GOAT"),
    )
    op.create_check_constraint(
        "ck_bucket_definitions_farm_type",
        "bucket_definitions",
        "farm_type IN ('GOAT', 'BUFFALO_DAIRY')",
    )
    # The initial schema created the code uniqueness as an unnamed constraint;
    # PostgreSQL named it ``<table>_<column>_key``.
    op.drop_constraint("bucket_definitions_code_key", "bucket_definitions", type_="unique")
    op.create_unique_constraint(
        "uq_bucket_definitions_type_code", "bucket_definitions", ["farm_type", "code"]
    )

    op.add_column(
        "feed_recipes",
        sa.Column("farm_type", sa.String(length=20), nullable=False, server_default="GOAT"),
    )
    op.create_check_constraint(
        "ck_feed_recipes_farm_type", "feed_recipes", "farm_type IN ('GOAT', 'BUFFALO_DAIRY')"
    )

    op.add_column(
        "vaccine_templates",
        sa.Column("farm_type", sa.String(length=20), nullable=False, server_default="GOAT"),
    )
    op.create_check_constraint(
        "ck_vaccine_templates_farm_type",
        "vaccine_templates",
        "farm_type IN ('GOAT', 'BUFFALO_DAIRY')",
    )
    op.drop_constraint("vaccine_templates_name_key", "vaccine_templates", type_="unique")
    op.create_unique_constraint(
        "uq_vaccine_templates_type_name", "vaccine_templates", ["farm_type", "name"]
    )

    op.drop_constraint("ck_breeding_records_method", "breeding_records", type_="check")
    op.create_check_constraint(
        "ck_breeding_records_method",
        "breeding_records",
        "method IN ('NATURAL', 'AI', 'AI_SEXED')",
    )
    op.alter_column("breeding_records", "buck_id", existing_type=sa.Integer(), nullable=True)
    op.add_column(
        "breeding_records", sa.Column("semen_sire_name", sa.String(length=120), nullable=True)
    )
    op.create_check_constraint(
        "ck_breeding_records_sire_identity",
        "breeding_records",
        "(method <> 'NATURAL' OR (buck_id IS NOT NULL AND semen_sire_name IS NULL)) "
        "AND (semen_sire_name IS NULL OR method IN ('AI', 'AI_SEXED'))",
    )

    op.add_column("transactions", sa.Column("milk_litres", sa.Numeric(12, 3), nullable=True))
    op.add_column(
        "transactions", sa.Column("milk_unit_price_per_litre", sa.Numeric(14, 2), nullable=True)
    )
    op.create_check_constraint(
        "ck_transactions_milk_provenance",
        "transactions",
        "(milk_litres IS NULL AND milk_unit_price_per_litre IS NULL) OR "
        "(category = 'MILK' AND type = 'INCOME' "
        "AND milk_litres BETWEEN 0.001 AND 1000000 "
        "AND milk_unit_price_per_litre BETWEEN 0 AND 1000000000)",
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


def downgrade() -> None:
    op.drop_index("ix_milk_records_farm_animal_date_id", table_name="milk_records")
    op.drop_index("ix_milk_records_farm_date_id", table_name="milk_records")
    op.drop_index("ix_milk_records_animal_id", table_name="milk_records")
    op.drop_index("ix_milk_records_farm_id", table_name="milk_records")
    op.drop_table("milk_records")

    op.drop_constraint("ck_transactions_milk_provenance", "transactions", type_="check")
    op.drop_column("transactions", "milk_unit_price_per_litre")
    op.drop_column("transactions", "milk_litres")

    op.drop_constraint("ck_breeding_records_sire_identity", "breeding_records", type_="check")
    op.drop_column("breeding_records", "semen_sire_name")
    op.alter_column("breeding_records", "buck_id", existing_type=sa.Integer(), nullable=False)
    op.drop_constraint("ck_breeding_records_method", "breeding_records", type_="check")
    op.create_check_constraint(
        "ck_breeding_records_method", "breeding_records", "method IN ('NATURAL')"
    )

    # Drop the species-scoped uniqueness and remove the dairy rows BEFORE
    # restoring the single-column uniqueness: while both species' rows exist,
    # names like "FMD" are duplicated and the unique constraint would fail.
    op.drop_constraint("uq_vaccine_templates_type_name", "vaccine_templates", type_="unique")
    op.drop_constraint("ck_vaccine_templates_farm_type", "vaccine_templates", type_="check")
    op.execute("DELETE FROM vaccine_templates WHERE farm_type <> 'GOAT'")
    # Restore the ORIGINAL auto-generated names: a downgrade→upgrade roundtrip
    # re-runs this migration's upgrade, which drops the auto-named constraints.
    op.create_unique_constraint("vaccine_templates_name_key", "vaccine_templates", ["name"])
    op.drop_column("vaccine_templates", "farm_type")

    op.drop_constraint("ck_feed_recipes_farm_type", "feed_recipes", type_="check")
    # Recipes are referenced by their lines and by per-farm finished stock;
    # clear those before removing the dairy rows themselves.
    op.execute(
        "DELETE FROM feed_finished_stock WHERE recipe_code IN "
        "(SELECT code FROM feed_recipes WHERE farm_type <> 'GOAT')"
    )
    op.execute(
        "DELETE FROM feed_recipe_lines WHERE recipe_id IN "
        "(SELECT id FROM feed_recipes WHERE farm_type <> 'GOAT')"
    )
    op.execute("DELETE FROM feed_recipes WHERE farm_type <> 'GOAT'")
    op.drop_column("feed_recipes", "farm_type")

    op.drop_constraint("uq_bucket_definitions_type_code", "bucket_definitions", type_="unique")
    op.drop_constraint("ck_bucket_definitions_farm_type", "bucket_definitions", type_="check")
    op.execute("DELETE FROM bucket_definitions WHERE farm_type <> 'GOAT'")
    op.create_unique_constraint("bucket_definitions_code_key", "bucket_definitions", ["code"])
    op.drop_column("bucket_definitions", "farm_type")

    op.drop_constraint("ck_farms_farm_type", "farms", type_="check")
    op.drop_column("farms", "farm_type")
