"""Real tenant pages, positive-cardinality probes and committed configuration."""

from datetime import UTC, date, datetime

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import MultipleResultsFound

from app.db import get_sessionmaker
from app.models import Animal, Farm, Task
from app.services.cadence import (
    _farm_earliest_introduction,
    _farm_has_active_animals,
    _task_exists,
    ensure_cadence_farm_batch,
)
from app.utils import business_date

from .conftest import owner_with_farm
from .test_health_extended import make_batch


async def _public_tenant_with_initial_native_stock(
    client: httpx.AsyncClient, label: str, *, head: int = 1
) -> tuple[int, list[int]]:
    headers = await owner_with_farm(client, email=f"cadence-page-{label}@example.test")
    farm_id = int(headers["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        animals = [
            Animal(
                farm_id=farm_id,
                tag_number=f"PAGE-{label}-{index}",
                sex="F",
                source="PURCHASED",
                current_bucket="FOUNDATION",
                status="ACTIVE",
                estimated_dob=date(2023, 1, 1),
                purchase_date=date(2026, 1, 1),
            )
            for index in range(head)
        ]
        db.add_all(animals)
        await db.commit()
        return farm_id, [animal.id for animal in animals]


async def _assert_daily_board(farm_id: int, expected: date) -> None:
    async with get_sessionmaker()() as observer:
        tasks = list(
            (await observer.execute(select(Task).where(Task.farm_id == farm_id))).scalars()
        )
        daily = [task for task in tasks if task.title_key == "morning_feed_routine"]
        assert [(task.due_date, task.status) for task in daily] == [(expected, "PENDING")]


@pytest.mark.parametrize("batch_size", [0, 1, 1000, 1001])
async def test_native_cadence_page_admits_exactly_the_documented_size_range(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, batch_size: int
) -> None:
    farm_id, _ = await _public_tenant_with_initial_native_stock(client, f"size-{batch_size}")
    reference = date(2026, 11, 2)
    monkeypatch.setattr("app.services.cadence.today", lambda timezone_name: reference)
    async with get_sessionmaker()() as db:
        if batch_size in {0, 1001}:
            with pytest.raises(ValueError, match="batch_size must be between 1 and 1000"):
                await ensure_cadence_farm_batch(db, batch_size=batch_size)
        else:
            try:
                visited, cursor = await ensure_cadence_farm_batch(db, batch_size=batch_size)
            except ValueError as error:
                pytest.fail(f"A supported native page size must materialize its tenant: {error}")
            assert (visited, cursor) == (1, farm_id)
    if batch_size in {1, 1000}:
        await _assert_daily_board(farm_id, reference)
    else:
        async with get_sessionmaker()() as observer:
            assert not list(
                (await observer.execute(select(Task.id).where(Task.farm_id == farm_id))).scalars()
            )


async def test_omitting_native_page_cursor_includes_the_first_actual_tenant(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    farm_id, _ = await _public_tenant_with_initial_native_stock(client, "first")
    assert farm_id == 1
    reference = date(2026, 11, 2)
    monkeypatch.setattr("app.services.cadence.today", lambda timezone_name: reference)
    async with get_sessionmaker()() as db:
        visited, cursor = await ensure_cadence_farm_batch(db, batch_size=1)
        assert (visited, cursor) == (1, farm_id), (
            "The default page must not starve the first tenant"
        )
    await _assert_daily_board(farm_id, reference)


async def test_native_page_refreshes_a_real_cached_tenants_committed_business_timezone(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    farm_id, _ = await _public_tenant_with_initial_native_stock(client, "timezone")
    instant = datetime(2026, 10, 6, 4, tzinfo=UTC)

    def fixed_instant_business_day(timezone_name: str) -> date:
        return business_date(instant, timezone_name)

    monkeypatch.setattr("app.services.cadence.today", fixed_instant_business_day)
    async with get_sessionmaker()() as caller:
        cached = await caller.get(Farm, farm_id)
        assert cached is not None and cached.timezone == "Asia/Kolkata"
        async with get_sessionmaker()() as configuration_writer:
            committed = await configuration_writer.get(Farm, farm_id)
            assert committed is not None
            # A real native configuration write, independent of the stale
            # sweep session. No clinical history or query output is replaced.
            committed.timezone = "Pacific/Honolulu"
            await configuration_writer.commit()
        assert cached.timezone == "Asia/Kolkata"
        visited, cursor = await ensure_cadence_farm_batch(caller, batch_size=1)
        assert (visited, cursor) == (1, farm_id)
    await _assert_daily_board(farm_id, date(2026, 10, 5))
    async with get_sessionmaker()() as observer:
        farm = await observer.get(Farm, farm_id)
        assert farm is not None and farm.timezone == "Pacific/Honolulu"


@pytest.mark.parametrize("probe", ["animals", "tasks"])
async def test_native_cadence_existence_probes_accept_two_real_matching_rows(
    client: httpx.AsyncClient, probe: str
) -> None:
    farm_id, animal_ids = await _public_tenant_with_initial_native_stock(client, probe, head=2)
    async with get_sessionmaker()() as db:
        duties = [
            Task(
                farm_id=farm_id,
                title=f"Operator inspection {index}",
                category="FAMACHA",
                due_date=date(2026, 11, 2),
                status="PENDING",
                auto_generated=False,
            )
            for index in range(2)
        ]
        db.add_all(duties)
        await db.commit()
        task_ids = {task.id for task in duties}
    async with get_sessionmaker()() as db:
        try:
            exists = (
                await _farm_has_active_animals(db, farm_id)
                if probe == "animals"
                else await _task_exists(db, farm_id, Task.category == "FAMACHA")
            )
        except MultipleResultsFound as error:
            pytest.fail(f"A positive-cardinality existence probe must resolve true: {error}")
        assert exists
        assert set(
            (await db.execute(select(Animal.id).where(Animal.farm_id == farm_id))).scalars()
        ) == set(animal_ids)
        assert (
            set((await db.execute(select(Task.id).where(Task.farm_id == farm_id))).scalars())
            == task_ids
        )


async def test_native_earliest_introduction_uses_only_its_real_purchase_tenant(
    client: httpx.AsyncClient,
) -> None:
    own = await owner_with_farm(client, email="cadence-intro-own@example.test")
    other = await owner_with_farm(client, email="cadence-intro-other@example.test")
    await make_batch(client, own, date="2026-01-10", count=1, sex="F")
    await make_batch(client, other, date="2026-01-05", count=1, sex="F")
    async with get_sessionmaker()() as db:
        assert await _farm_earliest_introduction(db, int(own["X-Farm-Id"])) == date(2026, 1, 10)
        assert await _farm_earliest_introduction(db, int(other["X-Farm-Id"])) == date(2026, 1, 5)
