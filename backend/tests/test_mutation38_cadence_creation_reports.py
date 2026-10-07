"""A helper-created cadence duty must survive its caller's conditional commit."""

from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_sessionmaker
from app.models import Animal, Farm, HealthRound, HealthRoundTarget, Task
from app.seed import seed_new_farm
from app.services.cadence import (
    _ensure_daily_feed_routine,
    _ensure_daily_water_check,
    _ensure_ppr_round,
)
from app.services.tasks import lock_manual_task_queue

from .conftest import owner_with_farm


async def _initial_restored_cadence_farm(
    client: httpx.AsyncClient, label: str, introduction: date = date(2025, 1, 2)
) -> tuple[int, int]:
    headers = await owner_with_farm(client, email=f"cadence-created-{label}@example.test")
    async with get_sessionmaker()() as db:
        bootstrap = await db.get(Farm, int(headers["X-Farm-Id"]))
        assert bootstrap is not None
        # Initially restored tenant/stock facts predate these genuine native
        # historical calendar references. No contemporary date is rewritten.
        farm = Farm(
            owner_id=bootstrap.owner_id,
            name=f"Restored calendar {label}",
            timezone="Asia/Kolkata",
            created_at=datetime(2025, 1, 1),
        )
        db.add(farm)
        await db.flush()
        await seed_new_farm(db, farm)
        animal = Animal(
            farm_id=farm.id,
            tag_number=f"CALENDAR-{label}",
            sex="F",
            source="PURCHASED",
            current_bucket="FOUNDATION",
            status="ACTIVE",
            estimated_dob=date(2023, 1, 1),
            purchase_date=introduction,
            created_at=datetime.combine(introduction, datetime.min.time()),
        )
        db.add(animal)
        await db.commit()
        return farm.id, animal.id


@pytest.mark.parametrize("programme", ["ppr", "feed", "water"])
async def test_new_cadence_work_survives_the_native_callers_conditional_commit(
    client: httpx.AsyncClient, programme: str
) -> None:
    farm_id, animal_id = await _initial_restored_cadence_farm(client, programme)
    helpers: dict[str, Callable[[AsyncSession, int, date], Awaitable[bool]]] = {
        "ppr": _ensure_ppr_round,
        "feed": _ensure_daily_feed_routine,
        "water": _ensure_daily_water_check,
    }
    keys = {
        "ppr": "ppr_vaccination_round",
        "feed": "morning_feed_routine",
        "water": "daily_water_check",
    }
    reference = date(2026, 9, 14)
    async with get_sessionmaker()() as caller:
        farm = await caller.get(Farm, farm_id)
        assert farm is not None
        await lock_manual_task_queue(caller, farm)
        try:
            created = await helpers[programme](caller, farm_id, reference)
        except (AttributeError, TypeError, ValueError) as error:
            pytest.fail(f"A valid first native {programme} round must materialize: {error}")
        # This is the actual documented caller protocol, not a recording mock:
        # only a helper that made work authorizes committing this transaction.
        if created:
            await caller.commit()
        else:
            await caller.rollback()
    async with get_sessionmaker()() as observer:
        tasks = list(
            (await observer.execute(select(Task).where(Task.farm_id == farm_id))).scalars()
        )
        assert len(tasks) == 1, (
            "The genuine first programme duty was lost before the board could show it"
        )
        task = tasks[0]
        assert task.title_key == keys[programme] and task.status == "PENDING"
        assert task.due_date == reference and task.auto_generated
        if programme == "ppr":
            round_ = await observer.get(HealthRound, task.id)
            assert round_ is not None and round_.required_components == ["PPR"]
            targets = set(
                (
                    await observer.execute(
                        select(HealthRoundTarget.animal_id).where(
                            HealthRoundTarget.task_id == task.id
                        )
                    )
                ).scalars()
            )
            assert targets == {animal_id}


async def test_annual_ppr_suppression_resolves_the_newest_of_two_actual_prior_rounds(
    client: httpx.AsyncClient,
) -> None:
    farm_id, animal_id = await _initial_restored_cadence_farm(client, "ppr-two")
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        await lock_manual_task_queue(db, farm)
        for prior in (date(2025, 1, 3), date(2026, 1, 3)):
            assert await _ensure_ppr_round(db, farm_id, prior)
        await db.commit()
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        await lock_manual_task_queue(db, farm)
        try:
            created = await _ensure_ppr_round(db, farm_id, date(2026, 1, 4))
        except MultipleResultsFound as error:
            pytest.fail(f"Newest PPR must resolve over real positive history: {error}")
        assert not created
        await db.commit()
    async with get_sessionmaker()() as db:
        tasks = list(
            (
                await db.execute(
                    select(Task).where(Task.farm_id == farm_id).order_by(Task.due_date)
                )
            ).scalars()
        )
        assert [task.due_date for task in tasks] == [date(2025, 1, 3), date(2026, 1, 3)]
        for task in tasks:
            assert await db.get(HealthRound, task.id) is not None
            assert set(
                (
                    await db.execute(
                        select(HealthRoundTarget.animal_id).where(
                            HealthRoundTarget.task_id == task.id
                        )
                    )
                ).scalars()
            ) == {animal_id}


async def test_ppr_operator_schedule_at_day_thirty_suppresses_a_new_current_round(
    client: httpx.AsyncClient,
) -> None:
    farm_id, animal_id = await _initial_restored_cadence_farm(client, "ppr-edge")
    reference = date(2026, 1, 4)
    planned = reference + timedelta(days=30)
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        await lock_manual_task_queue(db, farm)
        assert await _ensure_ppr_round(db, farm_id, planned)
        await db.commit()
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        await lock_manual_task_queue(db, farm)
        assert not await _ensure_ppr_round(db, farm_id, reference)
        await db.commit()
    async with get_sessionmaker()() as db:
        tasks = list((await db.execute(select(Task).where(Task.farm_id == farm_id))).scalars())
        assert len(tasks) == 1 and tasks[0].due_date == planned
        assert set(
            (
                await db.execute(
                    select(HealthRoundTarget.animal_id).where(
                        HealthRoundTarget.task_id == tasks[0].id
                    )
                )
            ).scalars()
        ) == {animal_id}
