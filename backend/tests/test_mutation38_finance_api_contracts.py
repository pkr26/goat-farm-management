"""Finance correction and lookup contracts over real PostgreSQL ledger facts."""

from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

from app.api.finance import (
    _corrected_feed_unit_price,
    _locked_source_animal,
    _reconcile_feed_purchase,
    _resolve_related_animal,
)
from app.db import get_sessionmaker
from app.models import Animal, Farm, InsurancePolicy, Transaction
from app.schemas.finance import TransactionCorrectionIn
from app.utils import today

from .conftest import owner_with_farm
from .test_finance_bugs import _inventory_item, _restock
from .test_finance_extended import add_txn, custom_role_id, txn_payload, worker_headers

MAX_DATABASE_KEY = 2_147_483_647


async def test_correction_preserves_narrative_and_default_first_page(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="finance-correction-narrative@farm.in")
    booked = await add_txn(client, owner, amount=100)
    response = await client.post(
        f"/api/finance/transactions/{booked['id']}/correct",
        headers=owner,
        json=txn_payload(
            amount=110,
            notes="  Actual invoice reference GRN-550  ",
            reason="Record the corrected supplier total with its reference",
        ),
    )
    assert response.status_code == 201, response.text
    assert response.json()["notes"] == "Actual invoice reference GRN-550"
    listing = await client.get("/api/finance", headers=owner)
    assert listing.status_code == 200, listing.text
    page = listing.json()
    assert page["offset"] == 0 and page["transactions_total"] == 2
    assert [row["id"] for row in page["transactions"]] == [response.json()["id"], booked["id"]]


async def test_missing_source_animal_returns_the_declared_conflict_without_a_null_dereference(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="finance-missing-source@farm.in")
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        assert farm is not None
        try:
            with pytest.raises(HTTPException) as rejected:
                await _locked_source_animal(db, farm, MAX_DATABASE_KEY + 1)
        except AttributeError as exc:
            pytest.fail(f"A missing source must not dereference None: {exc}")
        assert rejected.value.status_code == 409
        assert "no longer on this farm" in str(rejected.value.detail)


async def _first_inventory(client: httpx.AsyncClient, owner: dict[str, str]) -> dict[str, Any]:
    response = await client.get("/api/feeding/inventory", headers=owner)
    assert response.status_code == 200, response.text
    return dict(response.json()[0])


@pytest.mark.parametrize("key", [0, MAX_DATABASE_KEY], ids=["restored-zero", "largest-int4"])
async def test_finance_animal_helpers_preserve_the_full_declared_key_band(
    client: httpx.AsyncClient, key: int
) -> None:
    owner = await owner_with_farm(client, email="finance-animal-key@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        db.add(
            Animal(
                id=key,
                farm_id=farm_id,
                tag_number="RESTORED-FINANCE-LINK",
                sex="F",
                source="PURCHASED",
                current_bucket="FOUNDATION",
                status="ACTIVE",
            )
        )
        await db.commit()
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        if key == 0:
            with pytest.raises(HTTPException) as rejected:
                await _resolve_related_animal(db, farm, key)
            assert rejected.value.status_code == 400
            with pytest.raises(HTTPException) as rejected:
                await _locked_source_animal(db, farm, key)
            assert rejected.value.status_code == 409
        else:
            try:
                resolved = await _resolve_related_animal(db, farm, key)
                locked = await _locked_source_animal(db, farm, key)
            except HTTPException as exc:
                pytest.fail(f"The largest existing int4 Animal link must resolve: {exc}")
            assert resolved == (key, "RESTORED-FINANCE-LINK")
            assert locked.id == key and locked.farm_id == farm_id


