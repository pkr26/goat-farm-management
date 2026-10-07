"""Procurement retains complete page windows and credible arrival weights."""

from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import PurchaseBatch, WeightRecord
from app.utils import today

from .conftest import owner_with_farm


async def test_purchase_list_default_window_contains_the_first_100_of_102_batches(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    # Imported batch summaries can legitimately have no linked animals/tasks.
    # Use ordinary generated positive PKs with all current constraints active.
    async with get_sessionmaker()() as db:
        batches = [
            PurchaseBatch(
                farm_id=int(owner["X-Farm-Id"]),
                date=today(),
                supplier=f"Completed paper batch {index:03d}",
                count=1,
                sex="F",
            )
            for index in range(102)
        ]
        db.add_all(batches)
        await db.commit()
        expected_ids = sorted((batch.id for batch in batches), reverse=True)
    response = await client.get("/api/purchases", headers=owner)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 102
    assert body["limit"] == 100
    assert body["offset"] == 0
    assert [batch["id"] for batch in body["batches"]] == expected_ids[:100]
    second = await client.get("/api/purchases", headers=owner, params={"offset": 100})
    assert second.status_code == 200, second.text
    assert [batch["id"] for batch in second.json()["batches"]] == expected_ids[100:]


@pytest.mark.parametrize("query", ["2147483647", "#2147483647"])
async def test_purchase_search_accepts_a_restored_batch_at_the_int4_id_ceiling(
    client: httpx.AsyncClient, query: str
) -> None:
    owner = await owner_with_farm(client)
    async with get_sessionmaker()() as db:
        db.add(
            PurchaseBatch(
                id=2147483647,
                farm_id=int(owner["X-Farm-Id"]),
                date=today(),
                supplier="Restored upper-key supplier",
                count=1,
                sex="F",
            )
        )
        await db.commit()
    response = await client.get("/api/purchases", headers=owner, params={"q": query})
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    assert [batch["id"] for batch in response.json()["batches"]] == [2147483647]


@pytest.mark.parametrize(
    ("payload_weights", "expected_weights"),
    [
        pytest.param({"avg_weight_kg": 150.0}, [150.0], id="inclusive-average-cap"),
        pytest.param(
            {"individual_weights_kg": [0.5, 150.0]},
            [0.5, 150.0],
            id="positive-small-and-inclusive-individual-cap",
        ),
    ],
)
async def test_purchase_accepts_and_persists_positive_arrival_weight_boundaries(
    client: httpx.AsyncClient,
    payload_weights: dict[str, Any],
    expected_weights: list[float],
) -> None:
    owner = await owner_with_farm(client)
    payload = {
        "date": today().isoformat(),
        "supplier": "Arrival boundary supplier",
        "count": len(expected_weights),
        "create_animals": True,
        **payload_weights,
    }
    response = await client.post("/api/purchases/new", headers=owner, json=payload)
    assert response.status_code == 201, response.text
    detail = await client.get(f"/api/purchases/{response.json()['id']}", headers=owner)
    assert detail.status_code == 200, detail.text
    assert detail.json()["animals_total"] == len(expected_weights)
    animal_ids = [animal["id"] for animal in detail.json()["animals"]]
    async with get_sessionmaker()() as db:
        records = list(
            (
                await db.execute(
                    select(WeightRecord)
                    .where(WeightRecord.animal_id.in_(animal_ids))
                    .order_by(WeightRecord.animal_id)
                )
            ).scalars()
        )
        assert [record.weight_kg for record in records] == expected_weights
        assert {record.date for record in records} == {today()}
    for animal_id, expected_weight in zip(animal_ids, expected_weights, strict=True):
        profile = await client.get(f"/api/animals/{animal_id}", headers=owner)
        assert profile.status_code == 200, profile.text
        assert profile.json()["animal"]["latest_weight_kg"] == expected_weight
