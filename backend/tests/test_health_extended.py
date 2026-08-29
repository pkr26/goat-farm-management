"""Extended coverage for the health & purchases modules.

Endpoints under test:
- GET  /api/health/events            — latest 100 events, newest first
- POST /api/health/events            — record event (animal / bucket / batch scope)
- GET  /api/health/schedule/{id}     — per-animal vaccination schedule
- GET  /api/purchases                — list batches (animals_created / open_tasks)
- POST /api/purchases/new            — batch + QUARANTINE stubs + 45-day tasks + expense
- GET  /api/purchases/{batch_id}     — batch detail with tasks & stubbed animals

Domain rules under test: the 45-day quarantine protocol (rest days 1–3 →
deworm day 4 → liver tonic days 5–9 → PPR day 10 → ET+TT day 20 → Goat Pox
day 30 → FMD day 40 → zinc-sulfate footbath + release to FOUNDATION day 45),
deworming every 6 months, seeded vaccine schedule templates, purchase expense
booking as an ANIMAL_PURCHASE transaction.
"""

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import httpx
import pytest
from sqlalchemy import text

from app.db import get_sessionmaker
from app.utils import add_months, today

from .conftest import login, owner_with_farm
from .test_tasks_extended import add_worker, login_user, make_custom_role, worker_headers

WORKER_PW = "workerpass123"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def iso(d: date) -> str:
    return d.isoformat()


