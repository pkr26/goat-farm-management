"""A migrated purchase ledger retains the actual seller without inventing one."""

from datetime import timedelta

import httpx
import pytest

from app.utils import today

from .conftest import owner_with_farm


@pytest.mark.parametrize("seller", [None, "Paper register supplier"])
async def test_historical_purchase_ledger_names_the_recorded_seller_only_when_present(
    client: httpx.AsyncClient, seller: str | None
) -> None:
    owner = await owner_with_farm(client)
    acquired_on = today() - timedelta(days=7)
    tag = "HISTORICAL-SELLER"
    response = await client.post(
        "/api/animals",
        headers=owner | {"Idempotency-Key": "historical-seller-ledger"},
        json={
            "tag_number": tag,
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "historical_import_reason": "Migrated acquisition from the paper register",
            "purchase_date": acquired_on.isoformat(),
            "purchase_price": 1234.5,
            "seller_name": seller,
        },
    )
    assert response.status_code == 201, response.text
    animal = response.json()
    assert animal["seller_name"] == seller
    ledger = await client.get("/api/finance", headers=owner)
    assert ledger.status_code == 200, ledger.text
    assert ledger.json()["transactions_total"] == 1
    transaction = ledger.json()["transactions"][0]
    assert transaction["type"] == "EXPENSE"
    assert transaction["category"] == "ANIMAL_PURCHASE"
    assert transaction["date"] == acquired_on.isoformat()
    assert transaction["amount"] == 1234.5
    assert transaction["related_animal_id"] == animal["id"]
    assert transaction["source_type"] == "ANIMAL_PURCHASE"
    assert transaction["source_id"] == animal["id"]
    expected_note = f"Purchase of {tag}"
    if seller is not None:
        expected_note += f" from {seller}"
    assert transaction["notes"] == expected_note
