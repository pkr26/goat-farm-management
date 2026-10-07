"""A purchase exposes the literal day-by-day quarantine duties to the keeper."""

from datetime import timedelta

import httpx

from app.utils import today

from .conftest import owner_with_farm


async def test_purchase_quarantine_calendar_has_all_literal_due_dates_and_day_labels(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    arrival = today() - timedelta(days=7)
    created = await client.post(
        "/api/purchases/new",
        headers=owner,
        json={
            "date": arrival.isoformat(),
            "supplier": "Calendar supplier",
            "count": 1,
            "create_animals": True,
        },
    )
    assert created.status_code == 201, created.text
    batch_id = created.json()["id"]
    detail = await client.get(f"/api/purchases/{batch_id}", headers=owner)
    assert detail.status_code == 200, detail.text
    tasks = detail.json()["tasks"]
    assert len(tasks) == 11
    expected = {
        "quarantine_arrival_inspection": (1, "QUARANTINE"),
        "quarantine_rest": (1, "QUARANTINE"),
        "quarantine_deworm": (4, "DEWORMING"),
        "quarantine_liver_tonic": (5, "QUARANTINE"),
        "quarantine_ppr_vaccine": (10, "VACCINE"),
        "quarantine_fecal_exam": (13, "QUARANTINE"),
        "quarantine_et_tetanus_vaccine": (20, "VACCINE"),
        "quarantine_goat_pox_vaccine": (30, "VACCINE"),
        "quarantine_prerelease_review": (30, "QUARANTINE"),
        "quarantine_fmd_vaccine": (40, "VACCINE"),
        "quarantine_release": (45, "BUCKET_MOVE"),
    }
    assert {task["title_key"] for task in tasks} == set(expected)
    for task in tasks:
        day, category = expected[task["title_key"]]
        # Day1 is the arrival date; later labels are one-based protocol days.
        due = (arrival + timedelta(days=day - 1)).isoformat()
        assert task["due_date"] == due, task
        assert task["category"] == category, task
        assert task["title_args"]["day"] == day, task
        assert task["title_args"]["due_date"] == due, task
        assert task["title_args"]["batch_id"] == batch_id, task
        assert task["status"] == "PENDING", task
