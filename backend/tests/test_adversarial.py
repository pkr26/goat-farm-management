"""Adversarial / abuse-case tests (port of v1 tests/test_adversarial.py).

Route level, against the async JSON API (httpx + real PostgreSQL): malformed
input must never produce a 500, cross-tenant and replay attacks must be
rejected, state transitions must be guarded (no double-selling, no
re-ultrasound, no kidding without a confirmed pregnancy), and the domain
invariants (cull-flag lifecycle, recurring-duty idempotency, auto-tag
uniquification, feed-mix stock safety) must hold.

v1 → v2 translations:
- Form type/format garbage (bad enums, non-finite/negative numbers, future
  dates, out-of-range values) → 422 from the pydantic schemas.
- v1 flashed state/input errors → 400 with the message in `detail`.
- Replay/conflict guards (double ultrasound, double kidding, form-linked or
  not-due-yet task completion, self-verify, abort-after-kidding, sold-doe
  kidding) → 409 where the router says so.
- v1 service-level (in-memory SQLite) tests are ported to the equivalent
  route-level flows — setup through the API only. Where v1 completed an
  early auto task at the service level (weaning between a doe's kiddings),
  the API equivalent is a manual move back to RESTING, because the API
  blocks early auto-generated tasks by design.
- v1 quirks that no longer apply: flash messages, redirects, session
  cookies. test_next_redirect_stays_local asserts the JSON API simply never
  redirects; test_default_secret_session_forgery_fails forges JWTs instead
  of itsdangerous cookies; the legacy DB-seeded membership in
  test_team_takeover_and_escalation_guards is replaced by an API-reachable
  equivalent (a worker who later registers his own farm).
"""

from datetime import UTC, date, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.config import get_settings
from app.utils import today

from .conftest import login, owner_with_farm


# ---------------------------------------------------------------------------
# Setup helpers (everything goes through the API)
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
) -> int:
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
    return resp.json()["id"]


async def make_doe(
    client: httpx.AsyncClient, headers: dict, tag: str = "D-1", age_days: int = 800
) -> int:
    """A breeding-ready doe (>=10 months old, 26 kg entry weight, FOUNDATION)."""
    dob = date.today() - timedelta(days=age_days)
    return await make_animal(
        client,
        headers,
        tag=tag,
        sex="F",
        date_of_birth=iso(dob),
        weight_kg=26.0,
        weight_date=iso(dob),
    )


async def make_buck(client: httpx.AsyncClient, headers: dict, tag: str = "B-1") -> int:
    dob = date.today() - timedelta(days=800)
    return await make_animal(
        client,
        headers,
        tag=tag,
        sex="M",
        bucket="BREEDING",
        date_of_birth=iso(dob),
        weight_kg=30.0,
        weight_date=iso(dob),
    )


async def post_breeding(
    client: httpx.AsyncClient, headers: dict, doe_id: int, buck_id: int, **overrides: object
) -> httpx.Response:
    payload = {
        "doe_id": doe_id,
        "buck_id": buck_id,
        "breeding_date": iso(date.today()),
    } | overrides
    return await client.post("/api/breeding", json=payload, headers=headers)


async def make_breeding(
    client: httpx.AsyncClient, headers: dict, doe_id: int, buck_id: int, **overrides: object
) -> int:
    # This helper is used by outcome-flow tests.  A default observation date
    # must not predate the mandatory +32-day ultrasound check.
    overrides = {"breeding_date": iso(date.today() - timedelta(days=35))} | overrides
    resp = await post_breeding(client, headers, doe_id, buck_id, **overrides)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def breed_doe(
    client: httpx.AsyncClient, headers: dict, tag: str = "D-1", bred_days_ago: int = 35
) -> tuple[int, int, int]:
    """Breeding-ready doe + buck and one PENDING breeding. Returns (doe, buck, br) ids.
    `bred_days_ago` backdates the breeding so a kidding today lands at a
    realistic gestation (goats kid ~150 days post-breeding)."""
    doe_id = await make_doe(client, headers, tag=tag)
    buck_id = await make_buck(client, headers, tag=f"{tag}-BUCK")
    overrides: dict[str, object] = {}
    if bred_days_ago:
        overrides["breeding_date"] = iso(date.today() - timedelta(days=bred_days_ago))
    br_id = await make_breeding(client, headers, doe_id, buck_id, **overrides)
    return doe_id, buck_id, br_id


async def ultrasound(
    client: httpx.AsyncClient,
    headers: dict,
    br_id: int,
    pregnant: bool = True,
    kid_count: int = 2,
    date_str: str | None = None,
) -> httpx.Response:
    payload: dict[str, object] = {"pregnant": pregnant}
    if pregnant:
        payload["kid_count"] = kid_count
    if date_str is not None:
        payload["date"] = date_str
    return await client.post(
        f"/api/breeding/{br_id}/ultrasound",
        json=payload,
        headers=headers,
    )


async def post_kidding(
    client: httpx.AsyncClient,
    headers: dict,
    br_id: int,
    date_str: str | None = None,
    kids: list[dict] | None = None,
) -> httpx.Response:
    payload = {
        "breeding_record_id": br_id,
        "date": date_str or iso(date.today()),
        "ease": "NORMAL",
        "kids": kids if kids is not None else [{"sex": "M"}, {"sex": "F"}],
    }
    return await client.post("/api/kidding", json=payload, headers=headers)


