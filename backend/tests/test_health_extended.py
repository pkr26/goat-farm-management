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

import httpx

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
    return await client.post("/api/health/events", json=payload, headers=headers)


async def record_event(client: httpx.AsyncClient, headers: dict, **payload: object) -> list[dict]:
    resp = await post_event(client, headers, **payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def list_events(client: httpx.AsyncClient, headers: dict) -> list[dict]:
    resp = await client.get("/api/health/events", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def get_schedule(client: httpx.AsyncClient, headers: dict, animal_id: int) -> dict:
    resp = await client.get(f"/api/health/schedule/{animal_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def row_by_name(schedule: dict, name: str) -> dict:
    return next(r for r in schedule["rows"] if r["template_name"] == name)


async def list_batches(client: httpx.AsyncClient, headers: dict) -> list[dict]:
    resp = await client.get("/api/purchases", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def get_batch(client: httpx.AsyncClient, headers: dict, batch_id: int) -> dict:
    resp = await client.get(f"/api/purchases/{batch_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


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


async def test_record_event_very_long_notes(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    notes = "x" * 10_000  # notes is a Text column with no schema cap
    events = await record_event(
        client, headers, animal_id=animal["id"], type="TREATMENT", notes=notes
    )
    assert events[0]["notes"] == notes


async def test_record_event_unknown_extra_field_ignored(client: httpx.AsyncClient) -> None:
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
    assert resp.status_code == 201, resp.text
    event = resp.json()[0]
    assert "hacker_field" not in event


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
    await make_animal(client, headers, tag="BR-1", bucket="BREEDING")
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


async def test_batch_scope_still_targets_animals_after_release(
    client: httpx.AsyncClient,
) -> None:
    """Released (FOUNDATION) animals keep purchase_batch_id — a batch-scoped
    booster event still reaches them."""
    headers = await owner_with_farm(client)
    batch = await make_batch(client, headers, count=2, date=iso(today() - timedelta(days=50)))
    detail = await get_batch(client, headers, batch["id"])
    footbath = next(t for t in detail["tasks"] if t["category"] == "BUCKET_MOVE")
    resp = await client.post(f"/api/tasks/{footbath['id']}/complete", headers=headers)
    assert resp.status_code == 200, resp.text
    detail = await get_batch(client, headers, batch["id"])
    assert all(a["current_bucket"] == "FOUNDATION" for a in detail["animals"])
    events = await record_event(
        client, headers, scope="batch", purchase_batch_id=batch["id"], type="VACCINE"
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
    future = today() + timedelta(days=2)  # tomorrow is allowed (tz headroom)
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
        client, headers, animal_id=animal["id"], type="VACCINE", next_due_date="2099-01-01"
    )
    assert events[0]["next_due_date"] == "2099-01-01"


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


async def test_event_ignores_quarantine_category_task_id(client: httpx.AsyncClient) -> None:
    """Only VACCINE/DEWORMING duties may be closed through the health form;
    a smuggled QUARANTINE task_id is ignored while the event is recorded."""
    headers = await owner_with_farm(client)
    detail = await _backdated_batch_with_tasks(client, headers)
    rest_task = next(t for t in detail["tasks"] if t["category"] == "QUARANTINE")
    await record_event(
        client,
        headers,
        scope="batch",
        purchase_batch_id=detail["batch"]["id"],
        type="VITAMIN",
        task_id=rest_task["id"],
    )
    detail = await get_batch(client, headers, detail["batch"]["id"])
    task = next(t for t in detail["tasks"] if t["id"] == rest_task["id"])
    assert task["status"] == "PENDING"


async def test_event_ignores_bucket_move_task_id(client: httpx.AsyncClient) -> None:
    """The day-45 release duty cannot be closed via the health form — and the
    release side effect must not fire either."""
    headers = await owner_with_farm(client)
    detail = await _backdated_batch_with_tasks(client, headers)
    release_task = next(t for t in detail["tasks"] if t["category"] == "BUCKET_MOVE")
    await record_event(
        client,
        headers,
        scope="batch",
        purchase_batch_id=detail["batch"]["id"],
        type="FOOTBATH",
        product_name="10% Zinc Sulfate",
        task_id=release_task["id"],
    )
    detail = await get_batch(client, headers, detail["batch"]["id"])
    task = next(t for t in detail["tasks"] if t["id"] == release_task["id"])
    assert task["status"] == "PENDING"
    assert all(a["current_bucket"] == "QUARANTINE" for a in detail["animals"])


async def test_event_with_already_done_task_id_is_harmless(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    detail = await _backdated_batch_with_tasks(client, headers)
    ppr_task = next(t for t in detail["tasks"] if "PPR" in t["title"])
    payload: dict = {
        "scope": "batch",
        "purchase_batch_id": detail["batch"]["id"],
        "type": "VACCINE",
        "task_id": ppr_task["id"],
    }
    await record_event(client, headers, **payload)
    # replay with the same (now DONE) task_id: event still recorded, no error
    events = await record_event(client, headers, **payload)
    assert len(events) == 2
    detail = await get_batch(client, headers, detail["batch"]["id"])
    task = next(t for t in detail["tasks"] if t["id"] == ppr_task["id"])
    assert task["status"] == "DONE"


async def test_event_with_nonexistent_task_id_still_records(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    events = await record_event(
        client, headers, animal_id=animal["id"], type="VACCINE", task_id=999999
    )
    assert len(events) == 1


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
    floater_role = await make_custom_role(client, headers, "Floater", ["health.manage"])
    await add_worker(client, headers, floater_role, "floater@farm.in")
    floater, floater_id = await login_user(client, "floater@farm.in")
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

    # Direct user assignment (no matching role) also satisfies the rule.
    deworm_task = next(t for t in detail["tasks"] if t["category"] == "DEWORMING")
    resp = await client.post(
        "/api/tasks",
        json={
            "title": "Extra deworm round",
            "due_date": iso(today()),
            "category": "DEWORMING",
            "assigned_user_id": floater_id,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    manual_duty_id = resp.json()["id"]
    events = await record_event(
        client,
        floater,
        scope="batch",
        purchase_batch_id=batch_id,
        type="DEWORMING",
        product_name="Albendazole",
        task_id=manual_duty_id,
    )
    assert len(events) == 2
    detail = await get_batch(client, headers, batch_id)
    assert next(t for t in detail["tasks"] if t["id"] == deworm_task["id"])["status"] == "PENDING"
    # ...while floater's own directly-assigned duty was closed and attributed.
    resp = await client.get("/api/tasks", headers=headers)
    manual = next(t for t in resp.json()["completed"] if t["id"] == manual_duty_id)
    assert manual["status"] == "DONE"
    assert manual["completed_by_id"] == floater_id


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
    # FMD booster is 3.5 weeks after the first dose
    assert fmd["booster_due"] == iso(expected_first + timedelta(weeks=3.5))
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
    batch = await make_batch(client, headers, count=5, create_animals=False)
    assert batch["animals_created"] == 0
    detail = await get_batch(client, headers, batch["id"])
    assert detail["animals"] == []
    assert len(detail["tasks"]) == 8  # the protocol is still scheduled


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
    assert [a["tag_number"] for a in animals] == [
        f"B{batch['id']}-001",
        f"B{batch['id']}-002",
        f"B{batch['id']}-003",
    ]
    for animal in animals:
        assert animal["sex"] == "F"
        assert animal["breed"] == "Osmanabadi"
        assert animal["source"] == "PURCHASED"
        assert animal["current_bucket"] == "QUARANTINE"  # SPEC: new purchases
        assert animal["status"] == "ACTIVE"
        assert animal["purchase_date"] == iso(batch_date)
        assert animal["seller_name"] == "Kurnool Traders"
        assert animal["purchase_price"] == 3333.33  # total split per head
        assert animal["estimated_dob"] == iso(add_months(batch_date, -7))


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


async def test_create_batch_very_long_notes(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    notes = "n" * 10_000
    batch = await make_batch(client, headers, count=1, notes=notes)
    assert batch["notes"] == notes


async def test_list_batches_empty(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    assert await list_batches(client, headers) == []


async def test_list_batches_newest_first(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    older = await make_batch(client, headers, count=1, date=iso(today() - timedelta(days=30)))
    newer = await make_batch(client, headers, count=1, date=iso(today()))
    batches = await list_batches(client, headers)
    assert [b["id"] for b in batches] == [newer["id"], older["id"]]


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
    release = next(t for t in detail["tasks"] if t["category"] == "BUCKET_MOVE")
    resp = await client.post(f"/api/tasks/{release['id']}/complete", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "DONE"
    detail = await get_batch(client, headers, detail["batch"]["id"])
    # SPEC: day-45 footbath → release to FOUNDATION bucket
    assert all(a["current_bucket"] == "FOUNDATION" for a in detail["animals"])
    assert detail["batch"]["open_tasks"] == 7


async def test_day45_completion_leaves_dead_animals_untouched(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    detail = await _backdated_batch_with_tasks(client, headers, days=50, count=2)
    dead, live = detail["animals"][0], detail["animals"][1]
    await mark_dead(client, headers, dead["id"])
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


async def test_batch_zero_avg_age_gives_no_estimated_dob(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    batch = await make_batch(client, headers, count=1, avg_age_months=0)
    detail = await get_batch(client, headers, batch["id"])
    assert detail["animals"][0]["estimated_dob"] is None


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
    future = today() + timedelta(days=2)  # tomorrow is allowed (tz headroom)
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


async def test_batch_unknown_extra_field_ignored(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await _post_batch(
        client, headers, date=iso(today()), count=1, create_animals=False, admin=True
    )
    assert resp.status_code == 201
    assert "admin" not in resp.json()


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
async def test_missing_farm_header_400(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers)
    auth_only = {"Authorization": headers["Authorization"]}
    assert (await client.get("/api/health/events", headers=auth_only)).status_code == 400
    assert (await client.get("/api/purchases", headers=auth_only)).status_code == 400
    resp = await client.post(
        "/api/health/events",
        json={"animal_id": animal["id"], "type": "VACCINE"},
        headers=auth_only,
    )
    assert resp.status_code == 400
    resp = await client.post(
        "/api/purchases/new",
        json={"date": iso(today()), "count": 1},
        headers=auth_only,
    )
    assert resp.status_code == 400
    assert (
        await client.get(f"/api/health/schedule/{animal['id']}", headers=auth_only)
    ).status_code == 400


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


async def test_foreign_farm_header_403(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, "a@farm.in", "Farm A")
    owner_b = await owner_with_farm(client, "b@farm.in", "Farm B")
    cross = {"Authorization": owner_a["Authorization"], "X-Farm-Id": owner_b["X-Farm-Id"]}
    assert (await client.get("/api/health/events", headers=cross)).status_code == 403
    assert (await client.get("/api/purchases", headers=cross)).status_code == 403
    resp = await client.post(
        "/api/purchases/new", json={"date": iso(today()), "count": 1}, headers=cross
    )
    assert resp.status_code == 403


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


async def test_event_with_foreign_task_id_ignored(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, "a@farm.in", "Farm A")
    owner_b = await owner_with_farm(client, "b@farm.in", "Farm B")
    detail_a = await _backdated_batch_with_tasks(client, owner_a, days=50, count=1)
    ppr_task_a = next(t for t in detail_a["tasks"] if "PPR" in t["title"])
    animal_b = await make_animal(client, owner_b, tag="B-ONLY")
    # B records an event but smuggles A's task id — ignored, event still recorded
    events = await record_event(
        client, owner_b, animal_id=animal_b["id"], type="VACCINE", task_id=ppr_task_a["id"]
    )
    assert len(events) == 1
    detail_a = await get_batch(client, owner_a, detail_a["batch"]["id"])
    task = next(t for t in detail_a["tasks"] if t["id"] == ppr_task_a["id"])
    assert task["status"] == "PENDING"  # A's duty untouched


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
async def test_quarantine_deworming_via_bucket_scope(client: httpx.AsyncClient) -> None:
    """Day-4 protocol step executed the way the SPEC describes: deworm the
    whole quarantine ward at once."""
    headers = await owner_with_farm(client)
    detail = await _backdated_batch_with_tasks(client, headers, days=10, count=3)
    deworm_task = next(t for t in detail["tasks"] if t["category"] == "DEWORMING")
    events = await record_event(
        client,
        headers,
        scope="bucket",
        bucket="QUARANTINE",
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


async def test_batch_fractional_avg_age_truncates_for_estimated_dob(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    batch_date = today() - timedelta(days=10)
    batch = await make_batch(client, headers, count=1, date=iso(batch_date), avg_age_months=7.9)
    detail = await get_batch(client, headers, batch["id"])
    # int(7.9) == 7 whole months are subtracted from the arrival date
    assert detail["animals"][0]["estimated_dob"] == iso(add_months(batch_date, -7))


async def test_batch_detail_animals_ordered_by_tag(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    batch = await make_batch(client, headers, count=12)
    detail = await get_batch(client, headers, batch["id"])
    tags = [a["tag_number"] for a in detail["animals"]]
    assert tags == sorted(tags)
    assert tags[0] == f"B{batch['id']}-001"
    assert tags[-1] == f"B{batch['id']}-012"


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


async def test_two_batches_have_independent_counts(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    detail_a = await _backdated_batch_with_tasks(client, headers, days=50, count=2)
    batch_b = await make_batch(client, headers, count=5, supplier="Second Supplier")
    release = next(t for t in detail_a["tasks"] if t["category"] == "BUCKET_MOVE")
    resp = await client.post(f"/api/tasks/{release['id']}/complete", headers=headers)
    assert resp.status_code == 200, resp.text
    batches = {b["id"]: b for b in await list_batches(client, headers)}
    assert batches[detail_a["batch"]["id"]]["open_tasks"] == 7
    assert batches[detail_a["batch"]["id"]]["animals_created"] == 2
    assert batches[batch_b["id"]]["open_tasks"] == 8  # untouched by A's completion
    assert batches[batch_b["id"]]["animals_created"] == 5
