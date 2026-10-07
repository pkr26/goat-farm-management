"""A real unreferenced tenant removed after keyset selection cannot starve the page."""

import asyncio
from datetime import date, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import Result, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Executable

from app.db import get_sessionmaker
from app.models import Animal, Farm, Task
from app.seed import seed_new_farm
from app.services.cadence import ensure_cadence_farm_batch
from app.services.retention import _delete_batch

from .conftest import owner_with_farm


async def test_a_really_retained_missing_farm_does_not_starve_the_next_keyset_tenant(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await owner_with_farm(client, email="cadence-retained-page@example.test")
    async with get_sessionmaker()() as db:
        bootstrap = await db.get(Farm, int(headers["X-Farm-Id"]))
        assert bootstrap is not None
        # The first restored tenant has never had configuration or stock;
        # it has no inbound operational/auth FK and can be natively retained.
        empty = Farm(
            owner_id=bootstrap.owner_id,
            name="Unreferenced restored tenant",
            timezone="Asia/Kolkata",
            created_at=datetime(2025, 1, 1),
        )
        healthy = Farm(
            owner_id=bootstrap.owner_id,
            name="Later restored stocked tenant",
            timezone="Asia/Kolkata",
            created_at=datetime(2025, 1, 1),
        )
        db.add(empty)
        await db.flush()
        db.add(healthy)
        await db.flush()
        await seed_new_farm(db, healthy)
        stock = Animal(
            farm_id=healthy.id,
            tag_number="LATER-CADENCE-STOCK",
            sex="F",
            source="PURCHASED",
            current_bucket="FOUNDATION",
            status="ACTIVE",
            estimated_dob=date(2023, 1, 1),
            purchase_date=date(2025, 1, 2),
            created_at=datetime(2025, 1, 2),
        )
        db.add(stock)
        await db.commit()
        empty_id, healthy_id, stock_id = empty.id, healthy.id, stock.id
    reference = date(2026, 11, 2)
    monkeypatch.setattr("app.services.cadence.today", lambda timezone_name: reference)
    read_finished = asyncio.Event()
    continue_page = asyncio.Event()
    original_execute = AsyncSession.execute
    async with get_sessionmaker()() as caller:
        paused = False

        async def pause_after_the_real_keyset_select(
            db: AsyncSession,
            statement: Executable,
            params: dict[str, Any] | list[dict[str, Any]] | None = None,
            **kwargs: Any,
        ) -> Result[Any]:
            nonlocal paused
            result = await original_execute(db, statement, params, **kwargs)
            if db is caller and not paused:
                # This caller's first real execution is its keyset SELECT.
                # Keep the actual buffered Result and all business logic.
                paused = True
                read_finished.set()
                await continue_page.wait()
            return result

        monkeypatch.setattr(AsyncSession, "execute", pause_after_the_real_keyset_select)
        page = asyncio.create_task(
            ensure_cadence_farm_batch(caller, batch_size=2, after_farm_id=empty_id - 1)
        )
        try:
            try:
                await asyncio.wait_for(read_finished.wait(), timeout=30)
            except TimeoutError:
                pytest.fail("The actual cadence page did not finish its keyset read")
            async with get_sessionmaker()() as retention:
                removed = await _delete_batch(
                    retention,
                    table=Farm,
                    id_column=Farm.id,
                    candidates=select(Farm.id).where(Farm.id == empty_id),
                    batch_size=1,
                )
                assert removed == 1
                await retention.commit()
            continue_page.set()
            try:
                result = await asyncio.wait_for(page, timeout=30)
            except TimeoutError:
                pytest.fail("A missing first tenant must not stall the actual bounded page")
            assert result == (2, healthy_id)
        finally:
            continue_page.set()
            if not page.done():
                await asyncio.wait_for(page, timeout=30)
    async with get_sessionmaker()() as observer:
        assert await observer.get(Farm, empty_id) is None
        farm = await observer.get(Farm, healthy_id)
        animal = await observer.get(Animal, stock_id)
        assert farm is not None and animal is not None and animal.status == "ACTIVE"
        duties = list(
            (await observer.execute(select(Task).where(Task.farm_id == healthy_id))).scalars()
        )
        daily = [task for task in duties if task.title_key == "morning_feed_routine"]
        assert [(task.due_date, task.status) for task in daily] == [(reference, "PENDING")], (
            "Retaining an unreferenced first tenant must not starve a later stocked tenant"
        )
