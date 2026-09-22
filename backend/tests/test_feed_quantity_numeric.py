"""Exact, whole-gram persistence for feed quantities."""

import asyncio
import os
import subprocess
import sys
from decimal import Decimal

import asyncpg
import httpx
from sqlalchemy import Numeric, text

from app.db import get_engine, get_sessionmaker
from app.models import (
    BucketDefinition,
    BucketFeedSetting,
    FeedFinishedStock,
    FeedingRecord,
    FeedInventory,
    FeedRecipeLine,
)

from .conftest import BACKEND_DIR, TEST_DB, owner_with_farm

EXPECTED_NUMERIC_COLUMNS = {
    ("feed_inventory", "qty_on_hand"),
    ("feed_inventory", "reorder_level"),
    ("feed_finished_stock", "qty_on_hand"),
    ("feeding_records", "qty_kg"),
    ("bucket_definitions", "daily_kg_per_head"),
    ("bucket_feed_settings", "daily_kg_per_head"),
}


async def _purge_idempotency_for_migration() -> None:
    """Farm creation now persists an actor-scoped (NULL-farm) idempotency
    claim; f3d4e5f6a7b8's downgrade refuses to run while such rows exist, and
    this test downgrades far past that revision."""
    async with get_sessionmaker()() as db:
        await db.execute(text("DELETE FROM idempotency_records"))
        await db.commit()


async def _alembic(*args: str, succeeds: bool = True) -> subprocess.CompletedProcess[str]:
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
    )
    if succeeds:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode != 0, result.stdout + result.stderr
    return result