@pytest.mark.parametrize(
    ("amount", "quantity", "status", "price"),
    [
        (0.01, 1.0, 201, 0.01),
        (0.0, 1.0, 201, 0.0),
        (1_000_000_000.0, 1.0, 201, 1_000_000_000.0),
        (1_000_000_000.0, 0.999, 422, None),
    ],
    ids=["one-paise-floor", "explicit-free-stock", "unit-price-ceiling", "above-unit-ceiling"],
)
async def test_corrected_feed_price_admits_its_boundaries_and_rejects_only_above_them(
    client: httpx.AsyncClient,
    amount: float,
    quantity: float,
    status: int,
    price: float | None,
) -> None:
    owner = await owner_with_farm(client, email="finance-unit-price@farm.in")
    item = await _first_inventory(client, owner)
    booked = await _restock(client, owner, item["id"], qty_kg=1.0, price_per_kg=1.0)
    response = await client.post(
        f"/api/finance/transactions/{booked['id']}/correct",
        headers=owner,
        json=txn_payload(
            date=booked["date"],
            amount=amount,
            feed_quantity_kg=quantity,
            reason="Record the actual supplier invoice total and quantity",
        ),
    )
    assert response.status_code == status, response.text
    stock = await _inventory_item(client, owner, item["id"])
    if status == 201:
        assert response.json()["amount"] == amount
        assert stock["qty_on_hand"] == quantity
        assert stock["last_purchase_price_per_kg"] == price
        async with get_sessionmaker()() as db:
            replacement = await db.get(Transaction, response.json()["id"])
            assert replacement is not None
            assert replacement.feed_unit_price_per_kg == Decimal(str(price))
            assert replacement.feed_quantity_kg == Decimal(str(quantity))
    else:
        assert "above ₹1,000,000,000.00 per kg" in response.json()["detail"]
        assert stock["qty_on_hand"] == stock["last_purchase_price_per_kg"] == 1.0


@pytest.mark.parametrize("missing", ["quantity", "unit-price"], ids=["no-quantity", "legacy-price"])
async def test_native_feed_price_helper_preserves_absent_optional_provenance(
    client: httpx.AsyncClient, missing: str
) -> None:
    owner = await owner_with_farm(client, email="finance-optional-provenance@farm.in")
    item = await _first_inventory(client, owner)
    booked = await _restock(client, owner, item["id"], qty_kg=1.0, price_per_kg=1.0)
    async with get_sessionmaker()() as db:
        txn = await db.get(Transaction, booked["id"])
        assert txn is not None
        quantity = None if missing == "quantity" else Decimal("1.000")
        if missing == "unit-price":
            # Constraint-valid retained ledger-only FEED_PURCHASE form.
            txn.feed_inventory_id = None
            txn.feed_quantity_kg = None
            txn.feed_unit_price_per_kg = None
            await db.commit()
        try:
            result = _corrected_feed_unit_price(txn, Decimal("0.50"), quantity)
        except TypeError as exc:
            pytest.fail(f"Absent optional feed provenance must avoid arithmetic: {exc}")
        assert result is None


