"""A buck's prior duty suppresses rotation through the full 365-day window."""

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
from .type_helpers import json_int


@pytest.mark.parametrize("history_status", ["PENDING", "DONE", "SKIPPED"])
async def test_buck_rotation_history_includes_the_365th_day_in_every_status(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, history_status: str
) -> None:
    owner = await owner_with_farm(client, email=f"buck-window-{history_status.lower()}@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    reference = date(2026, 9, 14)
    freeze_business_date(monkeypatch, reference)
    ids: dict[int, int] = {}
    for days_ago in (364, 365, 366):
        animal = await make_animal(
            client,
            owner,
            f"BUCK-WINDOW-{days_ago}",
            sex="M",
            date_of_birth="2020-01-01",
        )
        animal_id = json_int(animal["id"])
        ids[days_ago] = animal_id
        await seed_history_task(
            farm_id,
            category="BUCK_ROTATION",
            title=f"Prior rotation for BUCK-WINDOW-{days_ago}",
            due_date=reference - timedelta(days=days_ago),
            status=history_status,
            animal_id=animal_id,
        )

    await run_ensure(farm_id)
    rotations = await farm_tasks(farm_id, "BUCK_ROTATION")
    generated = [task for task in rotations if task.due_date == reference]
    assert [(task.animal_id, task.status, task.title_key) for task in generated] == [
        (ids[366], "PENDING", "buck_rotation")
    ]
    counts = {
        animal_id: sum(task.animal_id == animal_id for task in rotations)
        for animal_id in ids.values()
    }
    assert counts == {
        ids[364]: 1,
        ids[365]: 1,
        ids[366]: 2,
    }

    # Reloading the same business date never duplicates the fresh duty.
    await run_ensure(farm_id)
    assert len(await farm_tasks(farm_id, "BUCK_ROTATION")) == 4

    # The oldest included fact becomes 366 days old tomorrow. The fresh
    # duty made today remains inside the window, in every history status.
    following_day = date(2026, 9, 15)
    freeze_business_date(monkeypatch, following_day)
    await run_ensure(farm_id)
    rotations = await farm_tasks(farm_id, "BUCK_ROTATION")
    assert [task.animal_id for task in rotations if task.due_date == following_day] == [ids[365]]
    assert len(rotations) == 5
