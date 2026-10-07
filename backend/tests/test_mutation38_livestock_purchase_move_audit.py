"""The initial quarantine move identifies its real procurement batch."""

from datetime import timedelta

import httpx

from app.utils import today

from .conftest import owner_with_farm


async def test_managed_arrival_history_names_the_batch_that_created_the_animal(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    acquisition = today() - timedelta(days=7)
    created = await client.post(
        "/api/animals",
        headers=owner,
        json={
            "tag_number": "BATCH-AUDIT-ARRIVAL",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "purchase_date": acquisition.isoformat(),
            "purchase_price": 3250.5,
            "seller_name": "Audited one-head supplier",
        },
    )
    assert created.status_code == 201, created.text
    purchases = await client.get("/api/purchases", headers=owner)
    assert purchases.status_code == 200, purchases.text
    assert purchases.json()["total"] == 1
    batch_id = purchases.json()["batches"][0]["id"]
    profile = await client.get(f"/api/animals/{created.json()['id']}", headers=owner)
    assert profile.status_code == 200, profile.text
    assert profile.json()["moves_total"] == 1
    move = profile.json()["moves"][0]
    assert move["from_bucket"] is None
    assert move["to_bucket"] == "QUARANTINE"
    assert move["effective_date"] == acquisition.isoformat()
    assert move["reason"] == f"Purchase batch #{batch_id}"
