"""Real recurring duty transitions serialize on their own queue before row mutations."""

import asyncio
from datetime import date

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text

from app.db import get_sessionmaker
from app.models import Farm, Task
from app.schemas.tasks import TaskCreateIn
from app.services.tasks import lock_manual_task_queue
from app.utils import today

from .conftest import owner_with_farm

MAX_DATABASE_KEY = 2_147_483_647


@pytest.mark.parametrize("days", [None, 1, 3650], ids=["one-off", "daily", "ten-year-cap"])
def test_supported_manual_recurrence_inputs_validate_without_calendar_exceptions(
    days: int | None,
) -> None:
    payload = {
        "title": "Actual recurring inspection",
        "due_date": date(2026, 1, 1),
        "recur_days": days,
    }
    try:
        result = TaskCreateIn.model_validate(payload)
    except (ValidationError, TypeError, OverflowError) as exc:
        # These are specific local validation/date-arithmetic failures on
        # independently supported pure inputs, with no database operation.
        pytest.fail(f"A supported duty recurrence failed its calendar adapter: {exc}")
    assert result.recur_days == days and result.due_date == date(2026, 1, 1)


@pytest.mark.parametrize("key", [0, 1, MAX_DATABASE_KEY], ids=["retained-zero", "first", "last"])
async def test_actual_recurring_task_identifier_gates_preserve_queue_acquisition_order(
    client: httpx.AsyncClient, key: int
) -> None:
    owner = await owner_with_farm(client, email="recurring-queue-band@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    series = f"retained-inspection-{key}"
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        if key == 1:
            # Allocate the first real key before restoring its occurrence so
            # an actual successor uses the next free identity rather than
            # colliding with an explicit restored primary key.
            allocated = await db.scalar(
                text("SELECT nextval(pg_get_serial_sequence('tasks', 'id'))")
            )
            assert allocated == key
        db.add(
            Task(
                id=key,
                farm_id=farm_id,
                title="Actual retained recurring inspection",
                due_date=today(farm.timezone),
                category="OTHER",
                status="PENDING",
                auto_generated=False,
                recur_days=7,
                recurring_series_id=series,
                created_by_id=farm.owner_id,
            )
        )
        await db.commit()
    async with get_sessionmaker()() as pin:
        farm = await pin.get(Farm, farm_id)
        assert farm is not None
        await lock_manual_task_queue(pin, farm)
        holder_pid = await pin.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(holder_pid, int)
        completion = asyncio.create_task(client.post(f"/api/tasks/{key}/complete", headers=owner))
        blocked = False
        deadline = asyncio.get_running_loop().time() + 15
        try:
            async with get_sessionmaker()() as observer:
                while not completion.done():
                    blocked = bool(
                        await observer.scalar(
                            text(
                                "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                                "WHERE datname=current_database() AND wait_event_type='Lock' "
                                "AND :holder=ANY(pg_blocking_pids(pid)))"
                            ),
                            {"holder": holder_pid},
                        )
                    )
                    if blocked:
                        break
                    if asyncio.get_running_loop().time() >= deadline:
                        raise TimeoutError("Neither recurring completion nor its PG wait appeared")
                    await asyncio.sleep(0.01)
        finally:
            await pin.rollback()
            response = await completion
        assert blocked == (key != 0), (
            "Valid recurring identifiers must serialize on the manual queue before Task writes; "
            "an inaccessible zero-key target must use its missing response without joining it"
        )
        assert response.status_code == (404 if key == 0 else 200), response.text
        if key == 0:
            assert response.json()["detail"] == "Task not found"
        else:
            assert response.json()["status"] == "DONE"
    async with get_sessionmaker()() as db:
        stored = await db.get(Task, key)
        assert stored is not None
        if key == 0:
            assert stored.status == "PENDING" and stored.completed_at is None
            assert (
                await db.scalar(
                    select(func.count(Task.id)).where(
                        Task.farm_id == farm_id, Task.recurring_series_id == series
                    )
                )
                == 1
            )
        else:
            assert stored.status == "DONE" and stored.completed_by_id is not None
            pending = list(
                (
                    await db.execute(
                        select(Task).where(
                            Task.farm_id == farm_id,
                            Task.recurring_series_id == series,
                            Task.status == "PENDING",
                        )
                    )
                ).scalars()
            )
            assert len(pending) == 1 and pending[0].id != key
            assert pending[0].recur_days == 7 and pending[0].title == stored.title
