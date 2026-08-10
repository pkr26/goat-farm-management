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

from datetime import timedelta
from decimal import Decimal

import httpx
import pytest

from app.db import get_sessionmaker
from app.models import FeedInventory, Transaction
from app.utils import today

from .conftest import owner_with_farm
from .test_finance_extended import txn_payload


async def _inventory_item(client: httpx.AsyncClient, owner: dict, item_id: int) -> dict:
    response = await client.get("/api/feeding/inventory", headers=owner)
    assert response.status_code == 200, response.text
    return next(item for item in response.json() if item["id"] == item_id)


async def _restock(
    client: httpx.AsyncClient,
    owner: dict,
    item_id: int,
    *,
    qty_kg: float,
    price_per_kg: float,
) -> dict:
    response = await client.post(
        f"/api/feeding/inventory/{item_id}/add",
        json={"qty_kg": qty_kg, "price_per_kg": price_per_kg},
        headers=owner,
    )
    assert response.status_code == 200, response.text
    rows = (await client.get("/api/finance", headers=owner)).json()["transactions"]
    return max(
        (row for row in rows if row["source_type"] == "FEED_PURCHASE"),
        key=lambda row: row["id"],
    )


def test_feed_purchase_model_metadata_matches_migration_contract() -> None:
    table = Transaction.__table__
    assert table.c.feed_inventory_id.nullable
    assert table.c.feed_quantity_kg.type.precision == 15
    assert table.c.feed_quantity_kg.type.scale == 3
    assert table.c.feed_unit_price_per_kg.type.precision == 14
    assert table.c.feed_unit_price_per_kg.type.scale == 2
    names = {constraint.name for constraint in table.constraints}
    assert "fk_transactions_farm_feed_inventory" in names
    assert "ck_transactions_feed_purchase_provenance" in names
    assert "ix_transactions_feed_inventory_id" in {index.name for index in table.indexes}
    assert "uq_feed_inventory_farm_id_id" in {
        constraint.name for constraint in FeedInventory.__table__.constraints
    }


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
    async with get_sessionmaker()() as db:
        stored = await db.get(Transaction, booked["id"])
        assert stored is not None
        assert stored.feed_inventory_id == item["id"]
        assert stored.feed_quantity_kg == Decimal("10.000")
        assert stored.feed_unit_price_per_kg == Decimal("20.00")

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

    # FEED_PURCHASE is a self-sourced ledger chain, not an allocation across
    # hidden restock rows. Its amount therefore remains auditable/correctable
    # instead of falling into the shared-source rejection path.
    repriced = await client.post(
        f"/api/finance/transactions/{corrected.json()['id']}/correct",
        json=correction | {"amount": 250.0, "reason": "Wrong unit price"},
        headers=owner,
    )
    assert repriced.status_code == 201, repriced.text
    assert repriced.json()["amount"] == 250.0
    assert repriced.json()["source_type"] == "FEED_PURCHASE"
    assert repriced.json()["source_id"] == booked["source_id"]
    inventory = await _inventory_item(client, owner, item["id"])
    assert inventory["last_purchase_price_per_kg"] == 25.0
    async with get_sessionmaker()() as db:
        stored = await db.get(Transaction, repriced.json()["id"])
        assert stored is not None
        assert stored.feed_quantity_kg == Decimal("10.000")
        assert stored.feed_unit_price_per_kg == Decimal("25.00")


