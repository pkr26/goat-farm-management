"""Registering one managed purchase creates one correctly dated one-head batch."""

from datetime import timedelta

import httpx
import pytest

from app.utils import today

from .conftest import owner_with_farm


@pytest.mark.parametrize("purchase_days_ago", [None, 7])
async def test_single_animal_purchase_has_one_head_and_the_effective_acquisition_date(
    client: httpx.AsyncClient, purchase_days_ago: int | None
) -> None:
    owner = await owner_with_farm(client)
    acquisition = today()
    payload: dict[str, object] = {
        "tag_number": "ONE-HEAD-PURCHASE",
        "sex": "F",
        "source": "PURCHASED",
        "current_bucket": "FOUNDATION",
        "purchase_price": 3250.5,
        "seller_name": "One-head supplier",
    }
    if purchase_days_ago is not None:
        acquisition -= timedelta(days=purchase_days_ago)
        payload["purchase_date"] = acquisition.isoformat()
    created = await client.post("/api/animals", json=payload, headers=owner)
    assert created.status_code == 201, created.text
    animal = created.json()
    assert animal["purchase_date"] == acquisition.isoformat()
    assert animal["current_bucket"] == "QUARANTINE"
    purchases = await client.get("/api/purchases", headers=owner)
    assert purchases.status_code == 200, purchases.text
    assert purchases.json()["total"] == 1
    listed = purchases.json()["batches"][0]
    assert listed["count"] == 1
    assert listed["animals_created"] == 1
    detail = await client.get(f"/api/purchases/{listed['id']}", headers=owner)
    assert detail.status_code == 200, detail.text
    batch = detail.json()
    assert batch["batch"]["count"] == 1
    assert batch["batch"]["date"] == acquisition.isoformat()
    assert batch["batch"]["total_price"] == 3250.5
    assert batch["animals_total"] == 1
    assert [member["id"] for member in batch["animals"]] == [animal["id"]]
    assert len(batch["tasks"]) == 11
