"""REGRESSION SUITE — the app bugs documented here are FIXED.

Each test keeps its STRONG assertion (the behavior the API contract implies)
and now PASSES: int32-overflow ids resolve to the documented 400/404 (never
an asyncpg DataError 500), over-long routes are rejected at the schema (422),
and non-finite floats get a clean sanitized 422 instead of crashing response
serialization. Do not weaken — these guard the fixed behavior.
"""

from datetime import timedelta

import httpx

from app.db import get_sessionmaker
from app.main import create_app
from app.models import HealthEvent
from app.utils import today

from .conftest import owner_with_farm
from .test_health_extended import (
    get_batch,
    get_schedule,
    iso,
    make_animal,
    make_batch,
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
            "historical_import_reason": "Existing-herd test fixture",
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
# — deworming recorded by drug name matches the Deworming template
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
# — a missed booster no longer shows DONE
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


async def test_authoritative_future_next_due_overrides_stale_booster_alarm(
    client: httpx.AsyncClient,
) -> None:
    """Finding #11: a vet's future date is authoritative, not OVERDUE."""
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, date_of_birth=iso(today() - timedelta(days=400)))
    future_due = today() + timedelta(days=45)
    await record_event(
        client,
        headers,
        animal_id=animal["id"],
        type="VACCINE",
        product_name="FMD vaccine",
        date=iso(today() - timedelta(days=60)),
        next_due_date=iso(future_due),
        schedule_template_name="FMD",
        next_due_authority="Veterinarian instruction VET-2026-08",
    )
    fmd = row_by_name(await get_schedule(client, headers, animal["id"]), "FMD")
    assert fmd["booster_due"] < iso(today())  # the ordinary booster window did lapse
    assert fmd["next_due"] == iso(future_due)
    assert fmd["status"] == "DONE"


async def test_booster_due_keeps_actual_primary_anchor_after_second_dose(
    client: httpx.AsyncClient,
) -> None:
    """Finding #33: recording a booster must not revert to the DOB plan."""
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, date_of_birth=iso(today() - timedelta(days=400)))
    primary_date = today() - timedelta(days=60)
    await record_event(
        client,
        headers,
        animal_id=animal["id"],
        type="VACCINE",
        product_name="FMD vaccine",
        date=iso(primary_date),
    )
    await record_event(
        client,
        headers,
        animal_id=animal["id"],
        type="VACCINE",
        product_name="FMD vaccine",
        date=iso(today() - timedelta(days=30)),
    )
    fmd = row_by_name(await get_schedule(client, headers, animal["id"]), "FMD")
    # FMD's booster_weeks (3.5) rounds half-up to a 25-day interval; date
    # arithmetic would otherwise truncate timedelta(weeks=3.5) down to 24.
    assert fmd["booster_due"] == iso(primary_date + timedelta(days=25))


async def test_batch_only_health_event_serializes_null_animal_id(
    client: httpx.AsyncClient,
) -> None:
    """Finding #30: the response mirrors the DB's valid batch-only shape."""
    headers = await owner_with_farm(client)
    batch = await make_batch(client, headers, count=1, create_animals=False)
    async with get_sessionmaker()() as db:
        event = HealthEvent(
            farm_id=int(headers["X-Farm-Id"]),
            animal_id=None,
            purchase_batch_id=batch["id"],
            date=today(),
            type="VACCINE",
            product_name="Batch certificate import",
        )
        db.add(event)
        await db.commit()
        event_id = event.id

    listed = await client.get("/api/health/events", headers=headers)
    assert listed.status_code == 200, listed.text
    row = next(item for item in listed.json()["events"] if item["id"] == event_id)
    assert row["animal_id"] is None
    assert row["animal_tag"] is None


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


# FIXED — regression test
# `date + timedelta(weeks=template.booster_weeks)` silently drops the
# timedelta's sub-day remainder (date arithmetic only reads whole days), so
# every seeded fractional booster_weeks (FMD/ET/HS/Goat Pox all use 3.5)
# always resolved to a flat 24 days instead of the true 24.5-day interval
# rounded half-up to 25.
async def test_fractional_booster_weeks_rounds_half_up_not_truncated(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, date_of_birth=iso(today() - timedelta(days=400)))
    primary_date = today() - timedelta(days=60)
    await record_event(
        client,
        headers,
        animal_id=animal["id"],
        type="VACCINE",
        product_name="FMD vaccine",
        date=iso(primary_date),
    )
    fmd = row_by_name(await get_schedule(client, headers, animal["id"]), "FMD")
    assert fmd["booster_due"] == iso(primary_date + timedelta(days=25))
    assert fmd["booster_due"] != iso(primary_date + timedelta(weeks=3.5))


# ---------------------------------------------------------------------------
# Audit 2026-08-10 regressions
# ---------------------------------------------------------------------------
# FIXED — regression test
# _HEALTH_TYPE_TO_TX_CATEGORY mapped VITAMIN to MEDICINE while its own
# governing comment groups TREATMENT/FOOTBATH/VITAMIN under VET ("vet
# consultation, hoof care, tonics") and reserves MEDICINE for the actual
# drug/vaccine spend (VACCINE/DEWORMING). Every vitamin/tonic round was
# therefore booked to the wrong P&L bucket: MEDICINE over-reported, VET
# under-reported, with the total unchanged so the drift was silent.
async def test_vitamin_event_cost_books_to_vet_not_medicine(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, tag="VIT-1")
    await record_event(
        client,
        headers,
        animal_id=animal["id"],
        type="VITAMIN",
        product_name="Vitamin AD3E injection",
        cost=500.0,
    )
    fin = (await client.get("/api/finance", headers=headers)).json()
    vet_txns = [t for t in fin["transactions"] if t["category"] == "VET" and t["type"] == "EXPENSE"]
    assert len(vet_txns) == 1
    assert vet_txns[0]["amount"] == 500.0
    assert not any(t["category"] == "MEDICINE" for t in fin["transactions"])


