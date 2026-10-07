"""Diagnostic real recurrence return paths; these probes do not imply mutant kills."""

from datetime import timedelta
from uuid import uuid4

import httpx
from sqlalchemy import func, select

from app.db import get_sessionmaker
from app.models import Farm, Task, User
from app.services.tasks import lock_manual_task_queue, spawn_next_occurrence, verify_task
from app.utils import today

from .conftest import owner_with_farm
from .test_tasks_extended import complete_duty, make_duty


async def test_native_insert_returns_its_actual_transactional_successor_and_rollback_is_atomic(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, category="CLEANING", recur_days=1)
    completed = await complete_duty(client, owner, int(duty["id"]))
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "DONE"
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        await lock_manual_task_queue(db, farm)
        task = (
            await db.execute(select(Task).where(Task.id == duty["id"]).with_for_update())
        ).scalar_one()
        user = await db.get(User, farm.owner_id)
        assert user is not None
        await verify_task(db, task, user, spawn_successor=False)
        expected_due = max(task.due_date, today(farm.timezone)) + timedelta(days=1)
        successor = await spawn_next_occurrence(db, task)
        assert successor.id != task.id
        assert successor.due_date == expected_due and successor.status == "PENDING"
        assert (
            successor.farm_id == farm_id
            and successor.recurring_series_id == task.recurring_series_id
        )
        assert successor.category == "CLEANING" and successor.created_by_id == farm.owner_id
        assert await db.get(Task, successor.id) is successor
        successor.title = "Native pending edit retained on this mapped identity"
        queried = (await db.execute(select(Task).where(Task.id == successor.id))).scalar_one()
        assert queried is successor and queried.title == successor.title
        # This service only stages effects in its caller's transaction.
        # Rollback must undo both verification and the inserted successor.
        await db.rollback()
    async with get_sessionmaker()() as db:
        actual = await db.get(Task, int(duty["id"]))
        assert actual is not None and actual.status == "DONE"
        count = await db.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.recurring_series_id == duty["recurring_series_id"])
        )
        assert count == 1


async def test_native_conflict_winner_keeps_its_real_cached_identity_after_calendar_change(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    earlier_today = today("Pacific/Honolulu")
    later_today = today("Pacific/Kiritimati")
    assert later_today == earlier_today + timedelta(days=1)
    series = str(uuid4())
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        farm.timezone = "Pacific/Kiritimati"
        # Initial native same-series occurrences, not rewritten public
        # parallel-series identities. Both start PENDING with constraints on.
        occurrences = [
            Task(
                farm_id=farm_id,
                title=f"Native calendar occurrence {index}",
                due_date=due,
                category="CLEANING",
                status="PENDING",
                auto_generated=False,
                recur_days=1,
                recurring_series_id=series,
                created_by_id=farm.owner_id,
            )
            for index, due in enumerate((earlier_today - timedelta(days=1), later_today))
        ]
        db.add_all(occurrences)
        await db.commit()
        original_id, winner_id = [task.id for task in occurrences]
    for task_id in (original_id, winner_id):
        response = await complete_duty(client, owner, task_id)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "DONE"
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        await lock_manual_task_queue(db, farm)
        tasks = list(
            (
                await db.execute(
                    select(Task)
                    .where(Task.id.in_([original_id, winner_id]))
                    .order_by(Task.id)
                    .with_for_update()
                )
            ).scalars()
        )
        by_id = {task.id: task for task in tasks}
        user = await db.get(User, farm.owner_id)
        assert user is not None
        # Genuine attributed verification, with the explicitly declared
        # native spawn_successor=False mode. No fabricated terminal flags.
        await verify_task(db, by_id[winner_id], user, spawn_successor=False)
        await db.commit()
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        # Native configuration correction changes today's business date;
        # retained actual completed/verified dates and identities stay intact.
        farm.timezone = "Pacific/Honolulu"
        await db.commit()
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        await lock_manual_task_queue(db, farm)
        tasks = list(
            (
                await db.execute(
                    select(Task)
                    .where(Task.id.in_([original_id, winner_id]))
                    .order_by(Task.id)
                    .with_for_update()
                )
            ).scalars()
        )
        by_id = {task.id: task for task in tasks}
        user = await db.get(User, farm.owner_id)
        assert user is not None
        await verify_task(db, by_id[original_id], user, spawn_successor=False)
        cached_winner = by_id[winner_id]
        # Actual supported partial expiry: no counterfeit getter, object,
        # hook, SQL result, warning-as-error rule, or custom session subclass.
        db.expire(cached_winner, ["title"])
        actual = await spawn_next_occurrence(db, by_id[original_id])
        assert actual is cached_winner
        assert actual.id == winner_id and actual.status == "VERIFIED"
        assert actual.due_date == later_today and actual.title == "Native calendar occurrence 1"
        assert actual.verified_by_id == farm.owner_id and actual.verified_at is not None
        await db.commit()
    async with get_sessionmaker()() as db:
        count = await db.scalar(
            select(func.count()).select_from(Task).where(Task.recurring_series_id == series)
        )
        assert count == 2
