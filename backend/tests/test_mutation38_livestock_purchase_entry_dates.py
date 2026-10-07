"""Managed arrivals may record a weight on their effective acquisition day."""

from datetime import timedelta

import httpx
import pytest

from app.utils import today

from .conftest import owner_with_farm


@pytest.mark.parametrize(
    ("purchase_offset", "weight_offset", "expected_status"),
    [(None, 0, 201), (-1, -1, 201), (None, -1, 422)],
)
async def test_managed_purchase_weight_respects_explicit_or_default_acquisition_day(
    client: httpx.AsyncClient,
    purchase_offset: int | None,
    weight_offset: int,
    expected_status: int,
) -> None:
    headers = await owner_with_farm(client)
    arrival = today()
    weight_day = arrival + timedelta(days=weight_offset)
    payload: dict[str, object] = {
        "tag_number": "MANAGED-ENTRY-DATE",
        "source": "PURCHASED",
        "sex": "F",
        "current_bucket": "FOUNDATION",
        "weight_kg": 24.25,
        "weight_date": weight_day.isoformat(),
    }
    if purchase_offset is not None:
        arrival += timedelta(days=purchase_offset)
        payload["purchase_date"] = arrival.isoformat()
    response = await client.post("/api/animals", headers=headers, json=payload)
    assert response.status_code == expected_status, response.text
    if expected_status == 422:
        assert response.json()["detail"] == "weight_date cannot predate purchase_date"
        register = await client.get("/api/animals", headers=headers)
        assert register.status_code == 200, register.text
        assert register.json()["total"] == 0
        return
    animal = response.json()
    assert animal["purchase_date"] == arrival.isoformat()
    assert animal["current_bucket"] == "QUARANTINE"
    assert animal["latest_weight_kg"] == 24.25
    profile = await client.get(f"/api/animals/{animal['id']}", headers=headers)
    assert profile.status_code == 200, profile.text
    assert [(row["date"], row["weight_kg"]) for row in profile.json()["weights"]] == [
        (weight_day.isoformat(), 24.25)
    ]
    purchases = await client.get("/api/purchases", headers=headers)
    assert purchases.status_code == 200, purchases.text
    assert purchases.json()["total"] == 1
    batch_id = purchases.json()["batches"][0]["id"]
    batch = await client.get(f"/api/purchases/{batch_id}", headers=headers)
    assert batch.status_code == 200, batch.text
    assert batch.json()["batch"]["date"] == arrival.isoformat()
    assert len(batch.json()["tasks"]) == 11
