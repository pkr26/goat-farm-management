"""Calendar bounds, inclusive backfill and declared herd-round snapshots."""

from datetime import date, datetime

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Animal, Farm, HealthRound, HealthRoundTarget, Task
from app.seed import seed_new_farm
from app.services.cadence import _ensure_calendar_rounds, _latest_occurrence, _month_bounds
from app.services.tasks import lock_manual_task_queue

from .conftest import owner_with_farm


@pytest.mark.parametrize(
    ("reference", "first", "last"),
    [
        (date(2025, 2, 19), date(2025, 2, 1), date(2025, 2, 28)),
        (date(2024, 2, 29), date(2024, 2, 1), date(2024, 2, 29)),
        (date(2026, 1, 31), date(2026, 1, 1), date(2026, 1, 31)),
        (date(2026, 4, 12), date(2026, 4, 1), date(2026, 4, 30)),
    ],
    ids=["common-february", "leap-february", "january", "april"],
)
def test_calendar_month_bounds_cover_the_whole_actual_month(
    reference: date, first: date, last: date
) -> None:
    try:
        actual = _month_bounds(reference)
    except (ValueError, IndexError) as error:
        pytest.fail(f"Valid calendar month must have usable first/last dates: {error}")
    assert actual == (first, last)
    assert first <= reference <= last


@pytest.mark.parametrize(
    ("month", "reference", "expected"),
    [
        (3, date(2026, 3, 31), date(2026, 3, 1)),
        (3, date(2026, 2, 28), date(2025, 3, 1)),
        (1, date(2026, 1, 4), date(2026, 1, 1)),
    ],
    ids=["arrived-march", "previous-march", "arrived-january"],
)
def test_latest_calendar_occurrence_is_the_actual_series_month_start(
    month: int, reference: date, expected: date
) -> None:
    try:
        actual = _latest_occurrence(month, reference)
    except ValueError as error:
        pytest.fail(f"Valid yearly series must resolve its first-day occurrence: {error}")
    assert actual == expected


async def _initial_restored_calendar_farm(
    client: httpx.AsyncClient, label: str, introduction: date = date(2026, 1, 1)
) -> tuple[int, int]:
    headers = await owner_with_farm(client, email=f"calendar-native-{label}@example.test")
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


async def test_calendar_dedupe_keeps_later_series_and_snapshots_the_actual_herd(
    client: httpx.AsyncClient,
) -> None:
    farm_id, animal_id = await _initial_restored_calendar_farm(client, "series")
    reference = date(2026, 9, 14)
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        retained = Task(
            farm_id=farm_id,
            title="FMD prior operator round March 2026",
            due_date=date(2026, 3, 20),
            category="VACCINE",
            status="PENDING",
            auto_generated=False,
            created_by_id=farm.owner_id,
        )
        db.add(retained)
        await db.commit()
        retained_id = retained.id
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        await lock_manual_task_queue(db, farm)
        try:
            created = await _ensure_calendar_rounds(db, farm_id, reference, date(2026, 1, 1))
        except ValueError as error:
            pytest.fail(
                f"Valid seasonal calendar must materialize its supported programmes: {error}"
            )
        assert created, "Later unscheduled calendar series must report the newly created duties"
        await db.commit()
    async with get_sessionmaker()() as db:
        tasks = list((await db.execute(select(Task).where(Task.farm_id == farm_id))).scalars())
        generated = [task for task in tasks if task.id != retained_id]
        assert {(task.title_key, task.due_date) for task in generated} == {
            ("fmd_vaccination_round", reference),
            ("et_hs_premonsoon_round", date(2026, 5, 31)),
            ("ccpp_round", date(2026, 1, 31)),
            ("deworming_round", date(2026, 1, 31)),
            ("deworming_round", date(2026, 6, 30)),
        }
        assert len(tasks) == 6 and all(task.status == "PENDING" for task in tasks)
        original = next(task for task in tasks if task.id == retained_id)
        assert original.title == "FMD prior operator round March 2026"
        for task in generated:
            round_ = await db.get(HealthRound, task.id)
            assert round_ is not None, (
                f"Generated herd duty {task.title_key} lost its declared round"
            )
            assert round_.required_components
            targets = set(
                (
                    await db.execute(
                        select(HealthRoundTarget.animal_id).where(
                            HealthRoundTarget.task_id == task.id
                        )
                    )
                ).scalars()
            )
            assert targets == {animal_id}


async def test_backfill_admits_a_round_whose_month_end_is_exactly_the_introduction_floor(
    client: httpx.AsyncClient,
) -> None:
    farm_id, animal_id = await _initial_restored_calendar_farm(
        client, "inclusive", date(2026, 3, 31)
    )
    reference = date(2026, 4, 2)
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        await lock_manual_task_queue(db, farm)
        assert await _ensure_calendar_rounds(db, farm_id, reference, date(2026, 3, 31))
        await db.commit()
    async with get_sessionmaker()() as db:
        tasks = list((await db.execute(select(Task).where(Task.farm_id == farm_id))).scalars())
        assert len(tasks) == 1
        assert (tasks[0].title_key, tasks[0].due_date) == (
            "fmd_vaccination_round",
            date(2026, 3, 31),
        )
        targets = set(
            (
                await db.execute(
                    select(HealthRoundTarget.animal_id).where(
                        HealthRoundTarget.task_id == tasks[0].id
                    )
                )
            ).scalars()
        )
        assert targets == {animal_id}
