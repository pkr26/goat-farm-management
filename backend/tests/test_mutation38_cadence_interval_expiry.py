"""Pending operator rounds suppress duplication until their inclusive expiry."""

from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Farm, Task
from app.services.cadence import _ensure_interval_rounds
from app.services.tasks import lock_manual_task_queue

from .conftest import owner_with_farm


@pytest.mark.parametrize(
    ("programme", "lookback"),
    [
        ("HOOF_TRIMMING", 182),
        ("SPRAYING", 182),
        ("DISINFECTION", 91),
        ("WEIGHING", 30),
        ("FAMACHA", 30),
    ],
)
async def test_interval_round_expires_only_after_its_last_inclusive_day(
    client: httpx.AsyncClient, programme: str, lookback: int
) -> None:
    headers = await owner_with_farm(client, email=f"cadence-expiry-{programme}@example.test")
    farm_id = int(headers["X-Farm-Id"])
    reference = date(2026, 9, 14)
    categories = ["HOOF_TRIMMING", "SPRAYING", "DISINFECTION", "WEIGHING", "FAMACHA"]
    async with get_sessionmaker()() as db:
        operator_rounds = [
            Task(
                farm_id=farm_id,
                title=f"Operator planned {category}",
                category=category,
                due_date=(reference - timedelta(days=lookback))
                if category == programme
                else reference,
                status="PENDING",
                auto_generated=False,
            )
            for category in categories
        ]
        db.add_all(operator_rounds)
        await db.commit()
        operator_ids = {task.id for task in operator_rounds}
    for day in (reference, reference + timedelta(days=1)):
        async with get_sessionmaker()() as db:
            farm = await db.get(Farm, farm_id)
            assert farm is not None
            await lock_manual_task_queue(db, farm)
            await _ensure_interval_rounds(db, farm_id, day)
            await db.commit()
        async with get_sessionmaker()() as observer:
            tasks = list(
                (await observer.execute(select(Task).where(Task.farm_id == farm_id))).scalars()
            )
            retained = {task.id for task in tasks if not task.auto_generated}
            assert retained == operator_ids
            generated = [task for task in tasks if task.auto_generated]
            if day == reference:
                assert not generated, "The last covered day must not duplicate a pending round"
            else:
                assert [(task.category, task.due_date, task.status) for task in generated] == [
                    (programme, day, "PENDING")
                ], "An expired programme must reappear without disturbing the other four rounds"
