"""Every positive small arrival weighing becomes a persisted weight baseline."""

import httpx
import pytest

from app.utils import today

from .conftest import owner_with_farm


@pytest.mark.parametrize("method", ["average", "individual"])
@pytest.mark.parametrize("weight", [0.5, 1.0, 1.01])
async def test_small_positive_purchase_weights_preserve_each_arrival_baseline(
    client: httpx.AsyncClient, method: str, weight: float
) -> None:
    owner = await owner_with_farm(client)
    arrival = today().isoformat()
    weights = [weight, weight] if method == "average" else [weight, weight + 0.01]
    field = "avg_weight_kg" if method == "average" else "individual_weights_kg"
    response = await client.post(
        "/api/purchases/new",
        headers=owner,
        json={
            "date": arrival,
            "count": 2,
            "create_animals": True,
            field: weight if method == "average" else weights,
        },
    )
    assert response.status_code == 201, response.text
    detail = await client.get(f"/api/purchases/{response.json()['id']}", headers=owner)
    assert detail.status_code == 200, detail.text
    animals = detail.json()["animals"]
    assert len(animals) == 2
    assert [animal["latest_weight_kg"] for animal in animals] == weights
    for animal, expected_weight in zip(animals, weights, strict=True):
        profile = await client.get(f"/api/animals/{animal['id']}", headers=owner)
        assert profile.status_code == 200, profile.text
        assert profile.json()["weights_total"] == 1
        recorded = profile.json()["weights"][0]
        assert recorded["date"] == arrival
        assert recorded["weight_kg"] == expected_weight
        assert recorded["notes"] == (
            "Estimated from purchase batch average"
            if method == "average"
            else "Arrival weight (individual)"
        )