async def test_legacy_feed_purchase_rejects_quantity_correction_with_the_declared_status(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="finance-legacy-quantity@farm.in")
    item = await _first_inventory(client, owner)
    booked = await _restock(client, owner, item["id"], qty_kg=1.0, price_per_kg=1.0)
    async with get_sessionmaker()() as db:
        txn = await db.get(Transaction, booked["id"], with_for_update=True)
        assert txn is not None
        txn.feed_inventory_id = None
        txn.feed_quantity_kg = None
        txn.feed_unit_price_per_kg = None
        await db.commit()
    response = await client.post(
        f"/api/finance/transactions/{booked['id']}/correct",
        headers=owner,
        json=txn_payload(
            date=booked["date"],
            amount=1,
            feed_quantity_kg=1,
            reason="The old invoice does not retain an actual quantity",
        ),
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "This feed purchase has no recorded quantity to correct"


async def test_native_hand_repair_rejects_partial_feed_provenance_before_reconciliation(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="finance-partial-repair@farm.in")
    item = await _first_inventory(client, owner)
    booked = await _restock(client, owner, item["id"], qty_kg=1, price_per_kg=1)
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        txn = await db.get(Transaction, booked["id"])
        assert farm is not None and txn is not None
        # The helper explicitly rejects hand-repaired partial provenance.
        # Keep the genuine in-memory repair unflushed, so no invalid database
        # row is manufactured and PostgreSQL constraints remain enabled.
        txn.feed_inventory_id = None
        payload = TransactionCorrectionIn.model_validate(
            txn_payload(date=booked["date"], amount=2, reason="Reject an incomplete native repair")
        )
        with db.no_autoflush, pytest.raises(HTTPException) as rejected:
            await _reconcile_feed_purchase(db, farm, txn, payload)
        assert rejected.value.status_code == 409
        assert rejected.value.detail == "This feed purchase has incomplete inventory provenance"
        await db.rollback()
    assert (await _inventory_item(client, owner, item["id"]))["last_purchase_price_per_kg"] == 1.0


async def test_native_latest_feed_purchase_rejects_missing_price_in_a_retained_repair(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="finance-latest-repair@farm.in")
    item = await _first_inventory(client, owner)
    older = await _restock(client, owner, item["id"], qty_kg=1, price_per_kg=1)
    newest = await _restock(client, owner, item["id"], qty_kg=1, price_per_kg=2)
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        retained = await db.get(Transaction, older["id"])
        txn = await db.get(Transaction, newest["id"])
        assert farm is not None and retained is not None and txn is not None
        retained.feed_unit_price_per_kg = None
        payload = TransactionCorrectionIn.model_validate(
            txn_payload(
                date=(today() - timedelta(days=1)).isoformat(),
                amount=newest["amount"],
                reason="The former latest purchase has incomplete native provenance",
            )
        )
        with db.no_autoflush, pytest.raises(HTTPException) as rejected:
            await _reconcile_feed_purchase(db, farm, txn, payload)
        assert rejected.value.status_code == 409
        assert rejected.value.detail == (
            "The latest feed purchase has incomplete inventory provenance"
        )
        await db.rollback()
    assert (await _inventory_item(client, owner, item["id"]))["last_purchase_price_per_kg"] == 2.0


@pytest.mark.parametrize("remaining", [0.0, 0.5], ids=["exactly-empty-stock", "positive-half-kilo"])
async def test_feed_quantity_correction_admits_the_actual_nonnegative_remaining_stock(
    client: httpx.AsyncClient, remaining: float
) -> None:
    owner = await owner_with_farm(client, email="finance-stock-boundary@farm.in")
    recipes = await client.get("/api/feeding/recipes", headers=owner)
    assert recipes.status_code == 200, recipes.text
    creep = next(recipe for recipe in recipes.json()["recipes"] if recipe["code"] == "CREEP")
    line = creep["lines"][0]
    used_kg = float(line["kg_per_100kg"])
    inventory = await client.get("/api/feeding/inventory", headers=owner)
    assert inventory.status_code == 200, inventory.text
    item = next(row for row in inventory.json() if row["ingredient"] == line["ingredient"])
    for row in inventory.json():
        if row["id"] != item["id"]:
            stocked = await client.post(
                f"/api/feeding/inventory/{row['id']}/add", headers=owner, json={"qty_kg": 100}
            )
            assert stocked.status_code == 200, stocked.text
    booked = await _restock(client, owner, item["id"], qty_kg=used_kg + 1, price_per_kg=1)
    mixed = await client.post(
        "/api/feeding/mix", headers=owner, json={"recipe_code": "CREEP", "batch_kg": 100}
    )
    assert mixed.status_code == 200, mixed.text
    assert (await _inventory_item(client, owner, item["id"]))["qty_on_hand"] == 1.0
    corrected_quantity = used_kg + remaining
    response = await client.post(
        f"/api/finance/transactions/{booked['id']}/correct",
        headers=owner,
        json=txn_payload(
            date=booked["date"],
            amount=booked["amount"],
            feed_quantity_kg=corrected_quantity,
            reason="Correct the invoice quantity after an actual feed mix",
        ),
    )
    assert response.status_code == 201, response.text
    assert (await _inventory_item(client, owner, item["id"]))["qty_on_hand"] == remaining
    async with get_sessionmaker()() as db:
        replacement = await db.get(Transaction, response.json()["id"])
        assert replacement is not None
        assert replacement.feed_quantity_kg == Decimal(str(corrected_quantity))


async def test_three_feed_purchases_reconcile_against_the_single_newest_source_chain(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="finance-three-purchases@farm.in")
    item = await _first_inventory(client, owner)
    oldest = await _restock(client, owner, item["id"], qty_kg=1, price_per_kg=10)
    await _restock(client, owner, item["id"], qty_kg=1, price_per_kg=20)
    await _restock(client, owner, item["id"], qty_kg=1, price_per_kg=30)
    response = await client.post(
        f"/api/finance/transactions/{oldest['id']}/correct",
        headers=owner,
        json=txn_payload(
            date=oldest["date"], amount=15, reason="Correct the oldest of three actual invoices"
        ),
    )
    assert response.status_code == 201, response.text
    stock = await _inventory_item(client, owner, item["id"])
    assert stock["qty_on_hand"] == 3.0 and stock["last_purchase_price_per_kg"] == 30.0


@pytest.mark.parametrize("key", [0, MAX_DATABASE_KEY], ids=["restored-zero", "largest-int4"])
async def test_transaction_correction_preserves_its_declared_route_key_band(
    client: httpx.AsyncClient, key: int
) -> None:
    owner = await owner_with_farm(client, email="finance-transaction-key@farm.in")
    async with get_sessionmaker()() as db:
        db.add(
            Transaction(
                id=key,
                farm_id=int(owner["X-Farm-Id"]),
                date=today(),
                type="EXPENSE",
                category="FEED",
                amount=Decimal("100.00"),
                notes="Constraint-valid retained manual invoice",
            )
        )
        await db.commit()
    response = await client.post(
        f"/api/finance/transactions/{key}/correct",
        headers=owner,
        json=txn_payload(amount=110, reason="Correct an existing retained manual invoice"),
    )
    assert response.status_code == (404 if key == 0 else 201), response.text
    if key != 0:
        assert response.json()["correction_of_id"] == key and response.json()["amount"] == 110


@pytest.mark.parametrize("key", [0, MAX_DATABASE_KEY], ids=["restored-zero", "largest-int4"])
async def test_insurance_renewal_preserves_its_declared_route_key_band(
    client: httpx.AsyncClient, key: int
) -> None:
    owner = await owner_with_farm(client, email="finance-policy-key@farm.in")
    async with get_sessionmaker()() as db:
        db.add(
            InsurancePolicy(
                id=key,
                farm_id=int(owner["X-Farm-Id"]),
                policy_number="RETAINED-NATIVE-POLICY",
                insurer="Retained livestock insurance",
                sum_insured=Decimal("1000.00"),
                premium=Decimal("10.00"),
                start_date=today(),
                renewal_date=today() + timedelta(days=30),
                status="active",
            )
        )
        await db.commit()
    renewal = (today() + timedelta(days=60)).isoformat()
    response = await client.post(
        f"/api/finance/insurance/{key}/renew", headers=owner, json={"renewal_date": renewal}
    )
    assert response.status_code == (404 if key == 0 else 200), response.text
    if key != 0:
        assert response.json()["id"] == key and response.json()["renewal_date"] == renewal


async def test_finance_summary_exposes_mortality_only_to_a_health_reader(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="finance-mortality-permissions@farm.in")
    owner_response = await client.get("/api/finance", headers=owner)
    assert owner_response.status_code == 200, owner_response.text
    assert isinstance(owner_response.json()["mortality_loss"], dict)
    role = await custom_role_id(client, owner, "Finance-only observer", ["finance.view"])
    worker = await worker_headers(client, owner, role, "finance-only-observer@farm.in")
    worker_response = await client.get("/api/finance", headers=worker)
    assert worker_response.status_code == 200, worker_response.text
    assert worker_response.json()["mortality_loss"] is None


async def test_manual_ledger_correction_rejects_a_feed_only_quantity_override(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="finance-manual-quantity@farm.in")
    booked = await add_txn(client, owner, amount=100)
    response = await client.post(
        f"/api/finance/transactions/{booked['id']}/correct",
        headers=owner,
        json=txn_payload(
            amount=100,
            feed_quantity_kg=1,
            reason="A manual ledger invoice has no stock quantity provenance",
        ),
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == (
        "feed_quantity_kg only applies to a feed-purchase correction"
    )


async def test_retained_zero_animal_policy_keeps_its_real_linked_tag_on_renewal(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="finance-retained-policy-link@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        db.add(
            Animal(
                id=0,
                farm_id=farm_id,
                tag_number="RETAINED-ZERO-LINK",
                sex="F",
                source="PURCHASED",
                current_bucket="FOUNDATION",
                status="ACTIVE",
            )
        )
        await db.flush()
        policy = InsurancePolicy(
            farm_id=farm_id,
            animal_id=0,
            policy_number="RETAINED-ZERO-ANIMAL",
            insurer="Retained livestock insurance",
            sum_insured=Decimal("1000.00"),
            premium=Decimal("10.00"),
            start_date=today(),
            renewal_date=today() + timedelta(days=30),
            status="active",
        )
        db.add(policy)
        await db.commit()
        policy_id = policy.id
    response = await client.post(
        f"/api/finance/insurance/{policy_id}/renew",
        headers=owner,
        json={"renewal_date": (today() + timedelta(days=60)).isoformat()},
    )
    assert response.status_code == 200, response.text
    assert response.json()["animal_id"] == 0
    assert response.json()["animal_tag"] == "RETAINED-ZERO-LINK"