async def make_animal(
    client: httpx.AsyncClient,
    headers: dict,
    tag: str = "A-001",
    sex: str = "F",
    bucket: str = "FOUNDATION",
    **overrides: object,
) -> dict:
    payload = {
        "tag_number": tag,
        "sex": sex,
        "source": "PURCHASED",
        "current_bucket": bucket,
    } | overrides
    if payload["source"] == "PURCHASED":
        payload.setdefault("historical_import_reason", "Existing-herd test fixture")
    resp = await client.post("/api/animals", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def make_batch(client: httpx.AsyncClient, headers: dict, **overrides: object) -> dict:
    payload: dict = {
        "date": iso(today()),
        "supplier": "Kurnool Traders",
        "count": 3,
    } | overrides
    resp = await client.post("/api/purchases/new", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def post_event(client: httpx.AsyncClient, headers: dict, **payload: object) -> httpx.Response:
    scope = payload.get("scope", "animal")
    if scope in {"bucket", "batch"} and "expected_animal_ids" not in payload:
        target = {"scope": scope}
        if scope == "bucket" and "bucket" in payload:
            target["bucket"] = payload["bucket"]
        if scope == "batch" and "purchase_batch_id" in payload:
            target["purchase_batch_id"] = payload["purchase_batch_id"]
        preview = await client.post("/api/health/events/preview", json=target, headers=headers)
        if preview.status_code != 200:
            return preview
        payload["expected_animal_ids"] = preview.json()["target_animal_ids"]
    return await client.post("/api/health/events", json=payload, headers=headers)


async def record_event(client: httpx.AsyncClient, headers: dict, **payload: object) -> list[dict]:
    resp = await post_event(client, headers, **payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def list_events(client: httpx.AsyncClient, headers: dict) -> list[dict]:
    resp = await client.get("/api/health/events", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["events"]


async def get_schedule(client: httpx.AsyncClient, headers: dict, animal_id: int) -> dict:
    resp = await client.get(f"/api/health/schedule/{animal_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def row_by_name(schedule: dict, name: str) -> dict:
    return next(r for r in schedule["rows"] if r["template_name"] == name)


async def list_batches(client: httpx.AsyncClient, headers: dict) -> list[dict]:
    resp = await client.get("/api/purchases", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["batches"]


async def get_batch(client: httpx.AsyncClient, headers: dict, batch_id: int) -> dict:
    resp = await client.get(f"/api/purchases/{batch_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def complete_quarantine_prerequisites(
    client: httpx.AsyncClient, headers: dict, detail: dict
) -> None:
    """Complete every recorded prerequisite through its own audited workflow."""
    batch_id = detail["batch"]["id"]
    for task in detail["tasks"]:
        if task["category"] == "BUCKET_MOVE":
            continue
        if task["category"] in {"VACCINE", "DEWORMING"}:
            response = await post_event(
                client,
                headers,
                scope="batch",
                purchase_batch_id=batch_id,
                type=task["category"],
                task_id=task["id"],
            )
        else:
            response = await client.post(f"/api/tasks/{task['id']}/complete", headers=headers)
        expected_status = 201 if task["category"] in {"VACCINE", "DEWORMING"} else 200
        assert response.status_code == expected_status, response.text


async def renumber_task(old_id: int, new_id: int) -> None:
    """Move a task row onto an explicit primary key (int4-ceiling coverage)."""
    async with get_sessionmaker()() as db:
        await db.execute(
            text("UPDATE tasks SET id = :new WHERE id = :old"), {"new": new_id, "old": old_id}
        )
        await db.commit()


async def transactions(client: httpx.AsyncClient, headers: dict) -> list[dict]:
    resp = await client.get("/api/finance", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["transactions"]


async def get_animal(client: httpx.AsyncClient, headers: dict, animal_id: int) -> dict:
    resp = await client.get(f"/api/animals/{animal_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["animal"]


async def mark_dead(client: httpx.AsyncClient, headers: dict, animal_id: int) -> None:
    resp = await client.post(
        f"/api/animals/{animal_id}/status", json={"new_status": "DEAD"}, headers=headers
    )
    assert resp.status_code == 200, resp.text


async def worker_with_role(client: httpx.AsyncClient, owner: dict, code: str, email: str) -> dict:
    """Owner adds a worker with preset role `code`; returns farm-scoped headers."""
    resp = await client.get("/api/team", headers=owner)
    assert resp.status_code == 200, resp.text
    role_id = next(r["id"] for r in resp.json()["roles"] if r["code"] == code)
    resp = await client.post(
        "/api/team/workers",
        json={"name": "Worker", "email": email, "password": WORKER_PW, "role_id": role_id},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    headers = await login(client, email, WORKER_PW)
    return headers | {"X-Farm-Id": owner["X-Farm-Id"]}


# ---------------------------------------------------------------------------
# Health events — happy paths
# ---------------------------------------------------------------------------
async def test_record_event_minimal_defaults(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    events = await record_event(client, headers, animal_id=animal["id"], type="VACCINE")
    assert len(events) == 1
    event = events[0]
    assert event["animal_id"] == animal["id"]
    assert event["animal_tag"] == animal["tag_number"]
    assert event["type"] == "VACCINE"
    assert event["date"] == iso(today())  # blank date means "today" (v1 semantics)
    assert event["purchase_batch_id"] is None
    assert event["cost"] is None
    assert event["product_name"] is None
    assert event["notes"] is None


async def test_record_event_explicit_past_date(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    past = today() - timedelta(days=30)
    events = await record_event(
        client, headers, animal_id=animal["id"], type="VACCINE", date=iso(past)
    )
    assert events[0]["date"] == iso(past)


async def test_record_event_all_types_accepted(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    for event_type in ["VACCINE", "DEWORMING", "TREATMENT", "FOOTBATH", "VITAMIN"]:
        events = await record_event(client, headers, animal_id=animal["id"], type=event_type)
        assert events[0]["type"] == event_type
    recorded = await list_events(client, headers)
    assert len(recorded) == 5


async def test_record_event_full_fields_roundtrip(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    past = today() - timedelta(days=10)
    future = today() + timedelta(days=180)
    events = await record_event(
        client,
        headers,
        animal_id=animal["id"],
        type="DEWORMING",
        date=iso(past),
        product_name="Albendazole",
        disease_target="Deworming",
        dose="5 ml",
        route="Oral",
        vet_name="Dr. Rao",
        cost=150.5,
        next_due_date=iso(future),
        schedule_template_name="Deworming",
        next_due_authority="Farm veterinarian record",
        notes="first round",
    )
    event = events[0]
    assert event["date"] == iso(past)
    assert event["product_name"] == "Albendazole"
    assert event["disease_target"] == "Deworming"
    assert event["dose"] == "5 ml"
    assert event["route"] == "Oral"
    assert event["vet_name"] == "Dr. Rao"
    assert event["cost"] == 150.5
    assert event["next_due_date"] == iso(future)
    assert event["notes"] == "first round"
    # round-trips through the list endpoint unchanged
    listed = await list_events(client, headers)
    assert listed[0]["product_name"] == "Albendazole"
    assert listed[0]["animal_tag"] == animal["tag_number"]


async def test_record_event_provenance_fields_roundtrip(client: httpx.AsyncClient) -> None:
    """Every statutory provenance column the route forwards must round-trip.

    Regression: the record_health_event call forwarded product_lot, expiry,
    validity, certificate, official tag, administered_by and the isolation date
    unasserted, so quietly storing NULL for any of them was invisible.
    """
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, tag="PROV-1")
    event_date = today() - timedelta(days=2)
    events = await record_event(
        client,
        headers,
        animal_id=animal["id"],
        date=iso(event_date),
        type="VACCINE",
        product_name="Raksha-PPR",
        disease_target="PPR",
        schedule_template_name="PPR",
        next_due_date=iso(event_date + timedelta(days=365)),
        next_due_authority="AHD circular 12/2026",
        product_lot="LOT-7781",
        product_manufactured_on=iso(event_date - timedelta(days=30)),
        product_expires_on=iso(event_date + timedelta(days=400)),
        vaccine_valid_until=iso(event_date + timedelta(days=390)),
        certificate_number="CERT-55",
        official_tag_number="IN-9001",
        administered_by="Paravet Rao",
        suspected_scheduled_disease=True,
        authority_notified_at=iso(event_date),
        isolation_started_at=iso(event_date),
    )
    event = events[0]
    assert event["next_due_authority"] == "AHD circular 12/2026"
    assert event["product_lot"] == "LOT-7781"
    assert event["product_manufactured_on"] == iso(event_date - timedelta(days=30))
    assert event["product_expires_on"] == iso(event_date + timedelta(days=400))
    assert event["vaccine_valid_until"] == iso(event_date + timedelta(days=390))
    assert event["certificate_number"] == "CERT-55"
    assert event["official_tag_number"] == "IN-9001"
    assert event["administered_by"] == "Paravet Rao"
    assert event["authority_notified_at"] == iso(event_date)
    assert event["isolation_started_at"] == iso(event_date)
    listed = await list_events(client, headers)
    assert listed[0]["certificate_number"] == "CERT-55"
    assert listed[0]["isolation_started_at"] == iso(event_date)


async def test_record_event_omitted_provenance_stays_null(client: httpx.AsyncClient) -> None:
    """Omitted provenance persists as NULL — never as a fabricated literal."""
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, tag="PROV-2")
    events = await record_event(client, headers, animal_id=animal["id"], type="VACCINE")
    event = events[0]
    assert event["next_due_authority"] is None
    assert event["product_lot"] is None
    assert event["certificate_number"] is None
    assert event["official_tag_number"] is None
    assert event["administered_by"] is None
    assert event["product_expires_on"] is None
    assert event["vaccine_valid_until"] is None
    assert event["isolation_started_at"] is None


async def test_record_event_strips_whitespace(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    events = await record_event(
        client,
        headers,
        animal_id=animal["id"],
        type="VACCINE",
        product_name="  Ivermectin  ",
        vet_name="\tDr. Khan ",
        notes="  note with spaces  ",
    )
    assert events[0]["product_name"] == "Ivermectin"
    assert events[0]["vet_name"] == "Dr. Khan"
    assert events[0]["notes"] == "note with spaces"


async def test_record_event_blank_strings_become_null(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    events = await record_event(
        client,
        headers,
        animal_id=animal["id"],
        type="VACCINE",
        product_name="   ",
        disease_target="",
        dose="",
        route="",
        vet_name="",
        notes="  ",
    )
    event = events[0]
    assert event["product_name"] is None
    assert event["disease_target"] is None
    assert event["dose"] is None
    assert event["route"] is None
    assert event["vet_name"] is None
    assert event["notes"] is None


async def test_scheduled_disease_rejects_whitespace_target_before_mutation(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    response = await client.post(
        "/api/health/events",
        json={
            "scope": "animal",
            "animal_id": animal["id"],
            "type": "TREATMENT",
            "suspected_scheduled_disease": True,
            "disease_target": "   ",
        },
        headers=headers,
    )
    assert response.status_code == 422, response.text
    assert await list_events(client, headers) == []
    profile = await client.get(f"/api/animals/{animal['id']}", headers=headers)
    assert profile.status_code == 200
    assert profile.json()["animal"]["movement_restricted"] is False


@pytest.mark.parametrize(
    ("template", "authority"),
    [("   ", "Farm veterinarian record"), ("Deworming", "   ")],
)
async def test_next_due_rejects_whitespace_provenance(
    client: httpx.AsyncClient, template: str, authority: str
) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    response = await client.post(
        "/api/health/events",
        json={
            "scope": "animal",
            "animal_id": animal["id"],
            "date": iso(today()),
            "type": "DEWORMING",
            "next_due_date": iso(today() + timedelta(days=30)),
            "schedule_template_name": template,
            "next_due_authority": authority,
        },
        headers=headers,
    )
    assert response.status_code == 422, response.text
    assert await list_events(client, headers) == []


async def test_record_event_zero_cost_stored_as_zero(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    events = await record_event(client, headers, animal_id=animal["id"], type="VACCINE", cost=0)
    assert events[0]["cost"] == 0.0  # explicit ₹0 is not NULL


async def test_record_event_huge_finite_cost(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    events = await record_event(
        client,
        headers,
        animal_id=animal["id"],
        type="TREATMENT",
        cost=1e9,  # the ₹1e9 money cap
    )
    assert events[0]["cost"] == 1e9
    resp = await post_event(client, headers, animal_id=animal["id"], type="TREATMENT", cost=1e15)
    assert resp.status_code == 422  # beyond the cap — B2 float-overflow bound


async def test_record_event_far_past_date_accepted(client: httpx.AsyncClient) -> None:
    """HealthEventIn has no lower year bound (unlike purchases); a 2001 entry
    is valid backfill of historical records."""
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    events = await record_event(
        client, headers, animal_id=animal["id"], type="VACCINE", date="2001-06-15"
    )
    assert events[0]["date"] == "2001-06-15"


async def test_record_event_unicode_and_emoji(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    events = await record_event(
        client,
        headers,
        animal_id=animal["id"],
        type="VITAMIN",
        product_name="विटामिन AD3E 🐐💉",
        vet_name="Dr. पवन",
    )
    assert events[0]["product_name"] == "विटामिन AD3E 🐐💉"
    listed = await list_events(client, headers)
    assert listed[0]["vet_name"] == "Dr. पवन"


async def test_record_event_sql_injection_string_stored_literally(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    payload = "'; DROP TABLE animals; --"
    events = await record_event(
        client, headers, animal_id=animal["id"], type="TREATMENT", product_name=payload
    )
    assert events[0]["product_name"] == payload
    # nothing was dropped: the animal and the event list are still there
    assert (await get_animal(client, headers, animal["id"]))["tag_number"] == animal["tag_number"]
    assert len(await list_events(client, headers)) == 1


async def test_record_event_notes_length_is_bounded(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    notes = "x" * 4_000
    events = await record_event(
        client, headers, animal_id=animal["id"], type="TREATMENT", notes=notes
    )
    assert events[0]["notes"] == notes
    resp = await post_event(
        client,
        headers,
        animal_id=animal["id"],
        type="TREATMENT",
        notes="x" * 4_001,
    )
    assert resp.status_code == 422


async def test_record_event_unknown_extra_field_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    resp = await post_event(
        client,
        headers,
        animal_id=animal["id"],
        type="VACCINE",
        hacker_field="pwned",
        farm_id=999999,
    )
    assert resp.status_code == 422, resp.text
    assert await list_events(client, headers) == []


async def test_list_events_newest_first_by_date_then_id(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    old = today() - timedelta(days=20)
    await record_event(
        client, headers, animal_id=animal["id"], type="VACCINE", date=iso(old), product_name="OLD"
    )
    await record_event(client, headers, animal_id=animal["id"], type="VACCINE", product_name="NEW1")
    await record_event(client, headers, animal_id=animal["id"], type="VACCINE", product_name="NEW2")
    events = await list_events(client, headers)
    # today's events first (newest id first within the same date), then the old one
    assert [e["product_name"] for e in events] == ["NEW2", "NEW1", "OLD"]


async def test_list_events_capped_at_100(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    batch = await make_batch(client, headers, count=105)
    events = await record_event(
        client,
        headers,
        scope="batch",
        purchase_batch_id=batch["id"],
        type="DEWORMING",
    )
    assert len(events) == 105
    listed = await list_events(client, headers)
    assert len(listed) == 100  # endpoint keeps the latest 100


# ---------------------------------------------------------------------------
# Health events — scopes & cost splitting
# ---------------------------------------------------------------------------
async def test_bucket_scope_creates_one_event_per_active_animal(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    f1 = await make_animal(client, headers, tag="F-1")
    f2 = await make_animal(client, headers, tag="F-2")
    f3 = await make_animal(client, headers, tag="F-3")
    breeding_dob = today() - timedelta(days=800)
    await make_animal(
        client,
        headers,
        tag="BR-1",
        bucket="BREEDING",
        date_of_birth=iso(breeding_dob),
        weight_kg=26,
        weight_date=iso(breeding_dob),
    )
    events = await record_event(
        client, headers, scope="bucket", bucket="FOUNDATION", type="FOOTBATH"
    )
    assert len(events) == 3
    assert {e["animal_id"] for e in events} == {f1["id"], f2["id"], f3["id"]}
    assert all(e["type"] == "FOOTBATH" for e in events)
    # one row per animal in the log too
    assert len(await list_events(client, headers)) == 3


async def test_bucket_scope_cost_split_evenly_with_remainder(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for tag in ["C-1", "C-2", "C-3"]:
        await make_animal(client, headers, tag=tag)
    events = await record_event(
        client, headers, scope="bucket", bucket="FOUNDATION", type="DEWORMING", cost=100
    )
    costs = sorted(e["cost"] for e in events)
    # 100 / 3 = 33.33 each; the first animal absorbs the 1-paisa remainder
    assert costs == [33.33, 33.33, 33.34]
    assert round(sum(costs), 2) == 100.0  # the split sums back to the total


async def test_bucket_scope_tiny_cost_never_creates_a_negative_remainder(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    for tag in ["TINY-1", "TINY-2", "TINY-3", "TINY-4"]:
        await make_animal(client, headers, tag=tag)
    events = await record_event(
        client,
        headers,
        scope="bucket",
        bucket="FOUNDATION",
        type="DEWORMING",
        cost=0.02,
    )
    costs = sorted(e["cost"] for e in events)
    assert costs == [0.0, 0.0, 0.01, 0.01]
    assert round(sum(costs), 2) == 0.02


async def test_bucket_scope_no_cost_leaves_nulls(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for tag in ["N-1", "N-2"]:
        await make_animal(client, headers, tag=tag)
    events = await record_event(
        client, headers, scope="bucket", bucket="FOUNDATION", type="VACCINE"
    )
    assert all(e["cost"] is None for e in events)


async def test_batch_scope_targets_batch_animals_and_links_batch(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    batch = await make_batch(client, headers, count=3)
    detail = await get_batch(client, headers, batch["id"])
    batch_animal_ids = {a["id"] for a in detail["animals"]}
    events = await record_event(
        client,
        headers,
        scope="batch",
        purchase_batch_id=batch["id"],
        type="DEWORMING",
        product_name="Albendazole + Ivermectin",
    )
    assert len(events) == 3
    assert {e["animal_id"] for e in events} == batch_animal_ids
    assert all(e["purchase_batch_id"] == batch["id"] for e in events)
    assert all(e["animal_tag"] is not None for e in events)


async def test_batch_scope_ends_with_quarantine_release(
    client: httpx.AsyncClient,
) -> None:
    """Batch scope means the batch's ACTIVE QUARANTINE animals — the exact
    set the batch picker advertises and the preview snapshots. Released
    (FOUNDATION) animals keep purchase_batch_id for provenance but leave
    that set; they stay reachable through the bucket they now occupy."""
    headers = await owner_with_farm(client)
    batch = await make_batch(client, headers, count=2, date=iso(today() - timedelta(days=50)))
    detail = await get_batch(client, headers, batch["id"])
    await complete_quarantine_prerequisites(client, headers, detail)
    footbath = next(t for t in detail["tasks"] if t["category"] == "BUCKET_MOVE")
    resp = await client.post(f"/api/tasks/{footbath['id']}/complete", headers=headers)
    assert resp.status_code == 200, resp.text
    detail = await get_batch(client, headers, batch["id"])
    assert all(a["current_bucket"] == "FOUNDATION" for a in detail["animals"])
    resp = await post_event(
        client, headers, scope="batch", purchase_batch_id=batch["id"], type="VACCINE"
    )
    assert resp.status_code == 400, resp.text
    events = await record_event(
        client, headers, scope="bucket", bucket="FOUNDATION", type="VACCINE"
    )
    assert len(events) == 2


async def test_scope_excludes_dead_animals(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    live = await make_animal(client, headers, tag="LIVE-1")
    dead = await make_animal(client, headers, tag="DEAD-1")
    await mark_dead(client, headers, dead["id"])
    events = await record_event(
        client, headers, scope="bucket", bucket="FOUNDATION", type="VACCINE"
    )
    assert [e["animal_id"] for e in events] == [live["id"]]


async def test_scope_animal_on_dead_animal_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    await mark_dead(client, headers, animal["id"])
    resp = await post_event(client, headers, animal_id=animal["id"], type="TREATMENT")
    assert resp.status_code == 400, resp.text
    assert "No active animals" in resp.json()["detail"]


async def test_scope_animal_nonexistent_id_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await post_event(client, headers, animal_id=999999, type="VACCINE")
    assert resp.status_code == 400, resp.text


async def test_scope_bucket_with_no_animals_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await post_event(client, headers, scope="bucket", bucket="DELIVERY", type="VACCINE")
    assert resp.status_code == 400, resp.text


async def test_scope_batch_nonexistent_batch_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await post_event(
        client, headers, scope="batch", purchase_batch_id=999999, type="DEWORMING"
    )
    assert resp.status_code == 400, resp.text


async def test_scope_batch_without_animals_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    batch = await make_batch(client, headers, count=2, create_animals=False)
    resp = await post_event(
        client, headers, scope="batch", purchase_batch_id=batch["id"], type="DEWORMING"
    )
    assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# Health events — validation (422)
# ---------------------------------------------------------------------------
async def test_event_missing_type_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    resp = await post_event(client, headers, animal_id=animal["id"])
    assert resp.status_code == 422


async def test_event_null_type_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    resp = await post_event(client, headers, animal_id=animal["id"], type=None)
    assert resp.status_code == 422


async def test_event_empty_body_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await post_event(client, headers)
    assert resp.status_code == 422


async def test_event_invalid_type_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    for bad in ["vaccine", "ANTIBIOTIC", "Vaccine ", "", "IMMUNIZATION"]:
        resp = await post_event(client, headers, animal_id=animal["id"], type=bad)
        assert resp.status_code == 422, bad


async def test_event_invalid_scope_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    resp = await post_event(client, headers, scope="herd", animal_id=animal["id"], type="VACCINE")
    assert resp.status_code == 422


async def test_event_animal_scope_without_animal_id_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await post_event(client, headers, scope="animal", type="VACCINE")
    assert resp.status_code == 422
    assert "animal_id" in resp.text


async def test_event_bucket_scope_without_bucket_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await post_event(client, headers, scope="bucket", type="VACCINE")
    assert resp.status_code == 422
    assert "bucket" in resp.text


async def test_event_batch_scope_without_batch_id_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await post_event(client, headers, scope="batch", type="VACCINE")
    assert resp.status_code == 422
    assert "purchase_batch_id" in resp.text


async def test_event_rejects_bucket_on_animal_scope_instead_of_ignoring_it(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    response = await post_event(
        client,
        headers,
        scope="animal",
        animal_id=animal["id"],
        bucket="QUARANTINE",
        type="VACCINE",
    )
    assert response.status_code == 422, response.text
    assert "bucket only applies to bucket scope" in response.text
    assert await list_events(client, headers) == []


async def test_event_invalid_bucket_literal_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await post_event(client, headers, scope="bucket", bucket="PASTURE", type="VACCINE")
    assert resp.status_code == 422


async def test_event_animal_id_boundaries_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for bad_id in [0, -1, 2**62 + 1]:
        resp = await post_event(client, headers, animal_id=bad_id, type="VACCINE")
        assert resp.status_code == 422, bad_id


async def test_event_animal_id_wrong_types_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for bad_id in ["abc", 1.5, [1], {"id": 1}]:
        resp = await post_event(client, headers, animal_id=bad_id, type="VACCINE")
        assert resp.status_code == 422, bad_id


async def test_event_animal_id_just_below_db_range_is_400(client: httpx.AsyncClient) -> None:
    """An in-int32-range id that matches no animal passes the schema → 400."""
    headers = await owner_with_farm(client)
    resp = await post_event(client, headers, animal_id=2**31 - 1, type="VACCINE")
    assert resp.status_code == 400, resp.text


async def test_event_future_date_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    future = today() + timedelta(days=1)
    resp = await post_event(
        client, headers, animal_id=animal["id"], type="VACCINE", date=iso(future)
    )
    assert resp.status_code == 422


async def test_event_malformed_date_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    for bad in ["31-12-2026", "2026-13-01", "not-a-date", "2026/01/01"]:
        resp = await post_event(client, headers, animal_id=animal["id"], type="VACCINE", date=bad)
        assert resp.status_code == 422, bad


async def test_event_negative_cost_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    for bad_cost in [-1, -0.01, -1e9]:
        resp = await post_event(
            client, headers, animal_id=animal["id"], type="VACCINE", cost=bad_cost
        )
        assert resp.status_code == 422, bad_cost


async def test_event_product_name_too_long_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    resp = await post_event(
        client, headers, animal_id=animal["id"], type="VACCINE", product_name="p" * 121
    )
    assert resp.status_code == 422


async def test_event_product_name_max_length_ok(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    events = await record_event(
        client, headers, animal_id=animal["id"], type="VACCINE", product_name="p" * 120
    )
    assert events[0]["product_name"] == "p" * 120


async def test_event_disease_target_too_long_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    resp = await post_event(
        client, headers, animal_id=animal["id"], type="VACCINE", disease_target="d" * 121
    )
    assert resp.status_code == 422


async def test_event_dose_too_long_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    resp = await post_event(client, headers, animal_id=animal["id"], type="VACCINE", dose="d" * 61)
    assert resp.status_code == 422


async def test_event_route_too_long_for_schema_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    resp = await post_event(client, headers, animal_id=animal["id"], type="VACCINE", route="r" * 61)
    assert resp.status_code == 422


async def test_event_vet_name_too_long_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    resp = await post_event(
        client, headers, animal_id=animal["id"], type="VACCINE", vet_name="v" * 121
    )
    assert resp.status_code == 422


async def test_event_malformed_next_due_date_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    resp = await post_event(
        client, headers, animal_id=animal["id"], type="VACCINE", next_due_date="next week"
    )
    assert resp.status_code == 422


async def test_event_next_due_far_future_accepted(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    events = await record_event(
        client,
        headers,
        animal_id=animal["id"],
        type="VACCINE",
        next_due_date="2099-01-01",
        schedule_template_name="FMD",
        next_due_authority="Farm veterinarian record",
    )
    assert events[0]["next_due_date"] == "2099-01-01"


async def test_omitted_event_date_validates_followup_against_farm_today(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)

    def farm_today(timezone_name: str) -> date:
        assert timezone_name == "Asia/Kolkata"
        return date(2099, 1, 2)

    monkeypatch.setattr("app.api.health.today", farm_today)
    monkeypatch.setattr("app.services.chronology.today", farm_today)
    response = await post_event(
        client,
        headers,
        animal_id=animal["id"],
        type="VACCINE",
        next_due_date="2099-01-01",
        schedule_template_name="FMD",
        next_due_authority="Farm veterinarian record",
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "next_due_date must be after the health event date"


async def test_omitted_event_date_enforces_withdrawal_ceiling(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The farm-local default date must not bypass the immutable withdrawal cap."""
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    farm_today = date(2099, 1, 2)

    def current_business_date(timezone_name: str) -> date:
        assert timezone_name == "Asia/Kolkata"
        return farm_today

    monkeypatch.setattr("app.api.health.today", current_business_date)
    monkeypatch.setattr("app.services.chronology.today", current_business_date)

    rejected = await post_event(
        client,
        headers,
        animal_id=animal["id"],
        type="TREATMENT",
        withdrawal_until=iso(farm_today + timedelta(days=731)),
    )
    assert rejected.status_code == 422, rejected.text
    assert "730 days" in rejected.json()["detail"]
    assert rejected.json()["detail"] == (
        "withdrawal_until cannot be more than 730 days after the health event date"
    )

    accepted = await post_event(
        client,
        headers,
        animal_id=animal["id"],
        type="TREATMENT",
        withdrawal_until=iso(farm_today + timedelta(days=730)),
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()[0]["withdrawal_until"] == iso(farm_today + timedelta(days=730))


async def test_next_due_date_equal_to_the_resolved_event_date_is_rejected(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The follow-up guard is `<=`: an equal date is a 422, not a DB abort."""
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    farm_today = date(2099, 1, 2)

    def current_business_date(timezone_name: str) -> date:
        assert timezone_name == "Asia/Kolkata"
        return farm_today

    monkeypatch.setattr("app.api.health.today", current_business_date)
    monkeypatch.setattr("app.services.chronology.today", current_business_date)
    response = await post_event(
        client,
        headers,
        animal_id=animal["id"],
        type="VACCINE",
        next_due_date=iso(farm_today),
        schedule_template_name="FMD",
        next_due_authority="Farm veterinarian record",
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "next_due_date must be after the health event date"


async def test_product_expiry_before_the_resolved_event_date_is_rejected(
    client: httpx.AsyncClient,
) -> None:
    """With `date` omitted the endpoint owns the product-expiry guard."""
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, tag="EXPIRY-1")
    response = await post_event(
        client,
        headers,
        animal_id=animal["id"],
        type="TREATMENT",
        product_expires_on=iso(today() - timedelta(days=1)),
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "product expiry cannot predate the health event"
    assert await list_events(client, headers) == []


async def test_product_expiring_on_the_event_date_is_accepted(client: httpx.AsyncClient) -> None:
    """A product expiring the day it is administered is legal (strict `<`)."""
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, tag="EXPIRY-2")
    events = await record_event(
        client,
        headers,
        animal_id=animal["id"],
        type="TREATMENT",
        product_expires_on=iso(today()),
    )
    assert events[0]["product_expires_on"] == iso(today())


async def test_withdrawal_until_before_the_resolved_event_date_is_rejected(
    client: httpx.AsyncClient,
) -> None:
    """With `date` omitted the endpoint owns the withdrawal lower bound."""
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, tag="WDRAW-1")
    response = await post_event(
        client,
        headers,
        animal_id=animal["id"],
        type="TREATMENT",
        withdrawal_until=iso(today() - timedelta(days=1)),
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "withdrawal_until cannot be before the health event date"
    assert await list_events(client, headers) == []


async def test_withdrawal_until_on_the_event_date_is_accepted(client: httpx.AsyncClient) -> None:
    """A zero-day withdrawal ending on the treatment date is legal (strict `<`)."""
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, tag="WDRAW-2")
    events = await record_event(
        client,
        headers,
        animal_id=animal["id"],
        type="TREATMENT",
        withdrawal_until=iso(today()),
    )
    assert events[0]["withdrawal_until"] == iso(today())


async def test_selected_schedule_template_must_match_recorded_disease_target(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    response = await post_event(
        client,
        headers,
        animal_id=animal["id"],
        type="VACCINE",
        schedule_template_name="PPR",
        disease_target="FMD",
    )
    assert response.status_code == 422
    assert response.json()["detail"] == (
        "Disease target does not match the selected schedule template"
    )


async def test_event_task_id_boundaries_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    for bad_id in [0, -3, 2**62 + 1]:
        resp = await post_event(
            client, headers, animal_id=animal["id"], type="VACCINE", task_id=bad_id
        )
        assert resp.status_code == 422, bad_id


# ---------------------------------------------------------------------------
# Health form task linking (task_id completes VACCINE/DEWORMING duties)
# ---------------------------------------------------------------------------
async def _backdated_batch_with_tasks(
    client: httpx.AsyncClient, headers: dict, days: int = 50, count: int = 2
) -> dict:
    """A batch old enough that every quarantine duty is already due."""
    batch = await make_batch(client, headers, count=count, date=iso(today() - timedelta(days=days)))
    return await get_batch(client, headers, batch["id"])


async def test_event_completes_linked_vaccine_task(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    detail = await _backdated_batch_with_tasks(client, headers)
    ppr_task = next(t for t in detail["tasks"] if "PPR" in t["title"])
    assert ppr_task["status"] == "PENDING"
    events = await record_event(
        client,
        headers,
        scope="batch",
        purchase_batch_id=detail["batch"]["id"],
        type="VACCINE",
        product_name="PPR vaccine",
        task_id=ppr_task["id"],
    )
    assert len(events) == 2
    detail = await get_batch(client, headers, detail["batch"]["id"])
    done = next(t for t in detail["tasks"] if t["id"] == ppr_task["id"])
    assert done["status"] == "DONE"
    assert done["completed_by_id"] is not None
    assert done["completed_at"] is not None


async def test_event_completes_linked_deworming_task(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    detail = await _backdated_batch_with_tasks(client, headers)
    deworm_task = next(t for t in detail["tasks"] if t["category"] == "DEWORMING")
    await record_event(
        client,
        headers,
        scope="batch",
        purchase_batch_id=detail["batch"]["id"],
        type="DEWORMING",
        product_name="Albendazole",
        task_id=deworm_task["id"],
    )
    batch = await list_batches(client, headers)
    assert batch[0]["open_tasks"] == 7  # 8 protocol duties minus the completed one


async def test_linked_deworming_duty_validates_target_and_files_the_deworming_template(
    client: httpx.AsyncClient,
) -> None:
    """A DEWORMING duty must supply its own category to the template helpers.

    Regression: dropping `task.category` from target_matches_task /
    template_name_for_task let a PPR-targeted event close the deworming duty
    and filed the accepted event with no template and a blank target.
    """
    headers = await owner_with_farm(client)
    detail = await _backdated_batch_with_tasks(client, headers, days=50, count=1)
    deworm = next(t for t in detail["tasks"] if t["category"] == "DEWORMING")
    wrong = await post_event(
        client,
        headers,
        scope="batch",
        purchase_batch_id=detail["batch"]["id"],
        type="DEWORMING",
        disease_target="PPR",
        task_id=deworm["id"],
    )
    assert wrong.status_code == 422, wrong.text
    assert wrong.json()["detail"] == "Disease target does not match the linked task"

    events = await record_event(
        client,
        headers,
        scope="batch",
        purchase_batch_id=detail["batch"]["id"],
        type="DEWORMING",
        product_name="Albendazole",
        task_id=deworm["id"],
    )
    assert events[0]["schedule_template_name"] == "Deworming"
    assert events[0]["disease_target"] == "Deworming"
    refreshed = await get_batch(client, headers, detail["batch"]["id"])
    assert next(t for t in refreshed["tasks"] if t["id"] == deworm["id"])["status"] == "DONE"


async def test_deworming_duty_canonicalises_a_blank_disease_target(
    client: httpx.AsyncClient,
) -> None:
    """A blank target on a linked deworming duty is back-filled as "Deworming".

    Regression: canonical_target_for_task called without the task's category
    fell through the vaccine alias scan and stored NULL on every event row.
    """
    headers = await owner_with_farm(client)
    detail = await _backdated_batch_with_tasks(client, headers)
    deworm_task = next(t for t in detail["tasks"] if t["category"] == "DEWORMING")
    events = await record_event(
        client,
        headers,
        scope="batch",
        purchase_batch_id=detail["batch"]["id"],
        type="DEWORMING",
        product_name="Albendazole",
        task_id=deworm_task["id"],
    )
    assert [event["disease_target"] for event in events] == ["Deworming", "Deworming"]
    refreshed = await get_batch(client, headers, detail["batch"]["id"])
    assert next(t for t in refreshed["tasks"] if t["id"] == deworm_task["id"])["status"] == "DONE"


async def test_linked_future_health_task_cannot_be_completed_early(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    batch = await make_batch(client, headers, count=1, date=iso(today()))
    detail = await get_batch(client, headers, batch["id"])
    future = next(task for task in detail["tasks"] if "PPR" in task["title"])
    response = await post_event(
        client,
        headers,
        scope="batch",
        purchase_batch_id=batch["id"],
        date=iso(today()),
        type="VACCINE",
        product_name="PPR vaccine",
        task_id=future["id"],
    )
    assert response.status_code == 409, response.text
    assert "not due" in response.json()["detail"]
    assert response.json()["detail"] == "This linked health duty is not due yet"
    refreshed = await get_batch(client, headers, batch["id"])
    assert (
        next(task for task in refreshed["tasks"] if task["id"] == future["id"])["status"]
        == "PENDING"
    )
    assert await list_events(client, headers) == []


async def test_a_linked_task_at_the_int32_ceiling_still_completes(
    client: httpx.AsyncClient,
) -> None:
    """task_id == 2**31-1 is a legal int4 key: the ceiling guard is `<=`."""
    headers = await owner_with_farm(client)
    detail = await _backdated_batch_with_tasks(client, headers, days=12, count=1)
    ppr_task = next(t for t in detail["tasks"] if "PPR" in t["title"])
    ceiling_id = 2**31 - 1
    await renumber_task(ppr_task["id"], ceiling_id)
    response = await post_event(
        client,
        headers,
        scope="batch",
        purchase_batch_id=detail["batch"]["id"],
        expected_animal_ids=[detail["animals"][0]["id"]],
        type="VACCINE",
        product_name="PPR vaccine",
        task_id=ceiling_id,
    )
    assert response.status_code == 201, response.text
    refreshed = await get_batch(client, headers, detail["batch"]["id"])
    assert next(t for t in refreshed["tasks"] if t["id"] == ceiling_id)["status"] == "DONE"


async def test_a_linked_batch_task_at_the_int32_ceiling_still_rejects_a_subset(
    client: httpx.AsyncClient,
) -> None:
    """The stronger linked-batch snapshot rule still applies at the int4 ceiling."""
    headers = await owner_with_farm(client)
    detail = await _backdated_batch_with_tasks(client, headers, days=12, count=2)
    ppr_task = next(t for t in detail["tasks"] if "PPR" in t["title"])
    ceiling_id = 2**31 - 1
    await renumber_task(ppr_task["id"], ceiling_id)
    subset = sorted(animal["id"] for animal in detail["animals"])[:1]
    response = await post_event(
        client,
        headers,
        scope="batch",
        purchase_batch_id=detail["batch"]["id"],
        expected_animal_ids=subset,
        type="VACCINE",
        product_name="PPR vaccine",
        task_id=ceiling_id,
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == (
        "Reviewed target snapshot does not match the linked batch's active quarantine animals"
    )
    refreshed = await get_batch(client, headers, detail["batch"]["id"])
    assert next(t for t in refreshed["tasks"] if t["id"] == ceiling_id)["status"] == "PENDING"
    assert await list_events(client, headers) == []


async def test_manual_health_category_cannot_forge_a_workflow_task(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, tag="FUTURE-MANUAL")
    duty = await client.post(
        "/api/tasks",
        json={
            "title": "Future vaccine",
            "due_date": iso(today() + timedelta(days=5)),
            "category": "VACCINE",
            "animal_id": animal["id"],
        },
        headers=headers,
    )
    assert duty.status_code == 422, duty.text
    assert await list_events(client, headers) == []


async def test_health_event_chronology_and_manufacture_date_enforced(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    purchase_date = today() - timedelta(days=5)
    animal = await make_animal(
        client,
        headers,
        tag="CHRON-1",
        purchase_date=iso(purchase_date),
        estimated_dob=iso(today() - timedelta(days=500)),
    )
    before_purchase = await post_event(
        client,
        headers,
        animal_id=animal["id"],
        date=iso(purchase_date - timedelta(days=1)),
        type="TREATMENT",
    )
    assert before_purchase.status_code == 422, before_purchase.text
    assert before_purchase.json()["detail"] == (
        f"Health event cannot predate {animal['tag_number']}'s recorded purchase date"
    )
    after_manufacture = await post_event(
        client,
        headers,
        animal_id=animal["id"],
        date=iso(today()),
        type="TREATMENT",
        product_manufactured_on=iso(today() + timedelta(days=1)),
    )
    assert after_manufacture.status_code == 422, after_manufacture.text
    accepted = await record_event(
        client,
        headers,
        animal_id=animal["id"],
        date=iso(today()),
        type="TREATMENT",
        product_manufactured_on=iso(purchase_date - timedelta(days=30)),
    )
    assert accepted[0]["product_manufactured_on"] == iso(purchase_date - timedelta(days=30))


async def test_health_event_predating_the_animal_names_the_event_and_the_animal(
    client: httpx.AsyncClient,
) -> None:
    """The chronology label passed to the service is "Health event", verbatim."""
    headers = await owner_with_farm(client)
    animal = await make_animal(
        client, headers, tag="CHRON-DOB", estimated_dob=iso(today() - timedelta(days=100))
    )
    response = await post_event(
        client,
        headers,
        animal_id=animal["id"],
        type="TREATMENT",
        date=iso(today() - timedelta(days=200)),
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == (
        "Health event cannot predate CHRON-DOB's recorded birth date"
    )


async def test_health_event_chronology_rejection_names_the_offending_date(
    client: httpx.AsyncClient,
) -> None:
    """The 422 body must carry the ValueError text, not a collapsed placeholder.

    Regression: `detail=str(exc)` degrading to a constant made every one of the
    six date rejections in that try block indistinguishable to the operator.
    """
    headers = await owner_with_farm(client)
    purchase_date = today() - timedelta(days=5)
    animal = await make_animal(
        client, headers, tag="CHRON-DETAIL", purchase_date=iso(purchase_date)
    )
    before_purchase = await post_event(
        client,
        headers,
        animal_id=animal["id"],
        type="TREATMENT",
        date=iso(purchase_date - timedelta(days=1)),
    )
    assert before_purchase.status_code == 422, before_purchase.text
    assert before_purchase.json()["detail"] == (
        "Health event cannot predate CHRON-DETAIL's recorded purchase date"
    )
    in_the_future = await post_event(
        client,
        headers,
        animal_id=animal["id"],
        type="TREATMENT",
        date=iso(today() + timedelta(days=1)),
    )
    assert in_the_future.status_code == 422, in_the_future.text
    assert in_the_future.json()["detail"] == (
        "health event date cannot be in the future for this farm"
    )


async def test_product_and_vaccine_window_messages_and_boundaries(
    client: httpx.AsyncClient,
) -> None:
    """Both product windows are half-open, and each names its own field."""
    headers = await owner_with_farm(client, "window@farm.in")
    animal = await make_animal(client, headers, tag="WINDOW-1")

    same_day_product = await post_event(
        client,
        headers,
        animal_id=animal["id"],
        type="TREATMENT",
        date=iso(today()),
        product_manufactured_on=iso(today()),
    )
    assert same_day_product.status_code == 201, same_day_product.text

    same_day_validity = await post_event(
        client,
        headers,
        animal_id=animal["id"],
        type="VACCINE",
        date=iso(today()),
        vaccine_valid_until=iso(today()),
    )
    assert same_day_validity.status_code == 201, same_day_validity.text

    late_manufacture = await post_event(
        client,
        headers,
        animal_id=animal["id"],
        type="TREATMENT",
        date=iso(today() - timedelta(days=5)),
        product_manufactured_on=iso(today() - timedelta(days=1)),
    )
    assert late_manufacture.status_code == 422, late_manufacture.text
    assert late_manufacture.json()["detail"] == (
        "product manufacture date cannot follow the health event"
    )

    lapsed_validity = await post_event(
        client,
        headers,
        animal_id=animal["id"],
        type="VACCINE",
        date=iso(today()),
        vaccine_valid_until=iso(today() - timedelta(days=1)),
    )
    assert lapsed_validity.status_code == 422, lapsed_validity.text
    assert lapsed_validity.json()["detail"] == ("vaccine validity cannot predate the health event")


async def test_sale_and_cull_blocked_during_medicine_withdrawal(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, tag="WITHDRAW-1")
    await record_event(
        client,
        headers,
        animal_id=animal["id"],
        date=iso(today()),
        type="TREATMENT",
        product_name="Antibiotic",
        withdrawal_until=iso(today() + timedelta(days=7)),
    )
    for status in ("SOLD", "CULLED"):
        response = await client.post(
            f"/api/animals/{animal['id']}/status",
            json={"new_status": status, "date": iso(today())},
            headers=headers,
        )
        assert response.status_code == 409, (status, response.text)
        assert "withdrawal" in response.json()["detail"].lower()


async def test_event_rejects_incompatible_quarantine_task_id(client: httpx.AsyncClient) -> None:
    """A health entry cannot smuggle an unrelated quarantine task to completion."""
    headers = await owner_with_farm(client)
    detail = await _backdated_batch_with_tasks(client, headers)
    rest_task = next(t for t in detail["tasks"] if t["category"] == "QUARANTINE")
    response = await post_event(
        client,
        headers,
        scope="batch",
        purchase_batch_id=detail["batch"]["id"],
        type="VITAMIN",
        task_id=rest_task["id"],
    )
    assert response.status_code == 409
    detail = await get_batch(client, headers, detail["batch"]["id"])
    task = next(t for t in detail["tasks"] if t["id"] == rest_task["id"])
    assert task["status"] == "PENDING"


async def test_event_rejects_bucket_move_task_id(client: httpx.AsyncClient) -> None:
    """The day-45 release duty cannot be closed through a generic health form."""
    headers = await owner_with_farm(client)
    detail = await _backdated_batch_with_tasks(client, headers)
    release_task = next(t for t in detail["tasks"] if t["category"] == "BUCKET_MOVE")
    response = await post_event(
        client,
        headers,
        scope="batch",
        purchase_batch_id=detail["batch"]["id"],
        type="FOOTBATH",
        product_name="10% Zinc Sulfate",
        task_id=release_task["id"],
    )
    assert response.status_code == 409
    detail = await get_batch(client, headers, detail["batch"]["id"])
    task = next(t for t in detail["tasks"] if t["id"] == release_task["id"])
    assert task["status"] == "PENDING"
    assert all(a["current_bucket"] == "QUARANTINE" for a in detail["animals"])


@pytest.mark.parametrize("marker", ["footbath", "electrolyte"])
async def test_a_non_health_task_never_reaches_the_linked_batch_rule(
    client: httpx.AsyncClient, marker: str
) -> None:
    """A QUARANTINE/BUCKET_MOVE duty is not a health duty: 409, never a 422.

    Regression: dropping the VACCINE/DEWORMING category filter from the linked
    task metadata SELECT let a protocol duty from ANOTHER batch supply the
    linked batch id, turning the compatibility 409 into a scope 422.
    """
    headers = await owner_with_farm(client)
    first = await _backdated_batch_with_tasks(client, headers, days=9, count=2)
    second = await _backdated_batch_with_tasks(client, headers, days=9, count=2)
    other_task = next(t for t in first["tasks"] if marker in t["title"])
    assert other_task["category"] in {"QUARANTINE", "BUCKET_MOVE"}
    response = await post_event(
        client,
        headers,
        scope="batch",
        purchase_batch_id=second["batch"]["id"],
        expected_animal_ids=sorted(animal["id"] for animal in second["animals"]),
        type="VACCINE",
        product_name="PPR vaccine",
        task_id=other_task["id"],
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Linked health task is not pending or compatible"


async def test_event_with_already_done_task_id_is_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    detail = await _backdated_batch_with_tasks(client, headers)
    ppr_task = next(t for t in detail["tasks"] if "PPR" in t["title"])
    payload: dict = {
        "scope": "batch",
        "purchase_batch_id": detail["batch"]["id"],
        "type": "VACCINE",
        "task_id": ppr_task["id"],
    }
    events = await record_event(client, headers, **payload)
    # Replaying a completed task cannot create a second unlinked health record.
    replay = await post_event(client, headers, **payload)
    assert replay.status_code == 409
    assert replay.json()["detail"] == "Linked health task is not pending or compatible"
    assert len(events) == 2
    detail = await get_batch(client, headers, detail["batch"]["id"])
    task = next(t for t in detail["tasks"] if t["id"] == ppr_task["id"])
    assert task["status"] == "DONE"


async def test_event_with_nonexistent_task_id_is_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    response = await post_event(
        client, headers, animal_id=animal["id"], type="VACCINE", task_id=999999
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Linked health task is not pending or compatible"


async def test_task_id_above_the_int32_ceiling_is_a_409_not_a_500(
    client: httpx.AsyncClient,
) -> None:
    """A task id above the int4 ceiling is unmatchable, not an unhandled 500.

    Regression: the `task` sentinel degrading from None to a truthy placeholder
    made `task.farm_id` explode for any task_id BoundedId admits but int4 cannot
    hold, turning the documented 409 into an opaque 500.
    """
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, tag="OVF-1")
    response = await post_event(
        client, headers, animal_id=animal["id"], type="VACCINE", task_id=2**40
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Linked health task is not pending or compatible"


async def test_event_task_closure_enforces_assignment(client: httpx.AsyncClient) -> None:
    """The health form is not a backdoor around duty assignment: closing a
    linked VACCINE/DEWORMING duty requires the same rule as the duties page
    (_visible_to) — the acting user's role must be the duty's assigned role,
    or the duty must be assigned to the user directly. Violations → 403 and
    the event is not recorded."""
    headers = await owner_with_farm(client)
    detail = await _backdated_batch_with_tasks(client, headers)
    batch_id = detail["batch"]["id"]
    ppr_task = next(t for t in detail["tasks"] if "PPR" in t["title"])  # auto-assigned: VET role

    # Worker holding health.manage via a custom role the duty is NOT assigned to.
    floater_role = await make_custom_role(
        client, headers, "Floater", ["health.view", "health.manage"]
    )
    await add_worker(client, headers, floater_role, "floater@farm.in")
    floater, _floater_id = await login_user(client, "floater@farm.in")
    floater |= {"X-Farm-Id": headers["X-Farm-Id"]}

    resp = await post_event(
        client,
        floater,
        scope="batch",
        purchase_batch_id=batch_id,
        type="VACCINE",
        product_name="PPR vaccine",
        task_id=ppr_task["id"],
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "This duty is not assigned to you"
    assert await list_events(client, headers) == []  # the event was not recorded
    detail = await get_batch(client, headers, batch_id)
    assert next(t for t in detail["tasks"] if t["id"] == ppr_task["id"])["status"] == "PENDING"

    # The assigned role's worker (VET) closes it fine.
    vet, vet_id = await worker_headers(client, headers, "VET", "vet@farm.in")
    events = await record_event(
        client,
        vet,
        scope="batch",
        purchase_batch_id=batch_id,
        type="VACCINE",
        product_name="PPR vaccine",
        task_id=ppr_task["id"],
    )
    assert len(events) == 2
    detail = await get_batch(client, headers, batch_id)
    done = next(t for t in detail["tasks"] if t["id"] == ppr_task["id"])
    assert done["status"] == "DONE"
    assert done["completed_by_id"] == vet_id

    # Health workflow categories cannot be manufactured through the generic
    # task API, even by the owner. Only the authoritative health/purchase
    # services may create a VACCINE or DEWORMING duty.
    deworm_task = next(t for t in detail["tasks"] if t["category"] == "DEWORMING")
    resp = await client.post(
        "/api/tasks",
        json={
            "title": "Extra deworm round",
            "due_date": iso(today()),
            "category": "DEWORMING",
            "assigned_user_id": _floater_id,
        },
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    detail = await get_batch(client, headers, batch_id)
    assert next(t for t in detail["tasks"] if t["id"] == deworm_task["id"])["status"] == "PENDING"


# ---------------------------------------------------------------------------
# Vaccination schedule endpoint
# ---------------------------------------------------------------------------
SCHEDULE_TEMPLATE_NAMES = {
    "FMD",
    "PPR",
    "Enterotoxaemia (ET)",
    "Haemorrhagic Septicaemia (HS)",
    "Goat Pox",
    "Black Quarter",
    "Johne's Disease",
    "Anthrax",
    "ORF",
    "CCPP",
    "Deworming",
    "Anti-coccidial drench",
}


async def test_schedule_lists_seeded_templates(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    schedule = await get_schedule(client, headers, animal["id"])
    assert schedule["animal_id"] == animal["id"]
    assert {r["template_name"] for r in schedule["rows"]} == SCHEDULE_TEMPLATE_NAMES
    # the pregnancy-linked ET + TT pre-kidding template is handled via tasks,
    # not the per-animal age schedule
    assert len(schedule["rows"]) == 12


async def test_schedule_row_shape(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    schedule = await get_schedule(client, headers, animal["id"])
    for row in schedule["rows"]:
        assert isinstance(row["template_id"], int)
        assert row["template_name"]
        assert row["status"] in {"DONE", "OVERDUE", "UPCOMING", "UNKNOWN"}
        assert set(row) == {
            "template_id",
            "template_name",
            "timing_note",
            "first_due",
            "booster_due",
            "last_done",
            "next_due",
            "status",
        }


async def test_schedule_young_animal_upcoming(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    dob = today() - timedelta(days=30)  # 1 month old
    animal = await make_animal(client, headers, date_of_birth=iso(dob))
    schedule = await get_schedule(client, headers, animal["id"])
    fmd = row_by_name(schedule, "FMD")
    expected_first = add_months(dob, 3)
    assert fmd["first_due"] == iso(expected_first)
    assert fmd["status"] == "UPCOMING"
    # FMD booster is 3.5 weeks (24.5 days, rounded half-up to 25) after the first dose
    assert fmd["booster_due"] == iso(expected_first + timedelta(days=25))
    assert fmd["last_done"] is None
    assert fmd["next_due"] is None


async def test_schedule_old_animal_without_events_is_overdue(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    dob = today() - timedelta(days=400)
    animal = await make_animal(client, headers, date_of_birth=iso(dob))
    schedule = await get_schedule(client, headers, animal["id"])
    for name in ["FMD", "PPR", "Goat Pox", "CCPP", "Deworming"]:
        row = row_by_name(schedule, name)
        assert row["status"] == "OVERDUE", name
        assert row["last_done"] is None


async def test_schedule_unknown_dob_gives_unknown_status(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)  # no DOB, no estimated DOB
    schedule = await get_schedule(client, headers, animal["id"])
    fmd = row_by_name(schedule, "FMD")
    assert fmd["status"] == "UNKNOWN"
    assert fmd["first_due"] is None
    # Deworming is herd-wide recurring with no age-based first dose → due now
    assert row_by_name(schedule, "Deworming")["status"] == "OVERDUE"


async def test_schedule_uses_estimated_dob_for_purchased_stub(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    batch_date = today() - timedelta(days=60)
    batch = await make_batch(client, headers, count=1, date=iso(batch_date), avg_age_months=7)
    detail = await get_batch(client, headers, batch["id"])
    stub = detail["animals"][0]
    assert stub["estimated_dob"] == iso(add_months(batch_date, -7))
    schedule = await get_schedule(client, headers, stub["id"])
    fmd = row_by_name(schedule, "FMD")
    expected_first = add_months(add_months(batch_date, -7), 3)
    assert fmd["first_due"] == iso(expected_first)
    assert fmd["status"] == "OVERDUE"  # ~4 months old at arrival, 10 months now


async def test_schedule_done_after_matching_vaccine_event(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, date_of_birth=iso(today() - timedelta(days=400)))
    await record_event(
        client, headers, animal_id=animal["id"], type="VACCINE", product_name="PPR vaccine"
    )
    schedule = await get_schedule(client, headers, animal["id"])
    ppr = row_by_name(schedule, "PPR")
    assert ppr["last_done"] == iso(today())
    assert ppr["next_due"] == iso(add_months(today(), 36))  # repeat every 3 years
    assert ppr["status"] == "DONE"


async def test_schedule_overdue_when_repeat_window_lapsed(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, date_of_birth=iso(today() - timedelta(days=700)))
    done_date = today() - timedelta(days=210)  # 7 months ago
    await record_event(
        client,
        headers,
        animal_id=animal["id"],
        type="DEWORMING",
        date=iso(done_date),
        disease_target="Deworming",
        product_name="Albendazole",
    )
    schedule = await get_schedule(client, headers, animal["id"])
    deworm = row_by_name(schedule, "Deworming")
    assert deworm["last_done"] == iso(done_date)
    assert deworm["next_due"] == iso(add_months(done_date, 6))  # SPEC: every 6 months
    assert deworm["status"] == "OVERDUE"  # the 6-month window has lapsed


async def test_schedule_et_tt_abbreviation_matches_et_template(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, date_of_birth=iso(today() - timedelta(days=400)))
    await record_event(
        client, headers, animal_id=animal["id"], type="VACCINE", product_name="ET + TT"
    )
    schedule = await get_schedule(client, headers, animal["id"])
    et = row_by_name(schedule, "Enterotoxaemia (ET)")
    assert et["last_done"] == iso(today())
    assert et["status"] == "DONE"


async def test_schedule_done_without_repeat_has_no_next_due(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, date_of_birth=iso(today() - timedelta(days=200)))
    await record_event(
        client,
        headers,
        animal_id=animal["id"],
        type="VACCINE",
        product_name="Anti-coccidial drench",
    )
    schedule = await get_schedule(client, headers, animal["id"])
    row = row_by_name(schedule, "Anti-coccidial drench")
    assert row["status"] == "DONE"
    assert row["last_done"] == iso(today())
    assert row["next_due"] is None  # 'as needed', no repeat interval


async def test_schedule_ignores_treatment_events(client: httpx.AsyncClient) -> None:
    """Only VACCINE/DEWORMING rows feed the schedule; a TREATMENT mentioning
    PPR is not a vaccination."""
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, date_of_birth=iso(today() - timedelta(days=400)))
    await record_event(
        client, headers, animal_id=animal["id"], type="TREATMENT", product_name="PPR antibiotics"
    )
    schedule = await get_schedule(client, headers, animal["id"])
    ppr = row_by_name(schedule, "PPR")
    assert ppr["last_done"] is None
    assert ppr["status"] == "OVERDUE"


async def test_schedule_goat_pox_not_matched_by_generic_goat_product(
    client: httpx.AsyncClient,
) -> None:
    """Regression: the old first-word match marked "Goat Pox" DONE from any
    product/disease merely containing "goat". Matching is now on the full
    normalized template name (plus the parenthesized abbreviation)."""
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, date_of_birth=iso(today() - timedelta(days=400)))
    await record_event(
        client, headers, animal_id=animal["id"], type="VACCINE", product_name="Goat mineral drench"
    )
    schedule = await get_schedule(client, headers, animal["id"])
    assert row_by_name(schedule, "Goat Pox")["last_done"] is None
    # ...while the real vaccine, recorded by its full name, still matches.
    await record_event(
        client, headers, animal_id=animal["id"], type="VACCINE", product_name="  goat  POX vaccine "
    )
    schedule = await get_schedule(client, headers, animal["id"])
    row = row_by_name(schedule, "Goat Pox")
    assert row["last_done"] == iso(today())
    assert row["status"] == "DONE"


async def test_schedule_nonexistent_animal_404(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.get("/api/health/schedule/999999", headers=headers)
    assert resp.status_code == 404


async def test_schedule_zero_id_404(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.get("/api/health/schedule/0", headers=headers)
    assert resp.status_code == 404


async def test_schedule_malformed_id_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.get("/api/health/schedule/abc", headers=headers)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Purchases — happy paths
# ---------------------------------------------------------------------------
async def test_create_batch_minimal(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.post(
        "/api/purchases/new",
        json={"date": iso(today()), "count": 2},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    batch = resp.json()
    assert batch["count"] == 2
    assert batch["supplier"] is None
    assert batch["total_price"] is None
    assert batch["avg_age_months"] is None
    assert batch["animals_created"] == 2  # create_animals defaults to True
    assert batch["open_tasks"] == 8


async def test_create_batch_full_fields_roundtrip(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    batch_date = today() - timedelta(days=5)
    batch = await make_batch(
        client,
        headers,
        date=iso(batch_date),
        supplier="Adilabad Goat Mart",
        count=10,
        avg_age_months=7,
        avg_weight_kg=15.5,
        total_price=95000.0,
        notes="foundation stock",
    )
    assert batch["date"] == iso(batch_date)
    assert batch["supplier"] == "Adilabad Goat Mart"
    assert batch["count"] == 10
    assert batch["avg_age_months"] == 7
    assert batch["avg_weight_kg"] == 15.5
    assert batch["total_price"] == 95000.0
    assert batch["notes"] == "foundation stock"
    assert batch["animals_created"] == 10
    assert batch["open_tasks"] == 8


async def test_create_batch_without_animals(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    batch = await make_batch(client, headers, count=5, sex="M", create_animals=False)
    assert batch["animals_created"] == 0
    assert batch["sex"] == "M"
    detail = await get_batch(client, headers, batch["id"])
    assert detail["batch"]["sex"] == "M"
    assert detail["animals"] == []
    assert detail["tasks"] == []  # no animals means no impossible protocol workflow
    assert detail["batch"]["open_tasks"] == 0


async def test_create_batch_stub_animals_shape(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    batch_date = today() - timedelta(days=3)
    batch = await make_batch(
        client,
        headers,
        date=iso(batch_date),
        count=3,
        avg_age_months=7,
        total_price=10000.0,
    )
    detail = await get_batch(client, headers, batch["id"])
    animals = detail["animals"]
    tag_prefix = animals[0]["tag_number"].rsplit("-", 1)[0]
    assert tag_prefix.startswith(f"B{batch['id']}-")
    assert len(tag_prefix.removeprefix(f"B{batch['id']}-")) == 12
    assert [a["tag_number"] for a in animals] == [
        f"{tag_prefix}-0001",
        f"{tag_prefix}-0002",
        f"{tag_prefix}-0003",
    ]
    for animal in animals:
        assert animal["sex"] == "F"
        assert animal["breed"] == "Osmanabadi"
        assert animal["source"] == "PURCHASED"
        assert animal["current_bucket"] == "QUARANTINE"  # SPEC: new purchases
        assert animal["status"] == "ACTIVE"
        assert animal["purchase_date"] == iso(batch_date)
        assert animal["seller_name"] == "Kurnool Traders"
        assert animal["estimated_dob"] == iso(add_months(batch_date, -7))
    # Total split per head; the first animal absorbs the paise remainder so
    # Σ purchase_price equals the booked expense.
    assert [a["purchase_price"] for a in animals] == [3333.34, 3333.33, 3333.33]


async def test_create_batch_supplier_whitespace_stripped(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    batch = await make_batch(client, headers, supplier="  Kurnool Traders  ", notes="  note  ")
    assert batch["supplier"] == "Kurnool Traders"
    assert batch["notes"] == "note"


async def test_create_batch_blank_supplier_becomes_null(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    batch = await make_batch(client, headers, supplier="   ", count=1)
    assert batch["supplier"] is None
    detail = await get_batch(client, headers, batch["id"])
    assert detail["animals"][0]["seller_name"] is None


async def test_create_batch_unicode_supplier(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    batch = await make_batch(client, headers, supplier="ओस्मानाबादी ट्रेडर्स 🐐", count=1)
    assert batch["supplier"] == "ओस्मानाबादी ट्रेडर्स 🐐"
    detail = await get_batch(client, headers, batch["id"])
    assert detail["animals"][0]["seller_name"] == "ओस्मानाबादी ट्रेडर्स 🐐"


async def test_create_batch_sql_injection_supplier(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    payload = "'; DROP TABLE purchase_batches; --"
    batch = await make_batch(client, headers, supplier=payload, count=1)
    assert batch["supplier"] == payload
    assert len(await list_batches(client, headers)) == 1  # table intact


async def test_create_batch_notes_length_is_bounded(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    notes = "n" * 4_000
    batch = await make_batch(client, headers, count=1, notes=notes)
    assert batch["notes"] == notes
    resp = await _post_batch(client, headers, date=iso(today()), count=1, notes="n" * 4_001)
    assert resp.status_code == 422


async def test_list_batches_empty(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    assert await list_batches(client, headers) == []


async def test_list_batches_newest_first(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    older = await make_batch(client, headers, count=1, date=iso(today() - timedelta(days=30)))
    newer = await make_batch(client, headers, count=1, date=iso(today()))
    batches = await list_batches(client, headers)
    assert [b["id"] for b in batches] == [newer["id"], older["id"]]


async def test_list_batches_pagination_reports_total(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for i in range(3):
        await make_batch(client, headers, count=1, supplier=f"Supplier {i}")
    response = await client.get("/api/purchases", params={"limit": 1, "offset": 1}, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["batches"]) == 1
    assert body["total"] == 3
    assert body["limit"] == 1
    assert body["offset"] == 1


async def test_list_batches_searches_supplier_and_exact_id(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    first = await make_batch(client, headers, count=1, supplier="Literal 100% Farm")
    await make_batch(client, headers, count=1, supplier="Other Seller")

    by_supplier = await client.get("/api/purchases", params={"q": "100%"}, headers=headers)
    assert by_supplier.status_code == 200, by_supplier.text
    assert [row["id"] for row in by_supplier.json()["batches"]] == [first["id"]]
    assert by_supplier.json()["total"] == 1

    by_id = await client.get("/api/purchases", params={"q": f"#{first['id']}"}, headers=headers)
    assert by_id.status_code == 200, by_id.text
    assert [row["id"] for row in by_id.json()["batches"]] == [first["id"]]


async def test_batch_detail_counts(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    batch = await make_batch(client, headers, count=4)
    detail = await get_batch(client, headers, batch["id"])
    assert detail["batch"]["animals_created"] == 4
    assert detail["batch"]["open_tasks"] == 8
    assert len(detail["animals"]) == 4
    assert len(detail["tasks"]) == 8


async def test_batch_detail_not_found_404(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.get("/api/purchases/999999", headers=headers)
    assert resp.status_code == 404


async def test_batch_detail_zero_id_404(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.get("/api/purchases/0", headers=headers)
    assert resp.status_code == 404


async def test_batch_detail_malformed_id_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.get("/api/purchases/abc", headers=headers)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Purchases — the 45-day quarantine protocol (SPEC)
# ---------------------------------------------------------------------------
async def test_quarantine_tasks_match_spec_schedule(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    batch_date = today() - timedelta(days=60)
    batch = await make_batch(client, headers, count=2, date=iso(batch_date))
    detail = await get_batch(client, headers, batch["id"])
    tasks = detail["tasks"]  # ordered by due_date
    assert len(tasks) == 8
    # SPEC: rest days 1–3, deworm day 4, tonic days 5–9, PPR day 10,
    # ET+TT day 20, Goat Pox day 30, FMD day 40, footbath+release day 45.
    # Due date = arrival + (day - 1).
    expected = [
        (0, "QUARANTINE", "rest"),
        (3, "DEWORMING", "deworm"),
        (4, "QUARANTINE", "liver tonic"),
        (9, "VACCINE", "PPR"),
        (19, "VACCINE", "ET + Tetanus"),
        (29, "VACCINE", "Goat Pox"),
        (39, "VACCINE", "FMD"),
        (44, "BUCKET_MOVE", "FOUNDATION"),
    ]
    for task, (offset, category, keyword) in zip(tasks, expected, strict=True):
        assert task["due_date"] == iso(batch_date + timedelta(days=offset))
        assert task["category"] == category
        assert keyword in task["title"]
        assert task["status"] == "PENDING"
        assert task["purchase_batch_id"] == batch["id"]
        assert task["auto_generated"] is True


async def test_quarantine_task_titles_include_supplier_and_batch(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    batch = await make_batch(client, headers, count=1, supplier="Nanded Livestock")
    detail = await get_batch(client, headers, batch["id"])
    for task in detail["tasks"]:
        assert f"[Nanded Livestock #{batch['id']}]" in task["title"]


async def test_quarantine_task_titles_fallback_without_supplier(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    batch = await make_batch(client, headers, count=1, supplier=None)
    detail = await get_batch(client, headers, batch["id"])
    for task in detail["tasks"]:
        assert task["title"].startswith(f"[Purchase #{batch['id']}]")


async def test_day45_completion_releases_animals_to_foundation(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    detail = await _backdated_batch_with_tasks(client, headers, days=50, count=3)
    await complete_quarantine_prerequisites(client, headers, detail)
    release = next(t for t in detail["tasks"] if t["category"] == "BUCKET_MOVE")
    resp = await client.post(f"/api/tasks/{release['id']}/complete", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "DONE"
    detail = await get_batch(client, headers, detail["batch"]["id"])
    # SPEC: day-45 footbath → release to FOUNDATION bucket
    assert all(a["current_bucket"] == "FOUNDATION" for a in detail["animals"])
    assert detail["batch"]["open_tasks"] == 0


async def test_quarantine_release_requires_completed_records_and_no_disease_hold(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    detail = await _backdated_batch_with_tasks(client, headers, days=50, count=1)
    release = next(task for task in detail["tasks"] if task["category"] == "BUCKET_MOVE")
    incomplete = await client.post(f"/api/tasks/{release['id']}/complete", headers=headers)
    assert incomplete.status_code == 409
    assert "prerequisite" in incomplete.json()["detail"].lower()

    await complete_quarantine_prerequisites(client, headers, detail)
    held = await post_event(
        client,
        headers,
        scope="batch",
        purchase_batch_id=detail["batch"]["id"],
        type="TREATMENT",
        disease_target="Reportable-condition concern",
        suspected_scheduled_disease=True,
    )
    assert held.status_code == 201, held.text
    blocked = await client.post(f"/api/tasks/{release['id']}/complete", headers=headers)
    assert blocked.status_code == 409
    assert "restriction" in blocked.json()["detail"].lower()


async def test_movement_hold_blocks_sale_until_referenced_clearance(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, tag="HOLD-1")
    held = await post_event(
        client,
        headers,
        scope="animal",
        animal_id=animal["id"],
        type="TREATMENT",
        disease_target="Reportable-condition concern",
        suspected_scheduled_disease=True,
    )
    assert held.status_code == 201, held.text

    blocked = await client.post(
        f"/api/animals/{animal['id']}/status",
        json={"new_status": "SOLD", "sale_price": 1000},
        headers=headers,
    )
    assert blocked.status_code == 409
    assert "clearance" in blocked.json()["detail"].lower()
    blank = await client.post(
        f"/api/health/restrictions/{animal['id']}/clear",
        json={"clearance_reference": "   ", "expected_restriction_version": 1},
        headers=headers,
    )
    assert blank.status_code == 422

    cleared = await client.post(
        f"/api/health/restrictions/{animal['id']}/clear",
        json={
            "clearance_reference": "District AHD clearance AHD-2026-184",
            "expected_restriction_version": 1,
        },
        headers=headers,
    )
    assert cleared.status_code == 204, cleared.text
    after = await get_animal(client, headers, animal["id"])
    assert after["movement_restricted"] is False
    assert after["suspected_scheduled_disease"] is False
    assert after["restriction_reason"] is None
    assert after["restriction_cleared_at"] is not None
    assert after["restriction_cleared_by_id"] is not None
    assert after["restriction_clearance_reference"] == "District AHD clearance AHD-2026-184"

    sold = await client.post(
        f"/api/animals/{animal['id']}/status",
        json={"new_status": "SOLD", "sale_price": 1000},
        headers=headers,
    )
    assert sold.status_code == 200, sold.text


async def test_clear_restriction_requires_an_active_hold(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, tag="NO-HOLD")
    response = await client.post(
        f"/api/health/restrictions/{animal['id']}/clear",
        json={"clearance_reference": "Not applicable", "expected_restriction_version": 1},
        headers=headers,
    )
    assert response.status_code == 409


async def test_day45_completion_leaves_dead_animals_untouched(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    detail = await _backdated_batch_with_tasks(client, headers, days=50, count=2)
    dead, live = detail["animals"][0], detail["animals"][1]
    await mark_dead(client, headers, dead["id"])
    await complete_quarantine_prerequisites(client, headers, detail)
    release = next(t for t in detail["tasks"] if t["category"] == "BUCKET_MOVE")
    resp = await client.post(f"/api/tasks/{release['id']}/complete", headers=headers)
    assert resp.status_code == 200, resp.text
    dead_after = await get_animal(client, headers, dead["id"])
    live_after = await get_animal(client, headers, live["id"])
    assert dead_after["current_bucket"] == "QUARANTINE"  # dead animals never move
    assert dead_after["status"] == "DEAD"
    assert live_after["current_bucket"] == "FOUNDATION"


async def test_day45_completion_not_idempotent_via_api(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    detail = await _backdated_batch_with_tasks(client, headers, days=50)
    await complete_quarantine_prerequisites(client, headers, detail)
    release = next(t for t in detail["tasks"] if t["category"] == "BUCKET_MOVE")
    resp = await client.post(f"/api/tasks/{release['id']}/complete", headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await client.post(f"/api/tasks/{release['id']}/complete", headers=headers)
    assert resp.status_code == 400, resp.text  # not pending anymore


async def test_future_quarantine_task_cannot_be_completed_early(client: httpx.AsyncClient) -> None:
    """Auto-generated duties unlock on their due date (tasks API guard)."""
    headers = await owner_with_farm(client)
    batch = await make_batch(client, headers, count=1, date=iso(today()))  # all duties future
    detail = await get_batch(client, headers, batch["id"])
    release = next(t for t in detail["tasks"] if t["category"] == "BUCKET_MOVE")
    resp = await client.post(f"/api/tasks/{release['id']}/complete", headers=headers)
    assert resp.status_code == 409, resp.text
    detail = await get_batch(client, headers, batch["id"])
    assert all(a["current_bucket"] == "QUARANTINE" for a in detail["animals"])


# ---------------------------------------------------------------------------
# Purchases — finance side effects
# ---------------------------------------------------------------------------
async def test_purchase_books_expense_transaction(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    batch_date = today() - timedelta(days=2)
    batch = await make_batch(client, headers, count=5, total_price=45000.0, date=iso(batch_date))
    txns = await transactions(client, headers)
    assert len(txns) == 1
    txn = txns[0]
    assert txn["type"] == "EXPENSE"
    assert txn["category"] == "ANIMAL_PURCHASE"
    assert txn["amount"] == 45000.0
    assert txn["date"] == iso(batch_date)
    assert f"#{batch['id']}" in txn["notes"]
    assert "5 animals" in txn["notes"]
    assert "Kurnool Traders" in txn["notes"]


async def test_purchase_without_price_books_no_transaction(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_batch(client, headers, count=3, total_price=None)
    assert await transactions(client, headers) == []


async def test_purchase_with_zero_price_books_zero_transaction(client: httpx.AsyncClient) -> None:
    """An explicit ₹0 is a real (free) purchase: it books a ₹0 ANIMAL_PURCHASE
    expense and a 0.00 per-head price — distinct from omitting the price
    entirely, which books nothing (see the test above)."""
    headers = await owner_with_farm(client)
    batch = await make_batch(client, headers, count=3, total_price=0)
    txns = await transactions(client, headers)
    assert [(t["category"], t["amount"]) for t in txns] == [("ANIMAL_PURCHASE", 0.0)]
    detail = await get_batch(client, headers, batch["id"])
    assert all(a["purchase_price"] == 0.0 for a in detail["animals"])


# ---------------------------------------------------------------------------
# Purchases — validation (422) and boundaries
# ---------------------------------------------------------------------------
async def _post_batch(
    client: httpx.AsyncClient, headers: dict, **payload: object
) -> httpx.Response:
    return await client.post("/api/purchases/new", json=payload, headers=headers)


async def test_batch_missing_required_fields_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    assert (await _post_batch(client, headers, count=3)).status_code == 422  # no date
    assert (await _post_batch(client, headers, date=iso(today()))).status_code == 422  # no count
    assert (await _post_batch(client, headers)).status_code == 422  # neither


async def test_batch_count_boundaries(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for bad in [0, -1, -100, 1001, 5000, 2.5, "abc", None]:
        resp = await _post_batch(client, headers, date=iso(today()), count=bad)
        assert resp.status_code == 422, bad
    # both ends of the valid range work (SPEC plans ~50; cap is 1000)
    resp = await _post_batch(client, headers, date=iso(today()), count=1)
    assert resp.status_code == 201
    resp = await _post_batch(client, headers, date=iso(today()), count=1000, create_animals=False)
    assert resp.status_code == 201


async def test_batch_avg_age_months_boundaries(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for bad in [-1, -0.5, 241, 1000]:
        resp = await _post_batch(client, headers, date=iso(today()), count=1, avg_age_months=bad)
        assert resp.status_code == 422, bad
    for good in [0, 0.5, 240]:
        resp = await _post_batch(
            client, headers, date=iso(today()), count=1, avg_age_months=good, create_animals=False
        )
        assert resp.status_code == 201, good


# avg_age_months=0 means newborn kids: estimated_dob is the batch date itself
# (a falsy-0 bug used to store None, breaking age-based vaccine scheduling).
async def test_batch_zero_avg_age_gives_batch_date_as_estimated_dob(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    batch = await make_batch(client, headers, count=1, avg_age_months=0)
    detail = await get_batch(client, headers, batch["id"])
    assert detail["animals"][0]["estimated_dob"] == batch["date"]


async def test_batch_negative_weight_or_price_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await _post_batch(client, headers, date=iso(today()), count=1, avg_weight_kg=-0.1)
    assert resp.status_code == 422
    resp = await _post_batch(client, headers, date=iso(today()), count=1, total_price=-100)
    assert resp.status_code == 422


async def test_batch_zero_weight_accepted(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await _post_batch(client, headers, date=iso(today()), count=1, avg_weight_kg=0)
    assert resp.status_code == 201


async def test_batch_date_validation(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    future = today() + timedelta(days=1)
    for bad in [iso(future), "1999-12-31", "01-01-2026", "not-a-date", "2026-02-30"]:
        resp = await _post_batch(client, headers, date=bad, count=1)
        assert resp.status_code == 422, bad
    # the year-2000 lower bound is inclusive
    resp = await _post_batch(client, headers, date="2000-01-01", count=1, create_animals=False)
    assert resp.status_code == 201


async def test_batch_supplier_too_long_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await _post_batch(client, headers, date=iso(today()), count=1, supplier="s" * 121)
    assert resp.status_code == 422
    resp = await _post_batch(client, headers, date=iso(today()), count=1, supplier="s" * 120)
    assert resp.status_code == 201


async def test_batch_unknown_extra_field_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await _post_batch(
        client, headers, date=iso(today()), count=1, create_animals=False, admin=True
    )
    assert resp.status_code == 422
    assert await list_batches(client, headers) == []


# ---------------------------------------------------------------------------
# Auth — no/garbage credentials → 401
# ---------------------------------------------------------------------------
async def test_health_endpoints_require_auth(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    assert (await client.get("/api/health/events")).status_code == 401
    resp = await client.post(
        "/api/health/events", json={"animal_id": animal["id"], "type": "VACCINE"}
    )
    assert resp.status_code == 401
    assert (await client.get(f"/api/health/schedule/{animal['id']}")).status_code == 401


async def test_purchase_endpoints_require_auth(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    batch = await make_batch(client, headers, count=1)
    assert (await client.get("/api/purchases")).status_code == 401
    resp = await client.post("/api/purchases/new", json={"date": iso(today()), "count": 1})
    assert resp.status_code == 401
    assert (await client.get(f"/api/purchases/{batch['id']}")).status_code == 401


async def test_garbage_and_wrong_scheme_tokens_401(client: httpx.AsyncClient) -> None:
    assert (
        await client.get("/api/health/events", headers={"Authorization": "Bearer garbage"})
    ).status_code == 401
    assert (
        await client.get("/api/health/events", headers={"Authorization": "Token abc"})
    ).status_code == 401
    assert (
        await client.get("/api/purchases", headers={"Authorization": "Bearer "})
    ).status_code == 401


# ---------------------------------------------------------------------------
# X-Farm-Id header — missing/malformed/foreign
# ---------------------------------------------------------------------------
async def test_missing_farm_header_422(client: httpx.AsyncClient) -> None:
    """X-Farm-Id is a required header in the contract — a
    missing one now fails request validation (422) before deps run."""
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    auth_only = {"Authorization": headers["Authorization"]}
    assert (await client.get("/api/health/events", headers=auth_only)).status_code == 422
    assert (await client.get("/api/purchases", headers=auth_only)).status_code == 422
    resp = await client.post(
        "/api/health/events",
        json={"animal_id": animal["id"], "type": "VACCINE"},
        headers=auth_only,
    )
    assert resp.status_code == 422
    resp = await client.post(
        "/api/purchases/new",
        json={"date": iso(today()), "count": 1},
        headers=auth_only,
    )
    assert resp.status_code == 422
    assert (
        await client.get(f"/api/health/schedule/{animal['id']}", headers=auth_only)
    ).status_code == 422


async def test_malformed_farm_header_400(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for bad in ["abc", "", "1.5", "0", "-3", str(2**62)]:
        farm_headers = headers | {"X-Farm-Id": bad}
        assert (await client.get("/api/health/events", headers=farm_headers)).status_code == 400, (
            bad
        )
        assert (await client.get("/api/purchases", headers=farm_headers)).status_code == 400, bad


async def test_nonexistent_farm_404(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    ghost = headers | {"X-Farm-Id": "424242"}
    assert (await client.get("/api/health/events", headers=ghost)).status_code == 404
    assert (await client.get("/api/purchases", headers=ghost)).status_code == 404


async def test_foreign_farm_header_404(client: httpx.AsyncClient) -> None:
    """Unknown and forbidden farms share one 404 — a 403 here
    would let any authenticated user enumerate sequential farm ids."""
    owner_a = await owner_with_farm(client, "a@farm.in", "Farm A")
    owner_b = await owner_with_farm(client, "b@farm.in", "Farm B")
    cross = {"Authorization": owner_a["Authorization"], "X-Farm-Id": owner_b["X-Farm-Id"]}
    assert (await client.get("/api/health/events", headers=cross)).status_code == 404
    assert (await client.get("/api/purchases", headers=cross)).status_code == 404
    resp = await client.post(
        "/api/purchases/new", json={"date": iso(today()), "count": 1}, headers=cross
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tenancy isolation — no cross-farm reads or writes
# ---------------------------------------------------------------------------
async def test_events_isolated_between_farms(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, "a@farm.in", "Farm A")
    owner_b = await owner_with_farm(client, "b@farm.in", "Farm B")
    animal_a = await make_animal(client, owner_a, tag="A-ONLY")
    await record_event(client, owner_a, animal_id=animal_a["id"], type="VACCINE")
    assert await list_events(client, owner_b) == []
    assert len(await list_events(client, owner_a)) == 1


async def test_purchases_isolated_between_farms(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, "a@farm.in", "Farm A")
    owner_b = await owner_with_farm(client, "b@farm.in", "Farm B")
    await make_batch(client, owner_a, count=2)
    assert await list_batches(client, owner_b) == []
    assert len(await list_batches(client, owner_a)) == 1


async def test_schedule_of_foreign_animal_404(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, "a@farm.in", "Farm A")
    owner_b = await owner_with_farm(client, "b@farm.in", "Farm B")
    animal_a = await make_animal(client, owner_a, tag="A-ONLY")
    resp = await client.get(f"/api/health/schedule/{animal_a['id']}", headers=owner_b)
    assert resp.status_code == 404


async def test_batch_detail_of_foreign_batch_404(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, "a@farm.in", "Farm A")
    owner_b = await owner_with_farm(client, "b@farm.in", "Farm B")
    batch_a = await make_batch(client, owner_a, count=1)
    resp = await client.get(f"/api/purchases/{batch_a['id']}", headers=owner_b)
    assert resp.status_code == 404


async def test_event_with_foreign_animal_id_rejected(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, "a@farm.in", "Farm A")
    owner_b = await owner_with_farm(client, "b@farm.in", "Farm B")
    animal_a = await make_animal(client, owner_a, tag="A-ONLY")
    resp = await post_event(client, owner_b, animal_id=animal_a["id"], type="VACCINE")
    assert resp.status_code == 400, resp.text  # treated as no match — no leak
    assert await list_events(client, owner_a) == []  # and nothing was written


async def test_event_with_foreign_batch_id_rejected(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, "a@farm.in", "Farm A")
    owner_b = await owner_with_farm(client, "b@farm.in", "Farm B")
    batch_a = await make_batch(client, owner_a, count=2)
    resp = await post_event(
        client, owner_b, scope="batch", purchase_batch_id=batch_a["id"], type="DEWORMING"
    )
    assert resp.status_code == 400, resp.text


async def test_event_with_foreign_task_id_is_rejected(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, "a@farm.in", "Farm A")
    owner_b = await owner_with_farm(client, "b@farm.in", "Farm B")
    detail_a = await _backdated_batch_with_tasks(client, owner_a, days=50, count=1)
    ppr_task_a = next(t for t in detail_a["tasks"] if "PPR" in t["title"])
    animal_b = await make_animal(client, owner_b, tag="B-ONLY")
    # B cannot smuggle A's task id into an otherwise valid local event.
    response = await post_event(
        client, owner_b, animal_id=animal_b["id"], type="VACCINE", task_id=ppr_task_a["id"]
    )
    assert response.status_code == 409
    detail_a = await get_batch(client, owner_a, detail_a["batch"]["id"])
    task = next(t for t in detail_a["tasks"] if t["id"] == ppr_task_a["id"])
    assert task["status"] == "PENDING"  # A's duty untouched


async def test_another_farms_task_id_never_reaches_the_linked_batch_rule(
    client: httpx.AsyncClient,
) -> None:
    """A batch-scoped write must not learn anything about another farm's duty.

    Regression: dropping `Task.farm_id == farm.id` from the linked-task metadata
    SELECT let farm B's batch id flow into farm A's scope check, replacing the
    compatibility 409 with a 422 that leaks the foreign duty's existence.
    """
    owner_a = await owner_with_farm(client, "xa@farm.in", "A Farm")
    owner_b = await owner_with_farm(client, "xb@farm.in", "B Farm")
    detail_a = await _backdated_batch_with_tasks(client, owner_a, days=9, count=2)
    detail_b = await _backdated_batch_with_tasks(client, owner_b, days=9, count=2)
    foreign_task = next(t for t in detail_b["tasks"] if "PPR" in t["title"])
    response = await post_event(
        client,
        owner_a,
        scope="batch",
        purchase_batch_id=detail_a["batch"]["id"],
        expected_animal_ids=sorted(animal["id"] for animal in detail_a["animals"]),
        type="VACCINE",
        product_name="PPR vaccine",
        task_id=foreign_task["id"],
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Linked health task is not pending or compatible"
    refreshed_b = await get_batch(client, owner_b, detail_b["batch"]["id"])
    task_b = next(t for t in refreshed_b["tasks"] if t["id"] == foreign_task["id"])
    assert task_b["status"] == "PENDING"


# ---------------------------------------------------------------------------
# RBAC — workers need health.* / purchases.* permissions
# ---------------------------------------------------------------------------
async def test_vet_worker_can_use_health_module(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    vet = await worker_with_role(client, owner, "VET", "vet@farm.in")
    assert (await client.get("/api/health/events", headers=vet)).status_code == 200
    assert (
        await client.get(f"/api/health/schedule/{animal['id']}", headers=vet)
    ).status_code == 200
    resp = await client.post(
        "/api/health/events",
        json={"animal_id": animal["id"], "type": "VACCINE"},
        headers=vet,
    )
    assert resp.status_code == 201, resp.text


async def test_vet_worker_cannot_use_purchases_module(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    batch = await make_batch(client, owner, count=1)
    vet = await worker_with_role(client, owner, "VET", "vet@farm.in")
    assert (await client.get("/api/purchases", headers=vet)).status_code == 403
    assert (await client.get(f"/api/purchases/{batch['id']}", headers=vet)).status_code == 403
    resp = await client.post(
        "/api/purchases/new", json={"date": iso(today()), "count": 1}, headers=vet
    )
    assert resp.status_code == 403


async def test_purchase_detail_uses_permission_scoped_animal_and_task_shapes(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    batch = await make_batch(
        client,
        owner,
        count=1,
        supplier="Private Supplier",
        total_price=9000,
        notes="Private procurement note",
    )
    owner_detail = await get_batch(client, owner, batch["id"])
    assigned_task_id = owner_detail["tasks"][0]["id"]

    purchase_only_role = await make_custom_role(
        client, owner, "Purchase Reader", ["purchases.view"]
    )
    await add_worker(client, owner, purchase_only_role, "purchase-reader@farm.in")
    purchase_only, _ = await login_user(client, "purchase-reader@farm.in")
    purchase_only |= {"X-Farm-Id": owner["X-Farm-Id"]}

    operator_role = await make_custom_role(
        client,
        owner,
        "Purchase Animal Task Operator",
        ["purchases.view", "animals.view", "tasks.view"],
    )
    await add_worker(client, owner, operator_role, "purchase-operator@farm.in")
    operator, _ = await login_user(client, "purchase-operator@farm.in")
    operator |= {"X-Farm-Id": owner["X-Farm-Id"]}

    # Make one schedule row visible through the task module's normal role
    # scope. The other quarantine rows remain procurement-only summaries.
    from sqlalchemy import update as sa_update

    from app.db import get_sessionmaker
    from app.models import Task

    async with get_sessionmaker()() as db:
        await db.execute(
            sa_update(Task)
            .where(Task.id == assigned_task_id)
            .values(assigned_role_id=operator_role, assigned_user_id=None)
        )
        await db.commit()

    purchase_detail = await get_batch(client, purchase_only, batch["id"])
    assert purchase_detail["batch"]["total_price"] == 9000.0
    assert purchase_detail["batch"]["notes"] == "Private procurement note"
    assert len(purchase_detail["animals"]) == 1
    assert set(purchase_detail["animals"][0]) == {
        "id",
        "tag_number",
        "sex",
        "current_bucket",
        "status",
    }
    assert purchase_detail["animals"][0]["current_bucket"] == "QUARANTINE"
    assert purchase_detail["animals"][0]["status"] == "ACTIVE"
    assert all(
        set(task)
        == {
            "id",
            "title",
            "due_date",
            "status",
            "category",
        }
        for task in purchase_detail["tasks"]
    )

    operator_detail = await get_batch(client, operator, batch["id"])
    operator_animal = operator_detail["animals"][0]
    assert "created_at" in operator_animal  # full AnimalOut contract
    assert operator_animal["purchase_price"] == 9000.0
    assert operator_animal["purchase_date"] == iso(today())
    assert operator_animal["seller_name"] == "Private Supplier"
    assert operator_animal["sale_price"] is None
    assert operator_animal["notes"] is None
    assert operator_animal["restriction_reason"] is None

    visible_task = next(task for task in operator_detail["tasks"] if task["id"] == assigned_task_id)
    assert "assigned_role_id" in visible_task
    assert visible_task["assigned_role_id"] == operator_role
    assert visible_task["assigned_role_name"] == "Purchase Animal Task Operator"
    hidden_task = next(task for task in operator_detail["tasks"] if task["id"] != assigned_task_id)
    assert set(hidden_task) == {
        "id",
        "title",
        "due_date",
        "status",
        "category",
    }

    owner_detail = await get_batch(client, owner, batch["id"])
    assert owner_detail["animals"][0]["purchase_price"] == 9000.0
    assert owner_detail["animals"][0]["seller_name"] == "Private Supplier"
    assert all("assigned_role_id" in task for task in owner_detail["tasks"])
    assert all("verification_note" in task for task in owner_detail["tasks"])


async def test_mover_worker_forbidden_from_health_and_purchases(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    mover = await worker_with_role(client, owner, "MOVER", "mover@farm.in")
    assert (await client.get("/api/health/events", headers=mover)).status_code == 403
    assert (
        await client.get(f"/api/health/schedule/{animal['id']}", headers=mover)
    ).status_code == 403
    resp = await client.post(
        "/api/health/events",
        json={"animal_id": animal["id"], "type": "VACCINE"},
        headers=mover,
    )
    assert resp.status_code == 403
    assert (await client.get("/api/purchases", headers=mover)).status_code == 403
    resp = await client.post(
        "/api/purchases/new", json={"date": iso(today()), "count": 1}, headers=mover
    )
    assert resp.status_code == 403


async def test_cleaner_worker_forbidden_everywhere_here(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    cleaner = await worker_with_role(client, owner, "CLEANER", "cleaner@farm.in")
    assert (await client.get("/api/health/events", headers=cleaner)).status_code == 403
    assert (
        await client.get(f"/api/health/schedule/{animal['id']}", headers=cleaner)
    ).status_code == 403
    assert (await client.get("/api/purchases", headers=cleaner)).status_code == 403


# ---------------------------------------------------------------------------
# Health + purchases integration
# ---------------------------------------------------------------------------
async def test_quarantine_deworming_via_linked_batch_scope(client: httpx.AsyncClient) -> None:
    """A day-4 batch task can only be closed through that exact batch scope."""
    headers = await owner_with_farm(client)
    detail = await _backdated_batch_with_tasks(client, headers, days=10, count=3)
    deworm_task = next(t for t in detail["tasks"] if t["category"] == "DEWORMING")
    events = await record_event(
        client,
        headers,
        scope="batch",
        purchase_batch_id=detail["batch"]["id"],
        type="DEWORMING",
        product_name="Albendazole/Closantel oral + Ivermectin SC",
        task_id=deworm_task["id"],
        cost=300,
    )
    assert len(events) == 3
    assert round(sum(e["cost"] for e in events), 2) == 300.0
    detail = await get_batch(client, headers, detail["batch"]["id"])
    task = next(t for t in detail["tasks"] if t["id"] == deworm_task["id"])
    assert task["status"] == "DONE"


async def test_batch_events_visible_in_health_log_with_batch_link(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    batch = await make_batch(client, headers, count=2)
    await record_event(
        client, headers, scope="batch", purchase_batch_id=batch["id"], type="VACCINE"
    )
    events = await list_events(client, headers)
    assert len(events) == 2
    assert all(e["purchase_batch_id"] == batch["id"] for e in events)
    assert all(e["animal_tag"].startswith(f"B{batch['id']}-") for e in events)


# ---------------------------------------------------------------------------
# Additional boundary & behavior documentation
# ---------------------------------------------------------------------------
async def test_event_date_today_boundary_accepted(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    events = await record_event(
        client, headers, animal_id=animal["id"], type="VACCINE", date=iso(today())
    )
    assert events[0]["date"] == iso(today())


async def test_purchase_date_today_boundary_accepted(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    batch = await make_batch(client, headers, date=iso(today()), count=1)
    assert batch["date"] == iso(today())


async def test_event_on_quarantined_animal_allowed(client: httpx.AsyncClient) -> None:
    """Quarantine is a bucket, not a lock — the day-4 deworming targets
    animals while they sit in QUARANTINE."""
    headers = await owner_with_farm(client)
    batch = await make_batch(client, headers, count=1)
    detail = await get_batch(client, headers, batch["id"])
    stub = detail["animals"][0]
    assert stub["current_bucket"] == "QUARANTINE"
    events = await record_event(client, headers, animal_id=stub["id"], type="DEWORMING")
    assert len(events) == 1
    assert events[0]["animal_tag"] == stub["tag_number"]


async def test_cost_split_two_animals_is_exact(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for tag in ["E-1", "E-2"]:
        await make_animal(client, headers, tag=tag)
    events = await record_event(
        client, headers, scope="bucket", bucket="FOUNDATION", type="VACCINE", cost=100
    )
    assert sorted(e["cost"] for e in events) == [50.0, 50.0]


async def test_batch_fractional_avg_age_preserved_in_estimated_dob(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    batch_date = today() - timedelta(days=10)
    batch = await make_batch(client, headers, count=1, date=iso(batch_date), avg_age_months=7.9)
    detail = await get_batch(client, headers, batch["id"])
    # 7.9 months = seven whole calendar months plus 0.9 of the eighth,
    # interpolated over THAT month's actual day span (not a fixed 30.44-day
    # mean, which could round past the anchor and make a younger stated age
    # yield an earlier birth date). The fractional age must not disappear.
    newer_anchor = add_months(batch_date, -7)
    older_anchor = add_months(batch_date, -8)
    fractional_days = int(
        (Decimal("0.9") * (newer_anchor - older_anchor).days).to_integral_value(
            rounding=ROUND_HALF_UP
        )
    )
    expected_dob = newer_anchor - timedelta(days=fractional_days)
    assert detail["animals"][0]["estimated_dob"] == iso(expected_dob)
    # The fraction genuinely moved the estimate earlier than the whole-month
    # anchor, and never past the next anchor.
    assert older_anchor < expected_dob < newer_anchor


async def test_batch_detail_animals_ordered_by_tag(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    batch = await make_batch(client, headers, count=12)
    detail = await get_batch(client, headers, batch["id"])
    tags = [a["tag_number"] for a in detail["animals"]]
    assert tags == sorted(tags)
    tag_prefix = tags[0].rsplit("-", 1)[0]
    assert tags[0] == f"{tag_prefix}-0001"
    assert tags[-1] == f"{tag_prefix}-0012"


async def test_open_tasks_drop_as_protocol_duties_complete(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    detail = await _backdated_batch_with_tasks(client, headers, days=50, count=1)
    assert (await list_batches(client, headers))[0]["open_tasks"] == 8
    ppr_task = next(t for t in detail["tasks"] if "PPR" in t["title"])
    await record_event(
        client,
        headers,
        scope="batch",
        purchase_batch_id=detail["batch"]["id"],
        type="VACCINE",
        product_name="PPR vaccine",
        task_id=ppr_task["id"],
    )
    assert (await list_batches(client, headers))[0]["open_tasks"] == 7


async def test_schedule_ppr_has_no_booster(client: httpx.AsyncClient) -> None:
    """SPEC: PPR first dose at 3 months, no booster, repeat every 3 years."""
    headers = await owner_with_farm(client)
    dob = today() - timedelta(days=30)
    animal = await make_animal(client, headers, date_of_birth=iso(dob))
    schedule = await get_schedule(client, headers, animal["id"])
    ppr = row_by_name(schedule, "PPR")
    assert ppr["first_due"] == iso(add_months(dob, 3))
    assert ppr["booster_due"] is None
    assert ppr["status"] == "UPCOMING"


async def test_schedule_timing_notes_seeded(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    schedule = await get_schedule(client, headers, animal["id"])
    assert "September" in row_by_name(schedule, "FMD")["timing_note"]
    assert "June" in row_by_name(schedule, "Deworming")["timing_note"]


async def test_schedule_last_done_uses_latest_matching_event(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, date_of_birth=iso(today() - timedelta(days=400)))
    first = today() - timedelta(days=100)
    await record_event(
        client, headers, animal_id=animal["id"], type="VACCINE", product_name="PPR", date=iso(first)
    )
    await record_event(
        client, headers, animal_id=animal["id"], type="VACCINE", product_name="PPR booster shot"
    )
    schedule = await get_schedule(client, headers, animal["id"])
    ppr = row_by_name(schedule, "PPR")
    assert ppr["last_done"] == iso(today())  # the most recent matching event


async def test_health_log_orders_across_mixed_scopes(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, tag="SOLO-1")
    batch = await make_batch(client, headers, count=2)
    old = today() - timedelta(days=5)
    await record_event(
        client, headers, animal_id=animal["id"], type="TREATMENT", date=iso(old), notes="old"
    )
    await record_event(
        client, headers, scope="batch", purchase_batch_id=batch["id"], type="VACCINE"
    )
    events = await list_events(client, headers)
    assert len(events) == 3
    assert events[-1]["notes"] == "old"  # oldest last
    assert {e["purchase_batch_id"] for e in events[:2]} == {batch["id"]}


async def test_health_log_pagination_reports_full_count(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, tag="PAGE-HEALTH")
    created_ids = []
    for note in ("first", "second", "third"):
        events = await record_event(
            client,
            headers,
            animal_id=animal["id"],
            type="TREATMENT",
            notes=note,
        )
        created_ids.append(events[0]["id"])
    response = await client.get("/api/health/events?limit=2&offset=1", headers=headers)
    assert response.status_code == 200, response.text
    page = response.json()
    assert (page["total"], page["limit"], page["offset"]) == (3, 2, 1)
    assert [event["id"] for event in page["events"]] == [created_ids[1], created_ids[0]]


async def test_two_batches_have_independent_counts(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    detail_a = await _backdated_batch_with_tasks(client, headers, days=50, count=2)
    batch_b = await make_batch(client, headers, count=5, supplier="Second Supplier")
    await complete_quarantine_prerequisites(client, headers, detail_a)
    release = next(t for t in detail_a["tasks"] if t["category"] == "BUCKET_MOVE")
    resp = await client.post(f"/api/tasks/{release['id']}/complete", headers=headers)
    assert resp.status_code == 200, resp.text
    batches = {b["id"]: b for b in await list_batches(client, headers)}
    assert batches[detail_a["batch"]["id"]]["open_tasks"] == 0
    assert batches[detail_a["batch"]["id"]]["animals_created"] == 2
    assert batches[batch_b["id"]]["open_tasks"] == 8  # untouched by A's completion
    assert batches[batch_b["id"]]["animals_created"] == 5


# ---------------------------------------------------------------------------
# N1: health event costs must land in the ledger.
# ---------------------------------------------------------------------------
async def test_health_event_with_cost_creates_expense_transaction(
    client: httpx.AsyncClient,
) -> None:
    """Recording a health event with cost must book a matching EXPENSE
    Transaction — otherwise monthly P&L reads ₹0 medicine/vet spend even
    when HealthEvent.cost is set."""
    headers = await owner_with_farm(client)
    # Two animals in the same bucket → scope=bucket exercises the
    # multi-animal cost-split branch.
    await make_animal(client, headers, tag="HE-A1", bucket="RESTING")
    await make_animal(client, headers, tag="HE-A2", bucket="RESTING")
    await record_event(
        client,
        headers,
        scope="bucket",
        bucket="RESTING",
        type="VACCINE",
        product_name="PPR",
        cost=500.0,
    )
    fin = (await client.get("/api/finance", headers=headers)).json()
    med_txns = [
        t for t in fin["transactions"] if t["category"] == "MEDICINE" and t["type"] == "EXPENSE"
    ]
    assert len(med_txns) == 1
    assert med_txns[0]["amount"] == 500.0
    # Multi-animal round: cost is not attributable to one animal.
    assert med_txns[0]["related_animal_id"] is None
    assert fin["total_expense"] >= 500.0

    solo = await make_animal(client, headers, tag="HE-A3")
    await record_event(
        client,
        headers,
        scope="animal",
        animal_id=solo["id"],
        type="TREATMENT",
        product_name="Meloxicam",
        cost=150.0,
    )
    fin = (await client.get("/api/finance", headers=headers)).json()
    vet_txns = [t for t in fin["transactions"] if t["category"] == "VET" and t["amount"] == 150.0]
    assert len(vet_txns) == 1
    # Single-animal record: transaction is attributed to that animal.
    assert vet_txns[0]["related_animal_id"] == solo["id"]


async def test_health_event_without_cost_creates_no_transaction(
    client: httpx.AsyncClient,
) -> None:
    """Skipping the cost input (owner just wants an event log entry) must
    NOT create a phantom ₹0 transaction."""
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, tag="HE-B1")
    await record_event(
        client,
        headers,
        scope="animal",
        animal_id=animal["id"],
        type="VACCINE",
        product_name="FMD",
        # cost omitted
    )
    fin = (await client.get("/api/finance", headers=headers)).json()
    assert not any(t["category"] in {"MEDICINE", "VET"} for t in fin["transactions"])


# ---------------------------------------------------------------------------
# Audit 2026-08-09 regressions
# ---------------------------------------------------------------------------
async def test_supplier_name_cannot_hijack_the_quarantine_vaccine_template(
    client: httpx.AsyncClient,
) -> None:
    """Auto-generated titles embed the operator-supplied supplier, so a
    supplier named after a disease used to decide which programme item a
    quarantine duty belonged to."""
    headers = await owner_with_farm(client)
    batch = await make_batch(
        client,
        headers,
        count=1,
        supplier="PPR Traders",
        date=iso(today() - timedelta(days=50)),
    )
    detail = await get_batch(client, headers, batch["id"])
    et_task = next(t for t in detail["tasks"] if "ET + Tetanus" in t["title"])
    pox_task = next(t for t in detail["tasks"] if "Goat Pox" in t["title"])
    fmd_task = next(t for t in detail["tasks"] if "FMD" in t["title"])
    assert et_task["title"].startswith("[PPR Traders #")

    # This is one combined duty: recording ET alone must not silently close
    # the missing Tetanus half merely because the storage template is ET.
    half = await post_event(
        client,
        headers,
        scope="batch",
        purchase_batch_id=batch["id"],
        type="VACCINE",
        disease_target="ET",
        task_id=et_task["id"],
    )
    assert half.status_code == 422, half.text
    assert half.json()["detail"] == "Disease target does not match the linked task"

    # The correct entry is accepted and filed under the real programme item.
    events = await record_event(
        client,
        headers,
        scope="batch",
        purchase_batch_id=batch["id"],
        type="VACCINE",
        product_name="Raksha-ET",
        disease_target="ET + TT",
        task_id=et_task["id"],
    )
    assert events[0]["schedule_template_name"] == "Enterotoxaemia (ET)"

    # Claiming the supplier's disease against another duty is rejected.
    hijack = await post_event(
        client,
        headers,
        scope="batch",
        purchase_batch_id=batch["id"],
        type="VACCINE",
        disease_target="PPR",
        task_id=pox_task["id"],
    )
    assert hijack.status_code == 422, hijack.text
    assert hijack.json()["detail"] == "Disease target does not match the linked task"

    # The silent variant: a blank target must still resolve the real template.
    blank = await record_event(
        client,
        headers,
        scope="batch",
        purchase_batch_id=batch["id"],
        type="VACCINE",
        product_name="Raksha-Triovac",
        task_id=fmd_task["id"],
    )
    assert blank[0]["schedule_template_name"] == "FMD"
    assert blank[0]["disease_target"] == "FMD"
    refreshed = await get_batch(client, headers, batch["id"])
    assert next(t for t in refreshed["tasks"] if t["id"] == pox_task["id"])["status"] == "PENDING"


async def test_tag_number_cannot_hijack_the_pre_kidding_vaccine_template(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    doe = await make_animal(
        client,
        headers,
        tag="PPR-01",
        date_of_birth=iso(today() - timedelta(days=800)),
        weight_kg=26.0,
        weight_date=iso(today() - timedelta(days=800)),
        current_bucket="BREEDING",
    )
    buck = await make_animal(
        client,
        headers,
        tag="PPR-01-BUCK",
        sex="M",
        date_of_birth=iso(today() - timedelta(days=800)),
        weight_kg=30.0,
        weight_date=iso(today() - timedelta(days=800)),
        current_bucket="BREEDING",
    )
    breeding_date = today() - timedelta(days=120)
    created = await client.post(
        "/api/breeding",
        json={"doe_id": doe["id"], "buck_id": buck["id"], "breeding_date": iso(breeding_date)},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    confirmed = await client.post(
        f"/api/breeding/{created.json()['id']}/ultrasound",
        json={"pregnant": True, "kid_count": 1, "date": created.json()["ultrasound_date"]},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text

    tabs = await client.get("/api/tasks", headers=headers)
    duties = tabs.json()["today"] + tabs.json()["overdue"] + tabs.json()["upcoming"]
    pre_kidding = next(t for t in duties if t["category"] == "VACCINE")
    assert pre_kidding["title"] == "Pre-kidding ET+TT vaccine: PPR-01"

    hijack = await post_event(
        client,
        headers,
        scope="animal",
        animal_id=doe["id"],
        type="VACCINE",
        disease_target="PPR",
        task_id=pre_kidding["id"],
    )
    assert hijack.status_code == 422, hijack.text
    assert hijack.json()["detail"] == "Disease target does not match the linked task"

    # The combined duty is closed only when BOTH components were recorded.
    half = await post_event(
        client,
        headers,
        scope="animal",
        animal_id=doe["id"],
        type="VACCINE",
        disease_target="ET",
        task_id=pre_kidding["id"],
    )
    assert half.status_code == 422, half.text
    assert half.json()["detail"] == "Disease target does not match the linked task"

    recorded = await record_event(
        client,
        headers,
        scope="animal",
        animal_id=doe["id"],
        type="VACCINE",
        disease_target="ET + TT",
        task_id=pre_kidding["id"],
    )
    assert recorded[0]["schedule_template_name"] == "ET + TT pre-kidding"
    # ...and it is filed under THAT template id: the pre-kidding duty is not the
    # annual ET programme item, whose schedule row must stay untouched.
    schedule = await get_schedule(client, headers, doe["id"])
    annual_et = row_by_name(schedule, "Enterotoxaemia (ET)")
    assert annual_et["last_done"] is None
    assert annual_et["status"] != "DONE"


async def test_event_type_must_match_the_linked_task(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    detail = await _backdated_batch_with_tasks(client, headers, days=50, count=1)
    ppr_task = next(t for t in detail["tasks"] if "PPR" in t["title"])
    response = await post_event(
        client,
        headers,
        scope="batch",
        purchase_batch_id=detail["batch"]["id"],
        type="DEWORMING",
        product_name="Albendazole",
        task_id=ppr_task["id"],
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "Health event type must match the linked task"
    refreshed = await get_batch(client, headers, detail["batch"]["id"])
    assert next(t for t in refreshed["tasks"] if t["id"] == ppr_task["id"])["status"] == "PENDING"


async def test_animal_scoped_event_cannot_close_a_batch_linked_duty(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    detail = await _backdated_batch_with_tasks(client, headers, days=50, count=1)
    ppr_task = next(t for t in detail["tasks"] if "PPR" in t["title"])
    response = await post_event(
        client,
        headers,
        scope="animal",
        animal_id=detail["animals"][0]["id"],
        type="VACCINE",
        product_name="PPR vaccine",
        task_id=ppr_task["id"],
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "Health event scope must match the linked batch"


async def test_batch_scoped_event_cannot_close_an_animal_linked_duty(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    detail = await _backdated_batch_with_tasks(client, headers, days=50, count=1)
    doe = await make_animal(
        client,
        headers,
        tag="SCOPE-DOE",
        date_of_birth=iso(today() - timedelta(days=800)),
        weight_kg=26.0,
        weight_date=iso(today() - timedelta(days=800)),
        current_bucket="BREEDING",
    )
    buck = await make_animal(
        client,
        headers,
        tag="SCOPE-BUCK",
        sex="M",
        date_of_birth=iso(today() - timedelta(days=800)),
        weight_kg=30.0,
        weight_date=iso(today() - timedelta(days=800)),
        current_bucket="BREEDING",
    )
    created = await client.post(
        "/api/breeding",
        json={
            "doe_id": doe["id"],
            "buck_id": buck["id"],
            "breeding_date": iso(today() - timedelta(days=120)),
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    confirmed = await client.post(
        f"/api/breeding/{created.json()['id']}/ultrasound",
        json={"pregnant": True, "kid_count": 1, "date": created.json()["ultrasound_date"]},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    tabs = await client.get("/api/tasks", headers=headers)
    duties = tabs.json()["today"] + tabs.json()["overdue"] + tabs.json()["upcoming"]
    pre_kidding = next(
        t for t in duties if t["category"] == "VACCINE" and t["animal_id"] == doe["id"]
    )
    response = await post_event(
        client,
        headers,
        scope="batch",
        purchase_batch_id=detail["batch"]["id"],
        type="VACCINE",
        disease_target="ET + TT",
        task_id=pre_kidding["id"],
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "Health event scope must match the linked animal"


async def test_animal_scoped_event_cannot_close_another_animals_duty(
    client: httpx.AsyncClient,
) -> None:
    """The linked-animal guard is a disjunction: right scope, wrong animal fails.

    Regression: collapsing `scope != "animal" or animal_id != task.animal_id`
    into `and` let a vaccine recorded against an unrelated bystander close a
    doe's pre-kidding duty.
    """
    headers = await owner_with_farm(client)
    doe = await make_animal(
        client,
        headers,
        tag="PK-DOE",
        date_of_birth=iso(today() - timedelta(days=800)),
        weight_kg=26.0,
        weight_date=iso(today() - timedelta(days=800)),
        current_bucket="BREEDING",
    )
    buck = await make_animal(
        client,
        headers,
        tag="PK-BUCK",
        sex="M",
        date_of_birth=iso(today() - timedelta(days=800)),
        weight_kg=30.0,
        weight_date=iso(today() - timedelta(days=800)),
        current_bucket="BREEDING",
    )
    bystander = await make_animal(client, headers, tag="PK-OTHER")
    created = await client.post(
        "/api/breeding",
        json={
            "doe_id": doe["id"],
            "buck_id": buck["id"],
            "breeding_date": iso(today() - timedelta(days=120)),
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    confirmed = await client.post(
        f"/api/breeding/{created.json()['id']}/ultrasound",
        json={"pregnant": True, "kid_count": 1, "date": created.json()["ultrasound_date"]},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    tabs = await client.get("/api/tasks", headers=headers)
    duties = tabs.json()["today"] + tabs.json()["overdue"] + tabs.json()["upcoming"]
    pre_kidding = next(
        t for t in duties if t["category"] == "VACCINE" and t["animal_id"] == doe["id"]
    )
    response = await post_event(
        client,
        headers,
        scope="animal",
        animal_id=bystander["id"],
        type="VACCINE",
        disease_target="ET + TT",
        task_id=pre_kidding["id"],
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "Health event scope must match the linked animal"
    tabs = await client.get("/api/tasks", headers=headers)
    duties = tabs.json()["today"] + tabs.json()["overdue"] + tabs.json()["upcoming"]
    assert next(t for t in duties if t["id"] == pre_kidding["id"])["status"] == "PENDING"
    assert await list_events(client, headers) == []


async def test_linked_template_must_match_the_duty(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    detail = await _backdated_batch_with_tasks(client, headers, days=50, count=1)
    et_task = next(t for t in detail["tasks"] if "ET + Tetanus" in t["title"])
    response = await post_event(
        client,
        headers,
        scope="batch",
        purchase_batch_id=detail["batch"]["id"],
        type="VACCINE",
        schedule_template_name="PPR",
        task_id=et_task["id"],
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "Health template does not match the linked task"

    unknown = await post_event(
        client,
        headers,
        scope="batch",
        purchase_batch_id=detail["batch"]["id"],
        type="VACCINE",
        schedule_template_name="Made-up programme",
        task_id=et_task["id"],
    )
    assert unknown.status_code == 422, unknown.text
    assert unknown.json()["detail"] == "Unknown schedule template"


async def test_clear_restriction_version_mismatch_and_no_active_hold_are_distinct(
    client: httpx.AsyncClient,
) -> None:
    """The stale-version guard shadows the no-active-hold guard for an animal
    that never carried a hold, so both need their own case."""
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, tag="HOLD-CLEAR")
    await record_event(
        client,
        headers,
        scope="animal",
        animal_id=animal["id"],
        type="TREATMENT",
        disease_target="Reportable-condition concern",
        suspected_scheduled_disease=True,
    )
    first = await client.post(
        f"/api/health/restrictions/{animal['id']}/clear",
        json={"clearance_reference": "AHD-2026-1", "expected_restriction_version": 1},
        headers=headers,
    )
    assert first.status_code == 204, first.text
    assert first.content == b""

    again = await client.post(
        f"/api/health/restrictions/{animal['id']}/clear",
        json={"clearance_reference": "AHD-2026-2", "expected_restriction_version": 1},
        headers=headers,
    )
    assert again.status_code == 409, again.text
    assert again.json()["detail"] == "Animal has no active movement restriction"

    history = await client.get(f"/api/health/restrictions/{animal['id']}", headers=headers)
    assert history.status_code == 200, history.text
    assert [row["action"] for row in history.json()["actions"]].count("CLEARED") == 1
    after = await get_animal(client, headers, animal["id"])
    assert after["restriction_clearance_reference"] == "AHD-2026-1"


async def test_treatment_follow_up_date_is_recordable(client: httpx.AsyncClient) -> None:
    """next_due_date requires stated provenance, but a treatment/footbath/
    vitamin follow-up has no seeded programme item to bind it to."""
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, tag="FOLLOWUP-1")
    events = await record_event(
        client,
        headers,
        scope="animal",
        animal_id=animal["id"],
        type="TREATMENT",
        product_name="Oxytetracycline",
        next_due_date=iso(today() + timedelta(days=14)),
        schedule_template_name="Recheck course",
        next_due_authority="Dr. Rao",
    )
    assert events[0]["next_due_date"] == iso(today() + timedelta(days=14))
    assert events[0]["schedule_template_name"] == "Recheck course"
    # Free-text provenance must never bind a real vaccine template, so it
    # cannot leak into the vaccination programme.
    schedule = await get_schedule(client, headers, animal["id"])
    assert all(row["last_done"] is None for row in schedule["rows"])


async def test_vaccine_event_still_requires_a_seeded_template(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, tag="FOLLOWUP-2")
    response = await post_event(
        client,
        headers,
        scope="animal",
        animal_id=animal["id"],
        type="VACCINE",
        next_due_date=iso(today() + timedelta(days=14)),
        schedule_template_name="Recheck course",
        next_due_authority="Dr. Rao",
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "Unknown schedule template"


async def test_template_abbreviation_is_an_accepted_disease_target(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, tag="HS-1")
    events = await record_event(
        client,
        headers,
        scope="animal",
        animal_id=animal["id"],
        type="VACCINE",
        product_name="Raksha-HS",
        disease_target="HS",
        schedule_template_name="Haemorrhagic Septicaemia (HS)",
    )
    assert events[0]["schedule_template_name"] == "Haemorrhagic Septicaemia (HS)"
    assert row_by_name(
        await get_schedule(client, headers, animal["id"]), "Haemorrhagic Septicaemia (HS)"
    )["last_done"] == iso(today())


async def test_schedule_templates_lists_the_only_names_events_accept(
    client: httpx.AsyncClient,
) -> None:
    """The recording form needs the seeded names to be discoverable.

    ``validated_template`` accepts only an exact ``vaccine_templates.name`` for
    a VACCINE/DEWORMING event, and a ``next_due_date`` requires a schedule
    name — so without this endpoint the field was free text whose every value
    422'd unless the operator already knew one of the seeded programme names.
    """
    headers = await owner_with_farm(client)
    listing = await client.get("/api/health/schedule-templates", headers=headers)
    assert listing.status_code == 200, listing.text
    templates = listing.json()["templates"]
    assert templates, "seeded reference data must be exposed"
    names = [item["name"] for item in templates]
    assert names == sorted(names), "stable, ordered list"
    assert "Deworming" in names
    by_name = {item["name"]: item for item in templates}
    assert by_name["Deworming"]["event_type"] == "DEWORMING"
    assert all(
        item["event_type"] == "VACCINE" for name, item in by_name.items() if name != "Deworming"
    )

    # Every advertised name must actually be accepted by the write path.
    animal = await client.post(
        "/api/animals",
        json={
            "tag_number": "TPL-1",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "BREEDING",
        },
        headers=headers,
    )
    assert animal.status_code == 201, animal.text
    animal_id = animal.json()["id"]

    vaccine = next(item for item in templates if item["event_type"] == "VACCINE")
    recorded = await client.post(
        "/api/health/events",
        json={
            "scope": "animal",
            "animal_id": animal_id,
            "type": "VACCINE",
            "product_name": "Programme dose",
            "date": today().isoformat(),
            "next_due_date": (today() + timedelta(days=365)).isoformat(),
            "schedule_template_name": vaccine["name"],
            "next_due_authority": "Farm veterinarian",
        },
        headers=headers,
    )
    assert recorded.status_code in (200, 201), recorded.text
