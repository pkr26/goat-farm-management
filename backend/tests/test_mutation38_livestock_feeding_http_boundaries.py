"""Public feed histories, stock identity and dispense defaults preserve literal contracts."""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import FeedInventory
from app.models.enums import IngredientCategory
from app.utils import today

from .conftest import owner_with_farm
from .test_feeding_extended import dispense, get_inventory, mix_ready, stock_dry_roughage


@pytest.mark.parametrize("explicit_date", [False, True])
async def test_actual_dispensing_accepts_omitted_and_explicit_current_farm_date(
    client: httpx.AsyncClient,
    explicit_date: bool,
) -> None:
    owner = await owner_with_farm(client)
    await stock_dry_roughage(client, owner, 5)
    response = (
        await dispense(client, owner, date=today().isoformat())
        if explicit_date
        else await dispense(client, owner)
    )
    assert response.status_code == 201, response.text
    assert response.json()["date"] == today().isoformat()


async def test_feed_history_exact_inclusive_dates_omitted_bounds_and_default_page(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    await stock_dry_roughage(client, owner, 20)
    recorded_ids = []
    for qty in (1, 2):
        response = await dispense(client, owner, qty_kg=qty)
        assert response.status_code == 201, response.text
        recorded_ids.append(response.json()["id"])
    other = await owner_with_farm(client, "other-feed-history@example.test")
    await stock_dry_roughage(client, other, 5)
    foreign = await dispense(client, other)
    assert foreign.status_code == 201, foreign.text
    farm_day = today()
    cases = [
        ({}, list(reversed(recorded_ids))),
        ({"date_from": farm_day.isoformat()}, list(reversed(recorded_ids))),
        ({"date_to": farm_day.isoformat()}, list(reversed(recorded_ids))),
        (
            {"date_from": farm_day.isoformat(), "date_to": farm_day.isoformat()},
            list(reversed(recorded_ids)),
        ),
        ({"date_from": (farm_day + timedelta(days=1)).isoformat()}, []),
        ({"date_to": (farm_day - timedelta(days=1)).isoformat()}, []),
    ]
    for params, expected_ids in cases:
        history = await client.get("/api/feeding/records", params=params, headers=owner)
        assert history.status_code == 200, history.text
        data = history.json()
        assert [row["id"] for row in data["records"]] == expected_ids
        assert data["total"] == len(expected_ids)
        assert (data["limit"], data["offset"]) == (100, 0)
    reversed_range = await client.get(
        "/api/feeding/records",
        params={
            "date_from": (farm_day + timedelta(days=1)).isoformat(),
            "date_to": farm_day.isoformat(),
        },
        headers=owner,
    )
    assert reversed_range.status_code == 400, reversed_range.text
    assert reversed_range.json()["detail"] == "date_from must be on or before date_to"


@pytest.mark.parametrize("identity,expected", [(0, 404), (2_147_483_647, 200)])
async def test_stock_add_keeps_reserved_zero_and_accepts_restored_int4_ceiling_identity(
    client: httpx.AsyncClient,
    identity: int,
    expected: int,
) -> None:
    owner = await owner_with_farm(client)
    async with get_sessionmaker()() as db:
        # Feed inventory has no public create route. Ordinary constraint-valid
        # retained stock rows may have either explicit INTEGER boundary key.
        db.add(
            FeedInventory(
                id=identity,
                farm_id=int(owner["X-Farm-Id"]),
                ingredient=f"Retained dry fodder {identity}",
                category=IngredientCategory.ROUGHAGE_DRY.value,
                qty_on_hand=0,
            )
        )
        await db.commit()
    listed = await get_inventory(client, owner)
    assert any(item["id"] == identity for item in listed)
    added = await client.post(
        f"/api/feeding/inventory/{identity}/add",
        headers=owner | {"Idempotency-Key": "boundary-stock-restock"},
        json={"qty_kg": 1},
    )
    assert added.status_code == expected, added.text
    async with get_sessionmaker()() as db:
        row = await db.get(FeedInventory, identity)
        assert row is not None and row.qty_on_hand == (1 if expected == 200 else 0)


async def test_actual_finished_mix_stock_and_raw_inventory_stay_in_their_own_farm(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    other = await owner_with_farm(client, "other-finished-stock@example.test")
    await mix_ready(client, owner, "MAINTENANCE_75_25", 100)
    await mix_ready(client, other, "CREEP", 20)
    stock = await client.get("/api/feeding/finished-stock", headers=owner)
    assert stock.status_code == 200, stock.text
    assert len(stock.json()) == 1
    assert stock.json()[0]["recipe_code"] == "MAINTENANCE_75_25"
    assert stock.json()[0]["recipe_name"] == "Maintenance 75:25"
    assert stock.json()[0]["qty_on_hand"] == 100
    raw = await client.get("/api/feeding/inventory", headers=owner)
    assert raw.status_code == 200, raw.text
    async with get_sessionmaker()() as db:
        own_ids = set(
            (
                await db.execute(
                    select(FeedInventory.id).where(FeedInventory.farm_id == int(owner["X-Farm-Id"]))
                )
            ).scalars()
        )
    assert {item["id"] for item in raw.json()} == own_ids
