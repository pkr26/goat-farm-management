"""Exact birth facts override estimates when checking initial-weight chronology."""

from datetime import timedelta

import httpx
import pytest

from app.utils import today

from .conftest import owner_with_farm


@pytest.mark.parametrize(
    ("known_offset", "estimate_offset", "weight_offset", "expected_status"),
    [
        (-100, -50, -100, 201),
        (-100, None, -100, 201),
        (None, -100, -100, 201),
        (-100, None, -101, 422),
        (None, -100, -101, 422),
    ],
)
async def test_initial_weight_chronology_uses_the_exact_birth_date_before_an_estimate(
    client: httpx.AsyncClient,
    known_offset: int | None,
    estimate_offset: int | None,
    weight_offset: int,
    expected_status: int,
) -> None:
    headers = await owner_with_farm(client)
    reference = today()
    weight_day = reference + timedelta(days=weight_offset)
    payload: dict[str, object] = {
        "tag_number": "BIRTH-FACT-PRECEDENCE",
        "source": "PURCHASED",
        "sex": "F",
        "current_bucket": "FOUNDATION",
        "historical_import_reason": "Paper herd register with a corrected exact birth date",
        "weight_kg": 10.0,
        "weight_date": weight_day.isoformat(),
    }
    if known_offset is not None:
        payload["date_of_birth"] = (reference + timedelta(days=known_offset)).isoformat()
    if estimate_offset is not None:
        payload["estimated_dob"] = (reference + timedelta(days=estimate_offset)).isoformat()
    response = await client.post("/api/animals", headers=headers, json=payload)
    assert response.status_code == expected_status, response.text
    if expected_status == 422:
        assert response.json()["detail"] == "weight_date cannot predate the recorded birth date"
        register = await client.get("/api/animals", headers=headers)
        assert register.status_code == 200, register.text
        assert register.json()["total"] == 0
        return
    profile = await client.get(f"/api/animals/{response.json()['id']}", headers=headers)
    assert profile.status_code == 200, profile.text
    assert [(row["date"], row["weight_kg"]) for row in profile.json()["weights"]] == [
        (weight_day.isoformat(), 10.0)
    ]
    assert profile.json()["animal"]["date_of_birth"] == payload.get("date_of_birth")
    assert profile.json()["animal"]["estimated_dob"] == payload.get("estimated_dob")
