"""Native recurrence preserves retained series, calendar, and assignment facts."""

from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Self
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, MultipleResultsFound

import app.utils as utils
from app.db import get_sessionmaker
from app.models import Farm, Task, User
from app.models.constants import MAX_RECUR_DAYS
from app.services.tasks import (
    find_live_recurring_successor,
    lock_manual_task_queue,
    spawn_next_occurrence,
    verify_task,
)
from app.utils import today, utcnow

from .conftest import owner_with_farm
from .test_tasks_extended import complete_duty, make_duty, role_id, worker_headers


async def test_retained_multiple_pending_occurrences_reuse_the_earliest_real_successor(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="retained-series-reader@example.test")
    farm_id, series = int(headers["X-Farm-Id"]), str(uuid4())
    reference = today()
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        await lock_manual_task_queue(db, farm)
        # Distinct due dates satisfy the enabled per-series/date uniqueness
        # constraint. These initially retained rows represent several pending
        # historical occurrences, not duplicates of one contemporary action.
        earlier, later = [
            Task(
                farm_id=farm_id,
                title="Retained pending occurrence",
                due_date=reference - timedelta(days=offset),
                status="PENDING",
                category="OTHER",
                auto_generated=False,
                recur_days=1,
                recurring_series_id=series,
            )
            for offset in (2, 1)
        ]
        completed = Task(
            farm_id=farm_id,
            title="Retained terminal occurrence",
            due_date=reference - timedelta(days=3),
            status="DONE",
            completed_at=utcnow(),
            category="OTHER",
            auto_generated=False,
            recur_days=1,
            recurring_series_id=series,
        )
        db.add_all([earlier, later, completed])
        await db.commit()
        ids = earlier.id, later.id, completed.id
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        await lock_manual_task_queue(db, farm)
        task = (
            await db.execute(select(Task).where(Task.id == ids[2]).with_for_update())
        ).scalar_one()
        try:
            found = await find_live_recurring_successor(db, task)
            reused = await spawn_next_occurrence(db, task)
        except MultipleResultsFound as error:
            pytest.fail(
                f"A valid retained series must return its earliest pending occurrence: {error}"
            )
        assert found is not None and reused is found and found.id == ids[0]
        assert found.due_date == reference - timedelta(days=2)
        await db.commit()
    async with get_sessionmaker()() as db:
        rows = list(
            (await db.execute(select(Task).where(Task.recurring_series_id == series))).scalars()
        )
        assert {row.id for row in rows} == set(ids)
        assert sum(row.status == "PENDING" for row in rows) == 2


