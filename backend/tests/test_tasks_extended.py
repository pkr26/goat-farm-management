"""Extended tests for the tasks & duties module (/api/tasks).

Covers the duty engine end to end against the SPEC contract:
- manual duty creation (role / specific worker / unassigned, recurrence),
  including the kept v1 quirk that unknown or cross-farm assignee ids are
  silently stripped instead of rejected;
- tabbed list (today / overdue / upcoming / awaiting / completed) bucketing,
  ordering and the 100-row completed-history cap;
- worker visibility (own role / personal duties only; verification views
  farm-wide for tasks.verify holders);
- completion attribution, the not-due-yet guard for auto-generated duties,
  skip semantics;
- recurring duties spawning the next occurrence (with dedupe);
- the CLEANING verification loop (DONE = awaiting verification, verify →
  VERIFIED, reject with note → back to PENDING, two-person rule with the
  owner exemption);
- auto-generated tasks mapped to preset roles by category;
- form-linked duties (ULTRASOUND / KIDDING_DUE / VACCINE / DEWORMING) that
  refuse the bare complete button and must be closed through their form.
"""

from datetime import date, timedelta

import httpx
import pytest

from app.utils import today

from .conftest import owner_with_farm

WORKER_PW = "workerpass123"


# ---------------------------------------------------------------------------
# Helpers (everything goes through the API)
# ---------------------------------------------------------------------------
def iso(d: date) -> str:
    return d.isoformat()


async def login_user(
    client: httpx.AsyncClient, email: str, password: str = WORKER_PW
) -> tuple[dict, int]:
    """Login → (bearer headers, user id)."""
    resp = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]["id"]


async def role_id(client: httpx.AsyncClient, owner: dict, code: str) -> int:
    resp = await client.get("/api/team", headers=owner)
    assert resp.status_code == 200, resp.text
    return next(r["id"] for r in resp.json()["roles"] if r["code"] == code)


