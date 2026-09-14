"""Husbandry-standards upgrades for the terminal-status and move endpoints.

- POST /api/animals/{id}/status SOLD: derived sale price (weight × ₹/kg,
  paise-exact), weight/buyer persistence + ledger-note capture, unknown-DOB
  age advisory, soft 24–28 kg weight-window advisory, estimated-DOB males
  held to the meat-sale age gate.
- POST /api/animals/{id}/status DEAD: coded mortality cause (bounded
  vocabulary), disposal method, necropsy coherence.
- POST /api/animals/{id}/move RESTING → BREEDING: minimum rest-and-flush
  residency (GOAT_PROFILE.min_rest_flush_days), owner override bypass.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, get_args

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Animal, Bucket, BucketMove, MortalityCause
from app.models.species import GOAT_PROFILE
from app.schemas.animals import MortalityCauseStr
from app.services.animals import bucket_transition_error
from app.utils import today

from .conftest import owner_with_farm


def iso(d: date) -> str:
    return d.isoformat()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def make_animal(
    client: httpx.AsyncClient,
    headers: dict,
    tag: str,
    *,
    sex: str = "F",
    bucket: str = "FOUNDATION",
    **overrides: object,
) -> dict:
    payload: dict = {
        "tag_number": tag,
        "sex": sex,
        "source": "PURCHASED",
        "current_bucket": bucket,
        "historical_import_reason": "Existing-herd test fixture",
    } | overrides
    resp = await client.post("/api/animals", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def change_status(
    client: httpx.AsyncClient, headers: dict, animal_id: int, status: str, **overrides: object
) -> httpx.Response:
    return await client.post(
        f"/api/animals/{animal_id}/status",
        json={"new_status": status} | overrides,
        headers=headers,
    )


async def move_bucket(
    client: httpx.AsyncClient, headers: dict, animal_id: int, to_bucket: str, **overrides: object
) -> httpx.Response:
    return await client.post(
        f"/api/animals/{animal_id}/move",
        json={"to_bucket": to_bucket} | overrides,
        headers=headers,
    )


async def fetch_animal_columns(animal_id: int, *columns: Any) -> tuple:
    """Read persisted columns the wire model intentionally does not expose."""
    async with get_sessionmaker()() as db:
        row = (await db.execute(select(*columns).where(Animal.id == animal_id))).one_or_none()
    assert row is not None
    return row


def validation_error_text(resp: httpx.Response) -> str:
    """Flatten the structured request-validation body into assertable text.

    main.py's request-validation handler answers 422 with a list of
    {type, loc, msg} objects (never a reflected-input echo), so schema-level
    rejections must be asserted against loc/msg — not a free-form string.
    """
    detail = resp.json()["detail"]
    assert isinstance(detail, list), detail
    return " ".join(f"{error.get('loc', '')} {error.get('msg', '')}" for error in detail)


async def sale_transaction(client: httpx.AsyncClient, headers: dict, tag: str) -> dict:
    resp = await client.get("/api/finance", headers=headers)
    assert resp.status_code == 200, resp.text
    return next(
        txn
        for txn in resp.json()["transactions"]
        if txn["category"] == "ANIMAL_SALE" and tag in txn["notes"]
    )


# ---------------------------------------------------------------------------
# Vocabulary parity (mirrors test_schema_parity's enum/Literal guards)
# ---------------------------------------------------------------------------
def test_mortality_cause_str_matches_enum() -> None:
    assert set(get_args(MortalityCauseStr)) == {m.value for m in MortalityCause}


# ---------------------------------------------------------------------------
# bucket_transition_error — RESTING rest-and-flush window (pure function)
# ---------------------------------------------------------------------------
def _resting_doe() -> Animal:
    return Animal(
        tag_number="R-1",
        sex="F",
        status="ACTIVE",
        current_bucket=Bucket.RESTING.value,
        date_of_birth=today() - timedelta(days=500),
    )


def test_resting_to_breeding_blocked_inside_flush_window() -> None:
    when = today()
    resting_since = when - timedelta(days=GOAT_PROFILE.min_rest_flush_days - 1)
    error = bucket_transition_error(
        _resting_doe(),
        Bucket.BREEDING.value,
        context="manual",
        reference_date=when,
        facts=(24.0, False),
        resting_since=resting_since,
    )
    assert error is not None
    assert "rest-and-flush window" in error
    earliest = (resting_since + timedelta(days=GOAT_PROFILE.min_rest_flush_days)).isoformat()
    assert f"earliest re-entry is {earliest}" in error


def test_resting_to_breeding_blocked_for_breeding_context_at_day_nine() -> None:
    when = today()
    error = bucket_transition_error(
        _resting_doe(),
        Bucket.BREEDING.value,
        context="breeding",
        reference_date=when,
        facts=(24.0, False),
        resting_since=when - timedelta(days=9),
    )
    assert error is not None and "9 of the" in error


def test_resting_to_breeding_allowed_at_min_rest_flush_days() -> None:
    when = today()
    assert (
        bucket_transition_error(
            _resting_doe(),
            Bucket.BREEDING.value,
            context="manual",
            reference_date=when,
            facts=(24.0, False),
            resting_since=when - timedelta(days=GOAT_PROFILE.min_rest_flush_days),
        )
        is None
    )


def test_resting_flush_guard_bypassed_by_history_override() -> None:
    when = today()
    assert (
        bucket_transition_error(
            _resting_doe(),
            Bucket.BREEDING.value,
            context="history_override",
            reference_date=when,
            resting_since=when,  # day 0 — override must still bypass
        )
        is None
    )


def test_resting_flush_guard_not_raised_for_same_bucket_or_other_edges() -> None:
    when = today()
    doe = _resting_doe()
    # Same-bucket no-op never reaches the guard.
    assert (
        bucket_transition_error(doe, Bucket.RESTING.value, reference_date=when, resting_since=when)
        is None
    )
    # A FOUNDATION doe (facts eligible) with an irrelevant resting_since.
    foundation_doe = Animal(
        tag_number="F-1",
        sex="F",
        status="ACTIVE",
        current_bucket=Bucket.FOUNDATION.value,
        date_of_birth=today() - timedelta(days=500),
    )
    assert (
        bucket_transition_error(
            foundation_doe,
            Bucket.BREEDING.value,
            reference_date=when,
            facts=(24.0, False),
            resting_since=when,
        )
        is None
    )
    # RESTING → BREEDING under a context that is not legal for the edge is
    # rejected by the transition matrix, not by the flush window.
    error = bucket_transition_error(
        doe,
        Bucket.BREEDING.value,
        context="weaning",
        reference_date=when,
        facts=(24.0, False),
        resting_since=when - timedelta(days=90),
    )
    assert error is not None and "Illegal lifecycle transition" in error


def test_resting_flush_guard_without_residency_fact_is_not_enforced() -> None:
    # Documents the threading contract: a caller that cannot reach the edge
    # with a residency fact (legacy data with no BucketMove) leaves the
    # window unproven rather than guessing a date.
    when = today()
    assert (
        bucket_transition_error(
            _resting_doe(),
            Bucket.BREEDING.value,
            reference_date=when,
            facts=(24.0, False),
        )
        is None
    )


# ---------------------------------------------------------------------------
# POST /move — RESTING minimum stay through the endpoint
# ---------------------------------------------------------------------------
async def _breeding_eligible_resting_doe(
    client: httpx.AsyncClient, headers: dict, tag: str, *, purchase_date: date
) -> dict:
    # purchase_date drives the initial RESTING placement's effective_date
    # (the create path stamps entry with purchase_date ?? DOB ?? today), so
    # the fixture controls the doe's RESTING residency without raw SQL.
    return await make_animal(
        client,
        headers,
        tag,
        sex="F",
        bucket="RESTING",
        date_of_birth=iso(today() - timedelta(days=400)),
        purchase_date=iso(purchase_date),
        weight_kg=24.0,
        weight_date=iso(today()),
    )


async def test_move_resting_to_breeding_blocked_at_day_nine(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client, "flush9@farm.in", "Flush Day Nine")
    doe = await _breeding_eligible_resting_doe(
        client, headers, "FLU-F-9", purchase_date=today() - timedelta(days=9)
    )
    resp = await move_bucket(client, headers, doe["id"], "BREEDING")
    assert resp.status_code == 409, resp.text
    assert "rest-and-flush window" in resp.json()["detail"]
    assert str(GOAT_PROFILE.min_rest_flush_days) in resp.json()["detail"]


async def test_move_resting_to_breeding_allowed_at_day_ten(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client, "flush10@farm.in", "Flush Day Ten")
    doe = await _breeding_eligible_resting_doe(
        client,
        headers,
        "FLU-F-10",
        purchase_date=today() - timedelta(days=GOAT_PROFILE.min_rest_flush_days),
    )
    resp = await move_bucket(client, headers, doe["id"], "BREEDING")
    assert resp.status_code == 200, resp.text
    assert resp.json()["current_bucket"] == "BREEDING"


async def test_move_resting_to_breeding_override_bypasses_flush_window(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, "flushov@farm.in", "Flush Override")
    doe = await _breeding_eligible_resting_doe(client, headers, "FLU-F-OV", purchase_date=today())
    resp = await move_bucket(
        client,
        headers,
        doe["id"],
        "BREEDING",
        history_override=True,
        reason="correction: rest already served on paper records",
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["current_bucket"] == "BREEDING"


async def test_move_resting_to_breeding_backdated_entry_counts_residency(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, "flushbk@farm.in", "Flush Backdated")
    doe = await _breeding_eligible_resting_doe(client, headers, "FLU-F-BK", purchase_date=today())
    farm_id = int(headers["X-Farm-Id"])
    # A genuinely backdated RESTING entry (recorded late) must count as
    # residency — the window is effective-date based, like every other
    # bucket-age computation in the codebase.
    async with get_sessionmaker()() as db:
        entry = (
            await db.execute(
                select(BucketMove)
                .where(
                    BucketMove.farm_id == farm_id,
                    BucketMove.animal_id == doe["id"],
                    BucketMove.to_bucket == Bucket.RESTING.value,
                )
                .order_by(BucketMove.id.desc())
                .limit(1)
            )
        ).scalar_one()
        entry.effective_date = today() - timedelta(days=GOAT_PROFILE.min_rest_flush_days)
        await db.commit()
    resp = await move_bucket(client, headers, doe["id"], "BREEDING")
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# POST /status SOLD — sale capture upgrade
# ---------------------------------------------------------------------------
async def test_sold_derives_paise_exact_price_from_weight_and_rate(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, "sale1@farm.in", "Sale Capture Farm")
    male = await make_animal(
        client,
        headers,
        "SAL-M-1",
        sex="M",
        date_of_birth=iso(today() - timedelta(days=300)),
        purchase_date=iso(today()),
    )
    resp = await change_status(
        client,
        headers,
        male["id"],
        "SOLD",
        sale_weight_kg=25.5,
        sale_price_per_kg=580.25,
        buyer_name="Kishan Traders",
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # 25.5 kg × ₹580.25/kg = ₹14796.375 → ₹14796.38 (ROUND_HALF_UP, paise).
    assert body["sale_price"] == 14796.38
    assert body["sale_weight_kg"] == 25.5
    # buyer_name is a persisted sale fact the wire model does not expose (like
    # the death-audit fields below) — the ledger note below proves it was
    # captured, the row read proves it was stored.
    (weight, buyer) = await fetch_animal_columns(
        male["id"], Animal.sale_weight_kg, Animal.buyer_name
    )
    assert weight == Decimal("25.50")
    assert buyer == "Kishan Traders"
    txn = await sale_transaction(client, headers, male["tag_number"])
    assert txn["amount"] == 14796.38
    assert txn["notes"] == "Sale of SAL-M-1 at 25.5 kg @ ₹580.25/kg to Kishan Traders"


async def test_sold_explicit_price_wins_over_derivation(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client, "sale2@farm.in", "Sale Explicit Farm")
    male = await make_animal(
        client,
        headers,
        "SAL-M-2",
        sex="M",
        date_of_birth=iso(today() - timedelta(days=300)),
    )
    resp = await change_status(
        client,
        headers,
        male["id"],
        "SOLD",
        sale_price=12000,
        sale_weight_kg=25.0,
        sale_price_per_kg=580.0,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["sale_price"] == 12000.0


async def test_sold_derived_price_above_ledger_cap_is_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client, "sale2b@farm.in", "Sale Cap Farm")
    male = await make_animal(
        client,
        headers,
        "SAL-M-2B",
        sex="M",
        date_of_birth=iso(today() - timedelta(days=300)),
    )
    resp = await change_status(
        client,
        headers,
        male["id"],
        "SOLD",
        sale_weight_kg=1000.0,
        sale_price_per_kg=1_000_000_000.0,
    )
    assert resp.status_code == 422, resp.text
    assert "ledger cap" in resp.json()["detail"]


async def test_sold_male_below_weight_window_gets_advisory_note(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, "sale3@farm.in", "Sale Light Farm")
    male = await make_animal(
        client,
        headers,
        "SAL-M-3",
        sex="M",
        date_of_birth=iso(today() - timedelta(days=300)),
    )
    resp = await change_status(
        client, headers, male["id"], "SOLD", sale_price=4000, sale_weight_kg=22.0
    )
    assert resp.status_code == 200, resp.text  # soft window: sale proceeds
    (notes,) = await fetch_animal_columns(male["id"], Animal.status_notes)
    assert notes is not None and "sold below the 24–28 kg market window" in notes


async def test_sold_male_without_dob_gets_age_advisory_and_proceeds(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, "sale4@farm.in", "Sale No Dob Farm")
    male = await make_animal(client, headers, "SAL-M-4", sex="M")
    resp = await change_status(client, headers, male["id"], "SOLD", sale_price=5000)
    assert resp.status_code == 200, resp.text
    (notes,) = await fetch_animal_columns(male["id"], Animal.status_notes)
    assert notes is not None and "age unverifiable — no birth/estimated date" in notes


async def test_sold_male_with_only_estimated_dob_hits_meat_sale_gate(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, "sale5@farm.in", "Sale Est Dob Farm")
    male = await make_animal(
        client,
        headers,
        "SAL-M-5",
        sex="M",
        estimated_dob=iso(today() - timedelta(days=90)),
        purchase_date=iso(today()),
    )
    resp = await change_status(client, headers, male["id"], "SOLD", sale_price=3000)
    assert resp.status_code == 422, resp.text
    assert "meat-sale window" in resp.json()["detail"]


async def test_sold_advisories_compose_with_operator_notes(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client, "sale6@farm.in", "Sale Notes Farm")
    male = await make_animal(client, headers, "SAL-M-6", sex="M")
    resp = await change_status(
        client,
        headers,
        male["id"],
        "SOLD",
        sale_price=4000,
        sale_weight_kg=20.0,
        notes="emergency sale",
    )
    assert resp.status_code == 200, resp.text
    (notes,) = await fetch_animal_columns(male["id"], Animal.status_notes)
    assert notes is not None
    assert notes.startswith("emergency sale")
    assert "age unverifiable — no birth/estimated date" in notes
    assert "sold below the 24–28 kg market window" in notes


# ---------------------------------------------------------------------------
# POST /status DEAD — coded cause, disposal, necropsy
# ---------------------------------------------------------------------------
async def test_dead_records_coded_cause_disposal_and_necropsy(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, "mort1@farm.in", "Mortality Audit Farm")
    doe = await make_animal(client, headers, "MOR-F-1")
    resp = await change_status(
        client,
        headers,
        doe["id"],
        "DEAD",
        mortality_cause="pneumonia after unseasonal rain",
        mortality_cause_code="PNEUMONIA",
        disposal_method="Buried on farm, deep pit with lime",
        necropsy_done=True,
        necropsy_findings="Anteroventral lung consolidation; pleural adhesions.",
    )
    assert resp.status_code == 200, resp.text
    (
        free_text,
        code,
        disposal,
        necropsy_done,
        findings,
    ) = await fetch_animal_columns(
        doe["id"],
        Animal.mortality_cause,
        Animal.mortality_cause_code,
        Animal.disposal_method,
        Animal.necropsy_done,
        Animal.necropsy_findings,
    )
    assert free_text == "pneumonia after unseasonal rain"  # legacy field keeps working
    assert code == "PNEUMONIA"
    assert disposal == "Buried on farm, deep pit with lime"
    assert necropsy_done is True
    assert findings == "Anteroventral lung consolidation; pleural adhesions."


async def test_dead_without_necropsy_persists_false_and_nones(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client, "mort2@farm.in", "Mortality Plain Farm")
    doe = await make_animal(client, headers, "MOR-F-2")
    resp = await change_status(client, headers, doe["id"], "DEAD", mortality_cause_code="UNKNOWN")
    assert resp.status_code == 200, resp.text
    code, disposal, necropsy_done, findings = await fetch_animal_columns(
        doe["id"],
        Animal.mortality_cause_code,
        Animal.disposal_method,
        Animal.necropsy_done,
        Animal.necropsy_findings,
    )
    assert code == "UNKNOWN"
    assert disposal is None and findings is None
    assert necropsy_done is False


@pytest.mark.parametrize(
    ("status", "overrides", "fragment"),
    [
        ("DEAD", {"mortality_cause_code": "OLD_AGE_TYPOS"}, "mortality_cause_code"),
        ("DEAD", {"necropsy_findings": "lung lesions"}, "necropsy_done=true"),
        ("DEAD", {"necropsy_done": True, "disposal_method": "buried"}, None),  # allowed
        ("SOLD", {"sale_price_per_kg": 500.0}, "requires sale_weight_kg"),
        ("CULLED", {"sale_weight_kg": 25.0}, "require SOLD status"),
        ("CULLED", {"sale_price_per_kg": 500.0}, "require SOLD status"),
        ("SOLD", {"mortality_cause_code": "PNEUMONIA"}, "require DEAD status"),
        ("CULLED", {"necropsy_done": True}, "requires DEAD status"),
    ],
)
async def test_status_change_field_coherence_rejections(
    client: httpx.AsyncClient, status: str, overrides: dict, fragment: str | None
) -> None:
    headers = await owner_with_farm(client, "coher@farm.in", "Coherence Farm")
    animal = await make_animal(
        client,
        headers,
        f"COH-{status[:3]}-{sorted(overrides)[0][:6]}",
        sex="F",
        date_of_birth=iso(today() - timedelta(days=400)),
    )
    resp = await change_status(client, headers, animal["id"], status, **overrides)
    if fragment is None:
        assert resp.status_code == 200, resp.text  # necropsy without findings is fine
    else:
        assert resp.status_code == 422, resp.text
        assert fragment in validation_error_text(resp)
