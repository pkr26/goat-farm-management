"""A priced historical animal import must not book a keyless acquisition."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select

from app.db import get_sessionmaker
from app.main import create_app
from app.models import Animal, PurchaseBatch, Transaction
from app.utils import today

from .conftest import owner_with_farm


@pytest.mark.parametrize("price", [0.0, 123.45])
async def test_historical_priced_import_requires_a_key_before_any_money_or_animal_write(
    client: httpx.AsyncClient, price: float
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    acquired_on = today() - timedelta(days=7)
    payload = {
        "sex": "F",
        "source": "PURCHASED",
        "current_bucket": "FOUNDATION",
        "historical_import_reason": "Migrated acquisition from the paper ledger",
        "purchase_date": acquired_on.isoformat(),
        "purchase_price": price,
    }

    # This client has no fixture request hooks or helper that manufactures a
    # key. Omitting the tag also keeps these requests without a natural key.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(), raise_app_exceptions=False),
        base_url="http://test",
    ) as plain:
        for _ in range(2):
            refusal = await plain.post("/api/animals", json=payload, headers=owner)
            assert "Idempotency-Key" not in refusal.request.headers
            assert refusal.status_code == 422, refusal.text
            assert refusal.json()["detail"] == (
                "An Idempotency-Key header is required for purchased-animal creation."
            )

        async with get_sessionmaker()() as db:
            assert (
                await db.execute(select(func.count(Animal.id)).where(Animal.farm_id == farm_id))
            ).scalar_one() == 0
            assert (
                await db.execute(
                    select(func.count(Transaction.id)).where(Transaction.farm_id == farm_id)
                )
            ).scalar_one() == 0

        # An explicit key makes the identical body valid and replayable,
        # proving the refusal above was the money-booking admission rule.
        keyed = owner | {"Idempotency-Key": "historical-acquisition-replay"}
        accepted = await plain.post("/api/animals", json=payload, headers=keyed)
        assert accepted.status_code == 201, accepted.text
        replay = await plain.post("/api/animals", json=payload, headers=keyed)
        assert replay.status_code == 201, replay.text
        assert replay.headers.get("Idempotency-Replayed") == "true"
        assert replay.json() == accepted.json()

    animal_id = int(accepted.json()["id"])
    async with get_sessionmaker()() as db:
        assert (
            await db.execute(select(func.count(Animal.id)).where(Animal.farm_id == farm_id))
        ).scalar_one() == 1
        assert (
            await db.execute(
                select(func.count(PurchaseBatch.id)).where(PurchaseBatch.farm_id == farm_id)
            )
        ).scalar_one() == 0
        ledger = (
            (await db.execute(select(Transaction).where(Transaction.farm_id == farm_id)))
            .scalars()
            .all()
        )
        assert len(ledger) == 1
        assert ledger[0].related_animal_id == animal_id
        assert ledger[0].category == "ANIMAL_PURCHASE"
        assert ledger[0].amount == Decimal(str(price))
        assert ledger[0].date == acquired_on
