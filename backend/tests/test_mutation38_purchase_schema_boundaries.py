"""Purchase wire boundaries include source facts and retained response defaults."""

import httpx
from sqlalchemy import func, select

from app.db import get_sessionmaker
from app.models import IdempotencyRecord, PurchaseBatch, Transaction
from app.utils import today

from .conftest import owner_with_farm


async def test_purchase_origin_market_accepts_120_characters_and_persists_them(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    market = "M" * 120
    response = await client.post(
        "/api/purchases/new",
        headers=owner,
        json={
            "date": today().isoformat(),
            "count": 1,
            "create_animals": False,
            "origin_market": market,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["origin_market"] == market
    async with get_sessionmaker()() as db:
        batch = await db.get(PurchaseBatch, response.json()["id"])
        assert batch is not None
        assert batch.origin_market == market


async def test_purchase_transport_accepts_a_recorded_zero_hour_journey(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    response = await client.post(
        "/api/purchases/new",
        headers=owner,
        json={
            "date": today().isoformat(),
            "count": 1,
            "create_animals": False,
            "transport_hours": 0,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["transport_hours"] == 0
    async with get_sessionmaker()() as db:
        batch = await db.get(PurchaseBatch, response.json()["id"])
        assert batch is not None
        assert batch.transport_hours == 0


async def test_retained_older_purchase_response_replays_zero_optional_occupancy_counts(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    headers = owner | {"Idempotency-Key": "retained-purchase-response"}
    payload = {"date": today().isoformat(), "count": 1, "create_animals": False}
    original = await client.post("/api/purchases/new", json=payload, headers=headers)
    assert original.status_code == 201, original.text
    assert original.json()["animals_created"] == 0
    assert original.json()["open_tasks"] == 0

    # Restore an actual committed claim in the retained older response shape.
    # Required facts, request hash, scope, response status and dates remain;
    # only the optional aggregate fields added by the response schema are absent.
    async with get_sessionmaker()() as db:
        record = (
            await db.execute(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.farm_id == farm_id,
                    IdempotencyRecord.operation == "POST /api/purchases/new",
                )
            )
        ).scalar_one()
        assert record.completed_at is not None
        assert record.response_status == 201
        assert record.response_body is not None
        retained = dict(record.response_body)
        assert retained.pop("animals_created") == 0
        assert retained.pop("open_tasks") == 0
        record.response_body = retained
        await db.commit()

    replay = await client.post("/api/purchases/new", json=payload, headers=headers)
    assert replay.status_code == 201, replay.text
    assert replay.headers.get("Idempotency-Replayed") == "true"
    assert replay.json()["id"] == original.json()["id"]
    assert replay.json()["animals_created"] == 0
    assert replay.json()["open_tasks"] == 0
    async with get_sessionmaker()() as db:
        assert (
            await db.execute(
                select(func.count(PurchaseBatch.id)).where(PurchaseBatch.farm_id == farm_id)
            )
        ).scalar_one() == 1
        assert (
            await db.execute(
                select(func.count(Transaction.id)).where(Transaction.farm_id == farm_id)
            )
        ).scalar_one() == 1
