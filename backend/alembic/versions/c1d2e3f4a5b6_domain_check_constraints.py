"""Enforce domain value and state coherence with PostgreSQL CHECKs.

Revision ID: c1d2e3f4a5b6
Revises: b9e1c2d3f4a5
Create Date: 2026-08-09 04:00:00.000000+00:00

Every condition is preflighted before any constraint is installed. An upgrade
therefore fails atomically with the first ten offending primary keys instead
of partially hardening a database or silently rewriting historical facts.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: str | Sequence[str] | None = "b9e1c2d3f4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_BUCKETS = (
    "'QUARANTINE', 'FOUNDATION', 'BREEDING', 'PREGNANCY_EARLY', "
    "'PREGNANCY_LATE', 'DELIVERY', 'RECOVERY', 'RESTING', "
    "'MALE_KIDS', 'FEMALE_KIDS'"
)
_INGREDIENT_CATEGORIES = "'ROUGHAGE_WET', 'ROUGHAGE_DRY', 'CONCENTRATE'"

# (table, constraint name, CHECK expression). These are release artifacts, not
# user-controlled identifiers; quoting keeps the generated DDL explicit.
_CONSTRAINTS: tuple[tuple[str, str, str], ...] = (
    ("animals", "ck_animals_sex", "sex IN ('M', 'F')"),
    (
        "animals",
        "ck_animals_birth_type",
        "birth_type IS NULL OR birth_type IN "
        "('SINGLE', 'TWIN', 'TRIPLET', 'QUADRUPLET', 'MULTIPLET')",
    ),
    ("animals", "ck_animals_source", "source IN ('BORN', 'PURCHASED')"),
    (
        "animals",
        "ck_animals_current_bucket",
        f"current_bucket IN ({_BUCKETS})",
    ),
    (
        "animals",
        "ck_animals_status",
        "status IN ('ACTIVE', 'SOLD', 'DEAD', 'CULLED')",
    ),
    (
        "animals",
        "ck_animals_source_fields",
        "(source = 'BORN' AND purchase_date IS NULL AND purchase_price IS NULL "
        "AND seller_name IS NULL AND purchase_batch_id IS NULL) OR "
        "(source = 'PURCHASED' AND birth_type IS NULL AND birth_weight IS NULL)",
    ),
    (
        "animals",
        "ck_animals_status_date",
        "(status = 'ACTIVE' AND status_date IS NULL) OR "
        "(status IN ('SOLD', 'DEAD', 'CULLED') AND status_date IS NOT NULL)",
    ),
    (
        "animals",
        "ck_animals_sale_fields",
        "status = 'SOLD' OR (sale_price IS NULL AND buyer_name IS NULL)",
    ),
    (
        "animals",
        "ck_animals_mortality_fields",
        "status = 'DEAD' OR (mortality_cause IS NULL AND mortality_reported_at IS NULL)",
    ),
    (
        "animals",
        "ck_animals_bucket_sex",
        "(current_bucket <> 'MALE_KIDS' OR sex = 'M') AND "
        "(current_bucket <> 'FEMALE_KIDS' OR sex = 'F') AND "
        "(current_bucket NOT IN "
        "('PREGNANCY_EARLY', 'PREGNANCY_LATE', 'DELIVERY', 'RESTING') OR sex = 'F')",
    ),
    (
        "animals",
        "ck_animals_parent_identity",
        "(dam_id IS NULL OR dam_id <> id) AND "
        "(sire_id IS NULL OR sire_id <> id) AND "
        "(dam_id IS NULL OR sire_id IS NULL OR dam_id <> sire_id)",
    ),
    (
        "animals",
        "ck_animals_birth_weight_bounded",
        "birth_weight IS NULL OR "
        "(birth_weight >= 0 AND birth_weight <= 1000 AND "
        "birth_weight::text NOT IN ('NaN', 'Infinity', '-Infinity'))",
    ),
    (
        "animals",
        "ck_animals_purchase_price_bounded",
        "purchase_price IS NULL OR purchase_price <= 1000000000",
    ),
    (
        "animals",
        "ck_animals_sale_price_bounded",
        "sale_price IS NULL OR sale_price <= 1000000000",
    ),
    (
        "animals",
        "ck_animals_restriction_reason",
        "movement_restricted IS FALSE OR "
        "(restriction_reason IS NOT NULL AND btrim(restriction_reason) <> '')",
    ),
    (
        "animals",
        "ck_animals_suspected_disease_hold",
        "suspected_scheduled_disease IS FALSE OR "
        "(movement_restricted IS TRUE AND suspected_disease IS NOT NULL "
        "AND btrim(suspected_disease) <> '')",
    ),
    (
        "animals",
        "ck_animals_clearance_audit",
        "(restriction_cleared_at IS NULL AND restriction_clearance_reference IS NULL) OR "
        "(restriction_cleared_at IS NOT NULL AND restriction_clearance_reference IS NOT NULL "
        "AND btrim(restriction_clearance_reference) <> '')",
    ),
    (
        "weight_records",
        "ck_weight_records_weight_bounded",
        "weight_kg <= 1000 AND weight_kg::text NOT IN ('NaN', 'Infinity', '-Infinity')",
    ),
    (
        "bucket_moves",
        "ck_bucket_moves_from_bucket",
        f"from_bucket IS NULL OR from_bucket IN ({_BUCKETS})",
    ),
    ("bucket_moves", "ck_bucket_moves_to_bucket", f"to_bucket IN ({_BUCKETS})"),
    (
        "bucket_definitions",
        "ck_bucket_definitions_code",
        f"code IN ({_BUCKETS})",
    ),
    (
        "bucket_definitions",
        "ck_bucket_definitions_daily_kg",
        "daily_kg_per_head > 0 AND daily_kg_per_head <= 1000000 AND "
        "daily_kg_per_head::text NOT IN ('NaN', 'Infinity', '-Infinity')",
    ),
    (
        "bucket_definitions",
        "ck_bucket_definitions_sort_order",
        "sort_order >= 0",
    ),
    (
        "bucket_feed_settings",
        "ck_bucket_feed_settings_bucket",
        f"bucket IN ({_BUCKETS})",
    ),
    (
        "bucket_feed_settings",
        "ck_bucket_feed_settings_daily_kg",
        "daily_kg_per_head > 0 AND daily_kg_per_head <= 1000000 AND "
        "daily_kg_per_head::text NOT IN ('NaN', 'Infinity', '-Infinity')",
    ),
    (
        "purchase_batches",
        "ck_purchase_batches_count",
        "count BETWEEN 1 AND 1000",
    ),
    (
        "purchase_batches",
        "ck_purchase_batches_avg_age",
        "avg_age_months IS NULL OR "
        "(avg_age_months BETWEEN 0 AND 240 AND "
        "avg_age_months::text NOT IN ('NaN', 'Infinity', '-Infinity'))",
    ),
    (
        "purchase_batches",
        "ck_purchase_batches_avg_weight",
        "avg_weight_kg IS NULL OR "
        "(avg_weight_kg BETWEEN 0 AND 1000 AND "
        "avg_weight_kg::text NOT IN ('NaN', 'Infinity', '-Infinity'))",
    ),
    (
        "purchase_batches",
        "ck_purchase_batches_total_price",
        "total_price IS NULL OR total_price BETWEEN 0 AND 1000000000",
    ),
    (
        "health_events",
        "ck_health_events_type",
        "type IN ('VACCINE', 'DEWORMING', 'TREATMENT', 'FOOTBATH', 'VITAMIN')",
    ),
    (
        "health_events",
        "ck_health_events_target",
        "animal_id IS NOT NULL OR purchase_batch_id IS NOT NULL",
    ),
    (
        "health_events",
        "ck_health_events_cost_bounded",
        "cost IS NULL OR cost <= 1000000000",
    ),
    (
        "health_events",
        "ck_health_events_next_due_after_event",
        "next_due_date IS NULL OR next_due_date > date",
    ),
    (
        "health_events",
        "ck_health_events_manufactured_before_event",
        "product_manufactured_on IS NULL OR product_manufactured_on <= date",
    ),
    (
        "health_events",
        "ck_health_events_expiry_after_event",
        "product_expires_on IS NULL OR product_expires_on >= date",
    ),
    (
        "health_events",
        "ck_health_events_product_date_order",
        "product_manufactured_on IS NULL OR product_expires_on IS NULL OR "
        "product_expires_on >= product_manufactured_on",
    ),
    (
        "health_events",
        "ck_health_events_validity_after_event",
        "vaccine_valid_until IS NULL OR vaccine_valid_until >= date",
    ),
    (
        "health_events",
        "ck_health_events_validity_before_expiry",
        "vaccine_valid_until IS NULL OR product_expires_on IS NULL OR "
        "vaccine_valid_until <= product_expires_on",
    ),
    (
        "health_events",
        "ck_health_events_withdrawal_after_event",
        "withdrawal_until IS NULL OR withdrawal_until >= date",
    ),
    (
        "health_events",
        "ck_health_events_suspected_disease",
        "suspected_scheduled_disease IS FALSE OR "
        "(disease_target IS NOT NULL AND btrim(disease_target) <> '')",
    ),
    (
        "vaccine_templates",
        "ck_vaccine_templates_first_age",
        "first_dose_age_months IS NULL OR "
        "(first_dose_age_months > 0 AND first_dose_age_months <= 240 AND "
        "first_dose_age_months::text NOT IN ('NaN', 'Infinity', '-Infinity'))",
    ),
    (
        "vaccine_templates",
        "ck_vaccine_templates_booster",
        "booster_weeks IS NULL OR "
        "(booster_weeks > 0 AND booster_weeks <= 520 AND "
        "booster_weeks::text NOT IN ('NaN', 'Infinity', '-Infinity'))",
    ),
    (
        "vaccine_templates",
        "ck_vaccine_templates_repeat",
        "repeat_months IS NULL OR "
        "(repeat_months > 0 AND repeat_months <= 240 AND "
        "repeat_months::text NOT IN ('NaN', 'Infinity', '-Infinity'))",
    ),
    (
        "tasks",
        "ck_tasks_status",
        "status IN ('PENDING', 'DONE', 'SKIPPED', 'VERIFIED')",
    ),
    (
        "tasks",
        "ck_tasks_category",
        "category IN ('VACCINE', 'DEWORMING', 'ULTRASOUND', 'KIDDING_DUE', "
        "'WEANING', 'BUCKET_MOVE', 'QUARANTINE', 'FEED', 'CLEANING', 'OTHER')",
    ),
    (
        "tasks",
        "ck_tasks_recurrence",
        "(recur_days IS NULL AND recurring_series_id IS NULL) OR "
        "(recur_days IS NOT NULL AND recur_days BETWEEN 1 AND 3650 "
        "AND recurring_series_id IS NOT NULL "
        "AND btrim(recurring_series_id) <> '')",
    ),
    (
        "tasks",
        "ck_tasks_completion_timestamp",
        "status NOT IN ('DONE', 'VERIFIED') OR completed_at IS NOT NULL",
    ),
    (
        "tasks",
        "ck_tasks_verification_attribution",
        "verified_by_id IS NULL OR verified_at IS NOT NULL",
    ),
    (
        "tasks",
        "ck_tasks_verification_state",
        "(status = 'VERIFIED' AND verified_at IS NOT NULL) OR "
        "(status <> 'VERIFIED' AND verified_by_id IS NULL AND verified_at IS NULL)",
    ),
    (
        "tasks",
        "ck_tasks_skip_state",
        "(status = 'SKIPPED' AND skipped_at IS NOT NULL) OR "
        "(status <> 'SKIPPED' AND skipped_by_id IS NULL AND skipped_at IS NULL "
        "AND skip_reason IS NULL)",
    ),
    ("feed_recipes", "ck_feed_recipes_code_nonblank", "btrim(code) <> ''"),
    ("feed_recipes", "ck_feed_recipes_name_nonblank", "btrim(name) <> ''"),
    (
        "feed_recipe_lines",
        "ck_feed_recipe_lines_kg",
        "kg_per_100kg > 0 AND kg_per_100kg <= 100 AND "
        "kg_per_100kg::text NOT IN ('NaN', 'Infinity', '-Infinity')",
    ),
    (
        "feed_recipe_lines",
        "ck_feed_recipe_lines_category",
        f"category IN ({_INGREDIENT_CATEGORIES})",
    ),
    (
        "feed_recipe_lines",
        "ck_feed_recipe_lines_ingredient_nonblank",
        "btrim(ingredient) <> ''",
    ),
    (
        "feed_inventory",
        "ck_feed_inventory_category",
        f"category IN ({_INGREDIENT_CATEGORIES})",
    ),
    ("feed_inventory", "ck_feed_inventory_unit", "unit = 'kg'"),
    (
        "feed_inventory",
        "ck_feed_inventory_qty",
        "qty_on_hand >= 0 AND qty_on_hand::text NOT IN ('NaN', 'Infinity', '-Infinity')",
    ),
    (
        "feed_inventory",
        "ck_feed_inventory_reorder_level",
        "reorder_level IS NULL OR "
        "(reorder_level >= 0 AND "
        "reorder_level::text NOT IN ('NaN', 'Infinity', '-Infinity'))",
    ),
    (
        "feed_inventory",
        "ck_feed_inventory_last_price",
        "last_purchase_price_per_kg IS NULL OR last_purchase_price_per_kg BETWEEN 0 AND 1000000000",
    ),
    (
        "feed_inventory",
        "ck_feed_inventory_ingredient_nonblank",
        "btrim(ingredient) <> ''",
    ),
    (
        "feed_finished_stock",
        "ck_finished_feed_qty_finite",
        "qty_on_hand::text NOT IN ('NaN', 'Infinity', '-Infinity')",
    ),
    (
        "feeding_records",
        "ck_feeding_records_shift",
        "shift IN ('MORNING', 'AFTERNOON', 'NIGHT')",
    ),
    (
        "feeding_records",
        "ck_feeding_records_bucket",
        f"bucket IN ({_BUCKETS})",
    ),
    (
        "feeding_records",
        "ck_feeding_records_qty",
        "qty_kg > 0 AND qty_kg <= 1000000 AND qty_kg::text NOT IN ('NaN', 'Infinity', '-Infinity')",
    ),
    (
        "feeding_records",
        "ck_feeding_records_recipe_code",
        "recipe_code IS NULL OR btrim(recipe_code) <> ''",
    ),
    ("transactions", "ck_transactions_type", "type IN ('INCOME', 'EXPENSE')"),
    (
        "transactions",
        "ck_transactions_category",
        "category IN ('ANIMAL_SALE', 'ANIMAL_PURCHASE', 'FEED', 'MEDICINE', "
        "'VET', 'LABOUR', 'EQUIPMENT', 'MILK', 'MANURE', 'OTHER')",
    ),
    (
        "transactions",
        "ck_transactions_source_pair",
        "(source_type IS NULL AND source_id IS NULL) OR "
        "(source_type IS NOT NULL AND btrim(source_type) <> '' "
        "AND source_id IS NOT NULL AND source_id > 0)",
    ),
    (
        "transactions",
        "ck_transactions_not_self_correction",
        "correction_of_id IS NULL OR correction_of_id <> id",
    ),
    (
        "transactions",
        "ck_transactions_void_state",
        "(voided_at IS NULL AND voided_by_id IS NULL AND void_reason IS NULL) OR "
        "(voided_at IS NOT NULL AND void_reason IS NOT NULL AND btrim(void_reason) <> '')",
    ),
    ("breeding_records", "ck_breeding_records_method", "method IN ('NATURAL')"),
    (
        "breeding_records",
        "ck_breeding_records_outcome",
        "outcome IN ('PENDING', 'CONFIRMED_PREGNANT', 'FAILED', 'ABORTED')",
    ),
    (
        "breeding_records",
        "ck_breeding_records_heat_cycle",
        "heat_cycle_number BETWEEN 1 AND 99",
    ),
    (
        "breeding_records",
        "ck_breeding_records_distinct_parents",
        "doe_id <> buck_id",
    ),
    (
        "breeding_records",
        "ck_breeding_records_ultrasound_date",
        "ultrasound_date IS NULL OR ultrasound_date >= breeding_date",
    ),
    (
        "breeding_records",
        "ck_breeding_records_result_date",
        "ultrasound_result_date IS NULL OR ultrasound_result_date >= breeding_date",
    ),
    (
        "breeding_records",
        "ck_breeding_records_result_after_plan",
        "ultrasound_result_date IS NULL OR ultrasound_date IS NULL OR "
        "ultrasound_result_date >= ultrasound_date",
    ),
    (
        "breeding_records",
        "ck_breeding_records_expected_date",
        "expected_kidding_date IS NULL OR expected_kidding_date > breeding_date",
    ),
    (
        "breeding_records",
        "ck_breeding_records_kid_count",
        "kid_count_detected IS NULL OR kid_count_detected BETWEEN 1 AND 3",
    ),
    (
        "breeding_records",
        "ck_breeding_records_outcome_state",
        "(outcome = 'PENDING' AND ultrasound_done IS FALSE AND pregnant IS NULL "
        "AND kid_count_detected IS NULL AND ultrasound_result_date IS NULL) OR "
        "(outcome = 'CONFIRMED_PREGNANT' AND ultrasound_done IS TRUE "
        "AND pregnant IS TRUE) OR "
        "(outcome = 'FAILED' AND ultrasound_done IS TRUE AND pregnant IS FALSE "
        "AND kid_count_detected IS NULL) OR "
        "(outcome = 'ABORTED' AND pregnant IS FALSE)",
    ),
    (
        "breeding_records",
        "ck_breeding_records_expected_state",
        "(outcome = 'CONFIRMED_PREGNANT' AND expected_kidding_date IS NOT NULL) OR "
        "(outcome IN ('PENDING', 'FAILED') AND expected_kidding_date IS NULL) OR "
        "outcome = 'ABORTED'",
    ),
    (
        "kidding_records",
        "ck_kidding_records_ease",
        "ease IN ('NORMAL', 'ASSISTED', 'DIFFICULT')",
    ),
    ("kid_entries", "ck_kid_entries_sex", "sex IN ('M', 'F')"),
    (
        "kid_entries",
        "ck_kid_entries_birth_weight",
        "birth_weight IS NULL OR "
        "(birth_weight BETWEEN 0 AND 1000 AND "
        "birth_weight::text NOT IN ('NaN', 'Infinity', '-Infinity'))",
    ),
)


def _preflight() -> None:
    bind = op.get_bind()
    for table, name, condition in _CONSTRAINTS:
        # All identifiers are static constants above. Matching CHECK's null
        # semantics matters: NOT(NULL) remains NULL and is not a violation.
        statement = sa.text(
            f'SELECT id FROM "{table}" WHERE NOT ({condition}) ORDER BY id LIMIT 10'
        )
        invalid_ids = list(bind.execute(statement).scalars())
        if invalid_ids:
            formatted_ids = ", ".join(str(row_id) for row_id in invalid_ids)
            raise RuntimeError(
                f"Cannot install {name} on {table}; violating row ids: {formatted_ids}"
            )


def upgrade() -> None:
    _preflight()
    for table, name, condition in _CONSTRAINTS:
        op.execute(f'ALTER TABLE "{table}" ADD CONSTRAINT "{name}" CHECK ({condition}) NOT VALID')
    for table, name, _condition in _CONSTRAINTS:
        op.execute(f'ALTER TABLE "{table}" VALIDATE CONSTRAINT "{name}"')


def downgrade() -> None:
    for table, name, _condition in reversed(_CONSTRAINTS):
        op.execute(f'ALTER TABLE "{table}" DROP CONSTRAINT IF EXISTS "{name}"')
