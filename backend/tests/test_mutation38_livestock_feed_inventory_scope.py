"""A real two-farm stock catalogue must return only the authenticated farm's rows."""

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import FeedInventory

from .conftest import owner_with_farm


async def test_real_seeded_raw_inventory_is_complete_and_farm_local(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    other = await owner_with_farm(client, "other-raw-inventory@example.test")
    async with get_sessionmaker()() as db:
        own_ids = set(
            (
                await db.execute(
                    select(FeedInventory.id).where(FeedInventory.farm_id == int(owner["X-Farm-Id"]))
                )
            ).scalars()
        )
        other_ids = set(
            (
                await db.execute(
                    select(FeedInventory.id).where(FeedInventory.farm_id == int(other["X-Farm-Id"]))
                )
            ).scalars()
        )
    assert own_ids and other_ids and own_ids.isdisjoint(other_ids)
    response = await client.get("/api/feeding/inventory", headers=owner)
    assert response.status_code == 200, response.text
    assert {item["id"] for item in response.json()} == own_ids