async def _column_types() -> dict[tuple[str, str], tuple[str, int | None, int | None]]:
    async with get_sessionmaker()() as db:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT table_name, column_name, data_type,
                           numeric_precision, numeric_scale
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND (table_name, column_name) IN (
                        ('feed_inventory', 'qty_on_hand'),
                        ('feed_inventory', 'reorder_level'),
                        ('feed_finished_stock', 'qty_on_hand'),
                        ('feeding_records', 'qty_kg'),
                        ('bucket_definitions', 'daily_kg_per_head'),
                        ('bucket_feed_settings', 'daily_kg_per_head')
                      )
                    """
                )
            )
        ).all()
    return {
        (table, column): (data_type, precision, scale)
        for table, column, data_type, precision, scale in rows
    }


async def test_feed_quantity_schema_and_models_use_exact_gram_numeric() -> None:
    assert await _column_types() == dict.fromkeys(EXPECTED_NUMERIC_COLUMNS, ("numeric", 15, 3))

    attributes = (
        FeedInventory.qty_on_hand,
        FeedInventory.reorder_level,
        FeedFinishedStock.qty_on_hand,
        FeedingRecord.qty_kg,
        BucketDefinition.daily_kg_per_head,
        BucketFeedSetting.daily_kg_per_head,
    )
    for attribute in attributes:
        column_type = attribute.property.columns[0].type
        assert isinstance(column_type, Numeric)
        assert column_type.precision == 15
        assert column_type.scale == 3
        assert column_type.asdecimal is False

    # Recipe lines are percentages/ratios, not stock quantities: their
    # six-decimal tolerance must not be truncated to grams. Decimal-exact
    # numeric(15,6) (c3e5a9f1d7b4) replaces the old float8 while keeping
    # that tolerance, instead of the gram scale the stock columns use.
    recipe_column_type = FeedRecipeLine.kg_per_100kg.property.columns[0].type
    assert isinstance(recipe_column_type, Numeric)
    assert recipe_column_type.precision == 15
    assert recipe_column_type.scale == 6
    assert recipe_column_type.asdecimal is False


async def test_direct_sql_arithmetic_and_api_roundtrip_stay_exact_to_one_gram(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])

    async with get_sessionmaker()() as db:
        item_id = (
            await db.execute(
                text("SELECT id FROM feed_inventory WHERE farm_id = :farm_id ORDER BY id LIMIT 1"),
                {"farm_id": farm_id},
            )
        ).scalar_one()
        await db.execute(
            text("UPDATE feed_inventory SET qty_on_hand = 0 WHERE id = :id"),
            {"id": item_id},
        )
        # Exercise arithmetic in PostgreSQL itself: the old float8 ledger
        # could leave 0.999999999999... after this sequence.
        await db.execute(
            text(
                f"""
                DO $body$
                BEGIN
                  FOR counter IN 1..1000 LOOP
                    UPDATE feed_inventory
                    SET qty_on_hand = qty_on_hand + 0.001
                    WHERE id = {int(item_id)};
                  END LOOP;
                END
                $body$
                """
            )
        )
        stored = (
            await db.execute(
                text("SELECT qty_on_hand::text FROM feed_inventory WHERE id = :id"),
                {"id": item_id},
            )
        ).scalar_one()
        assert stored == "1.000"

        # NUMERIC applies the same half-up-at-one-gram storage boundary used
        # by the API's QuantityKgFloat validator.
        await db.execute(
            text("UPDATE feed_inventory SET qty_on_hand = 10.1235 WHERE id = :id"),
            {"id": item_id},
        )
        rounded = (
            await db.execute(
                text("SELECT qty_on_hand FROM feed_inventory WHERE id = :id"),
                {"id": item_id},
            )
        ).scalar_one()
        assert rounded == Decimal("10.124")
        await db.commit()

    response = await client.get("/api/feeding/inventory", headers=owner)
    assert response.status_code == 200, response.text
    item = next(row for row in response.json() if row["id"] == item_id)
    assert item["qty_on_hand"] == 10.124

    setting = await client.post(
        "/api/feeding/settings",
        json={"bucket": "FOUNDATION", "daily_kg_per_head": 1.2345},
        headers=owner,
    )
    assert setting.status_code == 204, setting.text
    async with get_sessionmaker()() as db:
        persisted_setting = (
            await db.execute(
                text(
                    "SELECT daily_kg_per_head::text FROM bucket_feed_settings "
                    "WHERE farm_id = :farm_id AND bucket = 'FOUNDATION'"
                ),
                {"farm_id": farm_id},
            )
        ).scalar_one()
    assert persisted_setting == "1.235"

    inventory = await client.get("/api/feeding/inventory", headers=owner)
    assert inventory.status_code == 200, inventory.text
    dry_stover = next(row for row in inventory.json() if row["ingredient"] == "Dry jowar stover")
    stocked = await client.post(
        f"/api/feeding/inventory/{dry_stover['id']}/add",
        json={"qty_kg": 0.3},
        headers=owner,
    )
    assert stocked.status_code == 200, stocked.text

    dispensed = await client.post(
        "/api/feeding/dispense",
        json={
            "bucket": "QUARANTINE",
            "shift": "MORNING",
            "recipe_code": "DRY_ROUGHAGE_ONLY",
            "qty_kg": 0.30000000000000004,
        },
        headers=owner,
    )
    assert dispensed.status_code == 201, dispensed.text
    assert dispensed.json()["qty_kg"] == 0.3
    async with get_sessionmaker()() as db:
        persisted_record = (
            await db.execute(
                text("SELECT qty_kg::text FROM feeding_records WHERE id = :id"),
                {"id": dispensed.json()["id"]},
            )
        ).scalar_one()
    assert persisted_record == "0.300"


async def test_migration_refuses_dirty_legacy_precision_then_reupgrades_cleanly(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])

    await get_engine().dispose()
    await _purge_idempotency_for_migration()
    await _alembic("downgrade", "f1b2c3d4e5f6")
    types_at_parent = await _column_types()
    assert all(data_type == "double precision" for data_type, _, _ in types_at_parent.values())

    connection = await asyncpg.connect(f"postgresql://localhost:5432/{TEST_DB}")
    try:
        item_id = await connection.fetchval(
            "SELECT id FROM feed_inventory WHERE farm_id = $1 ORDER BY id LIMIT 1",
            farm_id,
        )
        await connection.execute(
            "UPDATE feed_inventory SET qty_on_hand = 10.1234 WHERE id = $1",
            item_id,
        )
    finally:
        await connection.close()

    refused = await _alembic("upgrade", "head", succeeds=False)
    output = refused.stdout + refused.stderr
    assert "feed_inventory.qty_on_hand contains sub-gram values" in output
    assert f"row ids [{item_id}]" in output

    connection = await asyncpg.connect(f"postgresql://localhost:5432/{TEST_DB}")
    try:
        # The failed migration is transactional: no earlier column conversion
        # may leak through while the operator repairs the offending legacy row.
        assert (
            await connection.fetchval(
                """
                SELECT data_type FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'feed_inventory' AND column_name = 'qty_on_hand'
                """
            )
            == "double precision"
        )
        # Ordinary float arithmetic noise is not meaningful sub-gram data.
        # PostgreSQL's float8->numeric cast canonicalizes it before comparison.
        await connection.execute(
            "UPDATE feed_inventory SET qty_on_hand = 0.30000000000000004 WHERE id = $1",
            item_id,
        )
    finally:
        await connection.close()

    await _alembic("upgrade", "head")
    assert await _column_types() == dict.fromkeys(EXPECTED_NUMERIC_COLUMNS, ("numeric", 15, 3))
    async with get_sessionmaker()() as db:
        canonicalized = (
            await db.execute(
                text("SELECT qty_on_hand::text FROM feed_inventory WHERE id = :id"),
                {"id": item_id},
            )
        ).scalar_one()
    assert canonicalized == "0.300"

    await get_engine().dispose()
    await _purge_idempotency_for_migration()
    await _alembic("downgrade", "f1b2c3d4e5f6")
    connection = await asyncpg.connect(f"postgresql://localhost:5432/{TEST_DB}")
    try:
        await connection.execute(
            "UPDATE feed_inventory SET qty_on_hand = 1000000000000 WHERE id = $1",
            item_id,
        )
    finally:
        await connection.close()

    overflow_refused = await _alembic("upgrade", "head", succeeds=False)
    output = overflow_refused.stdout + overflow_refused.stderr
    assert "feed_inventory.qty_on_hand contains overflow values" in output
    assert f"row ids [{item_id}]" in output

    connection = await asyncpg.connect(f"postgresql://localhost:5432/{TEST_DB}")
    try:
        await connection.execute(
            "UPDATE feed_inventory SET qty_on_hand = 10.123 WHERE id = $1",
            item_id,
        )
    finally:
        await connection.close()

    await _alembic("upgrade", "head")
    async with get_sessionmaker()() as db:
        preserved = (
            await db.execute(
                text("SELECT qty_on_hand::text FROM feed_inventory WHERE id = :id"),
                {"id": item_id},
            )
        ).scalar_one()
    assert preserved == "10.123"

    # The model and migration remain in sync after the full downgrade/retry.
    await _alembic("check")
