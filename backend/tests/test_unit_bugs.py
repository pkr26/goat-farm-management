"""REGRESSION SUITE — the app bugs documented here are FIXED.

Each test originally FAILED against the app (schema admitted values the DB
column or the SPEC domain cannot hold, and the request died with a 500 in
Postgres). The fixes tightened the schemas to the actual column widths /
SPEC enums, so these payloads are now rejected with 422 (or ValidationError
at the schema layer). Kept as regression guards — do not weaken.

Two fixed bug classes:

A. Schema admitted strings longer than the database column → the request
   passed validation and died in Postgres with a 500 (asyncpg
   StringDataRightTruncation). Fixed by capping the schemas at the column
   widths (app/models.py is the source of truth; columns unchanged), so
   over-long input is now a clean 422. The neighboring boundary tests in
   tests/test_unit_extended.py prove the at-limit length still works.
B. Schema literals accepted values outside the SPEC/domain enum — now
   rejected by the tightened Literals/bounds.
"""

import httpx
import pytest
from pydantic import ValidationError

from app.schemas.breeding import UltrasoundIn
from app.schemas.kidding import KiddingCreateIn
from app.utils import today

from .conftest import owner_with_farm, register

TODAY = today().isoformat()


async def _make_animal(client: httpx.AsyncClient, headers: dict) -> int:
    resp = await client.post(
        "/api/animals",
        json={
            "tag_number": "BUG-1",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# FIXED — regression test
# POST /api/auth/register with a 200-char name used to 500: RegisterIn.name
# had NO length cap while User.name is String(120). The schema is now capped
# at the column width → clean 422. (This test previously asserted 201 — i.e.
# that the API should accept names the column cannot hold; the fix direction
# is schema-side rejection per the column contract, so it now asserts 422.)
async def test_register_name_longer_than_db_column_500s(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register",
        json={"email": "longname@farm.in", "password": "ownerpass123", "name": "N" * 200},
    )
    assert resp.status_code == 422, resp.text


# FIXED — regression test
# POST /api/auth/farms with a 150-char location used to 500: FarmCreateIn
# allowed max_length=200 but Farm.location is String(120). The schema cap is
# now 120 (column unchanged) → 422. (Previously asserted 201; corrected to
# the schema-side rejection, same as the register name above.)
async def test_farm_location_schema_max_exceeds_db_column(client: httpx.AsyncClient) -> None:
    headers = await register(client)
    resp = await client.post(
        "/api/auth/farms",
        json={"name": "Gamma Farm", "location": "L" * 150},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text


# FIXED — regression test
# POST /api/health/events with a 30-char route used to 500: HealthEventIn
# allowed max_length=60 but HealthEvent.route is String(20). The schema cap
# is now 20 (SPEC §HealthEvent documents routes like "SC" / "Oral" / "IM",
# so the 20-char column was right and the 60-char schema cap was wrong) →
# 422. (Previously asserted 201.)
async def test_health_event_route_schema_max_exceeds_db_column(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal_id = await _make_animal(client, headers)
    resp = await client.post(
        "/api/health/events",
        json={
            "scope": "animal",
            "animal_id": animal_id,
            "type": "TREATMENT",
            "route": "R" * 30,
        },
        headers=headers,
    )
    assert resp.status_code == 422, resp.text


# FIXED — regression test
# POST /api/finance/new with a 300-char note used to 500: TransactionIn
# .notes had no length cap while Transaction.notes is String(255). Capped at
# 255 → 422. (Previously asserted 201.)
async def test_finance_notes_unbounded_exceeds_db_column(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.post(
        "/api/finance/new",
        json={
            "date": TODAY,
            "type": "EXPENSE",
            "category": "OTHER",
            "amount": 10.0,
            "notes": "n" * 300,
        },
        headers=headers,
    )
    assert resp.status_code == 422, resp.text


# FIXED — regression test
# POST /api/animals/{id}/weight with a 300-char note used to 500: WeightIn
# .notes had no length cap while WeightRecord.notes is String(255). Capped
# at 255 → 422. (Previously asserted 201.)
async def test_weight_notes_unbounded_exceeds_db_column(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal_id = await _make_animal(client, headers)
    resp = await client.post(
        f"/api/animals/{animal_id}/weight",
        json={"weight_kg": 25.0, "notes": "n" * 300},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text


# FIXED — regression test
# POST /api/animals/{id}/move with a 300-char reason used to 500: MoveIn
# .reason had no length cap while BucketMove.reason is String(255). Capped
# at 255 → 422. (Previously asserted 200.)
async def test_move_reason_unbounded_exceeds_db_column(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal_id = await _make_animal(client, headers)
    resp = await client.post(
        f"/api/animals/{animal_id}/move",
        json={"to_bucket": "RESTING", "reason": "r" * 300},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text


# FIXED — regression test
# POST /api/animals/{id}/status with a 300-char note used to 500:
# StatusChangeIn.notes had no length cap while Animal.status_notes is
# String(255). Capped at 255 → 422. (Previously asserted 200.)
async def test_status_notes_unbounded_exceeds_db_column(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal_id = await _make_animal(client, headers)
    resp = await client.post(
        f"/api/animals/{animal_id}/status",
        json={"new_status": "DEAD", "notes": "n" * 300},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text


# FIXED — regression test
# schemas/kidding.py KiddingEaseStr used to include "CAESAREAN", but SPEC
# §KiddingRecord defines ease as NORMAL | ASSISTED | DIFFICULT and
# models.KiddingEase has exactly those three values. The literal is now
# tightened (and the CAESAREAN→NORMAL coercion in api/kidding.py deleted).
def test_kidding_ease_rejects_caesarean_per_spec() -> None:
    with pytest.raises(ValidationError):
        KiddingCreateIn(
            breeding_record_id=1,
            date=TODAY,
            ease="CAESAREAN",
            kids=[{"sex": "M"}],
        )


# FIXED — regression test
# SPEC §BreedingRecord documents kid_count_detected as "1/2/3 nullable"
# (matching BirthType SINGLE/TWIN/TRIPLET), but UltrasoundIn.kid_count used
# to allow ge=1, le=5. Now capped at le=3.
def test_ultrasound_kid_count_capped_at_3_per_spec() -> None:
    with pytest.raises(ValidationError):
        UltrasoundIn(pregnant=True, kid_count=4)