# FIXED — regression test
# POST /api/health/events/preview with scope=batch and purchase_batch_id at
# the BoundedId ceiling (2**62). The no-task batch branch pushed the id
# straight into `Animal.purchase_batch_id == <id>` against an int4 FK column
# → asyncpg "value out of int32 range" → 500, while the writer (line with
# `payload.purchase_batch_id <= MAX_INT32_ID`) and the batch selector both
# guard the same column. Expected the documented 400 (no active animals
# match), actual 500.
async def test_preview_batch_id_at_schema_max_should_400_not_500(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    resp = await client.post(
        "/api/health/events/preview",
        json={"scope": "batch", "purchase_batch_id": 2**62},
        headers=headers,
    )
    assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# — batch scope targets the advertised quarantine set
# ---------------------------------------------------------------------------
# The /purchase-batches selector advertises the batch's ACTIVE QUARANTINE
# count (that is the number the operator picks a batch by), and the
# task-linked path targets exactly that set — but the no-task batch preview
# and write only required status=ACTIVE, so a batch write silently reached
# animals already released to another bucket. Preview and write now share
# the selector's quarantine predicate.
async def _release_one_from_quarantine(
    client: httpx.AsyncClient, headers: dict, animal_id: int
) -> None:
    """Owner history-override move: the only per-animal path out of a
    purchased batch's quarantine without completing the whole protocol."""
    resp = await client.post(
        f"/api/animals/{animal_id}/move",
        json={
            "to_bucket": "FOUNDATION",
            "reason": "Cleared early by veterinarian",
            "history_override": True,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["current_bucket"] == "FOUNDATION"


async def test_batch_preview_targets_only_the_advertised_quarantine_set(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    batch = await make_batch(client, headers, count=3)
    detail = await get_batch(client, headers, batch["id"])
    ids = sorted(a["id"] for a in detail["animals"])
    await _release_one_from_quarantine(client, headers, ids[0])

    options = (await client.get("/api/health/purchase-batches", headers=headers)).json()
    advertised = next(b for b in options["batches"] if b["id"] == batch["id"])
    assert advertised["active_quarantine_animal_count"] == 2

    preview = await client.post(
        "/api/health/events/preview",
        json={"scope": "batch", "purchase_batch_id": batch["id"]},
        headers=headers,
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    # The snapshot must be the advertised quarantine set — not every ACTIVE
    # animal that ever belonged to the batch.
    assert sorted(body["target_animal_ids"]) == ids[1:]
    assert body["target_count"] == advertised["active_quarantine_animal_count"]


async def test_batch_write_rejects_snapshot_containing_released_animal(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    batch = await make_batch(client, headers, count=2)
    detail = await get_batch(client, headers, batch["id"])
    ids = sorted(a["id"] for a in detail["animals"])
    await _release_one_from_quarantine(client, headers, ids[0])

    # A snapshot reviewed before the release still lists both animals; the
    # write must refuse it as stale instead of dosing the released animal.
    resp = await client.post(
        "/api/health/events",
        json={
            "scope": "batch",
            "purchase_batch_id": batch["id"],
            "type": "VACCINE",
            "expected_animal_ids": ids,
        },
        headers=headers,
    )
    assert resp.status_code == 409, resp.text
    assert "stale" in resp.json()["detail"]


# FIXED — regression test
# POST /api/health/restrictions/{id}/clear reset movement_restricted,
# suspected_scheduled_disease and restriction_reason but left
# suspected_disease and authority_notified_at on the animal, so a cleared
# animal's profile still read as an active PPR suspicion already reported to
# the authority. Those columns describe the CURRENT episode only; the
# historical fact stays on the HealthEvent and the restriction actions.
async def test_clearance_resets_suspected_disease_and_authority_notified(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, tag="PPR-1")
    await record_event(
        client,
        headers,
        animal_id=animal["id"],
        type="TREATMENT",
        disease_target="PPR",
        suspected_scheduled_disease=True,
        authority_notified_at=iso(today()),
    )
    profile = (await client.get(f"/api/animals/{animal['id']}", headers=headers)).json()
    held = profile["animal"]
    assert held["suspected_disease"] == "PPR"
    assert held["authority_notified_at"] == iso(today())

    resp = await client.post(
        f"/api/health/restrictions/{animal['id']}/clear",
        json={
            "clearance_reference": "AHD clearance 2026/08-17",
            "expected_restriction_version": held["restriction_version"],
        },
        headers=headers,
    )
    assert resp.status_code == 204, resp.text

    cleared = (await client.get(f"/api/animals/{animal['id']}", headers=headers)).json()["animal"]
    assert cleared["movement_restricted"] is False
    assert cleared["suspected_scheduled_disease"] is False
    assert cleared["suspected_disease"] is None
    assert cleared["authority_notified_at"] is None
    # Clearing the animal's current-state columns is not an erasure: the
    # audit trail keeps the disease target on both restriction actions.
    history = (await client.get(f"/api/health/restrictions/{animal['id']}", headers=headers)).json()
    assert [a["action"] for a in history["actions"]] == ["CLEARED", "PLACED"]
    assert all(a["disease_target"] == "PPR" for a in history["actions"])
