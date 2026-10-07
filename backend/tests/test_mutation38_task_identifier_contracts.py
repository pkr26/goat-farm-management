"""Real retained duties preserve the native and public task identifier band."""

import httpx
import pytest
from fastapi import HTTPException

from app.api.tasks import _get_task, _lock_completion_animals
from app.db import get_sessionmaker
from app.models import Farm, Task
from app.utils import today

from .conftest import owner_with_farm
from .test_tasks_extended import make_animal

MAX_DATABASE_KEY = 2_147_483_647


@pytest.mark.parametrize("key", [0, MAX_DATABASE_KEY], ids=["retained-zero", "largest-int4"])
async def test_actual_task_lookup_and_completion_preserve_the_declared_identifier_band(
    client: httpx.AsyncClient, key: int
) -> None:
    owner = await owner_with_farm(client, email="task-retained-key@farm.in")
    animal_id = await make_animal(client, owner)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        db.add(
            Task(
                id=key,
                farm_id=farm_id,
                animal_id=animal_id,
                title="Actual retained one-off animal duty",
                due_date=today(),
                category="OTHER",
                status="PENDING",
                auto_generated=False,
            )
        )
        await db.commit()
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        animals = await _lock_completion_animals(db, farm, key)
        if key == 0:
            assert animals == [], (
                "The native identifier gate must not lock a zero-key retained duty's animal"
            )
            with pytest.raises(HTTPException) as rejected:
                await _get_task(db, farm, key)
            assert rejected.value.status_code == 404 and rejected.value.detail == "Task not found"
        else:
            assert [row.id for row in animals] == [animal_id]
            try:
                task = await _get_task(db, farm, key, for_update=True)
            except HTTPException as exc:
                pytest.fail(f"The largest actual int4 duty must remain reachable: {exc}")
            assert task.id == key and task.title == "Actual retained one-off animal duty"
        await db.rollback()
    completed = await client.post(f"/api/tasks/{key}/complete", headers=owner)
    assert completed.status_code == (404 if key == 0 else 200), completed.text
    async with get_sessionmaker()() as db:
        stored = await db.get(Task, key)
        assert stored is not None
        if key == 0:
            assert stored.status == "PENDING" and stored.completed_at is None
        else:
            assert stored.status == "DONE" and stored.completed_at is not None
            assert stored.completed_by_id is not None
            assert completed.json()["id"] == key and completed.json()["animal_id"] == animal_id
