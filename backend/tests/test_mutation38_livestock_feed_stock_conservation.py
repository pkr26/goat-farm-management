"""Real mix and dispensing operations conserve stock and report absent balances truthfully."""

from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import get_sessionmaker
from app.models import Farm, FeedFinishedStock, FeedingRecord, FeedInventory, FeedRecipe
from app.services.feeding import mix_feed_batch

from .conftest import owner_with_farm
from .test_feeding_extended import dispense, get_inventory, mix_ready, stock_all, stock_dry_roughage


async def test_two_fractional_real_mixes_conserve_each_ingredient_and_finished_feed(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    await mix_ready(client, owner, "CREEP", 1.003)
    second = await client.post(
        "/api/feeding/mix", headers=owner, json={"recipe_code": "CREEP", "batch_kg": 1.003}
    )
    assert second.status_code == 200, second.text
    inventory = {
        item["ingredient"]: Decimal(str(item["qty_on_hand"]))
        for item in await get_inventory(client, owner)
    }
    # Literal60/30/9/1 recipe, exact Hamilton grams602/301/90/10 per1003g.
    assert inventory["Crushed maize"] == Decimal("998.796")
    assert inventory["Soya DOC"] == Decimal("999.398")
    assert inventory["Maize DDGS"] == Decimal("999.820")
    assert inventory["Mineral mix"] == Decimal("999.980")
    stock = await client.get("/api/feeding/finished-stock", headers=owner)
    assert stock.status_code == 200, stock.text
    assert len(stock.json()) == 1 and stock.json()[0]["qty_on_hand"] == 2.006
    consumed = await dispense(client, owner, recipe_code="CREEP", qty_kg=2.006)
    assert consumed.status_code == 201, consumed.text
    async with get_sessionmaker()() as db:
        finished = (await db.execute(select(FeedFinishedStock))).scalar_one()
        assert finished.qty_on_hand == 0
        assert len(list((await db.execute(select(FeedingRecord))).scalars())) == 1


@pytest.mark.parametrize("finished", [False, True])
async def test_insufficient_existing_stock_is_rejected_without_negative_balance_or_record(
    client: httpx.AsyncClient,
    finished: bool,
) -> None:
    owner = await owner_with_farm(client)
    code = "CREEP" if finished else "DRY_ROUGHAGE_ONLY"
    if finished:
        await mix_ready(client, owner, code, 1)
    else:
        await stock_dry_roughage(client, owner, 1)
    refused = await dispense(client, owner, recipe_code=code, qty_kg=2)
    assert refused.status_code == 400, refused.text
    assert (
        "need 2.000 kg" in refused.json()["detail"] and "have 1.000 kg" in refused.json()["detail"]
    )
    async with get_sessionmaker()() as db:
        assert not list((await db.execute(select(FeedingRecord))).scalars())
        if finished:
            row = (await db.execute(select(FeedFinishedStock))).scalar_one()
            assert row.qty_on_hand == 1
        else:
            row_inventory = (
                await db.execute(
                    select(FeedInventory).where(FeedInventory.ingredient == "Dry jowar stover")
                )
            ).scalar_one()
            assert row_inventory.qty_on_hand == 1


async def test_absent_finished_stock_reports_zero_available_and_creates_no_dispensing_record(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    refused = await dispense(client, owner, recipe_code="CREEP", qty_kg=0.5)
    assert refused.status_code == 400, refused.text
    assert refused.json()["detail"] == "CREEP: need 0.500 kg ready feed, have 0.000 kg"
    async with get_sessionmaker()() as db:
        assert not list((await db.execute(select(FeedFinishedStock))).scalars())
        assert not list((await db.execute(select(FeedingRecord))).scalars())


async def test_missing_unreferenced_raw_row_reports_zero_and_cannot_create_feed_from_nothing(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    # A warm sparse retained stock catalogue is permitted by the current
    # schema. Only this wholly unreferenced inventory row is removed; every
    # original balance/identity is restored even on a mutated failure.
    async with get_sessionmaker()() as db:
        item = (
            await db.execute(
                select(FeedInventory).where(FeedInventory.ingredient == "Dry jowar stover")
            )
        ).scalar_one()
        original = {
            column.key: getattr(item, column.key) for column in FeedInventory.__table__.columns
        }
        await db.delete(item)
        await db.commit()
    try:
        refused = await dispense(client, owner, recipe_code="DRY_ROUGHAGE_ONLY", qty_kg=0.5)
        assert refused.status_code == 400, refused.text
        assert refused.json()["detail"] == "Dry jowar stover: need 0.500 kg, have 0.000 kg"
        async with get_sessionmaker()() as db:
            assert not list((await db.execute(select(FeedingRecord))).scalars())
    finally:
        async with get_sessionmaker()() as db:
            db.add(FeedInventory(**original))
            await db.commit()


async def test_native_mix_accepts_a_single_six_decimal_reference_quantum_at_mass_tolerance(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    await mix_ready(client, owner, "CREEP", 1)
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        recipe = (
            await db.execute(
                select(FeedRecipe)
                .where(FeedRecipe.code == "CREEP")
                .options(selectinload(FeedRecipe.lines))
            )
        ).scalar_one()
        assert farm is not None and db.autoflush is False
        maize = next(line for line in recipe.lines if line.ingredient == "Crushed maize")
        maize.kg_per_100kg = 60.000001
        try:
            try:
                mixed = await mix_feed_batch(db, farm, recipe.code, 1)
            except Exception as exc:
                pytest.fail(f"One allowed six-decimal reference quantum must mix: {exc!r}")
            assert mixed.id == recipe.id
            finished = (await db.execute(select(FeedFinishedStock))).scalar_one()
            assert finished.qty_on_hand == 2
        finally:
            await db.rollback()
    async with get_sessionmaker()() as db:
        finished = (await db.execute(select(FeedFinishedStock))).scalar_one()
        assert finished.qty_on_hand == 1


async def test_absent_unreferenced_recipe_ingredient_cannot_credit_finished_feed(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    await stock_all(client, owner, 1000)
    async with get_sessionmaker()() as db:
        item = (
            await db.execute(select(FeedInventory).where(FeedInventory.ingredient == "Mineral mix"))
        ).scalar_one()
        original = {
            column.key: getattr(item, column.key) for column in FeedInventory.__table__.columns
        }
        await db.delete(item)
        await db.commit()
    try:
        refused = await client.post(
            "/api/feeding/mix", headers=owner, json={"recipe_code": "CREEP", "batch_kg": 1}
        )
        assert refused.status_code == 400, refused.text
        assert refused.json()["detail"] == "Mineral mix: need 0.010 kg, have 0.000 kg"
        async with get_sessionmaker()() as db:
            assert not list((await db.execute(select(FeedFinishedStock))).scalars())
    finally:
        async with get_sessionmaker()() as db:
            db.add(FeedInventory(**original))
            await db.commit()
