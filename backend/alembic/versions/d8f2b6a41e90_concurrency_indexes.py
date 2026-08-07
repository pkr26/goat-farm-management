"""concurrency indexes

Revision ID: d8f2b6a41e90
Revises: c4b72167db86
Create Date: 2026-08-07 04:55:34.000000+00:00

Schema hygiene from the adversarial audit (B4/B9):

- uq_breeding_open_pregnancy: partial UNIQUE on breeding_records(doe_id)
  WHERE outcome = 'PENDING'. Every breeding row is born PENDING, so this
  serializes concurrent double-creates that race past the service pre-check
  (the loser gets an IntegrityError -> 409). CONFIRMED_PREGNANT is
  deliberately NOT in the predicate: record_kidding resolves a pregnancy by
  inserting the KiddingRecord row, not by changing the outcome, so covering
  CONFIRMED would permanently block re-breeding after a first kidding.
- uq_feed_inventory_farm_ingredient: one stock row per (farm, ingredient) —
  mix_feed_batch / add_feed_stock look the row up by that pair.
- Plain indexes on hot filter/join columns (tasks tabs, animal-scoped
  lists, recipe lines, transaction dates).

DIRTY-DATA NOTE: the unique index/constraint below fail to build on a
database that already suffered the races they prevent. Dedupe BEFORE
applying this migration, e.g.:

  -- duplicate open pregnancies per doe: abort all but the oldest PENDING row
  UPDATE breeding_records SET outcome = 'ABORTED'
  WHERE id IN (
      SELECT id FROM (
          SELECT id,
                 ROW_NUMBER() OVER (PARTITION BY doe_id ORDER BY id) AS rn
          FROM breeding_records
          WHERE outcome = 'PENDING'
      ) AS dupes
      WHERE rn > 1
  );

  -- duplicate (farm_id, ingredient) feed rows: fold the duplicates' stock
  -- into the oldest row, then drop the duplicates
  WITH ranked AS (
      SELECT id, farm_id, ingredient, qty_on_hand,
             ROW_NUMBER() OVER (PARTITION BY farm_id, ingredient ORDER BY id) AS rn,
             MIN(id) OVER (PARTITION BY farm_id, ingredient) AS keeper_id
      FROM feed_inventory
  ),
  dupes AS (
      SELECT keeper_id, SUM(qty_on_hand) AS extra_qty
      FROM ranked WHERE rn > 1
      GROUP BY keeper_id
  )
  UPDATE feed_inventory f
  SET qty_on_hand = f.qty_on_hand + d.extra_qty
  FROM dupes d
  WHERE f.id = d.keeper_id;

  DELETE FROM feed_inventory
  WHERE id IN (
      SELECT id FROM (
          SELECT id,
                 ROW_NUMBER() OVER (
                     PARTITION BY farm_id, ingredient ORDER BY id
                 ) AS rn
          FROM feed_inventory
      ) AS ranked
      WHERE rn > 1
  );
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d8f2b6a41e90"
down_revision: str | Sequence[str] | None = "c4b72167db86"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_breeding_open_pregnancy",
        "breeding_records",
        ["doe_id"],
        unique=True,
        postgresql_where=sa.text("outcome = 'PENDING'"),
    )
    op.create_unique_constraint(
        "uq_feed_inventory_farm_ingredient", "feed_inventory", ["farm_id", "ingredient"]
    )
    op.create_index(op.f("ix_animals_dam_id"), "animals", ["dam_id"], unique=False)
    op.create_index(
        op.f("ix_animals_purchase_batch_id"), "animals", ["purchase_batch_id"], unique=False
    )
    op.create_index(
        op.f("ix_breeding_records_buck_id"), "breeding_records", ["buck_id"], unique=False
    )
    op.create_index(
        op.f("ix_breeding_records_doe_id"), "breeding_records", ["doe_id"], unique=False
    )
    op.create_index(
        op.f("ix_farm_memberships_role_id"), "farm_memberships", ["role_id"], unique=False
    )
    op.create_index(
        op.f("ix_feed_recipe_lines_recipe_id"), "feed_recipe_lines", ["recipe_id"], unique=False
    )
    op.create_index(
        op.f("ix_health_events_animal_id"), "health_events", ["animal_id"], unique=False
    )
    op.create_index(
        op.f("ix_kid_entries_kidding_record_id"),
        "kid_entries",
        ["kidding_record_id"],
        unique=False,
    )
    op.create_index(op.f("ix_tasks_animal_id"), "tasks", ["animal_id"], unique=False)
    op.create_index(op.f("ix_tasks_assigned_role_id"), "tasks", ["assigned_role_id"], unique=False)
    op.create_index(op.f("ix_tasks_assigned_user_id"), "tasks", ["assigned_user_id"], unique=False)
    op.create_index(op.f("ix_tasks_due_date"), "tasks", ["due_date"], unique=False)
    op.create_index(op.f("ix_tasks_status"), "tasks", ["status"], unique=False)
    op.create_index(op.f("ix_transactions_date"), "transactions", ["date"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_transactions_date"), table_name="transactions")
    op.drop_index(op.f("ix_tasks_status"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_due_date"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_assigned_user_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_assigned_role_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_animal_id"), table_name="tasks")
    op.drop_index(op.f("ix_kid_entries_kidding_record_id"), table_name="kid_entries")
    op.drop_index(op.f("ix_health_events_animal_id"), table_name="health_events")
    op.drop_index(op.f("ix_feed_recipe_lines_recipe_id"), table_name="feed_recipe_lines")
    op.drop_index(op.f("ix_farm_memberships_role_id"), table_name="farm_memberships")
    op.drop_index(op.f("ix_breeding_records_doe_id"), table_name="breeding_records")
    op.drop_index(op.f("ix_breeding_records_buck_id"), table_name="breeding_records")
    op.drop_index(op.f("ix_animals_purchase_batch_id"), table_name="animals")
    op.drop_index(op.f("ix_animals_dam_id"), table_name="animals")
    op.drop_constraint("uq_feed_inventory_farm_ingredient", "feed_inventory", type_="unique")
    op.drop_index("uq_breeding_open_pregnancy", table_name="breeding_records")