async def test_correcting_older_feed_chain_does_not_clobber_latest_price(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    item = (await client.get("/api/feeding/inventory", headers=owner)).json()[0]
    older = await _restock(client, owner, item["id"], qty_kg=10.0, price_per_kg=20.0)
    await _restock(client, owner, item["id"], qty_kg=5.0, price_per_kg=30.0)

    corrected = await client.post(
        f"/api/finance/transactions/{older['id']}/correct",
        json=txn_payload(
            date=older["date"],
            amount=250.0,
            reason="Older supplier invoice was repriced",
        ),
        headers=owner,
    )
    assert corrected.status_code == 201, corrected.text
    assert (await _inventory_item(client, owner, item["id"]))["last_purchase_price_per_kg"] == 30.0


async def test_feed_correction_rejects_positive_total_that_rounds_unit_price_to_zero(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    item = (await client.get("/api/feeding/inventory", headers=owner)).json()[0]
    booked = await _restock(client, owner, item["id"], qty_kg=10.0, price_per_kg=20.0)

    refused = await client.post(
        f"/api/finance/transactions/{booked['id']}/correct",
        json=txn_payload(
            date=booked["date"],
            amount=0.01,
            reason="Incorrect total",
        ),
        headers=owner,
    )

    assert refused.status_code == 422, refused.text
    assert "at least ₹0.01 per kg" in refused.json()["detail"]
    assert (await _inventory_item(client, owner, item["id"]))["last_purchase_price_per_kg"] == 20.0


async def test_feed_date_reorder_uses_exact_structured_unit_price(
    client: httpx.AsyncClient,
) -> None:
    """₹0.33 / 0.333 kg is lossy; the stored ₹1.00 unit price must win."""
    owner = await owner_with_farm(client)
    item = (await client.get("/api/feeding/inventory", headers=owner)).json()[0]
    await _restock(client, owner, item["id"], qty_kg=0.333, price_per_kg=1.0)
    latest = await _restock(client, owner, item["id"], qty_kg=1.0, price_per_kg=2.0)

    moved_back = await client.post(
        f"/api/finance/transactions/{latest['id']}/correct",
        json=txn_payload(
            date=(today() - timedelta(days=1)).isoformat(),
            amount=latest["amount"],
            reason="Invoice belongs to yesterday",
        ),
        headers=owner,
    )
    assert moved_back.status_code == 201, moved_back.text
    assert (await _inventory_item(client, owner, item["id"]))["last_purchase_price_per_kg"] == 1.0


async def test_feed_purchase_quantity_correction_fixes_inventory_and_unit_price(
    client: httpx.AsyncClient,
) -> None:
    """Bug 5 — TransactionCorrectionIn had no feed_quantity_kg field, so a
    mistyped restock quantity permanently inflated FeedInventory.qty_on_hand
    with no way to correct it, and _corrected_feed_unit_price always divided
    the corrected amount by the ORIGINAL (wrong) quantity — repricing the
    ledger to the true invoice total baked a wildly wrong unit price into
    FeedInventory.last_purchase_price_per_kg. Fixed: a correction can now
    also supply the true quantity, which reprices off the corrected quantity
    and adjusts qty_on_hand by the delta.
    """
    owner = await owner_with_farm(client)
    item = (await client.get("/api/feeding/inventory", headers=owner)).json()[0]
    # Operator meant 100 kg @ ₹20/kg = ₹2000 but mistyped 1000 kg @ ₹2/kg —
    # same invoice total, wrong split between quantity and unit price.
    booked = await _restock(client, owner, item["id"], qty_kg=1000.0, price_per_kg=2.0)
    assert booked["amount"] == 2000.0
    assert (await _inventory_item(client, owner, item["id"]))["qty_on_hand"] == 1000.0

    corrected = await client.post(
        f"/api/finance/transactions/{booked['id']}/correct",
        json=txn_payload(
            date=booked["date"],
            amount=2000.0,
            feed_quantity_kg=100.0,
            reason="Quantity was mistyped as 1000 kg instead of 100 kg",
        ),
        headers=owner,
    )
    assert corrected.status_code == 201, corrected.text
    assert corrected.json()["source_type"] == "FEED_PURCHASE"

    inventory = await _inventory_item(client, owner, item["id"])
    assert inventory["qty_on_hand"] == 100.0
    assert inventory["last_purchase_price_per_kg"] == 20.0
    async with get_sessionmaker()() as db:
        stored = await db.get(Transaction, corrected.json()["id"])
        assert stored is not None
        assert stored.feed_quantity_kg == Decimal("100.000")
        assert stored.feed_unit_price_per_kg == Decimal("20.00")


async def test_feed_purchase_quantity_correction_refuses_to_drive_stock_negative(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    item = (await client.get("/api/feeding/inventory", headers=owner)).json()[0]
    booked = await _restock(client, owner, item["id"], qty_kg=100.0, price_per_kg=10.0)

    async with get_sessionmaker()() as db:
        inventory = await db.get(FeedInventory, item["id"], with_for_update=True)
        assert inventory is not None
        # 90 kg of this purchase has already been mixed/dispensed elsewhere.
        inventory.qty_on_hand = 10.0
        await db.commit()

    refused = await client.post(
        f"/api/finance/transactions/{booked['id']}/correct",
        json=txn_payload(
            date=booked["date"],
            amount=booked["amount"],
            feed_quantity_kg=5.0,
            reason="Quantity was overstated",
        ),
        headers=owner,
    )
    assert refused.status_code == 409, refused.text
    assert "already been used" in refused.json()["detail"]
    assert (await _inventory_item(client, owner, item["id"]))["qty_on_hand"] == 10.0


async def test_legacy_feed_purchase_corrections_and_ambiguity_boundary(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    item = (await client.get("/api/feeding/inventory", headers=owner)).json()[0]
    structured = await _restock(client, owner, item["id"], qty_kg=10.0, price_per_kg=20.0)
    legacy = await _restock(client, owner, item["id"], qty_kg=5.0, price_per_kg=30.0)
    async with get_sessionmaker()() as db:
        row = await db.get(Transaction, legacy["id"], with_for_update=True)
        assert row is not None
        row.feed_inventory_id = None
        row.feed_quantity_kg = None
        row.feed_unit_price_per_kg = None
        await db.commit()

    # A pre-provenance row never fed the displayed last-purchase price, so
    # amount/date corrections are pure ledger edits: they must succeed and
    # must leave the inventory's displayed price untouched.
    legacy_amount = await client.post(
        f"/api/finance/transactions/{legacy['id']}/correct",
        json=txn_payload(
            date=legacy["date"],
            amount=legacy["amount"] + 1,
            reason="Legacy invoice repriced",
        ),
        headers=owner,
    )
    assert legacy_amount.status_code == 201, legacy_amount.text
    assert legacy_amount.json()["amount"] == legacy["amount"] + 1
    assert legacy_amount.json()["source_id"] == legacy["source_id"]
    assert (await _inventory_item(client, owner, item["id"]))["last_purchase_price_per_kg"] == 30.0

    # The newer legacy key could belong to this item, so repricing the older
    # structured chain must not guess and overwrite the displayed last price.
    ambiguous = await client.post(
        f"/api/finance/transactions/{structured['id']}/correct",
        json=txn_payload(
            date=structured["date"],
            amount=250.0,
            reason="Structured invoice repriced",
        ),
        headers=owner,
    )
    assert ambiguous.status_code == 409, ambiguous.text
    assert "legacy feed purchase may be the latest" in ambiguous.json()["detail"].lower()
    assert (await _inventory_item(client, owner, item["id"]))["last_purchase_price_per_kg"] == 30.0

    legacy_replacement = legacy_amount.json()
    notes_only = await client.post(
        f"/api/finance/transactions/{legacy_replacement['id']}/correct",
        json=txn_payload(
            date=legacy_replacement["date"],
            amount=legacy_replacement["amount"],
            notes="Legacy invoice reference",
            reason="Attach invoice reference",
        ),
        headers=owner,
    )
    assert notes_only.status_code == 201, notes_only.text
