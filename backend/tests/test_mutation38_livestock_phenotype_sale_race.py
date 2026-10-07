"""A phenotype edit queued behind a real sale must recheck the live-animal guard."""

import asyncio
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import animals as animals_api
from app.db import get_sessionmaker
from app.models import Animal, Farm, Transaction, User
from app.schemas.animals import AnimalOut, StatusChangeIn
from app.utils import today

from .conftest import owner_with_farm
from .test_concurrency import make_animal


async def test_phenotype_edit_waiting_for_a_sale_rejects_the_committed_sold_state(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client, email="phenotype-sale-race@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    animal_id = await make_animal(
        client, owner, tag="PH-SALE-RACE", coat_color="black", horned=True
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
            # The actual status endpoint has acquired its Animal lock and
            # flushed the sale, ledger and lifecycle cascade. Pause only
            # the commit boundary, preserving all real mutation behavior.
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
            headers=owner | {"Idempotency-Key": "phenotype-concurrent-sale"},
        )
    )
    edit: asyncio.Task[httpx.Response] | None = None
    try:
        await asyncio.wait_for(sale_prepared.wait(), timeout=10)
        edit = asyncio.create_task(
            client.patch(
                f"/api/animals/{animal_id}",
                json={"coat_color": "brown", "horned": False},
                headers=owner,
            )
        )
        # Inspect a real PG lock dependency, not elapsed wall-clock time:
        # the competing write has reached the uncommitted sale transaction.
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
            raise AssertionError("The phenotype edit never queued behind the actual sale")
        permit_sale_commit.set()
        sold, edited = await asyncio.gather(sale, edit)
    finally:
        permit_sale_commit.set()
        pending = [task for task in (sale, edit) if task is not None]
        for task in pending:
            if not task.done():
                task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    assert sold.status_code == 200, sold.text
    assert sold.json()["status"] == "SOLD"
    assert edited.status_code == 409, edited.text
    async with get_sessionmaker()() as observer:
        animal = await observer.get(Animal, animal_id)
        assert animal is not None
        assert (animal.status, animal.coat_color, animal.horned) == ("SOLD", "black", True)
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