async def get_profile(client: httpx.AsyncClient, headers: dict, animal_id: int) -> dict:
    resp = await client.get(f"/api/animals/{animal_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def get_animal(client: httpx.AsyncClient, headers: dict, animal_id: int) -> dict:
    return (await get_profile(client, headers, animal_id))["animal"]


async def list_animals(client: httpx.AsyncClient, headers: dict, **params: str) -> list[dict]:
    resp = await client.get("/api/animals", headers=headers, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()["animals"]


async def get_breeding(client: httpx.AsyncClient, headers: dict, br_id: int) -> dict:
    resp = await client.get(f"/api/breeding/{br_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def breeding_records(client: httpx.AsyncClient, headers: dict) -> list[dict]:
    resp = await client.get("/api/breeding", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["records"]


async def kidding_records(client: httpx.AsyncClient, headers: dict) -> list[dict]:
    resp = await client.get("/api/kidding", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["records"]


async def transactions(client: httpx.AsyncClient, headers: dict) -> list[dict]:
    resp = await client.get("/api/finance", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["transactions"]


async def health_events(client: httpx.AsyncClient, headers: dict) -> list[dict]:
    resp = await client.get("/api/health/events", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["events"]


async def inventory(client: httpx.AsyncClient, headers: dict) -> list[dict]:
    resp = await client.get("/api/feeding/inventory", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def purchase_batches(client: httpx.AsyncClient, headers: dict) -> list[dict]:
    resp = await client.get("/api/purchases", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["batches"]


async def task_tabs(client: httpx.AsyncClient, headers: dict) -> dict:
    resp = await client.get("/api/tasks", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def pending_tasks(tabs: dict) -> list[dict]:
    return tabs["today"] + tabs["overdue"] + tabs["upcoming"]


def all_tasks(tabs: dict) -> list[dict]:
    return pending_tasks(tabs) + tabs["awaiting"] + tabs["completed"]


def find_tasks(tabs: dict, **match: object) -> list[dict]:
    return [t for t in all_tasks(tabs) if all(t[k] == v for k, v in match.items())]


async def role_ids_by_code(client: httpx.AsyncClient, headers: dict) -> dict[str, int]:
    resp = await client.get("/api/team", headers=headers)
    assert resp.status_code == 200, resp.text
    return {r["code"]: r["id"] for r in resp.json()["roles"] if r["code"]}


async def worker_headers(client: httpx.AsyncClient, email: str, password: str, owner: dict) -> dict:
    return (await login(client, email, password)) | {"X-Farm-Id": owner["X-Farm-Id"]}


# ---------------------------------------------------------------------------
# 1. Malformed input must never 500
# ---------------------------------------------------------------------------
async def test_weight_garbage_date_bcs_and_negative_weight(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    aid = await make_animal(client, owner)
    for payload in [
        {"date": "not-a-date", "weight_kg": 10},
        {"weight_kg": 10, "bcs": "abc"},
        {"weight_kg": "-5"},  # negative weight rejected
        {"weight_kg": 10, "bcs": 99},  # BCS outside 1–5 rejected
    ]:
        resp = await client.post(f"/api/animals/{aid}/weight", json=payload, headers=owner)
        assert resp.status_code == 422, payload
    assert (await get_profile(client, owner, aid))["weights"] == []


async def test_status_change_garbage_inputs(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    aid = await make_animal(client, owner)
    resp = await client.post(
        f"/api/animals/{aid}/status",
        json={"new_status": "SOLD", "date": "garbage", "sale_price": "xyz"},
        headers=owner,
    )
    assert resp.status_code == 422
    assert await transactions(client, owner) == []


async def test_create_animal_rejects_bad_enums_and_empty_tag(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {"tag_number": "G-1", "sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"}
    for override, expected in [
        ({"sex": "X"}, 422),
        ({"source": "STOLEN"}, 422),
        ({"current_bucket": "MOON"}, 422),
        ({"birth_type": "QUINTUPLET"}, 422),
        ({"tag_number": ""}, 422),  # below min_length=1 (whitespace-only auto-generates)
        ({"date_of_birth": "32/13/2020"}, 422),
        ({"purchase_price": "abc"}, 422),
    ]:
        resp = await client.post("/api/animals", json=base | override, headers=owner)
        assert resp.status_code == expected, override
    assert (await client.get("/api/animals", headers=owner)).json()["total"] == 0


async def test_breeding_garbage_date_and_cycle(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner)
    buck = await make_buck(client, owner)
    for overrides in [{"breeding_date": "garbage"}, {"heat_cycle_number": "abc"}]:
        resp = await post_breeding(client, owner, doe, buck, **overrides)
        assert resp.status_code == 422, overrides
    assert await breeding_records(client, owner) == []


async def test_breeding_rejects_ineligible_doe_and_buck(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    male = await make_buck(client, owner, tag="M-1")
    doe = await make_doe(client, owner)
    sold = await make_animal(client, owner, tag="S-1")
    resp = await client.post(
        f"/api/animals/{sold}/status", json={"new_status": "SOLD"}, headers=owner
    )
    assert resp.status_code == 200, resp.text
    for doe_id, buck_id in [(male, male), (doe, doe), (sold, male)]:
        resp = await post_breeding(client, owner, doe_id, buck_id)
        assert resp.status_code == 400, (doe_id, buck_id)
    assert await breeding_records(client, owner) == []


async def test_purchase_batch_zero_count_and_garbage(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    for payload in [
        {"date": iso(date.today()), "count": 0, "total_price": 1000, "create_animals": True},
        {"date": iso(date.today()), "count": -3, "create_animals": True},
        {"date": "garbage", "count": 5},
        {"date": iso(date.today()), "count": 5, "avg_age_months": "abc"},
    ]:
        resp = await client.post("/api/purchases/new", json=payload, headers=owner)
        assert resp.status_code == 422, payload
    assert await purchase_batches(client, owner) == []


async def test_health_event_garbage_ids_and_cost(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    aid = await make_animal(client, owner)
    base = {"scope": "animal", "animal_id": aid, "date": iso(date.today()), "type": "TREATMENT"}
    for override in [
        {"animal_id": "abc"},
        {"date": "garbage"},
        {"cost": "abc"},
        {"task_id": "abc"},
    ]:
        resp = await client.post("/api/health/events", json=base | override, headers=owner)
        assert resp.status_code == 422, override
    assert await health_events(client, owner) == []


async def test_feeding_garbage_inputs(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post(
        "/api/feeding/dispense",
        json={"bucket": "BREEDING", "shift": "MORNING", "qty_kg": 5, "date": "garbage"},
        headers=owner,
    )
    assert resp.status_code == 422
    item_id = (await inventory(client, owner))[0]["id"]
    resp = await client.post(
        f"/api/feeding/inventory/{item_id}/add",
        json={"qty_kg": 10, "price_per_kg": "abc"},
        headers=owner,
    )
    assert resp.status_code == 422
    resp = await client.post(
        "/api/feeding/mix",
        json={"recipe_code": "FATTENING_50_50", "batch_kg": "-100"},
        headers=owner,
    )
    assert resp.status_code == 422
    # negative mix must not have added stock
    assert all(i["qty_on_hand"] == 0 for i in await inventory(client, owner))


async def test_finance_garbage_and_cross_farm_animal(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    foreign_animal = await make_animal(client, owner_b, tag="B-999")

    resp = await client.post(
        "/api/finance/new",
        json={"date": "garbage", "type": "INCOME", "category": "OTHER", "amount": 100},
        headers=owner_a,
    )
    assert resp.status_code == 422
    resp = await client.post(
        "/api/finance/new",
        json={
            "date": iso(date.today()),
            "type": "INCOME",
            "category": "OTHER",
            "amount": 100,
            "related_animal_id": foreign_animal,
        },
        headers=owner_a,
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "Related animal is not on this farm"
    assert await transactions(client, owner_a) == []


async def test_manual_task_garbage_inputs(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {"title": "X", "due_date": iso(date.today())}
    for override in [
        {"due_date": "garbage"},
        {"recur_days": "abc"},
        {"assigned_role_id": "abc"},
        {"assigned_user_id": "abc"},
    ]:
        resp = await client.post("/api/tasks", json=base | override, headers=owner)
        assert resp.status_code == 422, override
    assert all_tasks(await task_tabs(client, owner)) == []


# ---------------------------------------------------------------------------
# 2. Open redirect via the `next` parameter
# ---------------------------------------------------------------------------
async def test_next_redirect_stays_local(client: httpx.AsyncClient) -> None:
    """v1: a forged `next=//evil.com` form field had to stay a local redirect.
    The JSON API never redirects — a smuggled `next` is ignored and no
    Location header is ever produced."""
    owner = await owner_with_farm(client)
    resp = await client.post(
        "/api/tasks",
        json={"title": "Clean", "due_date": iso(date.today()), "category": "CLEANING"},
        headers=owner,
    )
    task_id = resp.json()["id"]
    resp = await client.post(
        f"/api/tasks/{task_id}/complete", json={"next": "//evil.com/phish"}, headers=owner
    )
    assert resp.status_code == 200, resp.text
    assert "location" not in resp.headers


# ---------------------------------------------------------------------------
# 3. Replay / double-submission attacks
# ---------------------------------------------------------------------------
async def test_cannot_resell_a_sold_animal(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    aid = await make_animal(client, owner)
    payload = {"new_status": "SOLD", "date": iso(date.today()), "sale_price": 5000}
    resp = await client.post(f"/api/animals/{aid}/status", json=payload, headers=owner)
    assert resp.status_code == 200, resp.text
    # replay the sale (e.g. double-click / forged request)
    resp = await client.post(f"/api/animals/{aid}/status", json=payload, headers=owner)
    assert resp.status_code == 400
    assert len(await transactions(client, owner)) == 1  # exactly one income booked


async def test_ultrasound_resubmit_does_not_duplicate_tasks(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    _, _, br_id = await breed_doe(client, owner)
    resp = await ultrasound(client, owner, br_id, pregnant=True, kid_count=2)
    assert resp.status_code == 200, resp.text
    task_count = len(all_tasks(await task_tabs(client, owner)))
    resp = await ultrasound(client, owner, br_id, pregnant=True, kid_count=2)  # double submit
    assert resp.status_code == 409
    assert len(all_tasks(await task_tabs(client, owner))) == task_count


async def test_kidding_requires_confirmed_pregnancy(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    _, _, br_id = await breed_doe(client, owner)  # still PENDING
    resp = await post_kidding(client, owner, br_id, kids=[{"sex": "M"}])
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Kidding requires a confirmed pregnancy"
    assert await kidding_records(client, owner) == []  # no kidding without confirmation


async def test_kidding_duplicate_tags_rejected_gracefully(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    _, _, br_id = await breed_doe(client, owner)
    assert (await ultrasound(client, owner, br_id)).status_code == 200
    kids = [{"tag": "DUP-1", "sex": "M"}, {"tag": "DUP-1", "sex": "F"}]
    resp = await post_kidding(client, owner, br_id, kids=kids)
    assert resp.status_code == 400  # rejected, not a 500 IntegrityError
    assert resp.json()["detail"] == "Duplicate kid tags"
    assert await kidding_records(client, owner) == []


async def test_recurring_reject_loop_spawns_one_occurrence(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post(
        "/api/tasks",
        json={
            "title": "Daily sweep",
            "due_date": iso(date.today()),
            "category": "CLEANING",
            "recur_days": 1,
        },
        headers=owner,
    )
    task_id = resp.json()["id"]
    resp = await client.post(f"/api/tasks/{task_id}/complete", headers=owner)
    assert resp.status_code == 200, resp.text
    resp = await client.post(
        f"/api/tasks/{task_id}/reject", json={"note": "not clean"}, headers=owner
    )
    assert resp.status_code == 200, resp.text
    resp = await client.post(
        f"/api/tasks/{task_id}/complete", headers=owner
    )  # redo after rejection
    assert resp.status_code == 200, resp.text
    spawned = [
        t
        for t in find_tasks(await task_tabs(client, owner), title="Daily sweep", status="PENDING")
        if t["id"] != task_id
    ]
    assert len(spawned) == 1  # not one per completion attempt


async def test_complete_task_is_idempotent(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post(
        "/api/tasks",
        json={
            "title": "Daily sweep",
            "due_date": iso(date.today()),
            "category": "CLEANING",
            "recur_days": 1,
        },
        headers=owner,
    )
    task_id = resp.json()["id"]
    resp = await client.post(f"/api/tasks/{task_id}/complete", headers=owner)
    assert resp.status_code == 200, resp.text
    resp = await client.post(f"/api/tasks/{task_id}/complete", headers=owner)  # replay on DONE
    assert resp.status_code == 400
    spawned = [t for t in all_tasks(await task_tabs(client, owner)) if t["id"] != task_id]
    assert len(spawned) == 1


# ---------------------------------------------------------------------------
# 4. Domain invariants
# ---------------------------------------------------------------------------
async def test_cull_flag_cleared_on_later_confirmed_pregnancy(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, age_days=600)
    buck = await make_buck(client, owner)
    for bred_days_ago in (120, 70):
        br_id = await make_breeding(
            client,
            owner,
            doe,
            buck,
            breeding_date=iso(date.today() - timedelta(days=bred_days_ago)),
        )
        resp = await ultrasound(
            client,
            owner,
            br_id,
            pregnant=False,
            date_str=iso(date.today() - timedelta(days=bred_days_ago - 32)),
        )
        assert resp.status_code == 200, resp.text
    assert (await get_animal(client, owner, doe))["cull_candidate"] is True
    br_id = await make_breeding(
        client,
        owner,
        doe,
        buck,
        breeding_date=iso(date.today() - timedelta(days=35)),
    )
    resp = await ultrasound(client, owner, br_id, pregnant=True, kid_count=1)
    assert resp.status_code == 200, resp.text
    # she conceived — no longer a cull candidate
    assert (await get_animal(client, owner, doe))["cull_candidate"] is False


async def test_second_kidding_auto_tags_do_not_collide(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe, buck, br1 = await breed_doe(client, owner, bred_days_ago=310)
    assert (
        await ultrasound(
            client,
            owner,
            br1,
            kid_count=1,
            date_str=iso(date.today() - timedelta(days=278)),
        )
    ).status_code == 200
    resp = await post_kidding(
        client,
        owner,
        br1,
        date_str=iso(date.today() - timedelta(days=160)),
        kids=[{"sex": "M"}],
    )
    assert resp.status_code == 201, resp.text
    # Doe back to RESTING so she can be re-bred (the API blocks early auto
    # weaning tasks by design; a manual move is the route-level equivalent of
    # v1's service-level complete_task here).
    resp = await client.post(
        f"/api/animals/{doe}/move",
        json={
            "to_bucket": "RESTING",
            "history_override": True,
            "reason": "Historical first-cycle weaning fixture",
        },
        headers=owner,
    )
    assert resp.status_code == 200, resp.text
    br2 = await make_breeding(
        client, owner, doe, buck, breeding_date=iso(date.today() - timedelta(days=150))
    )
    assert (
        await ultrasound(
            client,
            owner,
            br2,
            kid_count=1,
            date_str=iso(date.today() - timedelta(days=118)),
        )
    ).status_code == 200
    resp = await post_kidding(client, owner, br2, kids=[{"sex": "M"}])
    assert resp.status_code == 201, resp.text
    tags = {a["tag_number"] for a in await list_animals(client, owner) if a["source"] == "BORN"}
    assert "D-1-K1" in tags
    assert len(tags) == 2
    assert next(tag for tag in tags if tag != "D-1-K1").startswith("D-1-K1-A")


async def test_kidding_closes_leftover_pregnancy_tasks(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    _, _, br_id = await breed_doe(client, owner, bred_days_ago=150)
    assert (await ultrasound(client, owner, br_id, kid_count=1)).status_code == 200
    kids = [{"tag": "K-1", "sex": "M", "birth_weight": 2.5}]
    resp = await post_kidding(client, owner, br_id, kids=kids)
    assert resp.status_code == 201, resp.text
    stale = [
        t for t in pending_tasks(await task_tabs(client, owner)) if t["breeding_record_id"] == br_id
    ]
    assert stale == []  # pre-kidding vaccine / move-to-DELIVERY must not linger


async def test_deworming_appears_in_vaccination_schedule(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    aid = await make_animal(
        client, owner, tag="D-1", date_of_birth=iso(date.today() - timedelta(days=200))
    )
    resp = await client.get(f"/api/health/schedule/{aid}", headers=owner)
    assert resp.status_code == 200, resp.text
    names = [row["template_name"] for row in resp.json()["rows"]]
    assert "Deworming" in names
    assert "ET + TT pre-kidding" not in names  # pregnancy-linked stays task-driven


async def test_negative_mix_rejected_without_touching_stock(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    for item in await inventory(client, owner):
        resp = await client.post(
            f"/api/feeding/inventory/{item['id']}/add", json={"qty_kg": 100}, headers=owner
        )
        assert resp.status_code == 200, resp.text
    resp = await client.post(
        "/api/feeding/mix",
        json={"recipe_code": "FATTENING_50_50", "batch_kg": "-100"},
        headers=owner,
    )
    assert resp.status_code == 422
    assert all(i["qty_on_hand"] == 100 for i in await inventory(client, owner))


async def test_health_form_rejects_a_done_task_id(client: httpx.AsyncClient) -> None:
    """A health form cannot append an event against an already-closed task."""
    owner = await owner_with_farm(client)
    resp = await client.post(
        "/api/purchases/new",
        json={"date": iso(today() - timedelta(days=21)), "count": 1, "create_animals": True},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    batch_id = resp.json()["id"]
    vaccine = next(
        task
        for task in all_tasks(await task_tabs(client, owner))
        if task["category"] == "VACCINE"
        and task["purchase_batch_id"] == batch_id
        and task["due_date"] <= iso(today())
    )
    task_id = vaccine["id"]
    preview = await client.post(
        "/api/health/events/preview",
        json={"scope": "batch", "purchase_batch_id": batch_id},
        headers=owner,
    )
    assert preview.status_code == 200, preview.text
    data = {
        "scope": "batch",
        "purchase_batch_id": batch_id,
        "expected_animal_ids": preview.json()["target_animal_ids"],
        "date": iso(today()),
        "type": "VACCINE",
        "task_id": task_id,
    }
    resp = await client.post("/api/health/events", json=data, headers=owner)
    assert resp.status_code == 201, resp.text
    tabs_after_first = await task_tabs(client, owner)
    done = find_tasks(tabs_after_first, id=task_id)
    assert len(done) == 1 and done[0]["status"] == "DONE"
    completed_at = done[0]["completed_at"]
    assert completed_at is not None
    # Replay is rejected and leaves the completed task unchanged.
    resp = await client.post("/api/health/events", json=data, headers=owner)
    assert resp.status_code == 409, resp.text
    tabs = await task_tabs(client, owner)
    done = find_tasks(tabs, id=task_id)
    assert done[0]["status"] == "DONE"
    assert done[0]["completed_at"] == completed_at
    assert len(all_tasks(tabs)) == len(all_tasks(tabs_after_first))


# ---------------------------------------------------------------------------
# 5. Wave 2 regressions
#    (non-finite numbers, lifecycle guards, state machine, RBAC hardening)
# ---------------------------------------------------------------------------
async def test_sale_price_nonfinite_and_negative_rejected(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    for i, bad_price in enumerate(("inf", "-inf", "nan", "1e999", "-5000")):
        aid = await make_animal(client, owner, tag=f"P-{i}")
        resp = await client.post(
            f"/api/animals/{aid}/status",
            json={"new_status": "SOLD", "date": iso(date.today()), "sale_price": bad_price},
            headers=owner,
        )
        assert resp.status_code == 422, bad_price
    assert await transactions(client, owner) == []
    assert await list_animals(client, owner, status="SOLD") == []
    resp = await client.get("/api/finance", headers=owner)
    assert resp.status_code == 200  # page not poisoned
    # A valid sale still works and books exactly one income.
    aid = await make_animal(client, owner, tag="OK-1")
    resp = await client.post(
        f"/api/animals/{aid}/status",
        json={"new_status": "SOLD", "date": iso(date.today()), "sale_price": 5000},
        headers=owner,
    )
    assert resp.status_code == 200, resp.text
    assert len(await transactions(client, owner)) == 1


async def test_weight_nonfinite_future_and_dead_animal_rejected(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    aid = await make_animal(client, owner)
    future = iso(date.today() + timedelta(days=30))
    for payload in [{"weight_kg": "nan"}, {"weight_kg": "inf"}, {"date": future, "weight_kg": 10}]:
        resp = await client.post(f"/api/animals/{aid}/weight", json=payload, headers=owner)
        assert resp.status_code == 422, payload
    assert (await get_profile(client, owner, aid))["weights"] == []
    # A dead animal accepts neither weights nor moves (forged POST).
    resp = await client.post(
        f"/api/animals/{aid}/status", json={"new_status": "DEAD"}, headers=owner
    )
    assert resp.status_code == 200, resp.text
    resp = await client.post(f"/api/animals/{aid}/weight", json={"weight_kg": 30}, headers=owner)
    assert resp.status_code == 400
    resp = await client.post(
        f"/api/animals/{aid}/move", json={"to_bucket": "QUARANTINE"}, headers=owner
    )
    assert resp.status_code == 409  # terminal lifecycle transition conflict
    profile = await get_profile(client, owner, aid)
    assert profile["weights"] == []
    assert profile["animal"]["current_bucket"] == "FOUNDATION"  # unmoved


async def test_create_animal_rejects_negative_numbers(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {"tag_number": "G-1", "sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"}
    for override in [{"purchase_price": "-500"}, {"birth_weight": "-2"}, {"purchase_price": "inf"}]:
        resp = await client.post("/api/animals", json=base | override, headers=owner)
        assert resp.status_code == 422, override
    assert (await client.get("/api/animals", headers=owner)).json()["total"] == 0


async def test_death_skips_pending_tasks_and_clears_cull_flag(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner)
    buck = await make_buck(client, owner)
    # Drive the cull flag on through the API: two consecutive FAILED cycles.
    for bred_days_ago in (120, 70):
        br_id = await make_breeding(
            client,
            owner,
            doe,
            buck,
            breeding_date=iso(date.today() - timedelta(days=bred_days_ago)),
        )
        assert (
            await ultrasound(
                client,
                owner,
                br_id,
                pregnant=False,
                date_str=iso(date.today() - timedelta(days=bred_days_ago - 32)),
            )
        ).status_code == 200
    assert (await get_animal(client, owner, doe))["cull_candidate"] is True
    br3 = await make_breeding(
        client,
        owner,
        doe,
        buck,
        breeding_date=iso(date.today() - timedelta(days=20)),
    )  # fresh PENDING ultrasound task
    resp = await client.post(
        f"/api/animals/{doe}/status", json={"new_status": "DEAD"}, headers=owner
    )
    assert resp.status_code == 200, resp.text
    animal = await get_animal(client, owner, doe)
    assert animal["cull_candidate"] is False
    tabs = await task_tabs(client, owner)
    assert [t for t in pending_tasks(tabs) if t["animal_id"] == doe] == []
    skipped = [
        t for t in all_tasks(tabs) if t["breeding_record_id"] == br3 and t["status"] == "SKIPPED"
    ]
    assert len(skipped) == 1  # ultrasound task cancelled — no pending work for a dead animal


async def test_ultrasound_rejects_garbage_pregnant_and_bad_kid_count(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    _, _, br_id = await breed_doe(client, owner)
    for payload in [
        {"pregnant": "maybe"},
        {"pregnant": True, "kid_count": "1e20"},
        {"pregnant": True, "kid_count": 0},
        {"pregnant": True, "kid_count": 9},
    ]:
        resp = await client.post(f"/api/breeding/{br_id}/ultrasound", json=payload, headers=owner)
        assert resp.status_code == 422, payload
    br = await get_breeding(client, owner, br_id)
    assert br["outcome"] == "PENDING"  # nothing recorded
    assert br["ultrasound_done"] is False


async def test_double_breeding_pending_doe_rejected(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe, buck, _ = await breed_doe(client, owner)
    resp = await post_breeding(client, owner, doe, buck)
    assert resp.status_code == 400  # rejected gracefully, no 500
    records = [r for r in await breeding_records(client, owner) if r["doe_id"] == doe]
    assert len(records) == 1


async def test_cull_flag_after_two_failed_cycles_route_level(client: httpx.AsyncClient) -> None:
    """The flag must fire at the SECOND consecutive failure (SPEC: 2 cycles)."""
    owner = await owner_with_farm(client)
    doe, buck, br1 = await breed_doe(client, owner, bred_days_ago=100)
    assert (
        await ultrasound(
            client,
            owner,
            br1,
            pregnant=False,
            date_str=iso(date.today() - timedelta(days=68)),
        )
    ).status_code == 200
    assert (await get_animal(client, owner, doe))["cull_candidate"] is False
    br2 = await make_breeding(client, owner, doe, buck)  # FAILED stays in BREEDING, re-breedable
    assert (await ultrasound(client, owner, br2, pregnant=False)).status_code == 200
    assert (await get_animal(client, owner, doe))["cull_candidate"] is True


async def test_abort_after_kidding_is_noop(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe, _, br_id = await breed_doe(client, owner, bred_days_ago=150)
    assert (await ultrasound(client, owner, br_id)).status_code == 200
    kids = [{"tag": "K-1", "sex": "M"}, {"tag": "K-2", "sex": "F"}]
    assert (await post_kidding(client, owner, br_id, kids=kids)).status_code == 201
    resp = await client.post(
        f"/api/breeding/{br_id}/abort",
        json={"loss_date": iso(today()), "cause": "UNKNOWN"},
        headers=owner,
    )
    assert resp.status_code == 409
    br = await get_breeding(client, owner, br_id)
    assert br["outcome"] == "CONFIRMED_PREGNANT"  # not rewritten
    assert (await get_animal(client, owner, doe))["current_bucket"] == "RECOVERY"  # still nursing


async def test_kidding_rejects_bad_dates_caps_and_weights(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    _, _, br_id = await breed_doe(client, owner, bred_days_ago=150)
    assert (await ultrasound(client, owner, br_id)).status_code == 200
    bred_on = date.fromisoformat((await get_breeding(client, owner, br_id))["breeding_date"])
    too_early = iso(bred_on - timedelta(days=1))
    future = iso(date.today() + timedelta(days=10))
    resp = await post_kidding(client, owner, br_id, date_str=too_early)
    assert resp.status_code == 400
    resp = await post_kidding(client, owner, br_id, date_str=future)
    assert resp.status_code == 422  # PastOrTodayDate schema guard
    resp = await post_kidding(client, owner, br_id, kids=[{"sex": "M"}] * 11)
    assert resp.status_code == 422
    resp = await post_kidding(client, owner, br_id, kids=[{"sex": "M", "birth_weight": "-3"}])
    assert resp.status_code == 422
    assert await kidding_records(client, owner) == []  # every variant rejected
    resp = await post_kidding(client, owner, br_id)  # valid one still works
    assert resp.status_code == 201, resp.text
    assert len(await kidding_records(client, owner)) == 1


# Gestation sanity window (services.record_kidding): goats kid at ~150 days
# (SPEC window 145–155); the service accepts a generous 100–200 day band for
# backdated record-keeping but rejects a "kidding" 1 day or 3 years
# post-breeding as the data-entry error it is.
@pytest.mark.parametrize(
    ("gestation_days", "expected"),
    [(99, 409), (201, 409), (100, 201), (150, 201), (200, 201)],
)
async def test_kidding_gestation_window(
    client: httpx.AsyncClient, gestation_days: int, expected: int
) -> None:
    owner = await owner_with_farm(client)
    _, _, br_id = await breed_doe(client, owner, bred_days_ago=gestation_days)
    assert (await ultrasound(client, owner, br_id)).status_code == 200
    resp = await post_kidding(client, owner, br_id, kids=[{"sex": "M"}])
    assert resp.status_code == expected, resp.text
    if expected != 201:
        assert "gestation" in resp.json()["detail"]
        assert await kidding_records(client, owner) == []


async def test_second_kidding_blank_tags_succeeds_and_uniquifies(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe, buck, br1 = await breed_doe(client, owner, bred_days_ago=310)
    assert (
        await ultrasound(
            client,
            owner,
            br1,
            date_str=iso(date.today() - timedelta(days=278)),
        )
    ).status_code == 200
    resp = await post_kidding(
        client,
        owner,
        br1,
        date_str=iso(date.today() - timedelta(days=160)),
    )  # blank tags → D-1-K1/K2
    assert resp.status_code == 201, resp.text
    # Wean: the API blocks early auto tasks by design, so move the doe back to
    # RESTING manually (v1 used a service-level complete_task for this step).
    resp = await client.post(
        f"/api/animals/{doe}/move",
        json={
            "to_bucket": "RESTING",
            "history_override": True,
            "reason": "Historical first-cycle weaning fixture",
        },
        headers=owner,
    )
    assert resp.status_code == 200, resp.text
    br2 = await make_breeding(  # doe is back in RESTING, ready again
        client, owner, doe, buck, breeding_date=iso(date.today() - timedelta(days=150))
    )
    assert (
        await ultrasound(
            client,
            owner,
            br2,
            date_str=iso(date.today() - timedelta(days=118)),
        )
    ).status_code == 200
    resp = await post_kidding(client, owner, br2)  # must NOT silently fail
    assert resp.status_code == 201, resp.text
    assert len(await kidding_records(client, owner)) == 2
    tags = {a["tag_number"] for a in await list_animals(client, owner) if a["source"] == "BORN"}
    assert {"D-1-K1", "D-1-K2"} <= tags
    assert len(tags) == 4
    assert any(tag.startswith("D-1-K1-A") for tag in tags)
    assert any(tag.startswith("D-1-K2-A") for tag in tags)


async def test_sold_doe_cannot_kidd(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe, _, br_id = await breed_doe(client, owner)
    assert (await ultrasound(client, owner, br_id)).status_code == 200
    resp = await client.post(
        f"/api/animals/{doe}/status",
        json={"new_status": "SOLD", "date": iso(today()), "sale_price": 9000},
        headers=owner,
    )
    assert resp.status_code == 200, resp.text
    resp = await post_kidding(client, owner, br_id)
    # Selling the doe auto-aborts her confirmed pregnancy (change_status), so
    # the breeding is ABORTED by the time the kidding is attempted — the
    # router's deliberate pre-check answers 400 for any non-CONFIRMED_PREGNANT
    # outcome (PENDING/FAILED/ABORTED alike), before the service's 409 path.
    assert resp.status_code == 400  # graceful rejection
    assert await kidding_records(client, owner) == []
    born = [a for a in await list_animals(client, owner) if a["source"] == "BORN"]
    assert born == []


async def test_health_nonfinite_cost_future_date_and_smuggled_task(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    aid = await make_animal(client, owner)
    future = iso(date.today() + timedelta(days=30))
    base = {"scope": "animal", "animal_id": aid, "date": iso(date.today()), "type": "TREATMENT"}
    for override in [{"cost": "inf"}, {"cost": "1e999"}, {"cost": "nan"}, {"date": future}]:
        resp = await client.post("/api/health/events", json=base | override, headers=owner)
        assert resp.status_code == 422, override
    assert await health_events(client, owner) == []
    # A smuggled non-health task id cannot be treated as a generic health event.
    resp = await client.post(
        "/api/tasks",
        json={"title": "Move pen", "due_date": iso(today()), "category": "OTHER"},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    task_id = resp.json()["id"]
    resp = await client.post("/api/health/events", json=base | {"task_id": task_id}, headers=owner)
    assert resp.status_code == 409, resp.text
    assert len(await health_events(client, owner)) == 0
    task = find_tasks(await task_tabs(client, owner), id=task_id)
    assert task[0]["status"] == "PENDING"


async def test_form_linked_and_early_auto_task_completion_blocked(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    _, _, br_id = await breed_doe(client, owner)
    us_task = find_tasks(
        await task_tabs(client, owner), category="ULTRASOUND", breeding_record_id=br_id
    )[0]
    resp = await client.post(f"/api/tasks/{us_task['id']}/complete", headers=owner)
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Use the linked form to complete this duty"
    assert find_tasks(await task_tabs(client, owner), id=us_task["id"])[0]["status"] == "PENDING"
    # Auto-generated protocol duties unlock on their due date; manual ones don't care.
    resp = await client.post(
        "/api/purchases/new",
        json={"date": iso(date.today()), "count": 1, "create_animals": True},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    batch_id = resp.json()["id"]
    auto = find_tasks(
        await task_tabs(client, owner), category="BUCKET_MOVE", purchase_batch_id=batch_id
    )[0]
    assert auto["due_date"] == iso(date.today() + timedelta(days=44))
    resp = await client.post(f"/api/tasks/{auto['id']}/complete", headers=owner)
    assert resp.status_code == 409
    resp = await client.post(
        "/api/tasks",
        json={
            "title": "Paint fence",
            "due_date": iso(date.today() + timedelta(days=44)),
            "category": "OTHER",
        },
        headers=owner,
    )
    manual_id = resp.json()["id"]
    resp = await client.post(f"/api/tasks/{manual_id}/complete", headers=owner)
    assert resp.status_code == 200, resp.text
    tabs = await task_tabs(client, owner)
    assert find_tasks(tabs, id=auto["id"])[0]["status"] == "PENDING"
    assert find_tasks(tabs, id=manual_id)[0]["status"] == "DONE"


async def test_recur_days_cap_and_skip_spawns_next(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post(
        "/api/tasks",
        json={"title": "Daily", "due_date": iso(date.today()), "recur_days": 999999999},
        headers=owner,
    )
    assert resp.status_code == 422  # capped — the series stays usable
    assert all_tasks(await task_tabs(client, owner)) == []
    resp = await client.post(
        "/api/tasks",
        json={"title": "Daily", "due_date": iso(date.today()), "recur_days": 7},
        headers=owner,
    )
    task_id = resp.json()["id"]
    resp = await client.post(f"/api/tasks/{task_id}/skip", headers=owner)
    assert resp.status_code == 200, resp.text
    tabs = await task_tabs(client, owner)
    original = find_tasks(tabs, id=task_id)[0]
    assert original["status"] == "SKIPPED"
    nxt = [t for t in find_tasks(tabs, title="Daily", status="PENDING") if t["id"] != task_id]
    assert len(nxt) == 1
    farms_response = await client.get("/api/auth/farms", headers=owner)
    assert farms_response.status_code == 200, farms_response.text
    farm = next(farm for farm in farms_response.json() if farm["id"] == int(owner["X-Farm-Id"]))
    assert nxt[0]["due_date"] == iso(
        max(date.fromisoformat(original["due_date"]), today(farm["timezone"])) + timedelta(days=7)
    )  # series survives a skip in the farm's timezone


async def test_self_verification_blocked_for_worker(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    cm_id = (await role_ids_by_code(client, owner))["CLEANER_MANAGER"]
    resp = await client.post(
        "/api/team/workers",
        json={"name": "Mgr", "email": "mgr@farm.in", "password": "mgrpass123", "role_id": cm_id},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    resp = await client.post(
        "/api/tasks",
        json={
            "title": "Scrub",
            "due_date": iso(date.today()),
            "category": "CLEANING",
            "assigned_role_id": cm_id,
        },
        headers=owner,
    )
    task_id = resp.json()["id"]
    worker = await worker_headers(client, "mgr@farm.in", "mgrpass123", owner)
    resp = await client.post(f"/api/tasks/{task_id}/complete", headers=worker)
    assert resp.status_code == 200, resp.text
    resp = await client.post(f"/api/tasks/{task_id}/verify", headers=worker)
    assert resp.status_code == 409  # two-person rule enforced
    assert find_tasks(await task_tabs(client, owner), id=task_id)[0]["status"] == "DONE"
    resp = await client.post(f"/api/tasks/{task_id}/verify", headers=owner)
    assert resp.status_code == 200, resp.text
    assert find_tasks(await task_tabs(client, owner), id=task_id)[0]["status"] == "VERIFIED"


async def test_finance_and_feed_nonfinite_rejected(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    for bad_amount in ("nan", "inf", "1e999"):
        resp = await client.post(
            "/api/finance/new",
            json={
                "date": iso(date.today()),
                "type": "INCOME",
                "category": "OTHER",
                "amount": bad_amount,
            },
            headers=owner,
        )
        assert resp.status_code == 422, bad_amount
    assert await transactions(client, owner) == []
    assert (await client.get("/api/finance", headers=owner)).status_code == 200
    resp = await client.post(
        "/api/feeding/mix",
        json={"recipe_code": "FATTENING_50_50", "batch_kg": "nan"},
        headers=owner,
    )
    assert resp.status_code == 422  # mix error surfaced, no 500
    item_id = (await inventory(client, owner))[0]["id"]
    resp = await client.post(
        f"/api/feeding/inventory/{item_id}/add", json={"qty_kg": "inf"}, headers=owner
    )
    assert resp.status_code == 422
    item = next(i for i in await inventory(client, owner) if i["id"] == item_id)
    assert item["qty_on_hand"] == 0  # no infinite stock


async def test_purchase_batch_bounds_validated(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    future = iso(date.today() + timedelta(days=10))
    for payload in [
        {"date": iso(date.today()), "count": 4, "total_price": "-5000"},
        {"date": iso(date.today()), "count": 1001},
        {"date": iso(date.today()), "count": 4, "avg_age_months": 100000},
        {"date": iso(date.today()), "count": 4, "avg_age_months": "-3"},
        {"date": iso(date.today()), "count": 4, "avg_weight_kg": "-1"},
        {"date": future, "count": 4},
        {"date": "9999-12-01", "count": 4},
    ]:
        resp = await client.post("/api/purchases/new", json=payload, headers=owner)
        assert resp.status_code == 422, payload
    assert await purchase_batches(client, owner) == []
    assert await transactions(client, owner) == []  # no negative expense booked
    resp = await client.post(
        "/api/purchases/new",
        json={"date": iso(date.today()), "count": 4, "total_price": 20000, "create_animals": True},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    assert len(await purchase_batches(client, owner)) == 1  # valid batch still works


async def test_team_takeover_and_escalation_guards(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    vet_id = (await role_ids_by_code(client, owner_a))["VET"]
    # A farm owner can no longer be absorbed into another farm as a "worker".
    resp = await client.post(
        "/api/team/workers",
        json={"name": "", "email": "b@farm.in", "password": "", "role_id": vet_id},
        headers=owner_a,
    )
    assert resp.status_code == 400
    # Nor can a farm owner's global password be rewritten from another farm.
    # v1 seeded the legacy membership row directly; the API equivalent is a
    # worker who later registers his OWN farm — the reset guard fires the same.
    resp = await client.post(
        "/api/team/workers",
        json={"name": "W", "email": "w@farm.in", "password": "workerpass123", "role_id": vet_id},
        headers=owner_a,
    )
    assert resp.status_code == 201, resp.text
    mid = resp.json()["id"]
    w_auth = await login(client, "w@farm.in", "workerpass123")
    resp = await client.post("/api/auth/farms", json={"name": "W Farm"}, headers=w_auth)
    assert resp.status_code == 201, resp.text
    resp = await client.post(
        f"/api/team/workers/{mid}/reset-password", json={"password": "pwnedpass1"}, headers=owner_a
    )
    assert resp.status_code == 400
    resp = await client.post(
        "/api/auth/login", json={"email": "w@farm.in", "password": "workerpass123"}
    )
    assert resp.status_code == 200  # password untouched…
    resp = await client.post(
        "/api/auth/login", json={"email": "w@farm.in", "password": "pwnedpass1"}
    )
    assert resp.status_code == 401  # …not rewritten
    # team.manage-only worker: manager-role editing is owner-only, so the
    # worker cannot use their own role as a privilege-escalation vehicle.
    resp = await client.post(
        "/api/team/roles", json={"name": "Manager", "permissions": ["team.manage"]}, headers=owner_a
    )
    assert resp.status_code == 201, resp.text
    mgr_role_id = resp.json()["id"]
    resp = await client.post(
        "/api/team/workers",
        json={"name": "M", "email": "m@farm.in", "password": "mgrpass123", "role_id": mgr_role_id},
        headers=owner_a,
    )
    assert resp.status_code == 201, resp.text
    own_mid = resp.json()["id"]
    worker = await worker_headers(client, "m@farm.in", "mgrpass123", owner_a)
    resp = await client.put(
        f"/api/team/roles/{mgr_role_id}",
        json={"name": "Manager", "permissions": ["team.manage", "finance.view", "animals.move"]},
        headers=worker,
    )
    assert resp.status_code == 403, resp.text
    roles = (await client.get("/api/team", headers=owner_a)).json()["roles"]
    mgr_role = next(r for r in roles if r["id"] == mgr_role_id)
    assert mgr_role["permissions"] == ["team.manage"]
    # …and can't deactivate himself; preset roles can't be deleted.
    resp = await client.put(
        f"/api/team/workers/{own_mid}/status",
        json={"is_active": False},
        headers=worker,
    )
    assert resp.status_code == 400
    resp = await client.delete(f"/api/team/roles/{vet_id}", headers=owner_a)
    assert resp.status_code == 400
    team = (await client.get("/api/team", headers=owner_a)).json()
    membership = next(m for m in team["memberships"] if m["id"] == own_mid)
    assert membership["is_active"] is True
    assert any(r["id"] == vet_id for r in team["roles"])


async def test_register_rejects_whitespace_password(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register", json={"email": "x@farm.in", "password": "        "}
    )
    assert resp.status_code == 400
    resp = await client.post("/api/auth/login", json={"email": "x@farm.in", "password": "        "})
    assert resp.status_code == 401  # no account was created


async def test_default_secret_session_forgery_fails(client: httpx.AsyncClient) -> None:
    """v1: without GOATFARM_SECRET the app signed session cookies with a random
    per-process key — cookies forged with the old public default were worthless.
    v2: access tokens are RS256 JWTs, so anything not signed with the server's
    private key must be rejected — a token signed with an attacker's key, or an
    HS256 alg-confusion forgery made with the PUBLIC key."""
    resp = await client.post(
        "/api/auth/register", json={"email": "victim@farm.in", "password": "victimpass123"}
    )
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["user"]["id"]
    valid = resp.json()["access_token"]
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "kind": "access",
        "jti": "forged",
        "iat": now,
        "exp": now + timedelta(seconds=600),
    }
    resp = await client.get("/api/auth/farms", headers={"Authorization": f"Bearer {valid}"})
    assert resp.status_code == 200  # control: the real token works
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(claims, wrong_key, algorithm="RS256")
    resp = await client.get("/api/auth/farms", headers={"Authorization": f"Bearer {forged}"})
    assert resp.status_code == 401
    pub_pem = get_settings().jwt_public_key_path.read_bytes()
    pub_key = serialization.load_pem_public_key(pub_pem)
    pub_der = pub_key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    confused = jwt.encode(claims, pub_der, algorithm="HS256")
    resp = await client.get("/api/auth/farms", headers={"Authorization": f"Bearer {confused}"})
    assert resp.status_code == 401