async def test_native_successor_uses_the_actual_farm_midnight_and_maximum_interval(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = await owner_with_farm(client, email="native-series-calendar@example.test")
    farm_id = int(headers["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        farm.timezone = "Pacific/Honolulu"
        await db.commit()
    instant = datetime(2026, 10, 5, 6, 30, tzinfo=UTC)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            assert tz is not None
            return cls.fromtimestamp(instant.timestamp(), tz)

    # Pin the external clock only. Genuine today and IANA conversion execute;
    # authentication and all persisted setup precede this clock boundary.
    monkeypatch.setattr(utils, "datetime", FixedDatetime)
    reference = today("Pacific/Honolulu")
    assert reference == date(2026, 10, 4) and today() == date(2026, 10, 5)
    for interval in (1, MAX_RECUR_DAYS):
        series = str(uuid4())
        async with get_sessionmaker()() as db:
            farm = await db.get(Farm, farm_id)
            assert farm is not None
            await lock_manual_task_queue(db, farm)
            original = Task(
                farm_id=farm_id,
                title="Native completed calendar duty",
                due_date=reference - timedelta(days=2),
                status="DONE",
                category="OTHER",
                auto_generated=False,
                completed_at=utcnow(),
                recur_days=interval,
                recurring_series_id=series,
                created_by_id=farm.owner_id,
            )
            db.add(original)
            await db.flush()
            try:
                successor = await spawn_next_occurrence(db, original)
            except (ValueError, OverflowError) as error:
                pytest.fail(f"A valid native recurrence must produce its next date: {error}")
            assert successor.id != original.id and successor.status == "PENDING"
            assert successor.due_date == reference + timedelta(days=interval)
            assert successor.recur_days == interval and successor.recurring_series_id == series
            assert successor.created_by_id == farm.owner_id
            await db.commit()
        async with get_sessionmaker()() as db:
            pending = (
                await db.execute(
                    select(Task).where(Task.recurring_series_id == series, Task.status == "PENDING")
                )
            ).scalar_one()
            assert pending.due_date == reference + timedelta(days=interval)


@pytest.mark.parametrize(
    "due",
    [date.max - timedelta(days=1), date.max],
    ids=["last-valid-next-date", "unrepresentable-next-date"],
)
async def test_native_retained_date_boundary_has_a_representable_successor_or_domain_error(
    client: httpx.AsyncClient,
    due: date,
) -> None:
    headers = await owner_with_farm(client, email="native-series-date-range@example.test")
    farm_id, series = int(headers["X-Farm-Id"]), str(uuid4())
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        await lock_manual_task_queue(db, farm)
        # The native helper explicitly guards the whole persisted date range,
        # including initially retained metadata beyond the public year band.
        # This plans a successor; it neither moves an animal nor changes a
        # contemporary clinical event or the business clock.
        retained = Task(
            farm_id=farm_id,
            title="Retained native date-range duty",
            due_date=due,
            status="DONE",
            completed_at=utcnow(),
            category="OTHER",
            auto_generated=False,
            recur_days=1,
            recurring_series_id=series,
        )
        db.add(retained)
        await db.flush()
        retained_id = retained.id
        if due == date.max:
            try:
                await spawn_next_occurrence(db, retained)
            except ValueError as error:
                assert str(error) == "Recurring duty cannot schedule a representable next date"
            except OverflowError as error:
                pytest.fail(
                    f"An unrepresentable native recurrence must produce its domain error: {error}"
                )
            else:
                pytest.fail("An unrepresentable native recurrence must be rejected")
            assert list(
                (
                    await db.execute(select(Task.id).where(Task.recurring_series_id == series))
                ).scalars()
            ) == [retained_id]
        else:
            try:
                successor = await spawn_next_occurrence(db, retained)
            except (ValueError, OverflowError) as error:
                pytest.fail(
                    f"The last representable native successor must remain admissible: {error}"
                )
            assert successor.id != retained_id and successor.due_date == date.max
            assert successor.status == "PENDING" and successor.recurring_series_id == series
        await db.commit()


async def test_native_review_repairs_retained_personal_role_before_pending_successor_insert(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="native-series-personal-owner@example.test")
    worker, worker_id = await worker_headers(
        client, headers, "CLEANER", "native-series-personal-worker@example.test"
    )
    expected_role = await role_id(client, headers, "CLEANER")
    duty = await make_duty(
        client,
        headers,
        "Retained native recurring personal duty",
        category="CLEANING",
        recur_days=1,
        assigned_user_id=worker_id,
    )
    completed = await complete_duty(client, worker, duty["id"])
    assert completed.status_code == 200, completed.text
    async with get_sessionmaker()() as db:
        original = await db.get(Task, duty["id"])
        assert original is not None and original.status == "DONE"
        assert original.completed_at is not None and original.completed_by_id == worker_id
        completed_at, series = original.completed_at, original.recurring_series_id
        await db.execute(update(Task).where(Task.id == original.id).values(assigned_role_id=None))
        await db.commit()
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(headers["X-Farm-Id"]))
        assert farm is not None
        owner = await db.get(User, farm.owner_id)
        assert owner is not None
        await lock_manual_task_queue(db, farm)
        original = (
            await db.execute(select(Task).where(Task.id == duty["id"]).with_for_update())
        ).scalar_one()
        assert original.assigned_role_id is None and original.assigned_user_id == worker_id
        try:
            reviewed = await verify_task(db, original, owner)
            await db.commit()
        except IntegrityError as error:
            pytest.fail(
                f"Native review must repair the retained role before its successor: {error}"
            )
        assert reviewed is original and original.status == "VERIFIED"
    async with get_sessionmaker()() as db:
        rows = list(
            (await db.execute(select(Task).where(Task.recurring_series_id == series))).scalars()
        )
        assert len(rows) == 2
        reviewed = next(row for row in rows if row.id == duty["id"])
        (pending,) = [row for row in rows if row.status == "PENDING"]
        assert reviewed.status == "VERIFIED" and reviewed.verified_by_id == owner.id
        assert reviewed.completed_at == completed_at and reviewed.completed_by_id == worker_id
        assert reviewed.assigned_role_id == pending.assigned_role_id == expected_role
        assert pending.assigned_user_id == worker_id and pending.recur_days == 1
        assert pending.due_date > reviewed.due_date and pending.category == "CLEANING"
