"""A positive kid-sized acquisition weight remains a recorded entry fact."""

import httpx
import pytest

from .conftest import owner_with_farm


@pytest.mark.parametrize("entry_weight", [0.5, 1.0, 1.01])
async def test_registration_persists_positive_entry_weights_at_and_below_one_kg(
    client: httpx.AsyncClient, entry_weight: float
) -> None:
    owner = await owner_with_farm(client)
    response = await client.post(
        "/api/animals",
        headers=owner,
        json={
            "tag_number": "SMALL-POSITIVE-ENTRY",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "weight_kg": entry_weight,
        },
    )
    assert response.status_code == 201, response.text
    animal = response.json()
    assert animal["latest_weight_kg"] == entry_weight
    profile = await client.get(f"/api/animals/{animal['id']}", headers=owner)
    assert profile.status_code == 200, profile.text
    assert profile.json()["animal"]["latest_weight_kg"] == entry_weight
    assert profile.json()["weights_total"] == 1
    entry = profile.json()["weights"][0]
    assert entry["weight_kg"] == entry_weight
    assert entry["notes"] == "Entry weight"
