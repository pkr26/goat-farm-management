"""The operational board preserves scoped headcounts, feed and cohort age."""

from datetime import date, timedelta
from typing import Any

import httpx

from app.utils import today

from .conftest import owner_with_farm


async def _purchase(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    arrival: date,
    supplier: str,
    count: int = 1,
) -> dict[str, Any]:
    created = await client.post(
        "/api/purchases/new",
        headers=headers,
        json={
            "date": arrival.isoformat(),
            "supplier": supplier,
            "count": count,
            "create_animals": True,
        },
    )
    assert created.status_code == 201, created.text
    detail = await client.get(f"/api/purchases/{created.json()['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    return dict(detail.json())


async def test_bucket_board_preserves_own_active_headcount_and_feed_overrides(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    other = await owner_with_farm(client, "other-board@farm.in", "Other board farm")
    round_date = today()
    own_batch = await _purchase(client, owner, round_date, "Own board supplier", count=2)
    await _purchase(client, other, round_date, "Other board supplier")
    own_ids = sorted(animal["id"] for animal in own_batch["animals"])
    assert len(own_ids) == 2
    retired = await client.post(
        f"/api/animals/{own_ids[1]}/status",
        headers=owner,
        json={"new_status": "DEAD", "date": round_date.isoformat()},
    )
    assert retired.status_code == 200, retired.text
    for headers, daily_kg in ((owner, 2.5), (other, 3.75)):
        setting = await client.post(
            "/api/feeding/settings",
            headers=headers,
            json={"bucket": "QUARANTINE", "daily_kg_per_head": daily_kg},
        )
        assert setting.status_code == 204, setting.text
    response = await client.get("/api/buckets", headers=owner)
    assert response.status_code == 200, response.text
    quarantine = next(row for row in response.json() if row["bucket"] == "QUARANTINE")
    assert quarantine["animals_total"] == 1
    assert len(quarantine["animals"]) == 1
    assert quarantine["animals"][0]["id"] == own_ids[0]
    assert quarantine["daily_kg_per_head"] == 2.5
    assert sum(row["animals_total"] for row in response.json()) == 1


async def test_bucket_board_uses_each_animals_own_business_effective_move_date(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    round_date = today()
    older = await _purchase(client, owner, round_date - timedelta(days=7), "Older supplier")
    recent = await _purchase(client, owner, round_date - timedelta(days=3), "Recent supplier")
    older_id = older["animals"][0]["id"]
    recent_id = recent["animals"][0]["id"]
    response = await client.get("/api/buckets", headers=owner)
    assert response.status_code == 200, response.text
    quarantine = next(row for row in response.json() if row["bucket"] == "QUARANTINE")
    assert quarantine["animals_total"] == 2
    animals = {animal["id"]: animal for animal in quarantine["animals"]}
    assert set(animals) == {older_id, recent_id}
    assert animals[older_id]["days_in_current_bucket"] == 7
    assert animals[recent_id]["days_in_current_bucket"] == 3


async def test_bucket_board_same_day_arrival_has_zero_completed_cohort_days(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    batch = await _purchase(client, owner, today(), "Same-day supplier")
    animal_id = batch["animals"][0]["id"]
    response = await client.get("/api/buckets", headers=owner)
    assert response.status_code == 200, response.text
    quarantine = next(row for row in response.json() if row["bucket"] == "QUARANTINE")
    assert len(quarantine["animals"]) == 1
    assert quarantine["animals"][0]["id"] == animal_id
    assert quarantine["animals"][0]["days_in_current_bucket"] == 0
