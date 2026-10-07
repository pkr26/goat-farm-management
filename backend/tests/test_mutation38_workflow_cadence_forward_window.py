"""Operator duties suppress cadence through day 30, with no earlier auto duty present."""

from datetime import date, timedelta

import httpx
import pytest

from .conftest import owner_with_farm
from .test_cadence import (
    farm_tasks,
    freeze_business_date,
    make_animal,
    run_ensure,
    seed_history_task,
)


@pytest.mark.parametrize("days_ahead", [30, 31])
async def test_interval_forward_window_ends_on_the_thirtieth_day(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, days_ahead: int
) -> None:
    owner = await owner_with_farm(client, email=f"cadence-forward-{days_ahead}@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    await make_animal(client, owner, "CADENCE-FORWARD")
    reference = date(2026, 9, 14)
    freeze_business_date(monkeypatch, reference)
    categories = ["DISINFECTION", "FAMACHA", "HOOF_TRIMMING", "SPRAYING", "WEIGHING"]
    for category in categories:
        await seed_history_task(
            farm_id,
            category=category,
            title=f"Operator scheduled {category}",
            due_date=reference + timedelta(days=days_ahead),
            status="PENDING",
        )

    # Seed every operator duty before the first sweep. A duty the sweep
    # already generated today would hide an over-wide forward window.
    await run_ensure(farm_id)
    tasks = [task for task in await farm_tasks(farm_id) if task.category in categories]
    generated = [task for task in tasks if task.due_date == reference]
    expected = [] if days_ahead == 30 else categories
    assert sorted(task.category for task in generated) == expected
    assert all(task.status == "PENDING" for task in generated)
    assert len(tasks) == (5 if days_ahead == 30 else 10)
    assert sorted(task.title for task in tasks if task.due_date != reference) == [
        f"Operator scheduled {category}" for category in categories
    ]
