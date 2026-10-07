"""A service rejection rolls back real procurement writes and permits retry."""

from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import purchases as purchases_api
from app.db import get_sessionmaker
from app.models import Animal, IdempotencyRecord, PurchaseBatch, Task, Transaction, WeightRecord
from app.services.purchases import create_purchase_batch
from app.utils import today

from .conftest import owner_with_farm


async def test_purchase_service_rejection_returns_400_rolls_back_and_allows_same_key_retry(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    headers = owner | {"Idempotency-Key": "purchase-service-rejection-retry"}
    payload = {
        "date": today().isoformat(),
        "supplier": "Supplier validation seam",
        "count": 2,
        "avg_weight_kg": 30.0,
        "total_price": 200.0,
        "create_animals": True,
    }
    real_create = create_purchase_batch

    async def reject_after_real_writes(*args: Any, **kwargs: Any) -> PurchaseBatch:
        batch = await real_create(*args, **kwargs)
        db = args[0]
        assert isinstance(db, AsyncSession)
        await db.flush()
        assert batch.id > 0
        assert (
            await db.execute(select(func.count(Animal.id)).where(Animal.farm_id == farm_id))
        ).scalar_one() == 2
        assert (
            await db.execute(
                select(func.count(WeightRecord.id)).where(WeightRecord.farm_id == farm_id)
            )
        ).scalar_one() == 2
        assert (
            await db.execute(select(func.count(Task.id)).where(Task.purchase_batch_id == batch.id))
        ).scalar_one() == 11
        assert (
            await db.execute(
                select(func.count(Transaction.id)).where(Transaction.farm_id == farm_id)
            )
        ).scalar_one() == 1
        # Inject only the documented component rejection, after actual valid
        # service writes. This is failure/rollback coverage, not a claim that
        # ordinary current JSON inputs evade the schema and service guards.
        raise ValueError("Purchase service rejected supplier record")

    monkeypatch.setattr(purchases_api, "create_purchase_batch", reject_after_real_writes)
    rejected = await client.post("/api/purchases/new", headers=headers, json=payload)
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["detail"] == "Purchase service rejected supplier record"
    async with get_sessionmaker()() as db:
        for model in (PurchaseBatch, Animal, WeightRecord, Transaction):
            assert (
                await db.execute(select(func.count(model.id)).where(model.farm_id == farm_id))
            ).scalar_one() == 0
        assert (
            await db.execute(
                select(func.count(Task.id)).where(
                    Task.farm_id == farm_id, Task.purchase_batch_id.is_not(None)
                )
            )
        ).scalar_one() == 0
        assert (
            await db.execute(
                select(func.count(IdempotencyRecord.id)).where(
                    IdempotencyRecord.farm_id == farm_id,
                    IdempotencyRecord.operation == "POST /api/purchases/new",
                )
            )
        ).scalar_one() == 0

    monkeypatch.setattr(purchases_api, "create_purchase_batch", real_create)
    retried = await client.post("/api/purchases/new", headers=headers, json=payload)
    assert retried.status_code == 201, retried.text
    assert retried.headers.get("Idempotency-Replayed") != "true"
    detail = await client.get(f"/api/purchases/{retried.json()['id']}", headers=owner)
    assert detail.status_code == 200, detail.text
    assert detail.json()["animals_total"] == 2
    assert len(detail.json()["tasks"]) == 11
    async with get_sessionmaker()() as db:
        assert (
            await db.execute(
                select(func.count(Transaction.id)).where(Transaction.farm_id == farm_id)
            )
        ).scalar_one() == 1
