"""Public task deep links preserve the database identifier band and missing response."""

import httpx
import pytest

from app.db import get_sessionmaker
from app.models import Task
from app.utils import today

from .conftest import owner_with_farm

MAX_DATABASE_KEY = 2_147_483_647


@pytest.mark.parametrize("key", [0, MAX_DATABASE_KEY], ids=["retained-zero", "largest-int4"])
async def test_actual_retained_task_deep_link_preserves_the_public_identifier_band(
    client: httpx.AsyncClient, key: int
) -> None:
    owner = await owner_with_farm(client, email="task-deep-link-key@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    title = "Actual retained inspection duty"
    async with get_sessionmaker()() as db:
        db.add(
            Task(
                id=key,
                farm_id=farm_id,
                title=title,
                due_date=today(),
                category="OTHER",
                status="PENDING",
                auto_generated=False,
            )
        )
        await db.commit()

    response = await client.get(f"/api/tasks/{key}", headers=owner)
    assert response.status_code == (404 if key == 0 else 200), response.text
    if key == 0:
        assert response.json()["detail"] == "Task not found"
    else:
        payload = response.json()
        assert payload["id"] == key and payload["title"] == title
        assert payload["status"] == "PENDING" and payload["completed_at"] is None
    async with get_sessionmaker()() as db:
        stored = await db.get(Task, key)
        assert stored is not None and stored.farm_id == farm_id and stored.title == title
        assert stored.status == "PENDING" and stored.completed_at is None


async def test_out_of_int4_task_deep_link_uses_the_same_missing_response(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="task-deep-link-overflow@farm.in")
    response = await client.get(f"/api/tasks/{MAX_DATABASE_KEY + 1}", headers=owner)
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Task not found"
