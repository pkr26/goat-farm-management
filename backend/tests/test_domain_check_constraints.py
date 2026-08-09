"""PostgreSQL-enforced value and state coherence for domain records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import CheckConstraint, select, text
from sqlalchemy.exc import DBAPIError

from app.db import Base, get_sessionmaker
from app.models import (
    Animal,
    BreedingRecord,
    BucketFeedSetting,
    BucketMove,
    FeedFinishedStock,
    FeedingRecord,
    FeedInventory,
    FeedRecipe,
    FeedRecipeLine,
    HealthEvent,
    KiddingRecord,
    KidEntry,
    PurchaseBatch,
    Task,
    Transaction,
    User,
    WeightRecord,
)
from app.utils import today

from .conftest import owner_with_farm


@dataclass(frozen=True)
class InvalidMutation:
    sql: str
    params: dict[str, object]
    constraints: tuple[str, ...]


async def _valid_domain_graph(client: httpx.AsyncClient) -> dict[str, int]:
    owner = await owner_with_farm(client, email="domain-check-owner@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        user_id = (
            await db.execute(select(User.id).where(User.email == "domain-check-owner@farm.in"))
        ).scalar_one()
        batch = PurchaseBatch(
            farm_id=farm_id,
            date=today(),
            supplier="Constraint-safe supplier",
            count=2,
            avg_age_months=18,
            avg_weight_kg=28,
            total_price=Decimal("12000.00"),
        )
        db.add(batch)
        await db.flush()
        doe = Animal(
            farm_id=farm_id,
            tag_number="CHECK-DOE",
            sex="F",
            source="PURCHASED",
            purchase_date=today() - timedelta(days=365),
            purchase_price=Decimal("6000.00"),
            seller_name="Constraint-safe supplier",
            purchase_batch_id=batch.id,
            current_bucket="BREEDING",
        )
        buck = Animal(
            farm_id=farm_id,
            tag_number="CHECK-BUCK",
            sex="M",
            source="PURCHASED",
            current_bucket="BREEDING",
        )
        db.add_all([doe, buck])
        await db.flush()
        kid_animal = Animal(
            farm_id=farm_id,
            tag_number="CHECK-KID",
            sex="F",
            source="BORN",
            date_of_birth=today(),
            birth_type="SINGLE",
            birth_weight=2.7,
            dam_id=doe.id,
            sire_id=buck.id,
            current_bucket="RECOVERY",
        )
        db.add(kid_animal)
        await db.flush()
        weight = WeightRecord(
            animal_id=kid_animal.id,
            date=today(),
            weight_kg=3.0,
            bcs=3,
            created_by_id=user_id,
        )
        move = BucketMove(
            animal_id=kid_animal.id,
            from_bucket=None,
            to_bucket="RECOVERY",
            reason="Born",
            created_by_id=user_id,
        )
        setting = BucketFeedSetting(
            farm_id=farm_id,
            bucket="RECOVERY",
            daily_kg_per_head=1.5,
        )
        health = HealthEvent(
            farm_id=farm_id,
            animal_id=doe.id,
            purchase_batch_id=batch.id,
            date=today(),
            type="TREATMENT",
            cost=Decimal("25.00"),
            next_due_date=today() + timedelta(days=30),
            schedule_template_name="Deworming",
            next_due_authority="Constraint-safe veterinarian record",
            product_manufactured_on=today() - timedelta(days=90),
            product_expires_on=today() + timedelta(days=90),
            vaccine_valid_until=today() + timedelta(days=60),
            withdrawal_until=today() + timedelta(days=7),
            created_by_id=user_id,
        )
        breeding = BreedingRecord(
            farm_id=farm_id,
            doe_id=doe.id,
            buck_id=buck.id,
            breeding_date=today() - timedelta(days=150),
            method="NATURAL",
            heat_cycle_number=2,
            ultrasound_date=today() - timedelta(days=118),
            ultrasound_result_date=today() - timedelta(days=115),
            ultrasound_done=True,
            pregnant=True,
            kid_count_detected=1,
            expected_kidding_date=today(),
            outcome="CONFIRMED_PREGNANT",
            created_by_id=user_id,
        )
        db.add_all([weight, move, setting, health, breeding])
        await db.flush()
        kidding = KiddingRecord(
            farm_id=farm_id,
            doe_id=doe.id,
            date=today(),
            breeding_record_id=breeding.id,
            ease="NORMAL",
            created_by_id=user_id,
        )
        db.add(kidding)
        await db.flush()
        kid_entry = KidEntry(
            farm_id=farm_id,
            kidding_record_id=kidding.id,
            tag=kid_animal.tag_number,
            sex="F",
            birth_weight=2.7,
            status="ALIVE",
            animal_id=kid_animal.id,
        )
        task = Task(
            farm_id=farm_id,
            title="Constraint-safe recurring duty",
            due_date=today(),
            status="PENDING",
            animal_id=doe.id,
            purchase_batch_id=batch.id,
            breeding_record_id=breeding.id,
            category="OTHER",
            auto_generated=True,
            recur_days=7,
            recurring_series_id="constraint-safe-series",
        )
        recipe = (await db.execute(select(FeedRecipe).order_by(FeedRecipe.id))).scalars().first()
        inventory = (
            (
                await db.execute(
                    select(FeedInventory)
                    .where(FeedInventory.farm_id == farm_id)
                    .order_by(FeedInventory.id)
                )
            )
            .scalars()
            .first()
        )
        assert recipe is not None and inventory is not None
        recipe_line = (
            (
                await db.execute(
                    select(FeedRecipeLine)
                    .where(FeedRecipeLine.recipe_id == recipe.id)
                    .order_by(FeedRecipeLine.id)
                )
            )
            .scalars()
            .first()
        )
        assert recipe_line is not None
        finished = FeedFinishedStock(
            farm_id=farm_id,
            recipe_code=recipe.code,
            qty_on_hand=10,
        )
        feeding = FeedingRecord(
            farm_id=farm_id,
            date=today(),
            shift="MORNING",
            bucket="RECOVERY",
            recipe_code=recipe.code,
            qty_kg=1.5,
            created_by_id=user_id,
        )
        transaction = Transaction(
            farm_id=farm_id,
            date=today(),
            type="EXPENSE",
            category="OTHER",
            amount=Decimal("50.00"),
            source_type="TEST_SOURCE",
            source_id=batch.id,
            created_by_id=user_id,
        )
        db.add_all([kid_entry, task, finished, feeding, transaction])
        await db.commit()
        return {
            "animal": kid_animal.id,
            "doe": doe.id,
            "buck": buck.id,
            "weight": weight.id,
            "move": move.id,
            "setting": setting.id,
            "batch": batch.id,
            "health": health.id,
            "task": task.id,
            "recipe": recipe.id,
            "recipe_line": recipe_line.id,
            "inventory": inventory.id,
            "finished": finished.id,
            "feeding": feeding.id,
            "transaction": transaction.id,
            "breeding": breeding.id,
            "kidding": kidding.id,
            "kid_entry": kid_entry.id,
        }


async def test_every_model_check_is_installed_and_validated() -> None:
    expected = {
        (table.name, constraint.name)
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name is not None
    }
    async with get_sessionmaker()() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT rel.relname, con.conname, con.convalidated "
                    "FROM pg_constraint con "
                    "JOIN pg_class rel ON rel.oid = con.conrelid "
                    "WHERE con.contype = 'c'"
                )
            )
        ).all()
    actual = {(table, name) for table, name, _validated in rows}
    assert expected <= actual
    assert all(validated for table, name, validated in rows if (table, name) in expected)


async def test_valid_direct_sql_boundary_values_and_domain_graph_commit(
    client: httpx.AsyncClient,
) -> None:
    ids = await _valid_domain_graph(client)
    async with get_sessionmaker()() as db:
        await db.execute(
            text(
                "UPDATE purchase_batches SET count = 1000, avg_age_months = 240, "
                "avg_weight_kg = 1000, total_price = 1000000000 WHERE id = :id"
            ),
            {"id": ids["batch"]},
        )
        await db.execute(
            text("UPDATE weight_records SET weight_kg = 1000, bcs = 5 WHERE id = :id"),
            {"id": ids["weight"]},
        )
        await db.execute(
            text("UPDATE bucket_feed_settings SET daily_kg_per_head = 1000000 WHERE id = :id"),
            {"id": ids["setting"]},
        )
        await db.execute(
            text("UPDATE transactions SET amount = 1000000000 WHERE id = :id"),
            {"id": ids["transaction"]},
        )
        await db.commit()


async def test_direct_sql_rejects_invalid_domain_values_and_states(
    client: httpx.AsyncClient,
) -> None:
    ids = await _valid_domain_graph(client)
    cases = [
        InvalidMutation(
            "UPDATE animals SET sex = 'X' WHERE id = :id",
            {"id": ids["animal"]},
            ("ck_animals_sex",),
        ),
        InvalidMutation(
            "UPDATE animals SET birth_type = 'UNKNOWN' WHERE id = :id",
            {"id": ids["animal"]},
            ("ck_animals_birth_type",),
        ),
        InvalidMutation(
            "UPDATE animals SET source = 'TRANSFER' WHERE id = :id",
            {"id": ids["animal"]},
            ("ck_animals_source", "ck_animals_source_fields"),
        ),
        InvalidMutation(
            "UPDATE animals SET current_bucket = 'VOID' WHERE id = :id",
            {"id": ids["animal"]},
            ("ck_animals_current_bucket",),
        ),
        InvalidMutation(
            "UPDATE animals SET purchase_price = 1 WHERE id = :id",
            {"id": ids["animal"]},
            ("ck_animals_source_fields",),
        ),
        InvalidMutation(
            "UPDATE animals SET status_date = CURRENT_DATE WHERE id = :id",
            {"id": ids["animal"]},
            ("ck_animals_status_date",),
        ),
        InvalidMutation(
            "UPDATE animals SET sale_price = 1 WHERE id = :id",
            {"id": ids["animal"]},
            ("ck_animals_sale_fields",),
        ),
        InvalidMutation(
            "UPDATE animals SET mortality_cause = 'unknown' WHERE id = :id",
            {"id": ids["animal"]},
            ("ck_animals_mortality_fields",),
        ),
        InvalidMutation(
            "UPDATE animals SET current_bucket = 'MALE_KIDS' WHERE id = :id",
            {"id": ids["animal"]},
            ("ck_animals_bucket_sex",),
        ),
        InvalidMutation(
            "UPDATE animals SET sire_id = dam_id WHERE id = :id",
            {"id": ids["animal"]},
            ("ck_animals_parent_identity",),
        ),
        InvalidMutation(
            "UPDATE animals SET birth_weight = 'Infinity'::float8 WHERE id = :id",
            {"id": ids["animal"]},
            ("ck_animals_birth_weight_bounded",),
        ),
        InvalidMutation(
            "UPDATE animals SET purchase_price = 1000000000.01 WHERE id = :id",
            {"id": ids["doe"]},
            ("ck_animals_purchase_price_bounded",),
        ),
        InvalidMutation(
            "UPDATE animals SET movement_restricted = true WHERE id = :id",
            {"id": ids["animal"]},
            ("ck_animals_restriction_reason",),
        ),
        InvalidMutation(
            "UPDATE animals SET suspected_scheduled_disease = true WHERE id = :id",
            {"id": ids["animal"]},
            ("ck_animals_suspected_disease_hold",),
        ),
        InvalidMutation(
            "UPDATE animals SET restriction_cleared_at = now() WHERE id = :id",
            {"id": ids["animal"]},
            ("ck_animals_clearance_audit",),
        ),
        InvalidMutation(
            "UPDATE weight_records SET weight_kg = 1000.01 WHERE id = :id",
            {"id": ids["weight"]},
            ("ck_weight_records_weight_bounded",),
        ),
        InvalidMutation(
            "UPDATE bucket_moves SET from_bucket = 'VOID' WHERE id = :id",
            {"id": ids["move"]},
            ("ck_bucket_moves_from_bucket",),
        ),
        InvalidMutation(
            "UPDATE bucket_moves SET to_bucket = 'VOID' WHERE id = :id",
            {"id": ids["move"]},
            ("ck_bucket_moves_to_bucket",),
        ),
        InvalidMutation(
            "UPDATE bucket_definitions SET daily_kg_per_head = 0 WHERE code = 'RECOVERY'",
            {},
            ("ck_bucket_definitions_daily_kg",),
        ),
        InvalidMutation(
            "UPDATE bucket_definitions SET sort_order = -1 WHERE code = 'RECOVERY'",
            {},
            ("ck_bucket_definitions_sort_order",),
        ),
        InvalidMutation(
            "UPDATE bucket_feed_settings SET bucket = 'VOID' WHERE id = :id",
            {"id": ids["setting"]},
            ("ck_bucket_feed_settings_bucket",),
        ),
        InvalidMutation(
            "UPDATE bucket_feed_settings SET daily_kg_per_head = 0 WHERE id = :id",
            {"id": ids["setting"]},
            ("ck_bucket_feed_settings_daily_kg",),
        ),
        InvalidMutation(
            "UPDATE purchase_batches SET count = 0 WHERE id = :id",
            {"id": ids["batch"]},
            ("ck_purchase_batches_count",),
        ),
        InvalidMutation(
            "UPDATE purchase_batches SET avg_age_months = 241 WHERE id = :id",
            {"id": ids["batch"]},
            ("ck_purchase_batches_avg_age",),
        ),
        InvalidMutation(
            "UPDATE purchase_batches SET avg_weight_kg = 'NaN'::float8 WHERE id = :id",
            {"id": ids["batch"]},
            ("ck_purchase_batches_avg_weight",),
        ),
        InvalidMutation(
            "UPDATE purchase_batches SET total_price = 1000000000.01 WHERE id = :id",
            {"id": ids["batch"]},
            ("ck_purchase_batches_total_price",),
        ),
        InvalidMutation(
            "UPDATE health_events SET type = 'OTHER' WHERE id = :id",
            {"id": ids["health"]},
            ("ck_health_events_type",),
        ),
        InvalidMutation(
            "UPDATE health_events SET animal_id = NULL, purchase_batch_id = NULL WHERE id = :id",
            {"id": ids["health"]},
            ("ck_health_events_target",),
        ),
        InvalidMutation(
            "UPDATE health_events SET cost = 1000000000.01 WHERE id = :id",
            {"id": ids["health"]},
            ("ck_health_events_cost_bounded",),
        ),
        InvalidMutation(
            "UPDATE health_events SET next_due_date = date WHERE id = :id",
            {"id": ids["health"]},
            ("ck_health_events_next_due_after_event",),
        ),
        InvalidMutation(
            "UPDATE health_events SET product_manufactured_on = date + 1 WHERE id = :id",
            {"id": ids["health"]},
            ("ck_health_events_manufactured_before_event",),
        ),
        InvalidMutation(
            "UPDATE health_events SET product_expires_on = date - 1 WHERE id = :id",
            {"id": ids["health"]},
            ("ck_health_events_expiry_after_event", "ck_health_events_product_date_order"),
        ),
        InvalidMutation(
            "UPDATE health_events SET vaccine_valid_until = date - 1 WHERE id = :id",
            {"id": ids["health"]},
            ("ck_health_events_validity_after_event",),
        ),
        InvalidMutation(
            "UPDATE health_events SET withdrawal_until = date - 1 WHERE id = :id",
            {"id": ids["health"]},
            ("ck_health_events_withdrawal_after_event",),
        ),
        InvalidMutation(
            "UPDATE health_events SET suspected_scheduled_disease = true WHERE id = :id",
            {"id": ids["health"]},
            ("ck_health_events_suspected_disease",),
        ),
        InvalidMutation(
            "UPDATE vaccine_templates SET first_dose_age_months = -1 WHERE id = "
            "(SELECT id FROM vaccine_templates WHERE first_dose_age_months IS NOT NULL LIMIT 1)",
            {},
            ("ck_vaccine_templates_first_age",),
        ),
        InvalidMutation(
            "UPDATE tasks SET status = 'INVALID' WHERE id = :id",
            {"id": ids["task"]},
            ("ck_tasks_status",),
        ),
        InvalidMutation(
            "UPDATE tasks SET category = 'INVALID' WHERE id = :id",
            {"id": ids["task"]},
            ("ck_tasks_category",),
        ),
        InvalidMutation(
            "UPDATE tasks SET recur_days = NULL WHERE id = :id",
            {"id": ids["task"]},
            ("ck_tasks_recurrence",),
        ),
        InvalidMutation(
            "UPDATE tasks SET status = 'DONE' WHERE id = :id",
            {"id": ids["task"]},
            ("ck_tasks_completion_timestamp",),
        ),
        InvalidMutation(
            "UPDATE tasks SET status = 'VERIFIED', completed_at = now() WHERE id = :id",
            {"id": ids["task"]},
            ("ck_tasks_verification_state",),
        ),
        InvalidMutation(
            "UPDATE tasks SET status = 'SKIPPED' WHERE id = :id",
            {"id": ids["task"]},
            ("ck_tasks_skip_state",),
        ),
        InvalidMutation(
            "INSERT INTO feed_recipes (code, name) VALUES ('', 'Invalid recipe')",
            {},
            ("ck_feed_recipes_code_nonblank",),
        ),
        InvalidMutation(
            "UPDATE feed_recipes SET name = '' WHERE id = :id",
            {"id": ids["recipe"]},
            ("ck_feed_recipes_name_nonblank",),
        ),
        InvalidMutation(
            "UPDATE feed_recipe_lines SET kg_per_100kg = 0 WHERE id = :id",
            {"id": ids["recipe_line"]},
            ("ck_feed_recipe_lines_kg",),
        ),
        InvalidMutation(
            "UPDATE feed_recipe_lines SET category = 'INVALID' WHERE id = :id",
            {"id": ids["recipe_line"]},
            ("ck_feed_recipe_lines_category",),
        ),
        InvalidMutation(
            "UPDATE feed_recipe_lines SET ingredient = '' WHERE id = :id",
            {"id": ids["recipe_line"]},
            ("ck_feed_recipe_lines_ingredient_nonblank",),
        ),
        InvalidMutation(
            "UPDATE feed_inventory SET category = 'INVALID' WHERE id = :id",
            {"id": ids["inventory"]},
            ("ck_feed_inventory_category",),
        ),
        InvalidMutation(
            "UPDATE feed_inventory SET unit = 'lb' WHERE id = :id",
            {"id": ids["inventory"]},
            ("ck_feed_inventory_unit",),
        ),
        InvalidMutation(
            "UPDATE feed_inventory SET qty_on_hand = 'NaN'::float8 WHERE id = :id",
            {"id": ids["inventory"]},
            ("ck_feed_inventory_qty",),
        ),
        InvalidMutation(
            "UPDATE feed_inventory SET reorder_level = -1 WHERE id = :id",
            {"id": ids["inventory"]},
            ("ck_feed_inventory_reorder_level",),
        ),
        InvalidMutation(
            "UPDATE feed_inventory SET last_purchase_price_per_kg = 1000000000.01 WHERE id = :id",
            {"id": ids["inventory"]},
            ("ck_feed_inventory_last_price",),
        ),
        InvalidMutation(
            "UPDATE feed_finished_stock SET qty_on_hand = 'Infinity'::float8 WHERE id = :id",
            {"id": ids["finished"]},
            # numeric(15,3) rejects infinity at the type boundary before its
            # named finite-value CHECK can run; either path is a DB-hard deny.
            ("ck_finished_feed_qty_finite", "numeric field overflow"),
        ),
        InvalidMutation(
            "UPDATE feeding_records SET shift = 'MIDDAY' WHERE id = :id",
            {"id": ids["feeding"]},
            ("ck_feeding_records_shift",),
        ),
        InvalidMutation(
            "UPDATE feeding_records SET bucket = 'VOID' WHERE id = :id",
            {"id": ids["feeding"]},
            ("ck_feeding_records_bucket",),
        ),
        InvalidMutation(
            "UPDATE feeding_records SET qty_kg = 0 WHERE id = :id",
            {"id": ids["feeding"]},
            ("ck_feeding_records_qty",),
        ),
        InvalidMutation(
            "UPDATE feeding_records SET recipe_code = '' WHERE id = :id",
            {"id": ids["feeding"]},
            ("ck_feeding_records_recipe_code",),
        ),
        InvalidMutation(
            "UPDATE transactions SET type = 'TRANSFER' WHERE id = :id",
            {"id": ids["transaction"]},
            ("ck_transactions_type",),
        ),
        InvalidMutation(
            "UPDATE transactions SET category = 'INVALID' WHERE id = :id",
            {"id": ids["transaction"]},
            ("ck_transactions_category",),
        ),
        InvalidMutation(
            "UPDATE transactions SET source_id = NULL WHERE id = :id",
            {"id": ids["transaction"]},
            ("ck_transactions_source_pair",),
        ),
        InvalidMutation(
            "UPDATE transactions SET correction_of_id = id WHERE id = :id",
            {"id": ids["transaction"]},
            ("ck_transactions_not_self_correction",),
        ),
        InvalidMutation(
            "UPDATE transactions SET voided_at = now() WHERE id = :id",
            {"id": ids["transaction"]},
            ("ck_transactions_void_state",),
        ),
        InvalidMutation(
            "UPDATE breeding_records SET method = 'AI' WHERE id = :id",
            {"id": ids["breeding"]},
            ("ck_breeding_records_method",),
        ),
        InvalidMutation(
            "UPDATE breeding_records SET heat_cycle_number = 100 WHERE id = :id",
            {"id": ids["breeding"]},
            ("ck_breeding_records_heat_cycle",),
        ),
        InvalidMutation(
            "UPDATE breeding_records SET buck_id = doe_id WHERE id = :id",
            {"id": ids["breeding"]},
            ("ck_breeding_records_distinct_parents",),
        ),
        InvalidMutation(
            "UPDATE breeding_records SET ultrasound_date = breeding_date - 1 WHERE id = :id",
            {"id": ids["breeding"]},
            ("ck_breeding_records_ultrasound_date",),
        ),
        InvalidMutation(
            "UPDATE breeding_records SET ultrasound_result_date = breeding_date - 1 WHERE id = :id",
            {"id": ids["breeding"]},
            (
                "ck_breeding_records_result_date",
                "ck_breeding_records_result_after_plan",
            ),
        ),
        InvalidMutation(
            "UPDATE breeding_records SET expected_kidding_date = breeding_date WHERE id = :id",
            {"id": ids["breeding"]},
            ("ck_breeding_records_expected_date",),
        ),
        InvalidMutation(
            "UPDATE breeding_records SET kid_count_detected = 4 WHERE id = :id",
            {"id": ids["breeding"]},
            ("ck_breeding_records_kid_count",),
        ),
        InvalidMutation(
            "UPDATE breeding_records SET pregnant = false WHERE id = :id",
            {"id": ids["breeding"]},
            ("ck_breeding_records_outcome_state",),
        ),
        InvalidMutation(
            "UPDATE breeding_records SET expected_kidding_date = NULL WHERE id = :id",
            {"id": ids["breeding"]},
            ("ck_breeding_records_expected_state",),
        ),
        InvalidMutation(
            "UPDATE kidding_records SET ease = 'UNKNOWN' WHERE id = :id",
            {"id": ids["kidding"]},
            ("ck_kidding_records_ease",),
        ),
        InvalidMutation(
            "UPDATE kid_entries SET sex = 'X' WHERE id = :id",
            {"id": ids["kid_entry"]},
            ("ck_kid_entries_sex",),
        ),
        InvalidMutation(
            "UPDATE kid_entries SET birth_weight = 1000.01 WHERE id = :id",
            {"id": ids["kid_entry"]},
            ("ck_kid_entries_birth_weight",),
        ),
    ]

    for case in cases:
        async with get_sessionmaker()() as db:
            try:
                await db.execute(text(case.sql), case.params)
            except DBAPIError as exc:
                caught = exc
            else:
                pytest.fail(f"PostgreSQL accepted invalid mutation: {case.sql}")
            await db.rollback()
        assert any(name in str(caught) for name in case.constraints), (
            case.sql,
            str(caught),
        )
