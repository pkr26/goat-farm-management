"""Duty tab pages retain their published bounds, defaults and actual state cohorts."""

from datetime import timedelta

import httpx
import pytest

from app.utils import today

from .conftest import owner_with_farm
from .test_tasks_extended import make_duty


async def test_task_tabs_expose_the_published_default_page_metadata(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="task-default-tabs@farm.in")
    response = await client.get("/api/tasks", headers=owner)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["active_limit"] == body["completed_limit"] == 100
    for name in ["today", "overdue", "upcoming", "awaiting", "completed"]:
        assert body[name] == [] and body[name + "_total"] == 0 and body[name + "_offset"] == 0


@pytest.mark.parametrize("field", ["active_limit", "completed_limit"])
@pytest.mark.parametrize(
    ("value", "status"),
    [(0, 422), (1, 200), (200, 200), (201, 422)],
    ids=["zero", "min", "max", "above-max"],
)
async def test_task_tab_page_limit_keeps_its_public_inclusive_envelope(
    client: httpx.AsyncClient, field: str, value: int, status: int
) -> None:
    owner = await owner_with_farm(client, email="task-limit-envelope@farm.in")
    response = await client.get("/api/tasks", headers=owner, params={field: value})
    assert response.status_code == status, response.text
    if status == 200:
        assert response.json()[field] == value


@pytest.mark.parametrize(
    "field",
    ["today_offset", "overdue_offset", "upcoming_offset", "awaiting_offset", "completed_offset"],
)
@pytest.mark.parametrize(
    ("value", "status"),
    [(-1, 422), (0, 200), (10_000, 200), (10_001, 422)],
    ids=["negative", "first", "max", "above-max"],
)
async def test_task_tab_page_offsets_keep_the_shared_public_envelope(
    client: httpx.AsyncClient, field: str, value: int, status: int
) -> None:
    owner = await owner_with_farm(client, email="task-offset-envelope@farm.in")
    response = await client.get("/api/tasks", headers=owner, params={field: value})
    assert response.status_code == status, response.text
    if status == 200:
        assert response.json()[field] == value


async def test_task_tabs_preserve_actual_pending_review_and_completed_cohorts(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="task-real-cohorts@farm.in")
    current = await make_duty(client, owner, title="Today's work")
    overdue = await make_duty(
        client, owner, title="Yesterday's work", due=today() - timedelta(days=1)
    )
    upcoming = await make_duty(
        client, owner, title="Tomorrow's work", due=today() + timedelta(days=1)
    )
    review = await make_duty(
        client, owner, title="Cleaning that awaits review", category="CLEANING"
    )
    finished = await make_duty(client, owner, title="Completed owner inspection", category="OTHER")
    for duty in [review, finished]:
        completed = await client.post(f"/api/tasks/{duty['id']}/complete", headers=owner)
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "DONE"
    response = await client.get("/api/tasks", headers=owner)
    assert response.status_code == 200, response.text
    body = response.json()
    for tab, expected in [
        ("today", current["id"]),
        ("overdue", overdue["id"]),
        ("upcoming", upcoming["id"]),
        ("awaiting", review["id"]),
        ("completed", finished["id"]),
    ]:
        assert body[tab + "_total"] == 1
        assert [duty["id"] for duty in body[tab]] == [expected]