async def add_worker(
    client: httpx.AsyncClient, owner: dict, rid: int, email: str, name: str = "Worker"
) -> None:
    resp = await client.post(
        "/api/team/workers",
        json={"name": name, "email": email, "password": WORKER_PW, "role_id": rid},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text


async def worker_headers(
    client: httpx.AsyncClient, owner: dict, code: str, email: str, name: str = "Worker"
) -> tuple[dict, int]:
    """Owner adds a worker with preset role `code`; returns (farm headers, user id)."""
    rid = await role_id(client, owner, code)
    await add_worker(client, owner, rid, email, name)
    headers, user_id = await login_user(client, email)
    return headers | {"X-Farm-Id": owner["X-Farm-Id"]}, user_id


async def make_custom_role(
    client: httpx.AsyncClient, owner: dict, name: str, permissions: list[str]
) -> int:
    resp = await client.post(
        "/api/team/roles",
        json={"name": name, "description": "", "permissions": permissions},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def post_duty(client: httpx.AsyncClient, headers: dict, **payload: object) -> httpx.Response:
    return await client.post("/api/tasks", json=payload, headers=headers)


async def make_duty(
    client: httpx.AsyncClient,
    headers: dict,
    title: str = "Duty",
    due: date | None = None,
    **overrides: object,
) -> dict:
    payload = {"title": title, "due_date": iso(due or today())} | overrides
    resp = await post_duty(client, headers, **payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def get_tabs(client: httpx.AsyncClient, headers: dict) -> dict:
    resp = await client.get("/api/tasks", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def all_tasks(tabs: dict) -> list[dict]:
    return tabs["today"] + tabs["overdue"] + tabs["upcoming"] + tabs["awaiting"] + tabs["completed"]


def find_task(tabs: dict, task_id: int) -> dict:
    return next(t for t in all_tasks(tabs) if t["id"] == task_id)


async def complete_duty(client: httpx.AsyncClient, headers: dict, task_id: int) -> httpx.Response:
    return await client.post(f"/api/tasks/{task_id}/complete", headers=headers)


async def make_animal(client: httpx.AsyncClient, headers: dict, tag: str = "A-001") -> int:
    resp = await client.post(
        "/api/animals",
        json={
            "tag_number": tag,
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def make_doe(client: httpx.AsyncClient, headers: dict, tag: str = "D-101") -> dict:
    resp = await client.post(
        "/api/animals",
        json={
            "tag_number": tag,
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "BREEDING",
            "date_of_birth": iso(today() - timedelta(days=400)),
            "weight_kg": 26.0,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def make_buck(client: httpx.AsyncClient, headers: dict, tag: str = "B-01") -> dict:
    resp = await client.post(
        "/api/animals",
        json={
            "tag_number": tag,
            "sex": "M",
            "source": "PURCHASED",
            "current_bucket": "BREEDING",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def make_breeding(
    client: httpx.AsyncClient, headers: dict, doe: dict, buck: dict, breeding_date: date
) -> dict:
    resp = await client.post(
        "/api/breeding",
        json={
            "doe_id": doe["id"],
            "buck_id": buck["id"],
            "breeding_date": iso(breeding_date),
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def submit_ultrasound(
    client: httpx.AsyncClient, headers: dict, breeding_id: int, pregnant: bool, kid_count: int = 2
) -> dict:
    resp = await client.post(
        f"/api/breeding/{breeding_id}/ultrasound",
        json={"pregnant": pregnant, "kid_count": kid_count},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def make_pregnancy(
    client: httpx.AsyncClient, headers: dict, breeding_date: date
) -> tuple[dict, dict]:
    """Doe + buck + breeding + pregnant ultrasound → (doe, br)."""
    doe = await make_doe(client, headers)
    buck = await make_buck(client, headers)
    br = await make_breeding(client, headers, doe, buck, breeding_date)
    br = await submit_ultrasound(client, headers, br["id"], pregnant=True)
    return doe, br


async def record_kidding(
    client: httpx.AsyncClient,
    headers: dict,
    br: dict,
    kidding_date: date,
    kids: list[dict],
) -> dict:
    resp = await client.post(
        "/api/kidding",
        json={
            "breeding_record_id": br["id"],
            "date": iso(kidding_date),
            "ease": "NORMAL",
            "notes": "",
            "kids": kids,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def make_batch(
    client: httpx.AsyncClient, headers: dict, batch_date: date | None = None
) -> int:
    resp = await client.post(
        "/api/purchases/new",
        json={
            "date": iso(batch_date or today()),
            "supplier": "Kurnool Traders",
            "count": 2,
            "avg_age_months": 7,
            "avg_weight_kg": 15.0,
            "total_price": 20000.0,
            "notes": "",
            "create_animals": True,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# Manual duty creation — happy paths
# ---------------------------------------------------------------------------
async def test_create_minimal_duty_defaults(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Fix latch")
    assert duty["title"] == "Fix latch"
    assert duty["due_date"] == iso(today())
    assert duty["status"] == "PENDING"
    assert duty["category"] == "OTHER"
    assert duty["auto_generated"] is False
    assert duty["assigned_role_id"] is None
    assert duty["assigned_user_id"] is None
    assert duty["recur_days"] is None
    assert duty["completed_by_id"] is None
    assert duty["completed_at"] is None


@pytest.mark.parametrize(
    "category",
    [
        "VACCINE",
        "DEWORMING",
        "ULTRASOUND",
        "KIDDING_DUE",
        "WEANING",
        "BUCKET_MOVE",
        "QUARANTINE",
        "FEED",
        "CLEANING",
        "OTHER",
    ],
)
async def test_create_every_valid_category(client: httpx.AsyncClient, category: str) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, f"Duty {category}", category=category)
    assert duty["category"] == category


async def test_create_with_role_assignment(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    rid = await role_id(client, owner, "CLEANER")
    duty = await make_duty(client, owner, "Scrub pens", category="CLEANING", assigned_role_id=rid)
    assert duty["assigned_role_id"] == rid
    assert duty["assigned_role_name"] == "Cleaner"
    assert duty["assigned_user_id"] is None


async def test_create_with_worker_assignment(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    _, cleaner_id = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in", "Chandu")
    duty = await make_duty(client, owner, "Wash bottles", assigned_user_id=cleaner_id)
    assert duty["assigned_user_id"] == cleaner_id
    assert duty["assigned_user_name"] == "Chandu"
    assert duty["assigned_role_id"] is None


async def test_create_with_role_and_worker(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    _, cleaner_id = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    rid = await role_id(client, owner, "CLEANER")
    duty = await make_duty(client, owner, "Both", assigned_role_id=rid, assigned_user_id=cleaner_id)
    assert duty["assigned_role_id"] == rid
    assert duty["assigned_user_id"] == cleaner_id


async def test_create_with_recurrence(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Daily sweep", recur_days=1)
    assert duty["recur_days"] == 1


async def test_create_due_today_lands_in_today_tab(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Today job", due=today())
    tabs = await get_tabs(client, owner)
    assert duty["id"] in {t["id"] for t in tabs["today"]}
    assert duty["id"] not in {t["id"] for t in tabs["overdue"] + tabs["upcoming"]}


async def test_create_due_past_lands_in_overdue_tab(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Late job", due=today() - timedelta(days=3))
    tabs = await get_tabs(client, owner)
    assert duty["id"] in {t["id"] for t in tabs["overdue"]}


async def test_create_due_future_lands_in_upcoming_tab(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Future job", due=today() + timedelta(days=5))
    tabs = await get_tabs(client, owner)
    assert duty["id"] in {t["id"] for t in tabs["upcoming"]}


async def test_create_strips_title_whitespace(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "   padded title   ")
    assert duty["title"] == "padded title"


async def test_create_unicode_emoji_title(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "शेळींना पाणी द्या 🐐🚰")
    assert duty["title"] == "शेळींना पाणी द्या 🐐🚰"


async def test_create_sql_injection_title_stored_literally(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    evil = "x'; DROP TABLE tasks;--"
    duty = await make_duty(client, owner, evil)
    assert duty["title"] == evil
    tabs = await get_tabs(client, owner)  # table still there, still readable
    assert find_task(tabs, duty["id"])["title"] == evil


async def test_create_title_200_chars_ok(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "x" * 200)
    assert len(duty["title"]) == 200


async def test_create_ignores_unknown_extra_fields(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(
        client,
        owner,
        title="Extra fields",
        due_date=iso(today()),
        bogus_field="whatever",
        status="DONE",  # cannot smuggle a state in either
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "PENDING"


async def test_create_far_past_date(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Ancient", due=date(1900, 1, 1))
    assert duty["due_date"] == "1900-01-01"
    tabs = await get_tabs(client, owner)
    assert tabs["overdue"][-1]["id"] == duty["id"]  # oldest overdue sorts first


async def test_create_far_future_date(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Far future", due=date(2200, 1, 1))
    assert duty["due_date"] == "2200-01-01"
    tabs = await get_tabs(client, owner)
    assert tabs["upcoming"][-1]["id"] == duty["id"]


async def test_create_response_shape(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Shape check")
    assert set(duty) == {
        "id",
        "title",
        "due_date",
        "status",
        "category",
        "auto_generated",
        "animal_id",
        "purchase_batch_id",
        "breeding_record_id",
        "assigned_role_id",
        "assigned_user_id",
        "recur_days",
        "completed_by_id",
        "completed_at",
        "verified_by_id",
        "verified_at",
        "verification_note",
        "skipped_by_id",
        "assigned_role_name",
        "assigned_user_name",
        "animal_tag",
        "needs_verification",
        "action_url",
    }
    assert duty["needs_verification"] is False  # OTHER never needs verification
    assert duty["action_url"] is None


# ---------------------------------------------------------------------------
# Manual duty creation — validation
# ---------------------------------------------------------------------------
async def test_create_missing_title_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(client, owner, due_date=iso(today()))
    assert resp.status_code == 422


async def test_create_missing_due_date_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(client, owner, title="No date")
    assert resp.status_code == 422


async def test_create_empty_title_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(client, owner, title="", due_date=iso(today()))
    assert resp.status_code == 422


async def test_create_whitespace_only_title_400(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(client, owner, title="   ", due_date=iso(today()))
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Title is required"


async def test_create_title_201_chars_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(client, owner, title="x" * 201, due_date=iso(today()))
    assert resp.status_code == 422


async def test_create_title_10k_chars_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(client, owner, title="x" * 10_000, due_date=iso(today()))
    assert resp.status_code == 422


async def test_create_invalid_category_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(
        client, owner, title="Bad cat", due_date=iso(today()), category="MILKING"
    )
    assert resp.status_code == 422


async def test_create_lowercase_category_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(
        client, owner, title="Bad cat", due_date=iso(today()), category="cleaning"
    )
    assert resp.status_code == 422


async def test_create_null_category_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(client, owner, title="Null cat", due_date=iso(today()), category=None)
    assert resp.status_code == 422


async def test_create_bad_date_string_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(client, owner, title="Bad date", due_date="not-a-date")
    assert resp.status_code == 422


async def test_create_impossible_date_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(client, owner, title="Bad date", due_date="2026-13-40")
    assert resp.status_code == 422


async def test_create_null_due_date_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(client, owner, title="Null date", due_date=None)
    assert resp.status_code == 422


async def test_create_recur_days_zero_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(client, owner, title="R", due_date=iso(today()), recur_days=0)
    assert resp.status_code == 422


async def test_create_recur_days_negative_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(client, owner, title="R", due_date=iso(today()), recur_days=-1)
    assert resp.status_code == 422


async def test_create_recur_days_over_max_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(client, owner, title="R", due_date=iso(today()), recur_days=3651)
    assert resp.status_code == 422


async def test_create_recur_days_max_ok(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Decadal", recur_days=3650)
    assert duty["recur_days"] == 3650


async def test_create_recur_days_fractional_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(client, owner, title="R", due_date=iso(today()), recur_days=1.5)
    assert resp.status_code == 422


async def test_create_recur_days_string_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(client, owner, title="R", due_date=iso(today()), recur_days="weekly")
    assert resp.status_code == 422


async def test_create_role_id_zero_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(client, owner, title="R", due_date=iso(today()), assigned_role_id=0)
    assert resp.status_code == 422


async def test_create_role_id_negative_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(client, owner, title="R", due_date=iso(today()), assigned_role_id=-3)
    assert resp.status_code == 422


async def test_create_role_id_above_bound_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(
        client, owner, title="R", due_date=iso(today()), assigned_role_id=2**62 + 1
    )
    assert resp.status_code == 422


async def test_create_user_id_zero_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(client, owner, title="R", due_date=iso(today()), assigned_user_id=0)
    assert resp.status_code == 422


async def test_create_role_id_noninteger_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(client, owner, title="R", due_date=iso(today()), assigned_role_id="abc")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Assignee stripping (v1 quirk, kept): unknown/cross-farm ids are dropped
# ---------------------------------------------------------------------------
async def test_cross_farm_role_id_silently_stripped(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    rid_b = await role_id(client, owner_b, "CLEANER")
    duty = await make_duty(client, owner_a, "Cross-farm role", assigned_role_id=rid_b)
    assert duty["assigned_role_id"] is None
    assert duty["assigned_role_name"] is None


async def test_unknown_role_id_silently_stripped(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Ghost role", assigned_role_id=999_999)
    assert duty["assigned_role_id"] is None


async def test_cross_farm_user_id_silently_stripped(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    _, owner_b_id = await login_user(client, "b@farm.in", password="ownerpass123")
    duty = await make_duty(client, owner_a, "Cross-farm user", assigned_user_id=owner_b_id)
    assert duty["assigned_user_id"] is None


async def test_unknown_user_id_silently_stripped(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Ghost user", assigned_user_id=999_999)
    assert duty["assigned_user_id"] is None


async def test_owner_as_assigned_user_stripped(client: httpx.AsyncClient) -> None:
    """The farm owner is not a membership, so he cannot be a duty assignee."""
    owner = await owner_with_farm(client)
    _, owner_id = await login_user(client, "owner@farm.in", password="ownerpass123")
    duty = await make_duty(client, owner, "Assign to owner", assigned_user_id=owner_id)
    assert duty["assigned_user_id"] is None


async def test_deactivated_worker_stripped(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    _, cleaner_id = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    resp = await client.get("/api/team", headers=owner)
    mid = next(m["id"] for m in resp.json()["memberships"] if m["email"] == "cleaner@farm.in")
    resp = await client.post(f"/api/team/workers/{mid}/toggle", headers=owner)
    assert resp.status_code == 200, resp.text
    duty = await make_duty(client, owner, "Inactive worker", assigned_user_id=cleaner_id)
    assert duty["assigned_user_id"] is None


# ---------------------------------------------------------------------------
# Auth & tenancy
# ---------------------------------------------------------------------------
async def test_list_no_auth_401(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/tasks")
    assert resp.status_code == 401


async def test_create_no_auth_401(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/tasks", json={"title": "X", "due_date": iso(today())})
    assert resp.status_code == 401


async def test_complete_no_auth_401(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/tasks/1/complete")
    assert resp.status_code == 401


async def test_skip_no_auth_401(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/tasks/1/skip")
    assert resp.status_code == 401


async def test_verify_no_auth_401(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/tasks/1/verify")
    assert resp.status_code == 401


async def test_reject_no_auth_401(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/tasks/1/reject", json={"note": "x"})
    assert resp.status_code == 401


async def test_list_missing_farm_header_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    headers = {"Authorization": owner["Authorization"]}
    resp = await client.get("/api/tasks", headers=headers)
    # The header is required by the contract — missing fails request
    # validation (422) before the farm dependency runs.
    assert resp.status_code == 422
    assert "x-farm-id" in str(resp.json()["detail"]).lower()


async def test_create_missing_farm_header_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    headers = {"Authorization": owner["Authorization"]}
    resp = await client.post(
        "/api/tasks", json={"title": "X", "due_date": iso(today())}, headers=headers
    )
    assert resp.status_code == 422


async def test_list_noninteger_farm_header_400(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.get("/api/tasks", headers=owner | {"X-Farm-Id": "not-a-number"})
    assert resp.status_code == 400


async def test_list_out_of_range_farm_header_400(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.get("/api/tasks", headers=owner | {"X-Farm-Id": str(2**62)})
    assert resp.status_code == 400


async def test_list_unknown_farm_404(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.get("/api/tasks", headers=owner | {"X-Farm-Id": "999999"})
    assert resp.status_code == 404


async def test_non_member_farm_list_404(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    resp = await client.get("/api/tasks", headers=owner_b | {"X-Farm-Id": owner_a["X-Farm-Id"]})
    # Forbidden farms answer exactly like unknown ones (no farm-id
    # existence oracle).
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Farm not found"


async def test_non_member_farm_create_404(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    resp = await client.post(
        "/api/tasks",
        json={"title": "X", "due_date": iso(today())},
        headers=owner_b | {"X-Farm-Id": owner_a["X-Farm-Id"]},
    )
    assert resp.status_code == 404


async def test_cross_farm_task_complete_404(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    duty = await make_duty(client, owner_a, "Farm A duty")
    resp = await complete_duty(client, owner_b, duty["id"])
    assert resp.status_code == 404


async def test_cross_farm_task_skip_404(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    duty = await make_duty(client, owner_a, "Farm A duty")
    resp = await client.post(f"/api/tasks/{duty['id']}/skip", headers=owner_b)
    assert resp.status_code == 404


async def test_cross_farm_task_verify_404(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    duty = await make_duty(client, owner_a, "Farm A duty", category="CLEANING")
    assert (await complete_duty(client, owner_a, duty["id"])).status_code == 200
    resp = await client.post(f"/api/tasks/{duty['id']}/verify", headers=owner_b)
    assert resp.status_code == 404


async def test_cross_farm_task_reject_404(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    duty = await make_duty(client, owner_a, "Farm A duty", category="CLEANING")
    assert (await complete_duty(client, owner_a, duty["id"])).status_code == 200
    resp = await client.post(
        f"/api/tasks/{duty['id']}/reject", json={"note": "no"}, headers=owner_b
    )
    assert resp.status_code == 404


async def test_cross_farm_task_ids_never_leak_in_list(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    duty = await make_duty(client, owner_a, "Secret farm A duty")
    tabs_b = await get_tabs(client, owner_b)
    assert duty["id"] not in {t["id"] for t in all_tasks(tabs_b)}
    assert "Secret farm A duty" not in {t["title"] for t in all_tasks(tabs_b)}


async def test_malformed_task_id_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post("/api/tasks/abc/complete", headers=owner)
    assert resp.status_code == 422


async def test_zero_task_id_404(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post("/api/tasks/0/complete", headers=owner)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Permission gates
# ---------------------------------------------------------------------------
async def test_custom_role_without_tasks_view_403(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    rid = await make_custom_role(client, owner, "Observer", ["dashboard.view"])
    await add_worker(client, owner, rid, "observer@farm.in")
    headers, _ = await login_user(client, "observer@farm.in")
    headers |= {"X-Farm-Id": owner["X-Farm-Id"]}
    resp = await client.get("/api/tasks", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: tasks.view"


async def test_view_only_role_cannot_create(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    rid = await make_custom_role(client, owner, "Viewer", ["dashboard.view", "tasks.view"])
    await add_worker(client, owner, rid, "viewer@farm.in")
    headers, _ = await login_user(client, "viewer@farm.in")
    headers |= {"X-Farm-Id": owner["X-Farm-Id"]}
    resp = await post_duty(client, headers, title="X", due_date=iso(today()))
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: tasks.create"


async def test_view_only_role_cannot_complete(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Protected")
    rid = await make_custom_role(client, owner, "Viewer", ["dashboard.view", "tasks.view"])
    await add_worker(client, owner, rid, "viewer@farm.in")
    headers, _ = await login_user(client, "viewer@farm.in")
    headers |= {"X-Farm-Id": owner["X-Farm-Id"]}
    resp = await complete_duty(client, headers, duty["id"])
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: tasks.complete"


async def test_view_only_role_cannot_skip(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Protected")
    rid = await make_custom_role(client, owner, "Viewer", ["dashboard.view", "tasks.view"])
    await add_worker(client, owner, rid, "viewer@farm.in")
    headers, _ = await login_user(client, "viewer@farm.in")
    headers |= {"X-Farm-Id": owner["X-Farm-Id"]}
    resp = await client.post(f"/api/tasks/{duty['id']}/skip", headers=headers)
    assert resp.status_code == 403


async def test_view_only_role_cannot_verify(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Protected", category="CLEANING")
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    rid = await make_custom_role(client, owner, "Viewer", ["dashboard.view", "tasks.view"])
    await add_worker(client, owner, rid, "viewer@farm.in")
    headers, _ = await login_user(client, "viewer@farm.in")
    headers |= {"X-Farm-Id": owner["X-Farm-Id"]}
    resp = await client.post(f"/api/tasks/{duty['id']}/verify", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: tasks.verify"


async def test_view_only_role_cannot_reject(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Protected", category="CLEANING")
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    rid = await make_custom_role(client, owner, "Viewer", ["dashboard.view", "tasks.view"])
    await add_worker(client, owner, rid, "viewer@farm.in")
    headers, _ = await login_user(client, "viewer@farm.in")
    headers |= {"X-Farm-Id": owner["X-Farm-Id"]}
    resp = await client.post(f"/api/tasks/{duty['id']}/reject", json={"note": "x"}, headers=headers)
    assert resp.status_code == 403


async def test_cleaner_cannot_verify(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    cleaner, _ = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    rid = await role_id(client, owner, "CLEANER")
    duty = await make_duty(client, owner, "Scrub", category="CLEANING", assigned_role_id=rid)
    assert (await complete_duty(client, cleaner, duty["id"])).status_code == 200
    resp = await client.post(f"/api/tasks/{duty['id']}/verify", headers=cleaner)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: tasks.verify"


async def test_cleaner_cannot_reject(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    cleaner, _ = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    rid = await role_id(client, owner, "CLEANER")
    duty = await make_duty(client, owner, "Scrub", category="CLEANING", assigned_role_id=rid)
    assert (await complete_duty(client, cleaner, duty["id"])).status_code == 200
    resp = await client.post(f"/api/tasks/{duty['id']}/reject", json={"note": "x"}, headers=cleaner)
    assert resp.status_code == 403


async def test_mover_cannot_create_duty(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    mover, _ = await worker_headers(client, owner, "MOVER", "mover@farm.in")
    resp = await post_duty(client, mover, title="X", due_date=iso(today()))
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: tasks.create"


# ---------------------------------------------------------------------------
# Tabbed list (today / overdue / upcoming / awaiting / completed)
# ---------------------------------------------------------------------------
async def test_empty_farm_tabs_all_empty(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    tabs = await get_tabs(client, owner)
    assert set(tabs) == {"today", "overdue", "upcoming", "awaiting", "completed"}
    assert all(tabs[key] == [] for key in tabs)


async def test_pending_duty_not_in_completed_or_awaiting(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Pending cleaning", category="CLEANING")
    tabs = await get_tabs(client, owner)
    assert duty["id"] not in {t["id"] for t in tabs["completed"]}
    assert duty["id"] not in {t["id"] for t in tabs["awaiting"]}


async def test_overdue_tab_ordered_by_due_date(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await make_duty(client, owner, "recent", due=today() - timedelta(days=1))
    await make_duty(client, owner, "oldest", due=today() - timedelta(days=10))
    await make_duty(client, owner, "middle", due=today() - timedelta(days=5))
    tabs = await get_tabs(client, owner)
    assert [t["title"] for t in tabs["overdue"]] == ["oldest", "middle", "recent"]


async def test_upcoming_tab_ordered_by_due_date(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await make_duty(client, owner, "later", due=today() + timedelta(days=9))
    await make_duty(client, owner, "soon", due=today() + timedelta(days=2))
    await make_duty(client, owner, "mid", due=today() + timedelta(days=5))
    tabs = await get_tabs(client, owner)
    assert [t["title"] for t in tabs["upcoming"]] == ["soon", "mid", "later"]


async def test_completed_other_duty_lands_in_completed_tab(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Plain job")
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    tabs = await get_tabs(client, owner)
    assert duty["id"] in {t["id"] for t in tabs["completed"]}
    assert duty["id"] not in {t["id"] for t in tabs["awaiting"]}
    assert duty["id"] not in {t["id"] for t in tabs["today"]}


async def test_awaiting_tab_holds_done_cleaning_only(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    cleaning = await make_duty(client, owner, "Scrub", category="CLEANING")
    plain = await make_duty(client, owner, "Plain")
    assert (await complete_duty(client, owner, cleaning["id"])).status_code == 200
    assert (await complete_duty(client, owner, plain["id"])).status_code == 200
    tabs = await get_tabs(client, owner)
    assert {t["id"] for t in tabs["awaiting"]} == {cleaning["id"]}
    # a DONE cleaning duty is NOT history yet — it must stay out of completed
    assert cleaning["id"] not in {t["id"] for t in tabs["completed"]}
    assert plain["id"] in {t["id"] for t in tabs["completed"]}


async def test_verified_duty_in_completed_not_awaiting(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Scrub", category="CLEANING")
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    resp = await client.post(f"/api/tasks/{duty['id']}/verify", headers=owner)
    assert resp.status_code == 200, resp.text
    tabs = await get_tabs(client, owner)
    assert duty["id"] in {t["id"] for t in tabs["completed"]}
    assert duty["id"] not in {t["id"] for t in tabs["awaiting"]}


async def test_skipped_duty_lands_in_completed_tab(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Skip me")
    resp = await client.post(f"/api/tasks/{duty['id']}/skip", headers=owner)
    assert resp.status_code == 200, resp.text
    tabs = await get_tabs(client, owner)
    entry = next(t for t in tabs["completed"] if t["id"] == duty["id"])
    assert entry["status"] == "SKIPPED"
    assert duty["id"] not in {t["id"] for t in tabs["today"]}


async def test_completed_tab_newest_first(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    first = await make_duty(client, owner, "done first")
    second = await make_duty(client, owner, "done second")
    assert (await complete_duty(client, owner, first["id"])).status_code == 200
    assert (await complete_duty(client, owner, second["id"])).status_code == 200
    tabs = await get_tabs(client, owner)
    assert [t["title"] for t in tabs["completed"]][:2] == ["done second", "done first"]


async def test_completed_tab_capped_at_100(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    for i in range(105):
        duty = await make_duty(client, owner, f"bulk-{i:03d}")
        assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    tabs = await get_tabs(client, owner)
    assert len(tabs["completed"]) == 100


# ---------------------------------------------------------------------------
# Worker visibility
# ---------------------------------------------------------------------------
async def test_worker_sees_role_assigned_duty(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    cleaner, _ = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    rid = await role_id(client, owner, "CLEANER")
    duty = await make_duty(client, owner, "Role duty", category="CLEANING", assigned_role_id=rid)
    tabs = await get_tabs(client, cleaner)
    assert duty["id"] in {t["id"] for t in tabs["today"]}


async def test_worker_sees_personally_assigned_duty(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    mover, mover_id = await worker_headers(client, owner, "MOVER", "mover@farm.in")
    duty = await make_duty(client, owner, "Personal duty", assigned_user_id=mover_id)
    tabs = await get_tabs(client, mover)
    assert duty["id"] in {t["id"] for t in tabs["today"]}


async def test_worker_hides_other_roles_duty(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    mover, _ = await worker_headers(client, owner, "MOVER", "mover@farm.in")
    rid = await role_id(client, owner, "CLEANER")
    duty = await make_duty(client, owner, "Cleaner job", category="CLEANING", assigned_role_id=rid)
    tabs = await get_tabs(client, mover)
    assert duty["id"] not in {t["id"] for t in all_tasks(tabs)}


async def test_worker_hides_unassigned_duty(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    mover, _ = await worker_headers(client, owner, "MOVER", "mover@farm.in")
    duty = await make_duty(client, owner, "Owner-only job")
    tabs = await get_tabs(client, mover)
    assert duty["id"] not in {t["id"] for t in all_tasks(tabs)}


async def test_worker_hides_duty_assigned_to_another_worker(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    _, cleaner1_id = await worker_headers(client, owner, "CLEANER", "c1@farm.in")
    cleaner2, _ = await worker_headers(client, owner, "CLEANER", "c2@farm.in")
    duty = await make_duty(client, owner, "For c1 only", assigned_user_id=cleaner1_id)
    tabs = await get_tabs(client, cleaner2)
    assert duty["id"] not in {t["id"] for t in all_tasks(tabs)}


async def test_owner_sees_every_assignment(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    _, cleaner_id = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    rid = await role_id(client, owner, "MOVER")
    a = await make_duty(client, owner, "unassigned")
    b = await make_duty(client, owner, "role", assigned_role_id=rid)
    c = await make_duty(client, owner, "personal", assigned_user_id=cleaner_id)
    tabs = await get_tabs(client, owner)
    visible = {t["id"] for t in tabs["today"]}
    assert {a["id"], b["id"], c["id"]} <= visible


async def test_verifier_awaiting_tab_is_farmwide(client: httpx.AsyncClient) -> None:
    """A tasks.verify holder reviews OTHER roles' completed cleaning work."""
    owner = await owner_with_farm(client)
    cleaner, _ = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    manager, _ = await worker_headers(client, owner, "CLEANER_MANAGER", "cm@farm.in")
    rid = await role_id(client, owner, "CLEANER")
    duty = await make_duty(client, owner, "Scrub", category="CLEANING", assigned_role_id=rid)
    assert (await complete_duty(client, cleaner, duty["id"])).status_code == 200
    manager_tabs = await get_tabs(client, manager)
    assert duty["id"] in {t["id"] for t in manager_tabs["awaiting"]}


async def test_non_verifier_awaiting_tab_stays_scoped(client: httpx.AsyncClient) -> None:
    """Without tasks.verify the awaiting view is limited to the worker's own scope."""
    owner = await owner_with_farm(client)
    cleaner, _ = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    mover, _ = await worker_headers(client, owner, "MOVER", "mover@farm.in")
    rid = await role_id(client, owner, "CLEANER")
    duty = await make_duty(client, owner, "Scrub", category="CLEANING", assigned_role_id=rid)
    assert (await complete_duty(client, cleaner, duty["id"])).status_code == 200
    cleaner_tabs = await get_tabs(client, cleaner)
    assert duty["id"] in {t["id"] for t in cleaner_tabs["awaiting"]}  # his own work
    mover_tabs = await get_tabs(client, mover)
    assert duty["id"] not in {t["id"] for t in all_tasks(mover_tabs)}  # not his scope


async def test_worker_completed_tab_shows_verified_own_role_duty(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    cleaner, _ = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    rid = await role_id(client, owner, "CLEANER")
    duty = await make_duty(client, owner, "Scrub", category="CLEANING", assigned_role_id=rid)
    assert (await complete_duty(client, cleaner, duty["id"])).status_code == 200
    resp = await client.post(f"/api/tasks/{duty['id']}/verify", headers=owner)
    assert resp.status_code == 200, resp.text
    tabs = await get_tabs(client, cleaner)
    assert duty["id"] in {t["id"] for t in tabs["completed"]}
    assert duty["id"] not in {t["id"] for t in tabs["awaiting"]}


async def test_verifier_completed_tab_is_farmwide(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    cleaner, _ = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    manager, _ = await worker_headers(client, owner, "CLEANER_MANAGER", "cm@farm.in")
    rid = await role_id(client, owner, "CLEANER")
    duty = await make_duty(client, owner, "Scrub", category="CLEANING", assigned_role_id=rid)
    assert (await complete_duty(client, cleaner, duty["id"])).status_code == 200
    resp = await client.post(f"/api/tasks/{duty['id']}/verify", headers=owner)
    assert resp.status_code == 200, resp.text
    manager_tabs = await get_tabs(client, manager)
    assert duty["id"] in {t["id"] for t in manager_tabs["completed"]}


async def test_worker_still_hides_pending_done_duty_in_completed(client: httpx.AsyncClient) -> None:
    """A worker's completed tab must not show an unverified CLEANING duty as history."""
    owner = await owner_with_farm(client)
    cleaner, _ = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    rid = await role_id(client, owner, "CLEANER")
    duty = await make_duty(client, owner, "Scrub", category="CLEANING", assigned_role_id=rid)
    assert (await complete_duty(client, cleaner, duty["id"])).status_code == 200
    tabs = await get_tabs(client, cleaner)
    assert duty["id"] not in {t["id"] for t in tabs["completed"]}


# ---------------------------------------------------------------------------
# Complete
# ---------------------------------------------------------------------------
async def test_complete_happy_path_attribution(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    _, owner_id = await login_user(client, "owner@farm.in", password="ownerpass123")
    duty = await make_duty(client, owner, "Job")
    resp = await complete_duty(client, owner, duty["id"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "DONE"
    assert body["completed_by_id"] == owner_id
    assert body["completed_at"] is not None
    assert body["needs_verification"] is False


async def test_complete_manual_future_duty_allowed(client: httpx.AsyncClient) -> None:
    """The not-due-yet guard applies to auto-generated duties only."""
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Future manual", due=today() + timedelta(days=10))
    resp = await complete_duty(client, owner, duty["id"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "DONE"


async def test_complete_done_duty_400(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Job")
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    resp = await complete_duty(client, owner, duty["id"])
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Task is not pending"


async def test_complete_verified_duty_400(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Job", category="CLEANING")
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    resp = await client.post(f"/api/tasks/{duty['id']}/verify", headers=owner)
    assert resp.status_code == 200, resp.text
    resp = await complete_duty(client, owner, duty["id"])
    assert resp.status_code == 400


async def test_complete_skipped_duty_400(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Job")
    resp = await client.post(f"/api/tasks/{duty['id']}/skip", headers=owner)
    assert resp.status_code == 200, resp.text
    resp = await complete_duty(client, owner, duty["id"])
    assert resp.status_code == 400


async def test_complete_nonexistent_404(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await complete_duty(client, owner, 424242)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Task not found"


async def test_owner_completes_any_assignment(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    rid = await role_id(client, owner, "CLEANER")
    duty = await make_duty(client, owner, "Role job", category="CLEANING", assigned_role_id=rid)
    resp = await complete_duty(client, owner, duty["id"])
    assert resp.status_code == 200, resp.text


async def test_worker_completes_role_duty(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    cleaner, cleaner_id = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    rid = await role_id(client, owner, "CLEANER")
    duty = await make_duty(client, owner, "Scrub", category="CLEANING", assigned_role_id=rid)
    resp = await complete_duty(client, cleaner, duty["id"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["completed_by_id"] == cleaner_id


async def test_worker_completes_personal_duty(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    mover, mover_id = await worker_headers(client, owner, "MOVER", "mover@farm.in")
    duty = await make_duty(client, owner, "Personal", assigned_user_id=mover_id)
    resp = await complete_duty(client, mover, duty["id"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["completed_by_id"] == mover_id


async def test_worker_complete_other_role_duty_403(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    mover, _ = await worker_headers(client, owner, "MOVER", "mover@farm.in")
    rid = await role_id(client, owner, "CLEANER")
    duty = await make_duty(client, owner, "Scrub", category="CLEANING", assigned_role_id=rid)
    resp = await complete_duty(client, mover, duty["id"])
    assert resp.status_code == 403
    assert resp.json()["detail"] == "This duty is not assigned to you"
    tabs = await get_tabs(client, owner)
    assert find_task(tabs, duty["id"])["status"] == "PENDING"


async def test_worker_complete_unassigned_duty_403(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    mover, _ = await worker_headers(client, owner, "MOVER", "mover@farm.in")
    duty = await make_duty(client, owner, "Owner job")
    resp = await complete_duty(client, mover, duty["id"])
    assert resp.status_code == 403


async def test_worker_complete_other_workers_duty_403(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    _, cleaner1_id = await worker_headers(client, owner, "CLEANER", "c1@farm.in")
    cleaner2, _ = await worker_headers(client, owner, "CLEANER", "c2@farm.in")
    duty = await make_duty(client, owner, "For c1", assigned_user_id=cleaner1_id)
    resp = await complete_duty(client, cleaner2, duty["id"])
    assert resp.status_code == 403


async def test_auto_duty_not_due_yet_409(client: httpx.AsyncClient) -> None:
    """Auto-generated duties unlock on their due date."""
    owner = await owner_with_farm(client)
    # bred 120 days ago → EKD in 30 days → DELIVERY move duty due in 15 days
    _doe, br = await make_pregnancy(client, owner, today() - timedelta(days=120))
    tabs = await get_tabs(client, owner)
    move = next(t for t in all_tasks(tabs) if t["category"] == "BUCKET_MOVE")
    assert (
        move["due_date"]
        == (date.fromisoformat(br["expected_kidding_date"]) - timedelta(days=15)).isoformat()
    )
    resp = await complete_duty(client, owner, move["id"])
    assert resp.status_code == 409
    assert resp.json()["detail"] == "This duty is not due yet"


async def test_auto_duty_due_today_completes(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await make_batch(client, owner)  # batch date today → day 1–3 rest duty due today
    tabs = await get_tabs(client, owner)
    rest = next(t for t in tabs["today"] if t["category"] == "QUARANTINE")
    resp = await complete_duty(client, owner, rest["id"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "DONE"


async def test_double_complete_spawns_only_one_occurrence(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Weekly", recur_days=7)
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    assert (await complete_duty(client, owner, duty["id"])).status_code == 400
    tabs = await get_tabs(client, owner)
    spawned = [t for t in tabs["upcoming"] if t["title"] == "Weekly"]
    assert len(spawned) == 1


async def test_complete_cleaning_sets_needs_verification(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Scrub", category="CLEANING")
    resp = await complete_duty(client, owner, duty["id"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "DONE"
    assert body["needs_verification"] is True


async def test_recomplete_after_reject_clears_note(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    manager, _ = await worker_headers(client, owner, "CLEANER_MANAGER", "cm@farm.in")
    duty = await make_duty(client, owner, "Scrub", category="CLEANING")
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    resp = await client.post(
        f"/api/tasks/{duty['id']}/reject", json={"note": "dirty"}, headers=manager
    )
    assert resp.status_code == 200, resp.text
    resp = await complete_duty(client, owner, duty["id"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["verification_note"] is None


# ---------------------------------------------------------------------------
# Skip
# ---------------------------------------------------------------------------
async def test_skip_happy_path(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Skip me")
    resp = await client.post(f"/api/tasks/{duty['id']}/skip", headers=owner)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "SKIPPED"


async def test_skip_leaves_no_attribution(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Skip me")
    resp = await client.post(f"/api/tasks/{duty['id']}/skip", headers=owner)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["completed_by_id"] is None
    assert body["completed_at"] is None


async def test_skip_done_duty_400(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Job")
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    resp = await client.post(f"/api/tasks/{duty['id']}/skip", headers=owner)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Task is not pending"


async def test_skip_skipped_duty_400(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Job")
    resp = await client.post(f"/api/tasks/{duty['id']}/skip", headers=owner)
    assert resp.status_code == 200, resp.text
    resp = await client.post(f"/api/tasks/{duty['id']}/skip", headers=owner)
    assert resp.status_code == 400


async def test_skip_nonexistent_404(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post("/api/tasks/424242/skip", headers=owner)
    assert resp.status_code == 404


async def test_skip_other_roles_duty_403(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    mover, _ = await worker_headers(client, owner, "MOVER", "mover@farm.in")
    rid = await role_id(client, owner, "CLEANER")
    duty = await make_duty(client, owner, "Scrub", category="CLEANING", assigned_role_id=rid)
    resp = await client.post(f"/api/tasks/{duty['id']}/skip", headers=mover)
    assert resp.status_code == 403
    tabs = await get_tabs(client, owner)
    assert find_task(tabs, duty["id"])["status"] == "PENDING"


async def test_skipped_cleaning_needs_no_verification(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Scrub", category="CLEANING")
    resp = await client.post(f"/api/tasks/{duty['id']}/skip", headers=owner)
    assert resp.status_code == 200, resp.text
    tabs = await get_tabs(client, owner)
    assert duty["id"] in {t["id"] for t in tabs["completed"]}
    assert duty["id"] not in {t["id"] for t in tabs["awaiting"]}


async def test_skip_form_linked_duty_allowed(client: httpx.AsyncClient) -> None:
    """The linked-form guard applies to complete, not skip (skipping records no data)."""
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner)
    buck = await make_buck(client, owner)
    await make_breeding(client, owner, doe, buck, today() - timedelta(days=40))
    tabs = await get_tabs(client, owner)
    us = next(t for t in all_tasks(tabs) if t["category"] == "ULTRASOUND")
    resp = await client.post(f"/api/tasks/{us['id']}/skip", headers=owner)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "SKIPPED"


async def test_skip_auto_future_duty_allowed(client: httpx.AsyncClient) -> None:
    """The not-due-yet guard applies to complete, not skip."""
    owner = await owner_with_farm(client)
    _doe, _br = await make_pregnancy(client, owner, today() - timedelta(days=120))
    tabs = await get_tabs(client, owner)
    move = next(t for t in all_tasks(tabs) if t["category"] == "BUCKET_MOVE")
    assert move["due_date"] > iso(today())
    resp = await client.post(f"/api/tasks/{move['id']}/skip", headers=owner)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "SKIPPED"


# ---------------------------------------------------------------------------
# Recurrence
# ---------------------------------------------------------------------------
async def test_complete_recurring_spawns_next_occurrence(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    rid = await role_id(client, owner, "CLEANER")
    duty = await make_duty(
        client, owner, "Weekly deep clean", category="CLEANING", assigned_role_id=rid, recur_days=7
    )
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    tabs = await get_tabs(client, owner)
    spawned = [t for t in tabs["upcoming"] if t["title"] == "Weekly deep clean"]
    assert len(spawned) == 1
    nxt = spawned[0]
    assert nxt["id"] != duty["id"]
    assert nxt["due_date"] == iso(today() + timedelta(days=7))
    assert nxt["status"] == "PENDING"
    assert nxt["category"] == "CLEANING"
    assert nxt["assigned_role_id"] == rid
    assert nxt["recur_days"] == 7
    assert nxt["auto_generated"] is False  # series started as a manual duty
    assert nxt["completed_by_id"] is None


async def test_spawned_occurrence_due_tomorrow_lands_in_upcoming(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Daily", recur_days=1)
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    tabs = await get_tabs(client, owner)
    assert "Daily" in {t["title"] for t in tabs["upcoming"]}
    assert "Daily" not in {t["title"] for t in tabs["today"]}


async def test_recurrence_chain_continues(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Chain", recur_days=3)
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    tabs = await get_tabs(client, owner)
    second = next(t for t in tabs["upcoming"] if t["title"] == "Chain")
    assert (await complete_duty(client, owner, second["id"])).status_code == 200
    tabs = await get_tabs(client, owner)
    thirds = [
        t
        for t in tabs["upcoming"]
        if t["title"] == "Chain" and t["due_date"] == iso(today() + timedelta(days=6))
    ]
    assert len(thirds) == 1


async def test_non_recurring_completion_spawns_nothing(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "One-off")
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    tabs = await get_tabs(client, owner)
    occurrences = [t for t in all_tasks(tabs) if t["title"] == "One-off"]
    assert len(occurrences) == 1


async def test_reject_then_recomplete_does_not_duplicate_spawn(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    manager, _ = await worker_headers(client, owner, "CLEANER_MANAGER", "cm@farm.in")
    duty = await make_duty(client, owner, "Daily scrub", category="CLEANING", recur_days=1)
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    resp = await client.post(
        f"/api/tasks/{duty['id']}/reject", json={"note": "redo"}, headers=manager
    )
    assert resp.status_code == 200, resp.text
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    tabs = await get_tabs(client, owner)
    spawned = [
        t
        for t in tabs["upcoming"]
        if t["title"] == "Daily scrub" and t["due_date"] == iso(today() + timedelta(days=1))
    ]
    assert len(spawned) == 1  # deduped per series


async def test_parallel_series_same_title_stay_independent(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    rid_cleaner = await role_id(client, owner, "CLEANER")
    rid_mover = await role_id(client, owner, "MOVER")
    a = await make_duty(client, owner, "Sweep", assigned_role_id=rid_cleaner, recur_days=1)
    b = await make_duty(client, owner, "Sweep", assigned_role_id=rid_mover, recur_days=1)
    assert (await complete_duty(client, owner, a["id"])).status_code == 200
    assert (await complete_duty(client, owner, b["id"])).status_code == 200
    tabs = await get_tabs(client, owner)
    spawned = [t for t in tabs["upcoming"] if t["title"] == "Sweep"]
    assert len(spawned) == 2
    assert {t["assigned_role_id"] for t in spawned} == {rid_cleaner, rid_mover}


async def test_recurring_personal_assignment_carried(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    _, cleaner_id = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    duty = await make_duty(client, owner, "His job", assigned_user_id=cleaner_id, recur_days=2)
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    tabs = await get_tabs(client, owner)
    nxt = next(t for t in tabs["upcoming"] if t["title"] == "His job")
    assert nxt["assigned_user_id"] == cleaner_id
    assert nxt["due_date"] == iso(today() + timedelta(days=2))


async def test_recurring_large_interval(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Decadal", recur_days=3650)
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    tabs = await get_tabs(client, owner)
    nxt = next(t for t in tabs["upcoming"] if t["title"] == "Decadal")
    assert nxt["due_date"] == iso(today() + timedelta(days=3650))


async def test_skip_recurring_spawns_next_occurrence(client: httpx.AsyncClient) -> None:
    """A skipped occurrence must not kill the series."""
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Daily", recur_days=1)
    resp = await client.post(f"/api/tasks/{duty['id']}/skip", headers=owner)
    assert resp.status_code == 200, resp.text
    tabs = await get_tabs(client, owner)
    nxt = next(t for t in tabs["upcoming"] if t["title"] == "Daily")
    assert nxt["status"] == "PENDING"
    assert nxt["due_date"] == iso(today() + timedelta(days=1))


# ---------------------------------------------------------------------------
# CLEANING verification loop
# ---------------------------------------------------------------------------
async def test_full_verification_loop(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    cleaner, cleaner_id = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    manager, manager_id = await worker_headers(client, owner, "CLEANER_MANAGER", "cm@farm.in")
    rid = await role_id(client, owner, "CLEANER")
    duty = await make_duty(client, owner, "Scrub pens", category="CLEANING", assigned_role_id=rid)

    resp = await complete_duty(client, cleaner, duty["id"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "DONE"
    assert resp.json()["completed_by_id"] == cleaner_id

    resp = await client.post(f"/api/tasks/{duty['id']}/verify", headers=manager)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "VERIFIED"
    assert body["verified_by_id"] == manager_id
    assert body["verified_at"] is not None
    assert body["completed_by_id"] == cleaner_id  # completion attribution preserved
    assert body["completed_at"] is not None


async def test_completer_cannot_verify_own_work_409(client: httpx.AsyncClient) -> None:
    """Two-person rule: the worker who did the duty cannot verify it himself."""
    owner = await owner_with_farm(client)
    manager, _ = await worker_headers(client, owner, "CLEANER_MANAGER", "cm@farm.in")
    rid = await role_id(client, owner, "CLEANER_MANAGER")
    duty = await make_duty(client, owner, "Scrub", category="CLEANING", assigned_role_id=rid)
    assert (await complete_duty(client, manager, duty["id"])).status_code == 200
    resp = await client.post(f"/api/tasks/{duty['id']}/verify", headers=manager)
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Someone else must verify this duty"


async def test_other_verifier_can_verify_after_409(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    manager1, _ = await worker_headers(client, owner, "CLEANER_MANAGER", "cm1@farm.in")
    manager2, manager2_id = await worker_headers(client, owner, "CLEANER_MANAGER", "cm2@farm.in")
    rid = await role_id(client, owner, "CLEANER_MANAGER")
    duty = await make_duty(client, owner, "Scrub", category="CLEANING", assigned_role_id=rid)
    assert (await complete_duty(client, manager1, duty["id"])).status_code == 200
    assert (
        await client.post(f"/api/tasks/{duty['id']}/verify", headers=manager1)
    ).status_code == 409
    resp = await client.post(f"/api/tasks/{duty['id']}/verify", headers=manager2)
    assert resp.status_code == 200, resp.text
    assert resp.json()["verified_by_id"] == manager2_id


async def test_owner_can_verify_own_completion(client: httpx.AsyncClient) -> None:
    """The farm owner is exempt from the two-person rule."""
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Scrub", category="CLEANING")
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    resp = await client.post(f"/api/tasks/{duty['id']}/verify", headers=owner)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "VERIFIED"


async def test_verify_non_cleaning_done_duty_400(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Plain")
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    resp = await client.post(f"/api/tasks/{duty['id']}/verify", headers=owner)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Task is not awaiting verification"


async def test_verify_pending_duty_400(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Scrub", category="CLEANING")
    resp = await client.post(f"/api/tasks/{duty['id']}/verify", headers=owner)
    assert resp.status_code == 400


async def test_double_verify_400(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    manager, _ = await worker_headers(client, owner, "CLEANER_MANAGER", "cm@farm.in")
    duty = await make_duty(client, owner, "Scrub", category="CLEANING")
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    resp = await client.post(f"/api/tasks/{duty['id']}/verify", headers=manager)
    assert resp.status_code == 200, resp.text
    resp = await client.post(f"/api/tasks/{duty['id']}/verify", headers=manager)
    assert resp.status_code == 400


async def test_reject_pending_duty_400(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Scrub", category="CLEANING")
    resp = await client.post(f"/api/tasks/{duty['id']}/reject", json={"note": "x"}, headers=owner)
    assert resp.status_code == 400


async def test_reject_verified_duty_400(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    manager, _ = await worker_headers(client, owner, "CLEANER_MANAGER", "cm@farm.in")
    duty = await make_duty(client, owner, "Scrub", category="CLEANING")
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    resp = await client.post(f"/api/tasks/{duty['id']}/verify", headers=manager)
    assert resp.status_code == 200, resp.text
    resp = await client.post(f"/api/tasks/{duty['id']}/reject", json={"note": "x"}, headers=manager)
    assert resp.status_code == 400


async def test_reject_non_cleaning_done_duty_400(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Plain")
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    resp = await client.post(f"/api/tasks/{duty['id']}/reject", json={"note": "x"}, headers=owner)
    assert resp.status_code == 400


async def test_reject_with_note_sends_back_to_worker(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    cleaner, _ = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    manager, _ = await worker_headers(client, owner, "CLEANER_MANAGER", "cm@farm.in")
    rid = await role_id(client, owner, "CLEANER")
    duty = await make_duty(client, owner, "Scrub", category="CLEANING", assigned_role_id=rid)
    assert (await complete_duty(client, cleaner, duty["id"])).status_code == 200
    resp = await client.post(
        f"/api/tasks/{duty['id']}/reject", json={"note": "corners still dirty"}, headers=manager
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "PENDING"
    assert body["verification_note"] == "corners still dirty"
    # back on the worker's list with the note visible
    tabs = await get_tabs(client, cleaner)
    mine = find_task(tabs, duty["id"])
    assert mine["status"] == "PENDING"
    assert mine["verification_note"] == "corners still dirty"
    # no longer awaiting review anywhere
    manager_tabs = await get_tabs(client, manager)
    assert duty["id"] not in {t["id"] for t in manager_tabs["awaiting"]}


async def test_reject_keeps_completion_attribution(client: httpx.AsyncClient) -> None:
    """completed_by/at survive the reject as a record of the rejected attempt."""
    owner = await owner_with_farm(client)
    cleaner, cleaner_id = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    manager, _ = await worker_headers(client, owner, "CLEANER_MANAGER", "cm@farm.in")
    rid = await role_id(client, owner, "CLEANER")
    duty = await make_duty(client, owner, "Scrub", category="CLEANING", assigned_role_id=rid)
    assert (await complete_duty(client, cleaner, duty["id"])).status_code == 200
    resp = await client.post(
        f"/api/tasks/{duty['id']}/reject", json={"note": "redo"}, headers=manager
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["completed_by_id"] == cleaner_id
    assert body["completed_at"] is not None


async def test_reject_without_note(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    manager, _ = await worker_headers(client, owner, "CLEANER_MANAGER", "cm@farm.in")
    duty = await make_duty(client, owner, "Scrub", category="CLEANING")
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    resp = await client.post(f"/api/tasks/{duty['id']}/reject", json={}, headers=manager)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "PENDING"
    assert body["verification_note"] is None


async def test_reject_whitespace_note_stored_as_null(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    manager, _ = await worker_headers(client, owner, "CLEANER_MANAGER", "cm@farm.in")
    duty = await make_duty(client, owner, "Scrub", category="CLEANING")
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    resp = await client.post(
        f"/api/tasks/{duty['id']}/reject", json={"note": "   "}, headers=manager
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["verification_note"] is None


async def test_reject_note_255_chars_ok(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Scrub", category="CLEANING")
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    resp = await client.post(
        f"/api/tasks/{duty['id']}/reject", json={"note": "x" * 255}, headers=owner
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["verification_note"] == "x" * 255


async def test_reject_note_256_chars_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Scrub", category="CLEANING")
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    resp = await client.post(
        f"/api/tasks/{duty['id']}/reject", json={"note": "x" * 256}, headers=owner
    )
    assert resp.status_code == 422


async def test_verify_after_reject_400(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    manager, _ = await worker_headers(client, owner, "CLEANER_MANAGER", "cm@farm.in")
    duty = await make_duty(client, owner, "Scrub", category="CLEANING")
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    resp = await client.post(
        f"/api/tasks/{duty['id']}/reject", json={"note": "redo"}, headers=manager
    )
    assert resp.status_code == 200, resp.text
    resp = await client.post(f"/api/tasks/{duty['id']}/verify", headers=manager)
    assert resp.status_code == 400  # PENDING again — nothing to verify


async def test_verify_nonexistent_404(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post("/api/tasks/424242/verify", headers=owner)
    assert resp.status_code == 404


async def test_reject_nonexistent_404(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post("/api/tasks/424242/reject", json={"note": "x"}, headers=owner)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Auto-generated task assignment to preset roles (SPEC: category → role map)
# ---------------------------------------------------------------------------
async def test_ultrasound_task_assigned_to_vet(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner)
    buck = await make_buck(client, owner)
    br = await make_breeding(client, owner, doe, buck, today())
    tabs = await get_tabs(client, owner)
    us = next(t for t in all_tasks(tabs) if t["category"] == "ULTRASOUND")
    assert us["auto_generated"] is True
    assert us["assigned_role_name"] == "Veterinarian"
    assert us["animal_id"] == doe["id"]
    assert us["animal_tag"] == doe["tag_number"]
    assert us["breeding_record_id"] == br["id"]


async def test_pregnancy_followup_tasks_map_to_preset_roles(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    _doe, _br = await make_pregnancy(client, owner, today() - timedelta(days=35))
    tabs = await get_tabs(client, owner)
    roles = {
        t["category"]: t["assigned_role_name"]
        for t in all_tasks(tabs)
        if t["category"] in ("VACCINE", "BUCKET_MOVE", "KIDDING_DUE")
    }
    assert roles == {
        "VACCINE": "Veterinarian",
        "BUCKET_MOVE": "Animal Mover",
        "KIDDING_DUE": "Veterinarian",
    }


async def test_weaning_task_assigned_to_mover(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    _doe, br = await make_pregnancy(client, owner, today() - timedelta(days=160))
    kidding_date = date.fromisoformat(br["expected_kidding_date"])
    await record_kidding(
        client,
        owner,
        br,
        kidding_date,
        [{"tag": "K-1", "sex": "M", "birth_weight": 2.5, "status": "ALIVE"}],
    )
    tabs = await get_tabs(client, owner)
    weaning = next(t for t in all_tasks(tabs) if t["category"] == "WEANING")
    assert weaning["auto_generated"] is True
    assert weaning["assigned_role_name"] == "Animal Mover"


async def test_quarantine_batch_tasks_map_to_preset_roles(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await make_batch(client, owner)
    tabs = await get_tabs(client, owner)
    auto = [t for t in all_tasks(tabs) if t["auto_generated"]]
    assert len(auto) == 8
    by_category = {}
    for t in auto:
        by_category.setdefault(t["category"], set()).add(t["assigned_role_name"])
    assert by_category["QUARANTINE"] == {"Veterinarian"}
    assert by_category["DEWORMING"] == {"Veterinarian"}
    assert by_category["VACCINE"] == {"Veterinarian"}
    assert by_category["BUCKET_MOVE"] == {"Animal Mover"}


async def test_vet_sees_ultrasound_duty_mover_does_not(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    vet, _ = await worker_headers(client, owner, "VET", "vet@farm.in")
    mover, _ = await worker_headers(client, owner, "MOVER", "mover@farm.in")
    doe = await make_doe(client, owner)
    buck = await make_buck(client, owner)
    await make_breeding(client, owner, doe, buck, today())
    vet_tabs = await get_tabs(client, vet)
    assert "ULTRASOUND" in {t["category"] for t in all_tasks(vet_tabs)}
    mover_tabs = await get_tabs(client, mover)
    assert "ULTRASOUND" not in {t["category"] for t in all_tasks(mover_tabs)}


# ---------------------------------------------------------------------------
# Form-linked duties refuse the bare complete button
# ---------------------------------------------------------------------------
async def test_ultrasound_duty_generic_complete_409(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner)
    buck = await make_buck(client, owner)
    br = await make_breeding(client, owner, doe, buck, today() - timedelta(days=40))
    tabs = await get_tabs(client, owner)
    us = next(t for t in all_tasks(tabs) if t["category"] == "ULTRASOUND")
    assert us["due_date"] < iso(today())  # due, so only the form-link guard can stop it
    assert us["action_url"] == f"/breeding/{br['id']}/ultrasound"
    resp = await complete_duty(client, owner, us["id"])
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Use the linked form to complete this duty"


async def test_kidding_due_duty_generic_complete_409(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    _doe, br = await make_pregnancy(client, owner, today() - timedelta(days=160))
    tabs = await get_tabs(client, owner)
    duty = next(t for t in all_tasks(tabs) if t["category"] == "KIDDING_DUE")
    assert duty["due_date"] < iso(today())
    assert duty["action_url"] == f"/kidding/new?breeding_id={br['id']}"
    resp = await complete_duty(client, owner, duty["id"])
    assert resp.status_code == 409


async def test_vaccine_duty_generic_complete_409(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe, _br = await make_pregnancy(client, owner, today() - timedelta(days=120))
    tabs = await get_tabs(client, owner)
    duty = next(t for t in all_tasks(tabs) if t["category"] == "VACCINE")
    assert duty["due_date"] < iso(today())  # EKD-40 is 10 days in the past
    assert duty["action_url"] is not None
    assert duty["action_url"].startswith("/health/new?task_id=")
    assert f"task_id={duty['id']}" in duty["action_url"]
    assert f"animal_id={doe['id']}" in duty["action_url"]
    resp = await complete_duty(client, owner, duty["id"])
    assert resp.status_code == 409


async def test_deworming_duty_action_url_includes_batch(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    batch_id = await make_batch(client, owner, today() - timedelta(days=10))
    tabs = await get_tabs(client, owner)
    duty = next(t for t in all_tasks(tabs) if t["category"] == "DEWORMING")
    assert duty["due_date"] < iso(today())
    assert duty["action_url"] is not None
    assert f"task_id={duty['id']}" in duty["action_url"]
    assert f"purchase_batch_id={batch_id}" in duty["action_url"]
    resp = await complete_duty(client, owner, duty["id"])
    assert resp.status_code == 409


async def test_ultrasound_form_closes_the_duty(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner)
    buck = await make_buck(client, owner)
    br = await make_breeding(client, owner, doe, buck, today() - timedelta(days=40))
    await submit_ultrasound(client, owner, br["id"], pregnant=False)
    tabs = await get_tabs(client, owner)
    us = next(t for t in all_tasks(tabs) if t["category"] == "ULTRASOUND")
    assert us["status"] == "DONE"
    assert us["id"] in {t["id"] for t in tabs["completed"]}


async def test_manual_vaccine_duty_generic_complete_409(client: httpx.AsyncClient) -> None:
    """Even a manual VACCINE duty must be closed through the health form."""
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "PPR round", category="VACCINE")
    assert duty["action_url"] == f"/health/new?task_id={duty['id']}"
    resp = await complete_duty(client, owner, duty["id"])
    assert resp.status_code == 409


async def test_manual_deworming_duty_generic_complete_409(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Deworm herd", category="DEWORMING")
    resp = await complete_duty(client, owner, duty["id"])
    assert resp.status_code == 409


async def test_manual_vaccine_duty_completed_via_health_form(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    _, owner_id = await login_user(client, "owner@farm.in", password="ownerpass123")
    animal_id = await make_animal(client, owner)
    duty = await make_duty(client, owner, "PPR round", category="VACCINE")
    resp = await client.post(
        "/api/health/events",
        json={
            "scope": "animal",
            "animal_id": animal_id,
            "type": "VACCINE",
            "product_name": "PPR",
            "task_id": duty["id"],
        },
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    tabs = await get_tabs(client, owner)
    done = find_task(tabs, duty["id"])
    assert done["status"] == "DONE"
    assert done["completed_by_id"] == owner_id
    assert done["id"] in {t["id"] for t in tabs["completed"]}


async def test_ultrasound_form_stamps_task_attribution(client: httpx.AsyncClient) -> None:
    """Closing the ULTRASOUND duty through the breeding form attributes the
    completion (it used to go DONE with completed_by_id/completed_at NULL)."""
    owner = await owner_with_farm(client)
    _, owner_id = await login_user(client, "owner@farm.in", password="ownerpass123")
    doe = await make_doe(client, owner)
    buck = await make_buck(client, owner)
    br = await make_breeding(client, owner, doe, buck, today() - timedelta(days=40))
    await submit_ultrasound(client, owner, br["id"], pregnant=True)
    us = next(t for t in all_tasks(await get_tabs(client, owner)) if t["category"] == "ULTRASOUND")
    assert us["status"] == "DONE"
    assert us["completed_by_id"] == owner_id
    assert us["completed_at"] is not None


async def test_kidding_form_stamps_task_attribution(client: httpx.AsyncClient) -> None:
    """Same for the KIDDING_DUE duty closed through the kidding form."""
    owner = await owner_with_farm(client)
    _, owner_id = await login_user(client, "owner@farm.in", password="ownerpass123")
    _doe, br = await make_pregnancy(client, owner, today() - timedelta(days=160))
    await record_kidding(
        client,
        owner,
        br,
        date.fromisoformat(br["expected_kidding_date"]),
        [{"tag": "K-1", "sex": "M", "birth_weight": 2.5, "status": "ALIVE"}],
    )
    kd = next(t for t in all_tasks(await get_tabs(client, owner)) if t["category"] == "KIDDING_DUE")
    assert kd["status"] == "DONE"
    assert kd["completed_by_id"] == owner_id
    assert kd["completed_at"] is not None


async def test_skip_stamps_skipped_by(client: httpx.AsyncClient) -> None:
    """Skipping a duty is attributed (skipped_by_id), mirroring completed_by_id."""
    owner = await owner_with_farm(client)
    _, owner_id = await login_user(client, "owner@farm.in", password="ownerpass123")
    duty = await make_duty(client, owner, "Job")
    resp = await client.post(f"/api/tasks/{duty['id']}/skip", headers=owner)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "SKIPPED"
    assert body["skipped_by_id"] == owner_id
    skipped = find_task(await get_tabs(client, owner), duty["id"])
    assert skipped["skipped_by_id"] == owner_id


async def test_health_form_ignores_non_vaccine_task_id(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal_id = await make_animal(client, owner)
    duty = await make_duty(client, owner, "Plain duty")
    resp = await client.post(
        "/api/health/events",
        json={
            "scope": "animal",
            "animal_id": animal_id,
            "type": "VACCINE",
            "product_name": "PPR",
            "task_id": duty["id"],
        },
        headers=owner,
    )
    assert resp.status_code == 201, resp.text  # event still recorded
    tabs = await get_tabs(client, owner)
    assert find_task(tabs, duty["id"])["status"] == "PENDING"  # duty untouched


async def test_manual_ultrasound_duty_without_link_completable(client: httpx.AsyncClient) -> None:
    """A manual ULTRASOUND duty has no breeding record to link to, so no form exists."""
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Check doe", category="ULTRASOUND")
    assert duty["action_url"] is None
    resp = await complete_duty(client, owner, duty["id"])
    assert resp.status_code == 200, resp.text


async def test_manual_kidding_due_duty_without_link_completable(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Watch pen", category="KIDDING_DUE")
    assert duty["action_url"] is None
    resp = await complete_duty(client, owner, duty["id"])
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Adversarial strings in notes
# ---------------------------------------------------------------------------
async def test_reject_note_unicode_roundtrip(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Scrub", category="CLEANING")
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    resp = await client.post(
        f"/api/tasks/{duty['id']}/reject", json={"note": "अजून घाण आहे 🧹"}, headers=owner
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["verification_note"] == "अजून घाण आहे 🧹"


async def test_reject_note_sql_injection_stored_literally(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "Scrub", category="CLEANING")
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    evil = "') OR '1'='1'; DELETE FROM tasks;--"
    resp = await client.post(f"/api/tasks/{duty['id']}/reject", json={"note": evil}, headers=owner)
    assert resp.status_code == 200, resp.text
    assert resp.json()["verification_note"] == evil
    tabs = await get_tabs(client, owner)
    assert find_task(tabs, duty["id"])["verification_note"] == evil


async def test_title_with_newline_stored(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, "line one\nline two")
    assert duty["title"] == "line one\nline two"
