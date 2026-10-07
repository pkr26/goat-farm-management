"""Finance wire boundaries and real insurance register response contracts."""

from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError

from app.db import get_sessionmaker
from app.models import Animal, InsurancePolicy, Transaction
from app.schemas.finance import (
    InsurancePolicyIn,
    TransactionCorrectionIn,
    TransactionOut,
)
from app.utils import today

from .conftest import owner_with_farm
from .test_finance_extended import make_animal, txn_payload
from .test_finance_insurance import policy_payload

MAX_DATABASE_KEY = 2_147_483_647


@pytest.mark.parametrize(
    ("field", "length", "valid"),
    [
        ("policy_number", 0, False),
        ("policy_number", 1, True),
        ("policy_number", 60, True),
        ("policy_number", 61, False),
        ("insurer", 0, False),
        ("insurer", 1, True),
        ("insurer", 120, True),
        ("insurer", 121, False),
        ("notes", 255, True),
        ("notes", 256, False),
    ],
    ids=[
        "number-empty",
        "number-single",
        "number-max",
        "number-too-long",
        "insurer-empty",
        "insurer-single",
        "insurer-max",
        "insurer-too-long",
        "notes-max",
        "notes-too-long",
    ],
)
def test_policy_input_preserves_the_declared_text_boundaries(
    field: str, length: int, valid: bool
) -> None:
    payload = policy_payload(**{field: "X" * length})
    if valid:
        try:
            parsed = InsurancePolicyIn.model_validate(payload)
        except ValidationError as exc:
            pytest.fail(f"The declared insurance text boundary must be accepted: {exc}")
        assert getattr(parsed, field) == "X" * length
    else:
        with pytest.raises(ValidationError):
            InsurancePolicyIn.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "length", "valid"),
    [
        ("reason", 2, False),
        ("reason", 3, True),
        ("reason", 255, True),
        ("reason", 256, False),
        ("notes", 255, True),
        ("notes", 256, False),
    ],
    ids=[
        "reason-too-short",
        "reason-min",
        "reason-max",
        "reason-too-long",
        "notes-max",
        "notes-too-long",
    ],
)
def test_ledger_correction_input_preserves_the_declared_narrative_boundaries(
    field: str, length: int, valid: bool
) -> None:
    payload = (
        txn_payload(reason="Correct supplier invoice", **{field: "X" * length})
        if field != "reason"
        else txn_payload(reason="X" * length)
    )
    if valid:
        try:
            parsed = TransactionCorrectionIn.model_validate(payload)
        except ValidationError as exc:
            pytest.fail(f"The declared ledger narrative boundary must be accepted: {exc}")
        assert getattr(parsed, field) == "X" * length
    else:
        with pytest.raises(ValidationError):
            TransactionCorrectionIn.model_validate(payload)


@pytest.mark.parametrize(
    "span", [0, 1830, 1831], ids=["same-day-cover", "five-leap-year-cap", "beyond-cap"]
)
def test_insurance_native_input_preserves_the_independent_five_year_horizon(
    span: int,
) -> None:
    # Five periods of 366 days is the stated registration envelope, independent
    # of the service backstop and the implementation's copied constant.
    payload = policy_payload(
        start_date=today().isoformat(),
        renewal_date=(today() + timedelta(days=span)).isoformat(),
    )
    if span <= 1830:
        try:
            parsed = InsurancePolicyIn.model_validate(payload)
        except ValidationError as exc:
            pytest.fail(f"Valid inclusive insurance horizon must be accepted: {exc}")
        assert (parsed.renewal_date - parsed.start_date).days == span
    else:
        with pytest.raises(ValidationError):
            InsurancePolicyIn.model_validate(payload)


async def test_transaction_adapter_matches_actual_committed_ledger_wire_facts(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="finance-transaction-adapter@farm.in")
    response = await client.post(
        "/api/finance/new",
        headers=owner,
        json=txn_payload(amount=123, notes="Supplier invoice 123"),
    )
    assert response.status_code == 201, response.text
    wire = response.json()
    async with get_sessionmaker()() as db:
        row = await db.get(Transaction, wire["id"])
        assert row is not None
        try:
            adapted = TransactionOut.model_validate(row)
        except ValidationError as exc:
            pytest.fail(
                f"The declared ledger ORM adapter must accept an actual committed row: {exc}"
            )
        assert adapted.model_dump(mode="json") == wire


async def test_insurance_register_has_real_filtered_rows_and_asserted_success_responses(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="finance-insurance-register@farm.in")
    animal = await make_animal(client, owner)
    assert animal["id"] == 1
    linked_response = await client.post(
        "/api/finance/insurance",
        headers=owner,
        json=policy_payload(
            policy_number="LINKED-1",
            animal_id=animal["id"],
            notes="  Bank cover reference REF-17  ",
        ),
    )
    assert linked_response.status_code == 201, linked_response.text
    linked = linked_response.json()
    assert (
        linked["animal_tag"] == animal["tag_number"]
        and linked["notes"] == "Bank cover reference REF-17"
    )
    herd_response = await client.post(
        "/api/finance/insurance",
        headers=owner,
        json=policy_payload(
            policy_number="HERD-1",
            renewal_date=(today() + timedelta(days=366)).isoformat(),
        ),
    )
    assert herd_response.status_code == 201, herd_response.text
    herd = herd_response.json()
    assert herd["animal_id"] is None and herd["animal_tag"] is None
    foreign = await owner_with_farm(client, email="finance-insurance-other-farm@farm.in")
    foreign_response = await client.post(
        "/api/finance/insurance",
        headers=foreign,
        json=policy_payload(policy_number="FOREIGN-1"),
    )
    assert foreign_response.status_code == 201, foreign_response.text
    cases: list[tuple[dict[str, str | int], list[int]]] = [
        ({}, [linked["id"], herd["id"]]),
        ({"animal_id": 1}, [linked["id"]]),
        ({"status": "active"}, [linked["id"], herd["id"]]),
        ({"status": "claimed"}, []),
    ]
    for params, expected in cases:
        listed = await client.get("/api/finance/insurance", headers=owner, params=params)
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert [row["id"] for row in body["policies"]] == expected
        assert body["total"] == len(expected) and body["limit"] == 200 and body["offset"] == 0


