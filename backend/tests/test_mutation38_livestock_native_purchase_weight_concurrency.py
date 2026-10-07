"""A native caller's changed weight cohort must not silently acquire unweighed heads."""

import asyncio
import contextlib

import httpx
import pytest
from sqlalchemy import func, select, text

from app.db import get_sessionmaker
from app.models import Animal, Farm, PurchaseBatch, Task, Transaction, WeightRecord
from app.services.purchases import create_purchase_batch
from app.utils import today

from .conftest import owner_with_farm


async def _attempt_purchase(farm_id: int, weights: list[float]) -> bool:
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        try:
            await create_purchase_batch(
                db,
                farm,
                today(),
                "Native mutable arrival cohort",
                2,
                None,
                None,
                100.0,
                "Caller owns a mutable two-head arrival list",
                True,
                individual_weights_kg=weights,
            )
            await db.commit()
        except ValueError:
            await db.rollback()
            return True
    return False


async def _wait_for_purchase_insert_blocked_by(holder_pid: int, action: asyncio.Task[bool]) -> None:
    for _ in range(1000):
        assert not action.done(), "purchase must reach the held foreign-key lock"
        async with get_sessionmaker()() as db:
            blocked = (
                await db.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE datname = current_database() "
                        "AND wait_event_type = 'Lock' "
                        "AND :holder_pid = ANY(pg_blocking_pids(pid)) "
                        "AND query LIKE '%INSERT INTO purchase_batches%')"
                    ),
                    {"holder_pid": holder_pid},
                )
            ).scalar_one()
        if blocked:
            return
        await asyncio.sleep(0.01)
    pytest.fail("real purchase INSERT never waited on the held farm foreign-key lock")


async def test_native_mutable_arrival_list_cannot_commit_a_shortened_weight_cohort(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    weights = [12.0, 13.0]
    action: asyncio.Task[bool] | None = None
    async with get_sessionmaker()() as holder:
        holder_pid = (await holder.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        await holder.execute(select(Farm.id).where(Farm.id == farm_id).with_for_update())
        try:
            action = asyncio.create_task(_attempt_purchase(farm_id, weights))
            await _wait_for_purchase_insert_blocked_by(holder_pid, action)
            # The service has validated this ordinary native list, then awaited
            # real SQL. Its caller still owns that same mutable list. Only the
            # caller input changes; no model, session, schema or zip is mocked.
            weights.pop()
            await holder.commit()
            try:
                rejected = await action
            except Exception as error:
                pytest.fail(f"changed arrival cohort must reject atomically: {error!r}")
        finally:
            await holder.rollback()
            if action is not None and not action.done():
                action.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await action
    async with get_sessionmaker()() as db:
        batches = (
            await db.execute(
                select(func.count())
                .select_from(PurchaseBatch)
                .where(PurchaseBatch.farm_id == farm_id)
            )
        ).scalar_one()
        animals = (
            await db.execute(
                select(func.count()).select_from(Animal).where(Animal.farm_id == farm_id)
            )
        ).scalar_one()
        weights_count = (
            await db.execute(
                select(func.count())
                .select_from(WeightRecord)
                .where(WeightRecord.farm_id == farm_id)
            )
        ).scalar_one()
        transactions = (
            await db.execute(
                select(func.count()).select_from(Transaction).where(Transaction.farm_id == farm_id)
            )
        ).scalar_one()
        duties = (
            await db.execute(select(func.count()).select_from(Task).where(Task.farm_id == farm_id))
        ).scalar_one()
    assert rejected, (
        "a shortened cohort must raise ValueError instead of silently truncating; "
        f"committed batch/animal/weight/ledger/duty counts="
        f"{(batches, animals, weights_count, transactions, duties)}"
    )
    assert (batches, animals, weights_count, transactions, duties) == (0, 0, 0, 0, 0)
