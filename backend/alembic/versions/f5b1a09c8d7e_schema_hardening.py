"""FK ondelete, composite indices, CHECK constraints

Revision ID: f5b1a09c8d7e
Revises: e4a7c1f29b63
Create Date: 2026-08-08 12:00:00.000000+00:00

Schema hardening:

- FK ondelete policies: FarmMembership.user_id/farm_id/role_id cascade
  with the parent; Farm.owner_id is restricted (owner must be transferred
  before deletion); Animal.dam_id/sire_id set NULL (lineage disappears
  but the child stays).
- Composite index (farm_id, status, due_date) on tasks: every
  dashboard/tab query filters exactly that trio.
- Partial index on active animals: the hottest predicate.
- CHECK constraints on money/weight/BCS: DB-level guards against
  negatives. Full Numeric conversion is a separate, larger change.
- Hot-column indices for order-by/range queries.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f5b1a09c8d7e"
down_revision: str | Sequence[str] | None = "e4a7c1f29b63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # FK ondelete policies (drop + recreate with the new ON DELETE clause).
    # ------------------------------------------------------------------
    op.drop_constraint(
        op.f("farm_memberships_user_id_fkey"), "farm_memberships", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("farm_memberships_farm_id_fkey"), "farm_memberships", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("farm_memberships_role_id_fkey"), "farm_memberships", type_="foreignkey"
    )
    op.create_foreign_key(
        op.f("farm_memberships_user_id_fkey"),
        "farm_memberships",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("farm_memberships_farm_id_fkey"),
        "farm_memberships",
        "farms",
        ["farm_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("farm_memberships_role_id_fkey"),
        "farm_memberships",
        "roles",
        ["role_id"],
        ["id"],
        ondelete="CASCADE",
    )
    # Farm.owner_id: refuse the DELETE so orphaned farms can never happen —
    # transfer ownership first (endpoint TBD).
    op.drop_constraint(op.f("farms_owner_id_fkey"), "farms", type_="foreignkey")
    op.create_foreign_key(
        op.f("farms_owner_id_fkey"), "farms", "users", ["owner_id"], ["id"], ondelete="RESTRICT"
    )
    # Animal.dam_id / sire_id: lineage disappears but the child stays.
    op.drop_constraint(op.f("animals_dam_id_fkey"), "animals", type_="foreignkey")
    op.drop_constraint(op.f("animals_sire_id_fkey"), "animals", type_="foreignkey")
    op.create_foreign_key(
        op.f("animals_dam_id_fkey"), "animals", "animals", ["dam_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        op.f("animals_sire_id_fkey"), "animals", "animals", ["sire_id"], ["id"], ondelete="SET NULL"
    )

    # ------------------------------------------------------------------
    # Composite + partial indices for hot query paths.
    # ------------------------------------------------------------------
    op.create_index(
        "ix_tasks_farm_status_due", "tasks", ["farm_id", "status", "due_date"], unique=False
    )
    # Partial index: 95%+ of Animal queries filter status='ACTIVE'.
    op.execute("CREATE INDEX ix_animals_farm_active ON animals(farm_id) WHERE status = 'ACTIVE'")
    # Order-by / range predicate targets.
    op.create_index("ix_weight_records_date", "weight_records", ["date"], unique=False)
    op.create_index("ix_health_events_date", "health_events", ["date"], unique=False)
    op.create_index(
        "ix_breeding_records_breeding_date", "breeding_records", ["breeding_date"], unique=False
    )
    op.create_index(
        "ix_breeding_records_expected_kidding_date",
        "breeding_records",
        ["expected_kidding_date"],
        unique=False,
    )
    op.create_index("ix_kidding_records_doe_id", "kidding_records", ["doe_id"], unique=False)
    op.create_index(
        "ix_transactions_related_animal_id",
        "transactions",
        ["related_animal_id"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # CHECK constraints — DB-level guards against negative money/weight.
    # Deliberately narrow: nothing catches every future currency drift; this
    # is the contained defense until the Numeric migration.
    # ------------------------------------------------------------------
    op.create_check_constraint("ck_transactions_amount_nonneg", "transactions", "amount >= 0")
    op.create_check_constraint(
        "ck_animals_birth_weight_nonneg",
        "animals",
        "(birth_weight IS NULL OR birth_weight >= 0)",
    )
    op.create_check_constraint(
        "ck_animals_purchase_price_nonneg",
        "animals",
        "(purchase_price IS NULL OR purchase_price >= 0)",
    )
    op.create_check_constraint(
        "ck_animals_sale_price_nonneg",
        "animals",
        "(sale_price IS NULL OR sale_price >= 0)",
    )
    op.create_check_constraint(
        "ck_weight_records_weight_positive", "weight_records", "weight_kg > 0"
    )
    op.create_check_constraint(
        "ck_weight_records_bcs_range",
        "weight_records",
        "(bcs IS NULL OR bcs BETWEEN 1 AND 5)",
    )
    op.create_check_constraint(
        "ck_health_events_cost_nonneg",
        "health_events",
        "(cost IS NULL OR cost >= 0)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_health_events_cost_nonneg", "health_events", type_="check")
    op.drop_constraint("ck_weight_records_bcs_range", "weight_records", type_="check")
    op.drop_constraint("ck_weight_records_weight_positive", "weight_records", type_="check")
    op.drop_constraint("ck_animals_sale_price_nonneg", "animals", type_="check")
    op.drop_constraint("ck_animals_purchase_price_nonneg", "animals", type_="check")
    op.drop_constraint("ck_animals_birth_weight_nonneg", "animals", type_="check")
    op.drop_constraint("ck_transactions_amount_nonneg", "transactions", type_="check")

    op.drop_index("ix_transactions_related_animal_id", table_name="transactions")
    op.drop_index("ix_kidding_records_doe_id", table_name="kidding_records")
    op.drop_index("ix_breeding_records_expected_kidding_date", table_name="breeding_records")
    op.drop_index("ix_breeding_records_breeding_date", table_name="breeding_records")
    op.drop_index("ix_health_events_date", table_name="health_events")
    op.drop_index("ix_weight_records_date", table_name="weight_records")
    op.execute("DROP INDEX IF EXISTS ix_animals_farm_active")
    op.drop_index("ix_tasks_farm_status_due", table_name="tasks")

    op.drop_constraint(op.f("animals_sire_id_fkey"), "animals", type_="foreignkey")
    op.drop_constraint(op.f("animals_dam_id_fkey"), "animals", type_="foreignkey")
    op.create_foreign_key(op.f("animals_sire_id_fkey"), "animals", "animals", ["sire_id"], ["id"])
    op.create_foreign_key(op.f("animals_dam_id_fkey"), "animals", "animals", ["dam_id"], ["id"])

    op.drop_constraint(op.f("farms_owner_id_fkey"), "farms", type_="foreignkey")
    op.create_foreign_key(op.f("farms_owner_id_fkey"), "farms", "users", ["owner_id"], ["id"])

    op.drop_constraint(
        op.f("farm_memberships_role_id_fkey"), "farm_memberships", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("farm_memberships_farm_id_fkey"), "farm_memberships", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("farm_memberships_user_id_fkey"), "farm_memberships", type_="foreignkey"
    )
    op.create_foreign_key(
        op.f("farm_memberships_role_id_fkey"),
        "farm_memberships",
        "roles",
        ["role_id"],
        ["id"],
    )
    op.create_foreign_key(
        op.f("farm_memberships_farm_id_fkey"),
        "farm_memberships",
        "farms",
        ["farm_id"],
        ["id"],
    )
    op.create_foreign_key(
        op.f("farm_memberships_user_id_fkey"),
        "farm_memberships",
        "users",
        ["user_id"],
        ["id"],
    )