@pytest.mark.parametrize(
    ("parameter", "value", "status"),
    [
        ("animal_id", 0, 422),
        ("animal_id", MAX_DATABASE_KEY, 200),
        ("animal_id", MAX_DATABASE_KEY + 1, 422),
        ("limit", 200, 200),
        ("limit", 201, 422),
        ("offset", 0, 200),
    ],
    ids=[
        "zero-animal-filter",
        "largest-animal-filter",
        "overflow-animal-filter",
        "page-max",
        "page-overflow",
        "first-page",
    ],
)
async def test_insurance_query_gate_rejects_only_outside_its_declared_envelope(
    client: httpx.AsyncClient, parameter: str, value: int, status: int
) -> None:
    owner = await owner_with_farm(client, email="finance-insurance-query@farm.in")
    response = await client.get("/api/finance/insurance", headers=owner, params={parameter: value})
    assert response.status_code == status, response.text
    if status == 200:
        assert response.json()["policies"] == [] and response.json()["total"] == 0


@pytest.mark.parametrize(
    "key",
    [0, 1, MAX_DATABASE_KEY],
    ids=["restored-zero", "first-live-id", "largest-int4"],
)
async def test_claim_and_history_preserve_actual_policy_link_and_route_key_band(
    client: httpx.AsyncClient, key: int
) -> None:
    owner = await owner_with_farm(client, email="finance-insurance-retained-policy@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        db.add(
            Animal(
                id=key,
                farm_id=farm_id,
                tag_number="RETAINED-COVER-DOE",
                sex="F",
                source="PURCHASED",
                current_bucket="FOUNDATION",
                status="ACTIVE",
            )
        )
        await db.flush()
        db.add(
            InsurancePolicy(
                id=key,
                farm_id=farm_id,
                policy_number="RESTORED-AUDITED-POLICY",
                insurer="Retained livestock insurer",
                animal_id=key,
                sum_insured=Decimal("1000.00"),
                premium=Decimal("10.00"),
                start_date=today(),
                renewal_date=today() + timedelta(days=30),
                status="active",
            )
        )
        await db.commit()
    history = await client.get(f"/api/finance/insurance/{key}/history", headers=owner)
    assert history.status_code == (404 if key == 0 else 200), history.text
    if key:
        assert history.json()["policy"]["animal_tag"] == "RETAINED-COVER-DOE"
        assert history.json()["policy"]["id"] == key and history.json()["premiums"] == []
    claimed = await client.post(f"/api/finance/insurance/{key}/claim", headers=owner, json={})
    assert claimed.status_code == (404 if key == 0 else 200), claimed.text
    if key:
        body = claimed.json()
        assert body["status"] == "claimed" and body["animal_tag"] == "RETAINED-COVER-DOE"
        assert body["claim_date"] == today().isoformat() and body["claimed_by_id"] is not None
    else:
        async with get_sessionmaker()() as db:
            preserved = await db.get(InsurancePolicy, key)
            assert preserved is not None and preserved.status == "active"


@pytest.mark.parametrize("key", [0, MAX_DATABASE_KEY], ids=["restored-zero", "largest-int4"])
async def test_lifetime_pnl_preserves_its_actual_animal_route_key_band(
    client: httpx.AsyncClient, key: int
) -> None:
    owner = await owner_with_farm(client, email="finance-lifetime-key@farm.in")
    async with get_sessionmaker()() as db:
        db.add(
            Animal(
                id=key,
                farm_id=int(owner["X-Farm-Id"]),
                tag_number="RETAINED-LIFETIME-DOE",
                sex="F",
                source="PURCHASED",
                current_bucket="FOUNDATION",
                status="ACTIVE",
            )
        )
        await db.commit()
    response = await client.get(f"/api/finance/animals/{key}/lifetime-pnl", headers=owner)
    assert response.status_code == (404 if key == 0 else 200), response.text
    if key:
        body = response.json()
        assert body["animal_id"] == key and body["tag_number"] == "RETAINED-LIFETIME-DOE"
        assert (
            body["net"]
            == body["purchase_cost"]
            == body["health_cost"]
            == body["insurance_premiums"]
            == body["sale_income"]
            == 0.0
        )


async def test_missing_insurance_claim_has_the_stable_not_found_response(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="finance-missing-claim@farm.in")
    response = await client.post(
        f"/api/finance/insurance/{MAX_DATABASE_KEY}/claim", headers=owner, json={}
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Insurance policy not found"
