# FIXED — regression suite
# Bug 1 — POST /api/finance/new used to accept any `notes` length at the
# schema layer (TransactionIn.notes had no max_length) while the
# `transactions.notes` column is VARCHAR(255) → over-long notes raised
# StringDataRightTruncationError during commit → 500. Fixed: the schema is
# capped at 255 → 422.
# Bug 2 — schemas.common.BoundedId allows ids up to 2**62 while `animals.id`
# is a PostgreSQL INTEGER (int32). A `related_animal_id` above 2**31 - 1
# passed validation, then the `db.get(Animal, ...)` lookup in api/finance.py
# crashed with asyncpg DataError → 500. Fixed: lookups treat ids above the
# int4 ceiling as an invalid explicit reference (400), never binding them.
# Bug 3 — POST /api/finance/new with a non-finite amount (raw JSON `NaN` /
# `Infinity`): pydantic's FiniteFloat validator correctly raised, but
# FastAPI's default RequestValidationError handler failed to SERIALIZE the
# 422 body (the error payload embeds the offending float and Starlette's
# json.dumps runs with allow_nan=False) → crashed request. Fixed: a custom
# handler in app/main.py sanitizes non-finite/non-JSON-safe values before
# returning the standard 422 shape.
# Why these contradict the project contract:
# - tests/test_adversarial.py's own module docstring states the standard:
#   "malformed input must never produce a 500".
# - Every other free-text field in the schemas carries an explicit max_length
#   (e.g. AnimalCreateIn.tag_number max 50, TaskCreateIn.title max 200), so the
#   missing cap on TransactionIn.notes was an oversight, not a choice.
"""Finance bugs — REGRESSION SUITE, all fixed (see header comment)."""

import httpx
import pytest

from .conftest import owner_with_farm
from .test_finance_extended import txn_payload


async def test_create_notes_over_255_chars_should_not_500(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post("/api/finance/new", json=txn_payload(notes="x" * 256), headers=owner)
    # Expected: 201 (stored) or 422 (rejected) — never a 500 crash.
    assert resp.status_code in (201, 422), resp.text


async def test_create_notes_10k_chars_should_not_500(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post(
        "/api/finance/new", json=txn_payload(notes="y" * 10_000), headers=owner
    )
    assert resp.status_code in (201, 422), resp.text


async def test_long_notes_followup_list_still_works(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post("/api/finance/new", json=txn_payload(notes="z" * 300), headers=owner)
    assert resp.status_code in (201, 422), resp.text
    # A failed commit must not poison the session/app for later requests.
    lst = await client.get("/api/finance", headers=owner)
    assert lst.status_code == 200, lst.text
    if resp.status_code == 201:
        assert len(lst.json()["transactions"]) == 1
    else:
        assert lst.json()["transactions"] == []


async def test_related_animal_id_above_int32_should_not_500(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post(
        "/api/finance/new",
        json=txn_payload(related_animal_id=3_000_000_000),  # passes BoundedId (<= 2**62)
        headers=owner,
    )
    assert resp.status_code == 400, resp.text


async def test_related_animal_id_bounded_max_should_not_500(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post(
        "/api/finance/new", json=txn_payload(related_animal_id=2**62), headers=owner
    )
    assert resp.status_code == 400, resp.text


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
async def test_create_amount_non_finite_should_return_422(
    client: httpx.AsyncClient, literal: str
) -> None:
    owner = await owner_with_farm(client)
    # httpx refuses to serialize non-finite floats, so send the raw JSON literal.
    body = (
        b'{"date": "2025-01-15", "type": "EXPENSE", "category": "FEED", "amount": %s}'
        % literal.encode()
    )
    resp = await client.post(
        "/api/finance/new",
        content=body,
        headers=owner | {"Content-Type": "application/json"},
    )
    # Expected: a clean 422 (validation rejects non-finite amounts) — not a
    # crashed response while FastAPI serializes the validation error.
    assert resp.status_code == 422, resp.text


async def test_feed_restock_provenance_survives_an_audited_correction(
    client: httpx.AsyncClient,
) -> None:
    """Bug 4 — the automatic feed-purchase expense carried no source pair, so
    the finance table presented a system-generated row as "Manual entry". A
    restock persists no record of its own (only the running inventory
    balance, whose id repeats), so the ledger row is its own source: unique
    against the partial unique index over ACTIVE source pairs. That pair must
    still round-trip through a correction, whose replacement inherits it after
    the original is voided.
    """
    owner = await owner_with_farm(client)
    inventory = await client.get("/api/feeding/inventory", headers=owner)
    assert inventory.status_code == 200, inventory.text
    item = inventory.json()[0]
    restock = await client.post(
        f"/api/feeding/inventory/{item['id']}/add",
        json={"qty_kg": 10.0, "price_per_kg": 20.0},
        headers=owner,
    )
    assert restock.status_code == 200, restock.text
    (booked,) = (await client.get("/api/finance", headers=owner)).json()["transactions"]
    assert booked["source_type"] == "FEED_PURCHASE"
    assert booked["source_id"] == booked["id"]

    correction = txn_payload(
        date=booked["date"],
        amount=booked["amount"],
        notes="Supplier invoice GRN-4471",
        reason="Attach the supplier invoice number",
    )
    corrected = await client.post(
        f"/api/finance/transactions/{booked['id']}/correct", json=correction, headers=owner
    )
    assert corrected.status_code == 201, corrected.text
    assert corrected.json()["source_type"] == "FEED_PURCHASE"
    assert corrected.json()["source_id"] == booked["source_id"]

    # Consequence of gaining provenance: the restock's price already reached
    # FeedInventory.last_purchase_price_per_kg, so re-pricing the ledger row
    # alone would leave stock valuation contradicting the books. The generic
    # source-linked guard now refuses it and asks for a compensating entry.
    repriced = await client.post(
        f"/api/finance/transactions/{corrected.json()['id']}/correct",
        json=correction | {"amount": 250.0, "reason": "Wrong unit price"},
        headers=owner,
    )
    assert repriced.status_code == 409, repriced.text
    assert "FEED_PURCHASE" in repriced.json()["detail"]
