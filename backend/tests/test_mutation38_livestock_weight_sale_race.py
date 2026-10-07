"""A weight queued behind a real sale must recheck the live-animal guard."""

import asyncio
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import animals as animals_api
from app.db import get_sessionmaker
from app.models import Animal, Farm, Transaction, User, WeightRecord
from app.schemas.animals import AnimalOut, StatusChangeIn
from app.utils import today

from .conftest import owner_with_farm
from .test_concurrency import make_animal


async def test_weight_waiting_for_a_sale_rejects_the_committed_sold_state(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client, email="weight-sale-race@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    animal_id = await make_animal(client, owner, tag="WEIGHT-SALE-RACE")
    async with get_sessionmaker()() as observer:
        assert not list(
            (
                await observer.execute(
                    select(WeightRecord).where(WeightRecord.animal_id == animal_id)
                )
            ).scalars()
        )

    sale_prepared = asyncio.Event()
    permit_sale_commit = asyncio.Event()
    sale_pid = 0
    original_status_mutation = animals_api._change_status_mutation

    async def pause_prepared_sale(
        payload: StatusChangeIn,
        target_id: int,
        db: AsyncSession,
        farm: Farm,
        user: User,
        perms: set[str],
    ) -> tuple[AnimalOut, tuple[str, str] | None]:
        nonlocal sale_pid
        result = await original_status_mutation(payload, target_id, db, farm, user, perms)
        if target_id == animal_id and payload.new_status == "SOLD":
            # Run the actual public-sale mutation in full, retaining its
            # Animal lock and flushed ledger/lifecycle state. This barrier
            # controls only when the surrounding real request may commit.
            pid = await db.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(pid, int)
            sale_pid = pid
            sale_prepared.set()
            await permit_sale_commit.wait()
        return result

    monkeypatch.setattr(animals_api, "_change_status_mutation", pause_prepared_sale)
    sale = asyncio.create_task(
        client.post(
            f"/api/animals/{animal_id}/status",
            json={"new_status": "SOLD", "date": today().isoformat(), "sale_price": 5000},
            headers=owner | {"Idempotency-Key": "weight-concurrent-sale"},
        )
    )
    weighing: asyncio.Task[httpx.Response] | None = None
    try:
        try:
            await asyncio.wait_for(sale_prepared.wait(), timeout=30)
        except TimeoutError:
            pytest.fail("The real public sale did not reach its prepared commit boundary")
        weighing = asyncio.create_task(
            client.post(
                f"/api/animals/{animal_id}/weight",
                json={"weight_kg": 27.5, "date": today().isoformat()},
                headers=owner | {"Idempotency-Key": "weight-concurrent-reading"},
            )
        )
        # A PG dependency proves the actual competing request has reached
        # the sale's retained lock; no sleep determines the winning order.
        for _ in range(1000):
            async with get_sessionmaker()() as observer:
                queued = await observer.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE datname = current_database() "
                        "AND :sale_pid = ANY(pg_blocking_pids(pid)))"
                    ),
                    {"sale_pid": sale_pid},
                )
            if queued:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("The weight write never queued behind the actual sale")
        permit_sale_commit.set()
        try:
            sold, weighed = await asyncio.wait_for(asyncio.gather(sale, weighing), timeout=30)
        except TimeoutError:
            pytest.fail("The sale and queued weight did not complete after the sale was released")
    finally:
        permit_sale_commit.set()
        pending = [task for task in (sale, weighing) if task is not None]
        for task in pending:
            if not task.done():
                task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    assert sold.status_code == 200, sold.text
    assert sold.json()["status"] == "SOLD"
    assert weighed.status_code == 400, weighed.text
    assert "is sold" in weighed.json()["detail"]
    async with get_sessionmaker()() as observer:
        animal = await observer.get(Animal, animal_id)
        assert animal is not None
        assert animal.status == "SOLD"
        assert not list(
            (
                await observer.execute(
                    select(WeightRecord).where(WeightRecord.animal_id == animal_id)
                )
            ).scalars()
        )
        sales = list(
            (
                await observer.execute(
                    select(Transaction).where(
                        Transaction.farm_id == farm_id,
                        Transaction.source_type == "ANIMAL_SALE",
                        Transaction.source_id == animal_id,
                    )
                )
            ).scalars()
        )
        assert len(sales) == 1
        assert sales[0].amount == Decimal("5000.00")
