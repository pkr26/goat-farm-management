"""A normal purchased goat may register at the inclusive 150-kg credibility cap."""

import httpx
import pytest

from .conftest import owner_with_farm


@pytest.mark.parametrize(
    ("entry_weight", "expected_status"),
    [(149.99, 201), (150.0, 201), (150.01, 422)],
)
async def test_registration_accepts_the_literal_150_kg_adult_cap_and_rejects_above_it(
    client: httpx.AsyncClient, entry_weight: float, expected_status: int
) -> None:
    owner = await owner_with_farm(client)
    response = await client.post(
        "/api/animals",
        headers=owner,
        json={
            "tag_number": "ADULT-WEIGHT-CAP",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "weight_kg": entry_weight,
        },
    )
    assert response.status_code == expected_status, response.text
    register = await client.get("/api/animals", headers=owner)
    assert register.status_code == 200, register.text
    if expected_status == 201:
        animal = response.json()
        assert animal["latest_weight_kg"] == entry_weight
        assert register.json()["total"] == 1
        assert register.json()["animals"][0]["id"] == animal["id"]
        assert register.json()["animals"][0]["latest_weight_kg"] == entry_weight
    else:
        assert f"Weight {entry_weight:g} kg exceeds" in response.json()["detail"]
        assert "150 kg cap" in response.json()["detail"]
        assert register.json()["total"] == 0
