"""REGRESSION SUITE — the app bugs documented here are FIXED.

Each test keeps its STRONG assertion (the behavior the API contract implies)
and now PASSES: int32-overflow ids resolve to the documented 400/404 (never
an asyncpg DataError 500), over-long routes are rejected at the schema (422),
and non-finite floats get a clean sanitized 422 instead of crashing response
serialization. Do not weaken — these guard the fixed behavior.
"""

from datetime import timedelta

import httpx

from app.main import create_app
from app.utils import today

from .conftest import owner_with_farm
from .test_health_extended import (
    get_schedule,
    iso,
    make_animal,
    record_event,
    row_by_name,
)


async def _make_animal(client: httpx.AsyncClient, headers: dict) -> dict:
    resp = await client.post(
        "/api/animals",
        json={
            "tag_number": "A-001",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# FIXED — regression test
# POST /api/health/events with animal_id = 2**62.
# app/schemas/common.py BoundedId allows ids up to 2**62 ("PG bigint is also
# 64-bit"), but the animals.id column is PostgreSQL INTEGER (int32, see
# alembic/versions/ed5efe13a516_initial_schema.py). asyncpg raises
# "value out of int32 range" → unhandled 500. A schema-valid request must
# never 500: expected 400 (no active animals match), actual 500.
async def test_event_animal_id_at_schema_max_should_not_500(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.post(
        "/api/health/events",
        json={"animal_id": 2**62, "type": "VACCINE"},
        headers=headers,
    )
    assert resp.status_code == 400, resp.text


# FIXED — regression test
# GET /api/health/schedule/{animal_id} with a huge path id. The path
# parameter is a plain int with no upper bound; db.get(Animal, 2**62) hits
# the int32 animals.id column → asyncpg "value out of int32 range" → 500.
# Expected 404 (animal not found), actual 500.
async def test_schedule_huge_animal_id_should_404_not_500(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.get(f"/api/health/schedule/{2**62}", headers=headers)
    assert resp.status_code == 404, resp.text


# FIXED — regression test
# GET /api/purchases/{batch_id} with a huge path id — same int32 overflow as
# the schedule endpoint (purchase_batches.id is INTEGER). Expected 404,
# actual 500.
async def test_batch_detail_huge_id_should_404_not_500(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.get(f"/api/purchases/{2**62}", headers=headers)
    assert resp.status_code == 404, resp.text


# FIXED — regression test
# POST /api/health/events with a 21–60 char `route` used to 500:
# HealthEventIn allowed max_length=60 while health_events.route is
# String(20) (app/models.py:519 + initial migration). The schema cap is now
# 20 (SPEC §HealthEvent documents routes like "SC"/"Oral"/"IM" — the column
# was right, the schema was wrong), so this payload is rejected with 422.
# (This test previously asserted 201 — accepting what the column cannot
# hold; corrected to the schema-side rejection.)
async def test_event_route_within_schema_limit_should_not_500(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await _make_animal(client, headers)
    resp = await client.post(
        "/api/health/events",
        json={
            "animal_id": animal["id"],
            "type": "VACCINE",
            "route": "subcutaneous-left-flank",  # 25 chars: beyond the String(20) column
        },
        headers=headers,
    )
    assert resp.status_code == 422, resp.text


# FIXED — regression test
# POST /api/health/events with a non-finite cost (JSON `NaN`). The
# FiniteFloat validator (app/schemas/common.py) correctly rejects it — but
# FastAPI's default RequestValidationError handler then tries to serialize
# the offending input value (`nan`) into the 422 body, which crashes
# json.dumps ("Out of range float values are not JSON compliant") → wire
# status 500. The project's adversarial contract is that malformed input
# never produces a 500. Expected 422, actual 500. (Verified at the HTTP
# layer with raise_app_exceptions=False.)
async def test_event_nan_cost_should_422_not_500(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await _make_animal(client, headers)
    transport = httpx.ASGITransport(app=create_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as probe:
        resp = await probe.post(
            "/api/health/events",
            content=b'{"animal_id": %d, "type": "VACCINE", "cost": NaN}' % animal["id"],
            headers=headers | {"Content-Type": "application/json"},
        )
    assert resp.status_code == 422, resp.text


# FIXED — regression test
# POST /api/purchases/new with `total_price: Infinity` — same serialization
# crash as the NaN cost above (NonNegativeFloat catches it, the 422 response
# body cannot be rendered). Expected 422, actual 500.
async def test_batch_infinite_price_should_422_not_500(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    transport = httpx.ASGITransport(app=create_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as probe:
        resp = await probe.post(
            "/api/purchases/new",
            content=b'{"date": "2026-01-01", "count": 1, "total_price": Infinity}',
            headers=headers | {"Content-Type": "application/json"},
        )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# AUDIT 3-1 — deworming recorded by drug name matches the Deworming template
# ---------------------------------------------------------------------------
# The schedule matcher used to match templates purely by substring over
# product_name + disease_target. The natural deworming entry (type=DEWORMING,
# product "Albendazole", blank target) never contained "deworming", so every
# animal's June/January deworming showed OVERDUE forever. The Deworming
# template now matches on the event TYPE (and the "deworm" stem).
async def test_deworming_by_drug_name_marks_template_done(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, date_of_birth=iso(today() - timedelta(days=400)))
    assert (
        row_by_name(await get_schedule(client, headers, animal["id"]), "Deworming")["status"]
        == "OVERDUE"
    )
    await record_event(
        client, headers, animal_id=animal["id"], type="DEWORMING", product_name="Albendazole"
    )
    row = row_by_name(await get_schedule(client, headers, animal["id"]), "Deworming")
    assert row["last_done"] == iso(today())
    assert row["status"] == "DONE"  # next dose due in 6 months


async def test_deworm_stem_in_free_text_also_matches(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    await record_event(
        client,
        headers,
        animal_id=animal["id"],
        type="DEWORMING",
        notes="",
        product_name="Deworm bolus",
    )
    assert (
        row_by_name(await get_schedule(client, headers, animal["id"]), "Deworming")["status"]
        == "DONE"
    )


# Trade-name vaccines with a blank disease target used to miss their template
# the same way; a data-driven alias list now maps common brands.
async def test_trade_name_vaccine_matches_template(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, date_of_birth=iso(today() - timedelta(days=400)))
    await record_event(
        client, headers, animal_id=animal["id"], type="VACCINE", product_name="Raksha-Triovac"
    )
    fmd = row_by_name(await get_schedule(client, headers, animal["id"]), "FMD")
    assert fmd["last_done"] == iso(today())
    assert fmd["status"] == "DONE"


# ---------------------------------------------------------------------------
# AUDIT 3-5 — a missed booster no longer shows DONE
# ---------------------------------------------------------------------------
# Status only looked at first/last dose + repeat interval: a first-dose-only
# animal showed DONE even with the booster window lapsed. One matching event
# with an overdue booster now reports OVERDUE.
async def test_missed_booster_shows_overdue(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, date_of_birth=iso(today() - timedelta(days=400)))
    # FMD booster is due 3.5 weeks after the first dose; this one is 60 days stale.
    await record_event(
        client,
        headers,
        animal_id=animal["id"],
        type="VACCINE",
        product_name="FMD vaccine",
        date=iso(today() - timedelta(days=60)),
    )
    fmd = row_by_name(await get_schedule(client, headers, animal["id"]), "FMD")
    assert fmd["status"] == "OVERDUE"


async def test_booster_given_shows_done(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, date_of_birth=iso(today() - timedelta(days=400)))
    await record_event(
        client,
        headers,
        animal_id=animal["id"],
        type="VACCINE",
        product_name="FMD vaccine",
        date=iso(today() - timedelta(days=60)),
    )
    # The booster dose itself (second matching event) clears the OVERDUE.
    await record_event(
        client,
        headers,
        animal_id=animal["id"],
        type="VACCINE",
        product_name="FMD vaccine",
        date=iso(today() - timedelta(days=30)),
    )
    fmd = row_by_name(await get_schedule(client, headers, animal["id"]), "FMD")
    assert fmd["status"] == "DONE"  # repeat due 6 months after the last dose


async def test_recent_first_dose_booster_not_yet_due_stays_done(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, date_of_birth=iso(today() - timedelta(days=400)))
    await record_event(
        client, headers, animal_id=animal["id"], type="VACCINE", product_name="FMD vaccine"
    )
    fmd = row_by_name(await get_schedule(client, headers, animal["id"]), "FMD")
    assert fmd["status"] == "DONE"  # booster window is still open
