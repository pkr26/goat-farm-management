"""Extended tests for the tasks & duties module (/api/tasks).

Covers the duty engine end to end against the SPEC contract:
- safe manual duty creation (role / specific worker / unassigned, recurrence)
  with stale, inactive and cross-farm assignments rejected;
- tabbed list (today / overdue / upcoming / awaiting / completed) bucketing,
  ordering and the 100-row completed-history cap;
- worker visibility (own role / personal duties only; only the awaiting-review
  queue is farm-wide for tasks.verify holders);
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

import asyncio
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import select, text, update

import app.api.animals as animals_api
from app.api._shared import task_action_url
from app.db import get_sessionmaker
from app.main import create_app
from app.models import Farm, FarmMembership, Task, TaskStatus, User
from app.permissions import (
    ALL_PERMISSIONS,
    PERMISSION_DEPENDENCIES,
    ROLE_PRESETS,
    TASK_CATEGORY_ACTION_PERMISSIONS,
    TASK_CATEGORY_ROLE_MAP,
)
from app.services.tasks import task_scope
from app.utils import today

from .conftest import owner_with_farm

WORKER_PW = "workerpass123"


def lock_race_client() -> httpx.AsyncClient:
    """An independent ASGI client so two requests hold two DB transactions."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()),
        base_url="http://test",
    )


async def wait_for_lock_waiters(minimum: int = 1, timeout_seconds: float = 10.0) -> None:
    """Wait for at least ``minimum`` sessions blocked on PostgreSQL locks."""
    for _ in range(int(timeout_seconds / 0.01)):
        async with get_sessionmaker()() as db:
            blocked = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() "
                        "AND pid <> pg_backend_pid() "
                        "AND wait_event_type = 'Lock'"
                    )
                )
            ).scalar_one()
        if blocked >= minimum:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"expected {minimum} lock waiters, saw fewer")


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
            "historical_import_reason": "Existing-herd test fixture",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def make_doe(client: httpx.AsyncClient, headers: dict, tag: str = "D-101") -> dict:
    dob = today() - timedelta(days=800)
    resp = await client.post(
        "/api/animals",
        json={
            "tag_number": tag,
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "BREEDING",
            "date_of_birth": iso(dob),
            "weight_kg": 26.0,
            "weight_date": iso(dob),
            "historical_import_reason": "Existing-herd test fixture",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def make_buck(client: httpx.AsyncClient, headers: dict, tag: str = "B-01") -> dict:
    dob = today() - timedelta(days=800)
    resp = await client.post(
        "/api/animals",
        json={
            "tag_number": tag,
            "sex": "M",
            "source": "PURCHASED",
            "current_bucket": "BREEDING",
            "date_of_birth": iso(dob),
            "weight_kg": 30.0,
            "weight_date": iso(dob),
            "historical_import_reason": "Existing-herd test fixture",
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
    # The result is observed on the scheduled check, not "today": a pregnancy
    # confirmed today whose kidding is then recorded on the earlier expected
    # kidding date would predate its own confirmation.
    detail = await client.get(f"/api/breeding/{breeding_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    payload: dict[str, object] = {"pregnant": pregnant, "date": detail.json()["ultrasound_date"]}
    if pregnant:
        payload["kid_count"] = kid_count
    resp = await client.post(
        f"/api/breeding/{breeding_id}/ultrasound",
        json=payload,
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
    ["FEED", "CLEANING", "OTHER"],
)
async def test_create_every_safe_manual_category(client: httpx.AsyncClient, category: str) -> None:
    owner = await owner_with_farm(client)
    duty = await make_duty(client, owner, f"Duty {category}", category=category)
    assert duty["category"] == category


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
    ],
)
async def test_create_rejects_system_workflow_categories(
    client: httpx.AsyncClient, category: str
) -> None:
    owner = await owner_with_farm(client)
    response = await post_duty(
        client,
        owner,
        title=f"Forged {category}",
        due_date=iso(today()),
        category=category,
    )
    assert response.status_code == 422, response.text


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
    cleaner_role = await role_id(client, owner, "CLEANER")
    duty = await make_duty(client, owner, "Wash bottles", assigned_user_id=cleaner_id)
    assert duty["assigned_user_id"] == cleaner_id
    assert duty["assigned_user_name"] == "Chandu"
    assert duty["assigned_role_id"] == cleaner_role
    assert duty["assigned_role_name"] == "Cleaner"


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


async def test_create_rejects_unknown_extra_fields(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(
        client,
        owner,
        title="Extra fields",
        due_date=iso(today()),
        bogus_field="whatever",
        status="DONE",  # cannot smuggle a state in either
    )
    assert resp.status_code == 422, resp.text
    assert all_tasks(await get_tabs(client, owner)) == []


async def test_create_far_past_date(client: httpx.AsyncClient) -> None:
    """The duty-year band starts at 2000 (mirroring the purchase-date floor):
    year 1900 is refused; a 2001 duty is the oldest acceptable overdue item."""
    owner = await owner_with_farm(client)
    resp = await post_duty(client, owner, title="Ancient", due_date="1900-01-01")
    assert resp.status_code == 422, resp.text
    assert "between year 2000 and 2100" in resp.text

    duty = await make_duty(client, owner, "Ancient", due=date(2001, 1, 1))
    assert duty["due_date"] == "2001-01-01"
    tabs = await get_tabs(client, owner)
    assert tabs["overdue"][-1]["id"] == duty["id"]  # oldest overdue sorts first


async def test_create_far_future_date(client: httpx.AsyncClient) -> None:
    """The duty-year band ends at 2100: year 2200 is refused; a 2099 duty is
    the farthest acceptable upcoming item."""
    owner = await owner_with_farm(client)
    resp = await post_duty(client, owner, title="Far future", due_date="2200-01-01")
    assert resp.status_code == 422, resp.text
    assert "between year 2000 and 2100" in resp.text

    duty = await make_duty(client, owner, "Far future", due=date(2099, 1, 1))
    assert duty["due_date"] == "2099-01-01"
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
        "recurring_series_id",
        "completed_by_id",
        "completed_at",
        "verified_by_id",
        "verified_at",
        "verification_note",
        "skipped_by_id",
        "skipped_at",
        "skip_reason",
        "rejected_by_id",
        "rejected_at",
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


async def test_create_rejects_recurring_date_without_representable_successor(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    response = await post_duty(
        client,
        owner,
        title="Impossible recurrence",
        due_date=date.max.isoformat(),
        recur_days=1,
    )
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("action", ["complete", "skip"])
async def test_legacy_impossible_recurrence_returns_409_without_successor(
    client: httpx.AsyncClient,
    action: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        legacy = Task(
            farm_id=farm_id,
            title=f"Legacy max-date recurrence {action}",
            due_date=date.max,
            status=TaskStatus.PENDING.value,
            category="OTHER",
            auto_generated=False,
            recur_days=1,
            recurring_series_id=f"legacy-max-date-{action}",
        )
        db.add(legacy)
        await db.commit()
        task_id = legacy.id

    # Exercise the legacy overflow defense at the representable-date boundary.
    # In normal present-day requests the stricter future-recurring guard rejects
    # this row first, so model the day on which the occurrence is actually due.
    monkeypatch.setattr("app.api.tasks.today", lambda _timezone=None: date.max)
    response = await client.post(f"/api/tasks/{task_id}/{action}", headers=owner)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Recurring duty cannot schedule a representable next date"
    async with get_sessionmaker()() as db:
        rows = list(
            (
                await db.execute(
                    select(Task).where(Task.recurring_series_id == f"legacy-max-date-{action}")
                )
            ).scalars()
        )
    assert len(rows) == 1
    assert rows[0].status == TaskStatus.PENDING.value


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


@pytest.mark.parametrize("field", ["assigned_role_id", "assigned_user_id"])
async def test_create_schema_valid_but_int32_impossible_assignment_id_is_400_not_500(
    client: httpx.AsyncClient,
    field: str,
) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(
        client,
        owner,
        title="Impossible assignment",
        due_date=iso(today()),
        **{field: 2**31},
    )
    assert resp.status_code == 400, resp.text
    expected = (
        "Assigned role is not on this farm"
        if field == "assigned_role_id"
        else "Assigned worker is not an active member of this farm"
    )
    assert resp.json()["detail"] == expected


async def test_create_user_id_zero_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(client, owner, title="R", due_date=iso(today()), assigned_user_id=0)
    assert resp.status_code == 422


async def test_create_role_id_noninteger_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(client, owner, title="R", due_date=iso(today()), assigned_role_id="abc")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Invalid assignees fail closed: never acknowledge a duty that nobody can see
# ---------------------------------------------------------------------------
async def test_cross_farm_role_id_rejected(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    rid_b = await role_id(client, owner_b, "CLEANER")
    resp = await post_duty(
        client,
        owner_a,
        title="Cross-farm role",
        due_date=iso(today()),
        assigned_role_id=rid_b,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Assigned role is not on this farm"


async def test_unknown_role_id_rejected(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(
        client,
        owner,
        title="Ghost role",
        due_date=iso(today()),
        assigned_role_id=999_999,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Assigned role is not on this farm"


async def test_cross_farm_user_id_rejected(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    _, owner_b_id = await login_user(client, "b@farm.in", password="ownerpass123")
    resp = await post_duty(
        client,
        owner_a,
        title="Cross-farm user",
        due_date=iso(today()),
        assigned_user_id=owner_b_id,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Assigned worker is not an active member of this farm"


async def test_unknown_user_id_rejected(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(
        client,
        owner,
        title="Ghost user",
        due_date=iso(today()),
        assigned_user_id=999_999,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Assigned worker is not an active member of this farm"


async def test_owner_as_assigned_user_rejected(client: httpx.AsyncClient) -> None:
    """The farm owner is not a membership, so he cannot be a duty assignee."""
    owner = await owner_with_farm(client)
    _, owner_id = await login_user(client, "owner@farm.in", password="ownerpass123")
    resp = await post_duty(
        client,
        owner,
        title="Assign to owner",
        due_date=iso(today()),
        assigned_user_id=owner_id,
    )
    assert resp.status_code == 400


async def test_deactivated_worker_rejected(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    _, cleaner_id = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    resp = await client.get("/api/team", headers=owner)
    mid = next(m["id"] for m in resp.json()["memberships"] if m["email"] == "cleaner@farm.in")
    resp = await client.put(
        f"/api/team/workers/{mid}/status", json={"is_active": False}, headers=owner
    )
    assert resp.status_code == 200, resp.text
    resp = await post_duty(
        client,
        owner,
        title="Inactive worker",
        due_date=iso(today()),
        assigned_user_id=cleaner_id,
    )
    assert resp.status_code == 400


async def test_worker_and_role_must_match(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    _, cleaner_id = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    mover_role_id = await role_id(client, owner, "MOVER")
    resp = await post_duty(
        client,
        owner,
        title="Conflicting assignment",
        due_date=iso(today()),
        assigned_role_id=mover_role_id,
        assigned_user_id=cleaner_id,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Assigned worker does not hold the assigned role"


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
    assert set(tabs) == {
        "today",
        "overdue",
        "upcoming",
        "awaiting",
        "completed",
        "today_total",
        "today_offset",
        "overdue_total",
        "overdue_offset",
        "upcoming_total",
        "upcoming_offset",
        "awaiting_total",
        "awaiting_offset",
        "active_limit",
        "completed_total",
        "completed_limit",
        "completed_offset",
    }
    assert all(tabs[key] == [] for key in ("today", "overdue", "upcoming", "awaiting", "completed"))
    assert tabs["completed_total"] == 0
    assert tabs["today_total"] == tabs["overdue_total"] == tabs["upcoming_total"] == 0
    assert tabs["awaiting_total"] == 0


async def test_active_task_tabs_are_counted_and_independently_paginated(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    for title in ("first", "second", "third"):
        await make_duty(client, owner, title)

    response = await client.get(
        "/api/tasks",
        params={"active_limit": 2, "today_offset": 1},
        headers=owner,
    )
    assert response.status_code == 200, response.text
    tabs = response.json()
    assert tabs["today_total"] == 3
    assert tabs["today_offset"] == 1
    assert tabs["active_limit"] == 2
    assert [task["title"] for task in tabs["today"]] == ["second", "third"]
    assert tabs["overdue"] == [] and tabs["upcoming"] == []


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
    assert tabs["completed_total"] == 105
    page_two = await client.get(
        "/api/tasks", params={"completed_limit": 10, "completed_offset": 100}, headers=owner
    )
    assert page_two.status_code == 200, page_two.text
    assert len(page_two.json()["completed"]) == 5


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


async def test_personal_duty_is_hidden_from_role_peers_while_assignee_is_active(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    _, cleaner1_id = await worker_headers(client, owner, "CLEANER", "c1@farm.in")
    cleaner2, _ = await worker_headers(client, owner, "CLEANER", "c2@farm.in")
    duty = await make_duty(client, owner, "For c1 only", assigned_user_id=cleaner1_id)
    tabs = await get_tabs(client, cleaner2)
    assert duty["id"] not in {t["id"] for t in all_tasks(tabs)}


async def test_task_scope_legacy_personal_null_role_uses_retained_membership(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    _, assignee_id = await worker_headers(client, owner, "CLEANER", "legacy-a@farm.in")
    _, peer_id = await worker_headers(client, owner, "CLEANER", "legacy-b@farm.in")
    duty = await make_duty(client, owner, "Legacy personal", assigned_user_id=assignee_id)
    farm_id = int(owner["X-Farm-Id"])

    # D9 prevents new null-role personal rows. Transactional DDL lets this
    # test emulate one pre-repair legacy row and roll the constraint/data
    # change back atomically without weakening the test database.
    async with get_sessionmaker()() as db:
        await db.execute(
            text("ALTER TABLE tasks DROP CONSTRAINT ck_tasks_user_assignment_has_role")
        )
        await db.execute(update(Task).where(Task.id == duty["id"]).values(assigned_role_id=None))
        farm = await db.get(Farm, farm_id)
        peer = await db.get(User, peer_id)
        assert farm is not None and peer is not None
        scoped = await task_scope(db, farm, peer)
        visible_ids = set((await db.execute(scoped)).scalars())
        assert all(task.id != duty["id"] for task in visible_ids)
        await db.execute(
            update(FarmMembership)
            .where(
                FarmMembership.farm_id == farm_id,
                FarmMembership.user_id == assignee_id,
            )
            .values(is_active=False)
        )
        scoped = await task_scope(db, farm, peer)
        fallback_ids = set((await db.execute(scoped)).scalars())
        assert any(task.id == duty["id"] for task in fallback_ids)
        await db.rollback()


async def test_inactive_animal_pending_task_is_hidden_and_cannot_complete_or_skip(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal_id = await make_animal(client, owner, "INACTIVE-TASK")
    sold = await client.post(
        f"/api/animals/{animal_id}/status",
        json={"new_status": "SOLD", "sale_price": 100},
        headers=owner,
    )
    assert sold.status_code == 200, sold.text
    farm_id = int(owner["X-Farm-Id"])
    residue_count = 600
    async with get_sessionmaker()() as db:
        residues = [
            Task(
                farm_id=farm_id,
                title=f"Legacy inactive-animal residue {index}",
                due_date=today(),
                status=TaskStatus.PENDING.value,
                category="OTHER",
                animal_id=animal_id,
                auto_generated=False,
                recur_days=1 if index == 0 else None,
                recurring_series_id="inactive-animal-residue" if index == 0 else None,
            )
            for index in range(residue_count)
        ]
        db.add_all(residues)
        await db.commit()
        task_id = residues[0].id
        residue_ids = {task.id for task in residues}

    tabs = await get_tabs(client, owner)
    assert residue_ids.isdisjoint(task["id"] for task in all_tasks(tabs))
    assert tabs["today_total"] == tabs["overdue_total"] == tabs["upcoming_total"] == 0
    completed = await client.post(f"/api/tasks/{task_id}/complete", headers=owner)
    assert completed.status_code == 409, completed.text
    skipped = await client.post(f"/api/tasks/{task_id}/skip", headers=owner)
    assert skipped.status_code == 409, skipped.text
    async with get_sessionmaker()() as db:
        retained = (
            (
                await db.execute(
                    select(Task).where(
                        Task.animal_id == animal_id,
                        Task.status == TaskStatus.PENDING.value,
                    )
                )
            )
            .scalars()
            .all()
        )
        series_count = len(
            (
                await db.execute(
                    select(Task.id).where(Task.recurring_series_id == "inactive-animal-residue")
                )
            )
            .scalars()
            .all()
        )
    assert len(retained) == residue_count
    assert series_count == 1


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


async def test_verifier_completed_tab_remains_assignment_scoped(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    cleaner, _ = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    manager, _ = await worker_headers(client, owner, "CLEANER_MANAGER", "cm@farm.in")
    rid = await role_id(client, owner, "CLEANER")
    duty = await make_duty(client, owner, "Scrub", category="CLEANING", assigned_role_id=rid)
    assert (await complete_duty(client, cleaner, duty["id"])).status_code == 200
    resp = await client.post(f"/api/tasks/{duty['id']}/verify", headers=owner)
    assert resp.status_code == 200, resp.text
    manager_tabs = await get_tabs(client, manager)
    assert duty["id"] not in {t["id"] for t in manager_tabs["completed"]}
    assert all(row["title"] != "Scrub" for row in manager_tabs["completed"])


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


async def test_manual_bucket_move_category_is_rejected_before_any_lifecycle_side_effect(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe, _breeding = await make_pregnancy(client, owner, today() - timedelta(days=40))
    animal_id = doe["id"]
    before = await client.get(f"/api/animals/{animal_id}", headers=owner)
    assert before.status_code == 200, before.text
    assert before.json()["animal"]["current_bucket"] == "PREGNANCY_EARLY"
    response = await post_duty(
        client,
        owner,
        title="Inspect pen before deciding a move",
        due_date=iso(today()),
        category="BUCKET_MOVE",
        animal_id=animal_id,
    )
    assert response.status_code == 422, response.text
    profile = await client.get(f"/api/animals/{animal_id}", headers=owner)
    assert profile.status_code == 200, profile.text
    assert profile.json()["animal"]["current_bucket"] == "PREGNANCY_EARLY"
    assert profile.json()["moves"] == before.json()["moves"]


@pytest.mark.parametrize("category", ["WEANING", "BUCKET_MOVE"])
async def test_tasks_only_worker_cannot_trigger_legacy_manual_animal_side_effect(
    client: httpx.AsyncClient,
    category: str,
) -> None:
    owner = await owner_with_farm(client)
    role = await make_custom_role(
        client,
        owner,
        "Checklist only",
        ["tasks.view", "tasks.complete"],
    )
    await add_worker(client, owner, role, "checklist@farm.in")
    worker, _ = await login_user(client, "checklist@farm.in")
    worker["X-Farm-Id"] = owner["X-Farm-Id"]
    animal_id = await make_animal(client, owner, f"NO-SIDE-EFFECT-{category}")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        forged = Task(
            farm_id=farm_id,
            title=f"Forged manual {category}",
            due_date=today(),
            status=TaskStatus.PENDING.value,
            category=category,
            animal_id=animal_id,
            auto_generated=False,
            assigned_role_id=role,
        )
        db.add(forged)
        await db.commit()
        task_id = forged.id

    before = await client.get(f"/api/animals/{animal_id}", headers=owner)
    response = await complete_duty(client, worker, task_id)
    assert response.status_code == 409, response.text
    assert "authoritative generated duty" in response.json()["detail"]
    after = await client.get(f"/api/animals/{animal_id}", headers=owner)
    assert after.json()["animal"]["current_bucket"] == before.json()["animal"]["current_bucket"]
    assert after.json()["moves"] == before.json()["moves"]


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


async def test_role_peer_can_complete_personal_duty_only_after_assignee_is_inactive(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    _, cleaner1_id = await worker_headers(client, owner, "CLEANER", "c1@farm.in")
    cleaner2, cleaner2_id = await worker_headers(client, owner, "CLEANER", "c2@farm.in")
    duty = await make_duty(client, owner, "For c1", assigned_user_id=cleaner1_id)
    resp = await complete_duty(client, cleaner2, duty["id"])
    assert resp.status_code == 403, resp.text
    team = await client.get("/api/team", headers=owner)
    assignee_membership_id = next(
        row["id"] for row in team.json()["memberships"] if row["user_id"] == cleaner1_id
    )
    deactivated = await client.put(
        f"/api/team/workers/{assignee_membership_id}/status",
        json={"is_active": False},
        headers=owner,
    )
    assert deactivated.status_code == 200, deactivated.text
    resp = await complete_duty(client, cleaner2, duty["id"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["completed_by_id"] == cleaner2_id


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


async def test_pregnancy_move_duty_cannot_hide_a_recorded_movement_hold(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe, _br = await make_pregnancy(client, owner, today() - timedelta(days=135))
    held = await client.post(
        "/api/health/events",
        json={
            "scope": "animal",
            "animal_id": doe["id"],
            "type": "TREATMENT",
            "disease_target": "Reportable-condition concern",
            "suspected_scheduled_disease": True,
        },
        headers=owner,
    )
    assert held.status_code == 201, held.text
    move = next(
        task
        for task in all_tasks(await get_tabs(client, owner))
        if task["category"] == "BUCKET_MOVE"
    )

    resp = await complete_duty(client, owner, move["id"])
    assert resp.status_code == 409
    assert "restriction" in resp.json()["detail"].lower()
    assert find_task(await get_tabs(client, owner), move["id"])["status"] == "PENDING"


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


async def test_quarantine_protocol_tasks_cannot_be_skipped_into_release_deadlock(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    batch_id = await make_batch(client, owner)
    batch_tasks = [
        task
        for task in all_tasks(await get_tabs(client, owner))
        if task["purchase_batch_id"] == batch_id
    ]
    assert len(batch_tasks) == 8

    for task in batch_tasks:
        response = await client.post(
            f"/api/tasks/{task['id']}/skip",
            json={"reason": "Bypass protocol"},
            headers=owner,
        )
        assert response.status_code == 409, (task, response.text)
        assert response.json()["detail"] == (
            "Quarantine protocol duties cannot be skipped; complete the required workflow"
        )

    persisted = {
        task["id"]: task
        for task in all_tasks(await get_tabs(client, owner))
        if task["purchase_batch_id"] == batch_id
    }
    assert set(persisted) == {task["id"] for task in batch_tasks}
    assert all(task["status"] == "PENDING" for task in persisted.values())


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
async def test_verified_recurring_cleaning_spawns_next_occurrence(
    client: httpx.AsyncClient,
) -> None:
    """CLEANING needs verification, so DONE is not terminal: the successor
    spawns on verify (a reject could still reopen this occurrence)."""
    owner = await owner_with_farm(client)
    rid = await role_id(client, owner, "CLEANER")
    duty = await make_duty(
        client, owner, "Weekly deep clean", category="CLEANING", assigned_role_id=rid, recur_days=7
    )
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    tabs = await get_tabs(client, owner)
    assert not [t for t in tabs["upcoming"] if t["title"] == "Weekly deep clean"]
    # The farm owner is exempt from the someone-else-must-verify rule.
    resp = await client.post(f"/api/tasks/{duty['id']}/verify", headers=owner)
    assert resp.status_code == 200, resp.text
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
    duty = await make_duty(client, owner, "Chain", due=today() - timedelta(days=3), recur_days=3)
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    tabs = await get_tabs(client, owner)
    second = next(t for t in tabs["upcoming"] if t["title"] == "Chain")

    # A recurrence cannot be fast-forwarded before its farm-local due date.
    early = await complete_duty(client, owner, second["id"])
    assert early.status_code == 409, early.text
    assert early.json()["detail"] == "This duty is not due yet"

    # Advance this fixture to the next occurrence date without changing the
    # farm clock; the real deployment reaches this state as the day advances.
    async with get_sessionmaker()() as db:
        row = await db.get(Task, second["id"])
        assert row is not None
        row.due_date = today()
        await db.commit()
    assert (await complete_duty(client, owner, second["id"])).status_code == 200
    tabs = await get_tabs(client, owner)
    thirds = [
        t
        for t in tabs["upcoming"]
        if t["title"] == "Chain" and t["due_date"] == iso(today() + timedelta(days=3))
    ]
    assert len(thirds) == 1


async def test_future_recurring_occurrence_cannot_be_completed_or_skipped(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    first = await make_duty(client, owner, "No recurrence fast-forward", recur_days=1)
    assert (await complete_duty(client, owner, first["id"])).status_code == 200
    successor = next(
        task
        for task in (await get_tabs(client, owner))["upcoming"]
        if task["recurring_series_id"] == first["recurring_series_id"]
    )

    for action in ("complete", "skip"):
        response = await client.post(f"/api/tasks/{successor['id']}/{action}", headers=owner)
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "This duty is not due yet"

    async with get_sessionmaker()() as db:
        rows = list(
            (
                await db.execute(
                    select(Task)
                    .where(Task.recurring_series_id == first["recurring_series_id"])
                    .order_by(Task.due_date, Task.id)
                )
            ).scalars()
        )
    assert [row.status for row in rows] == [TaskStatus.DONE.value, TaskStatus.PENDING.value]


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
    # Neither completion spawned — DONE is not terminal for CLEANING. Only
    # the single verification mints the successor, so a reject → re-complete
    # loop cannot double the series.
    tabs = await get_tabs(client, owner)
    assert not [t for t in tabs["upcoming"] if t["title"] == "Daily scrub"]
    resp = await client.post(f"/api/tasks/{duty['id']}/verify", headers=manager)
    assert resp.status_code == 200, resp.text
    tabs = await get_tabs(client, owner)
    spawned = [
        t
        for t in tabs["upcoming"]
        if t["title"] == "Daily scrub" and t["due_date"] == iso(today() + timedelta(days=1))
    ]
    assert len(spawned) == 1  # exactly one successor for the whole episode


async def test_late_review_cannot_reopen_a_verified_occurrence(
    client: httpx.AsyncClient,
) -> None:
    """The successor only exists once its predecessor is VERIFIED, and a
    verified occurrence is final — so the old race (reject occurrence N after
    its spawned N+1 already ran) is structurally impossible now."""
    owner = await owner_with_farm(client)
    manager, _ = await worker_headers(client, owner, "CLEANER_MANAGER", "late-review@farm.in")
    first = await make_duty(
        client,
        owner,
        "Late-reviewed sweep",
        due=today() - timedelta(days=1),
        category="CLEANING",
        recur_days=1,
    )
    assert (await complete_duty(client, owner, first["id"])).status_code == 200
    resp = await client.post(f"/api/tasks/{first['id']}/verify", headers=manager)
    assert resp.status_code == 200, resp.text
    second = next(
        task
        for task in (await get_tabs(client, owner))["upcoming"]
        if task["title"] == "Late-reviewed sweep"
    )
    assert second["id"] != first["id"]

    rejected = await client.post(
        f"/api/tasks/{first['id']}/reject",
        json={"note": "Redo the first occurrence"},
        headers=manager,
    )
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["detail"] == "Task is not awaiting verification"

    # The series carries exactly one live successor; the closed occurrence
    # stays VERIFIED.
    tabs = await get_tabs(client, owner)
    assert find_task(tabs, first["id"])["status"] == "VERIFIED"
    live = [
        t
        for t in all_tasks(tabs)
        if t["title"] == "Late-reviewed sweep" and t["status"] == "PENDING"
    ]
    assert [t["id"] for t in live] == [second["id"]]


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


async def test_identical_parallel_recurring_series_do_not_merge(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    a = await make_duty(client, owner, "Identical sweep", recur_days=1)
    b = await make_duty(client, owner, "Identical sweep", recur_days=1)
    assert a["recurring_series_id"] != b["recurring_series_id"]
    assert (await complete_duty(client, owner, a["id"])).status_code == 200
    assert (await complete_duty(client, owner, b["id"])).status_code == 200
    spawned = [
        task
        for task in (await get_tabs(client, owner))["upcoming"]
        if task["title"] == "Identical sweep"
    ]
    assert len(spawned) == 2
    assert {task["recurring_series_id"] for task in spawned} == {
        a["recurring_series_id"],
        b["recurring_series_id"],
    }


async def test_recurring_personal_assignment_carried(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    _, cleaner_id = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    cleaner_role = await role_id(client, owner, "CLEANER")
    duty = await make_duty(client, owner, "His job", assigned_user_id=cleaner_id, recur_days=2)
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    tabs = await get_tabs(client, owner)
    nxt = next(t for t in tabs["upcoming"] if t["title"] == "His job")
    assert nxt["assigned_user_id"] == cleaner_id
    assert nxt["assigned_role_id"] == cleaner_role
    assert nxt["due_date"] == iso(today() + timedelta(days=2))


@pytest.mark.parametrize("action", ["complete", "skip"])
async def test_legacy_personal_recurrence_resolves_retained_role_before_spawn(
    client: httpx.AsyncClient,
    action: str,
) -> None:
    owner = await owner_with_farm(client)
    _, worker_id = await worker_headers(client, owner, "CLEANER", f"legacy-{action}@farm.in")
    peer, _ = await worker_headers(
        client,
        owner,
        "CLEANER",
        f"legacy-{action}-peer@farm.in",
    )
    cleaner_role = await role_id(client, owner, "CLEANER")
    duty = await make_duty(
        client,
        owner,
        f"Legacy recurrence {action}",
        assigned_user_id=worker_id,
        recur_days=1,
    )

    async with get_sessionmaker()() as db:
        await db.execute(
            text("ALTER TABLE tasks DROP CONSTRAINT ck_tasks_user_assignment_has_role")
        )
        await db.execute(update(Task).where(Task.id == duty["id"]).values(assigned_role_id=None))
        await db.commit()

    try:
        # The same-role peer sees the legacy row through task_scope and the
        # mutation lazily repairs the missing role before authorization and
        # successor insertion. This closes the compatibility gap where list
        # visibility could otherwise lead to an action-time 403 or CHECK 500.
        team = await client.get("/api/team", headers=owner)
        membership_id = next(
            row["id"] for row in team.json()["memberships"] if row["user_id"] == worker_id
        )
        deactivated = await client.put(
            f"/api/team/workers/{membership_id}/status",
            json={"is_active": False},
            headers=owner,
        )
        assert deactivated.status_code == 200, deactivated.text
        response = await client.post(f"/api/tasks/{duty['id']}/{action}", headers=peer)
        assert response.status_code == 200, response.text
        async with get_sessionmaker()() as db:
            rows = list(
                (
                    await db.execute(
                        select(Task)
                        .where(Task.recurring_series_id == duty["recurring_series_id"])
                        .order_by(Task.due_date, Task.id)
                    )
                ).scalars()
            )
        assert len(rows) == 2
        assert all(row.assigned_user_id == worker_id for row in rows)
        assert all(row.assigned_role_id == cleaner_role for row in rows)
        assert rows[-1].status == TaskStatus.PENDING.value
    finally:
        # Restore the invariant even if an assertion above fails, so a local
        # developer can continue running other tests in this same session DB.
        async with get_sessionmaker()() as db:
            await db.execute(
                text(
                    "UPDATE tasks AS task SET assigned_role_id = membership.role_id "
                    "FROM farm_memberships AS membership "
                    "WHERE task.assigned_user_id IS NOT NULL "
                    "AND task.assigned_role_id IS NULL "
                    "AND membership.farm_id = task.farm_id "
                    "AND membership.user_id = task.assigned_user_id"
                )
            )
            await db.execute(
                text(
                    "ALTER TABLE tasks ADD CONSTRAINT ck_tasks_user_assignment_has_role "
                    "CHECK (assigned_user_id IS NULL OR assigned_role_id IS NOT NULL) NOT VALID"
                )
            )
            await db.execute(
                text("ALTER TABLE tasks VALIDATE CONSTRAINT ck_tasks_user_assignment_has_role")
            )
            await db.commit()


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


async def test_reject_records_who_rejected_and_when(client: httpx.AsyncClient) -> None:
    """Rejection is attributed like complete/verify/skip, not note-only."""
    owner = await owner_with_farm(client)
    cleaner, _ = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    manager, manager_id = await worker_headers(client, owner, "CLEANER_MANAGER", "cm@farm.in")
    rid = await role_id(client, owner, "CLEANER")
    duty = await make_duty(client, owner, "Scrub", category="CLEANING", assigned_role_id=rid)
    assert duty["rejected_by_id"] is None
    assert duty["rejected_at"] is None
    assert (await complete_duty(client, cleaner, duty["id"])).status_code == 200
    resp = await client.post(
        f"/api/tasks/{duty['id']}/reject", json={"note": "corners still dirty"}, headers=manager
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rejected_by_id"] == manager_id
    assert body["rejected_at"] is not None
    # The worker sees who sent it back, not only the note.
    mine = find_task(await get_tabs(client, cleaner), duty["id"])
    assert mine["rejected_by_id"] == manager_id
    assert mine["rejected_at"] == body["rejected_at"]


async def test_rejection_trail_clears_when_the_duty_moves_on(client: httpx.AsyncClient) -> None:
    """rejected_by/at describe the rejection a row currently carries, exactly
    as skipped_by/at describe a SKIPPED one — they are cleared with the note
    when the duty is re-completed and re-verified."""
    owner = await owner_with_farm(client)
    cleaner, _ = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    manager, manager_id = await worker_headers(client, owner, "CLEANER_MANAGER", "cm@farm.in")
    rid = await role_id(client, owner, "CLEANER")
    duty = await make_duty(client, owner, "Scrub", category="CLEANING", assigned_role_id=rid)
    assert (await complete_duty(client, cleaner, duty["id"])).status_code == 200
    rejected = await client.post(
        f"/api/tasks/{duty['id']}/reject", json={"note": "redo"}, headers=manager
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["rejected_by_id"] == manager_id

    redone = await complete_duty(client, cleaner, duty["id"])
    assert redone.status_code == 200, redone.text
    assert redone.json()["rejected_by_id"] is None
    assert redone.json()["rejected_at"] is None
    assert redone.json()["verification_note"] is None

    verified = await client.post(f"/api/tasks/{duty['id']}/verify", headers=manager)
    assert verified.status_code == 200, verified.text
    assert verified.json()["status"] == "VERIFIED"
    assert verified.json()["rejected_by_id"] is None
    assert verified.json()["rejected_at"] is None


async def test_recurring_successor_carries_no_rejection_trail(client: httpx.AsyncClient) -> None:
    """A spawned occurrence is a fresh duty, not a copy of the rejected one."""
    owner = await owner_with_farm(client)
    cleaner, _ = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    manager, manager_id = await worker_headers(client, owner, "CLEANER_MANAGER", "cm@farm.in")
    rid = await role_id(client, owner, "CLEANER")
    duty = await make_duty(
        client, owner, "Daily scrub", category="CLEANING", assigned_role_id=rid, recur_days=1
    )
    assert (await complete_duty(client, cleaner, duty["id"])).status_code == 200
    rejected = await client.post(
        f"/api/tasks/{duty['id']}/reject", json={"note": "redo"}, headers=manager
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["rejected_by_id"] == manager_id
    assert (await complete_duty(client, cleaner, duty["id"])).status_code == 200
    # The successor is minted on the terminal transition — verification.
    resp = await client.post(f"/api/tasks/{duty['id']}/verify", headers=manager)
    assert resp.status_code == 200, resp.text

    tabs = await get_tabs(client, owner)
    successor = next(
        t for t in all_tasks(tabs) if t["title"] == "Daily scrub" and t["id"] != duty["id"]
    )
    assert successor["status"] == "PENDING"
    assert successor["rejected_by_id"] is None
    assert successor["rejected_at"] is None
    assert successor["verification_note"] is None


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
# The invariant behind the category → role map: the assignee can ACT on it
# ---------------------------------------------------------------------------
# Asserting the mapping (KIDDING_DUE → VET) is not the same as asserting the
# assignee can do the work. VET's preset carried no kidding permission at all,
# so KIDDING_DUE duties landed on a role that could neither open the kidding
# form nor record the kidding — and the duty refuses the bare complete button,
# so nobody holding it could ever close one.
@pytest.mark.parametrize(
    ("category", "role_code"),
    sorted(TASK_CATEGORY_ROLE_MAP.items()),
    ids=sorted(TASK_CATEGORY_ROLE_MAP),
)
def test_auto_assigned_role_holds_the_permissions_that_category_needs(
    category: str, role_code: str
) -> None:
    preset = next(p for p in ROLE_PRESETS if p["code"] == role_code)
    held = set(preset["permissions"])
    required = {"tasks.view", "tasks.complete"} | TASK_CATEGORY_ACTION_PERMISSIONS[category]
    # An action permission is unusable without its module view (api/team.py
    # enforces the same dependency when a role is edited).
    required |= {
        PERMISSION_DEPENDENCIES[code] for code in required if code in PERMISSION_DEPENDENCIES
    }
    assert required <= held, (
        f"{role_code} is the default assignee for {category} duties "
        f"but lacks {sorted(required - held)}"
    )


def test_every_auto_assigned_category_declares_its_action_permissions() -> None:
    assert set(TASK_CATEGORY_ACTION_PERMISSIONS) == set(TASK_CATEGORY_ROLE_MAP)
    for codes in TASK_CATEGORY_ACTION_PERMISSIONS.values():
        assert codes <= ALL_PERMISSIONS


def test_form_linked_categories_declare_the_permission_their_form_needs() -> None:
    """A form-linked duty can only be closed by submitting its linked form, so
    it must declare that form's permission — otherwise the invariant above has
    nothing to check and a newly form-linked category could be routed to a
    role that cannot submit it."""
    for category in TASK_CATEGORY_ROLE_MAP:
        probe = Task(
            farm_id=1,
            title="probe",
            due_date=today(),
            category=category,
            animal_id=1,
            breeding_record_id=1,
            purchase_batch_id=1,
        )
        if task_action_url(probe) is not None:
            assert TASK_CATEGORY_ACTION_PERMISSIONS[category], (
                f"{category} duties are closed through a form; declare its permission"
            )


async def test_vet_can_record_the_kidding_its_own_duty_demands(
    client: httpx.AsyncClient,
) -> None:
    """End to end for the mismatch above: the vet attends the kidding her
    KIDDING_DUE duty is for, and submitting the form is what closes it."""
    owner = await owner_with_farm(client)
    vet, vet_id = await worker_headers(client, owner, "VET", "vet@farm.in")
    _doe, br = await make_pregnancy(client, owner, today() - timedelta(days=160))
    duty = next(t for t in all_tasks(await get_tabs(client, vet)) if t["category"] == "KIDDING_DUE")
    assert duty["action_url"] == f"/kidding/new?breeding_id={br['id']}"

    # The form loads its pregnancy, then records the kidding.
    lookup = await client.get(f"/api/kidding/pregnancies/{br['id']}", headers=vet)
    assert lookup.status_code == 200, lookup.text
    kidding = await record_kidding(
        client,
        vet,
        br,
        date.fromisoformat(br["expected_kidding_date"]),
        [{"tag": "K-1", "sex": "F", "birth_weight": 2.6, "status": "ALIVE"}],
    )
    assert kidding["breeding_record_id"] == br["id"]

    closed = find_task(await get_tabs(client, owner), duty["id"])
    assert closed["status"] == "DONE"
    assert closed["completed_by_id"] == vet_id


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


async def test_unlinked_manual_vaccine_duty_is_rejected(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(
        client,
        owner,
        title="PPR vaccine due",
        due_date=iso(today()),
        category="VACCINE",
    )
    assert resp.status_code == 422


async def test_unlinked_manual_deworming_duty_is_rejected(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(
        client,
        owner,
        title="Deworm herd",
        due_date=iso(today()),
        category="DEWORMING",
    )
    assert resp.status_code == 422


async def test_generated_vaccine_duty_completed_via_health_form(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    _, owner_id = await login_user(client, "owner@farm.in", password="ownerpass123")
    doe, _breeding = await make_pregnancy(
        client,
        owner,
        today() - timedelta(days=140),
    )
    animal_id = doe["id"]
    duty = next(
        row
        for row in all_tasks(await get_tabs(client, owner))
        if row["category"] == "VACCINE" and row["animal_id"] == animal_id
    )
    assert duty["action_url"] == f"/health/new?task_id={duty['id']}&animal_id={animal_id}"
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
    resp = await client.post(
        f"/api/tasks/{duty['id']}/skip",
        json={"reason": "Pen unavailable"},
        headers=owner,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "SKIPPED"
    assert body["skipped_by_id"] == owner_id
    assert body["skipped_at"] is not None
    assert body["skip_reason"] == "Pen unavailable"
    skipped = find_task(await get_tabs(client, owner), duty["id"])
    assert skipped["skipped_by_id"] == owner_id
    assert skipped["skipped_at"] is not None


async def test_health_form_rejects_non_vaccine_task_id(client: httpx.AsyncClient) -> None:
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
    assert resp.status_code == 409, resp.text
    tabs = await get_tabs(client, owner)
    assert find_task(tabs, duty["id"])["status"] == "PENDING"  # duty untouched


async def test_manual_ultrasound_duty_is_rejected(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(
        client,
        owner,
        title="Check doe",
        due_date=iso(today()),
        category="ULTRASOUND",
    )
    assert resp.status_code == 422, resp.text


async def test_manual_kidding_due_duty_is_rejected(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_duty(
        client,
        owner,
        title="Watch pen",
        due_date=iso(today()),
        category="KIDDING_DUE",
    )
    assert resp.status_code == 422, resp.text


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


# ---------------------------------------------------------------------------
# Audit 2026-08-09 regressions
# ---------------------------------------------------------------------------
async def test_completed_tab_interleaves_skipped_duties_by_their_finish_instant(
    client: httpx.AsyncClient,
) -> None:
    """A SKIPPED row carries only skipped_at, and PostgreSQL sorts the NULL
    completed_at FIRST under DESC — so ordering on completed_at alone put every
    skip ahead of every real completion."""
    owner = await owner_with_farm(client)
    first = await make_duty(client, owner, "A completed first")
    skipped = await make_duty(client, owner, "B skipped second")
    last = await make_duty(client, owner, "C completed last")
    assert (await complete_duty(client, owner, first["id"])).status_code == 200
    assert (await client.post(f"/api/tasks/{skipped['id']}/skip", headers=owner)).status_code == 200
    assert (await complete_duty(client, owner, last["id"])).status_code == 200

    tabs = await get_tabs(client, owner)
    assert [t["title"] for t in tabs["completed"]][:3] == [
        "C completed last",
        "B skipped second",
        "A completed first",
    ]
    page_one = await client.get("/api/tasks", params={"completed_limit": 1}, headers=owner)
    assert page_one.status_code == 200, page_one.text
    assert [t["id"] for t in page_one.json()["completed"]] == [last["id"]]


async def test_weaning_duty_cannot_be_skipped_while_the_family_is_in_recovery(
    client: httpx.AsyncClient,
) -> None:
    """RECOVERY only opens for the weaning/postpartum contexts this duty
    produces, so skipping it used to strand the doe and her kids there."""
    owner = await owner_with_farm(client)
    doe, br = await make_pregnancy(client, owner, today() - timedelta(days=230))
    kidding_date = date.fromisoformat(br["expected_kidding_date"])
    await record_kidding(
        client,
        owner,
        br,
        kidding_date,
        [{"tag": "WEAN-K1", "sex": "F", "birth_weight": 2.5, "status": "ALIVE"}],
    )
    tabs = await get_tabs(client, owner)
    weaning = next(t for t in all_tasks(tabs) if t["category"] == "WEANING")

    refused = await client.post(
        f"/api/tasks/{weaning['id']}/skip",
        json={"reason": "handled offline"},
        headers=owner,
    )
    assert refused.status_code == 409, refused.text
    assert "postpartum recovery" in refused.json()["detail"]
    assert find_task(await get_tabs(client, owner), weaning["id"])["status"] == "PENDING"

    # The duty stays the working exit: completing it releases doe and kid.
    assert (await complete_duty(client, owner, weaning["id"])).status_code == 200
    doe_after = await client.get(f"/api/animals/{doe['id']}", headers=owner)
    assert doe_after.json()["animal"]["current_bucket"] == "RESTING"


async def test_postpartum_move_duty_cannot_be_skipped_while_the_doe_is_in_recovery(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    _doe, br = await make_pregnancy(client, owner, today() - timedelta(days=170))
    kidding_date = date.fromisoformat(br["expected_kidding_date"])
    await record_kidding(
        client,
        owner,
        br,
        kidding_date,
        [{"tag": "PP-K1", "sex": "F", "birth_weight": 2.0, "status": "STILLBORN"}],
    )
    tabs = await get_tabs(client, owner)
    postpartum = next(
        t for t in all_tasks(tabs) if t["category"] == "BUCKET_MOVE" and t["status"] == "PENDING"
    )
    refused = await client.post(f"/api/tasks/{postpartum['id']}/skip", headers=owner)
    assert refused.status_code == 409, refused.text
    assert "postpartum recovery" in refused.json()["detail"]


async def test_pregnancy_delivery_move_duty_is_still_skippable(
    client: httpx.AsyncClient,
) -> None:
    """The guard is scoped to RECOVERY: a pre-kidding move has other exits."""
    owner = await owner_with_farm(client)
    _doe, _br = await make_pregnancy(client, owner, today() - timedelta(days=140))
    tabs = await get_tabs(client, owner)
    move = next(t for t in all_tasks(tabs) if t["category"] == "BUCKET_MOVE")
    skipped = await client.post(f"/api/tasks/{move['id']}/skip", headers=owner)
    assert skipped.status_code == 200, skipped.text
    assert skipped.json()["status"] == "SKIPPED"


async def test_quarantine_duty_is_skippable_only_once_the_batch_has_no_active_animal(
    client: httpx.AsyncClient,
) -> None:
    """A batch that loses every animal can never write the linked health event
    nor pass the day-45 release, so its protocol duties need a terminal path."""
    owner = await owner_with_farm(client)
    await make_batch(client, owner, today() - timedelta(days=50))
    tabs = await get_tabs(client, owner)
    vaccine = next(t for t in all_tasks(tabs) if t["category"] == "VACCINE" and t["auto_generated"])
    blocked = await client.post(f"/api/tasks/{vaccine['id']}/skip", headers=owner)
    assert blocked.status_code == 409, blocked.text
    assert "cannot be skipped" in blocked.json()["detail"]

    animals = await client.get("/api/animals", headers=owner)
    assert animals.status_code == 200, animals.text
    for animal in animals.json()["animals"]:
        dead = await client.post(
            f"/api/animals/{animal['id']}/status",
            json={"new_status": "DEAD"},
            headers=owner,
        )
        assert dead.status_code == 200, dead.text

    tabs = await get_tabs(client, owner)
    for duty in [t for t in all_tasks(tabs) if t["auto_generated"] and t["status"] == "PENDING"]:
        closed = await client.post(
            f"/api/tasks/{duty['id']}/skip",
            json={"reason": "Batch lost every animal in quarantine"},
            headers=owner,
        )
        assert closed.status_code == 200, closed.text
    assert not [t for t in all_tasks(await get_tabs(client, owner)) if t["status"] == "PENDING"]


async def test_reject_repairs_a_legacy_personal_duty_instead_of_500(
    client: httpx.AsyncClient,
) -> None:
    """reject() flips the row to PENDING — the exact state
    ck_tasks_user_assignment_has_role constrains — so a pre-D9 personal row
    must be repaired first, like complete/skip already do."""
    owner = await owner_with_farm(client)
    worker, worker_id = await worker_headers(client, owner, "CLEANER", "legacy-reject@farm.in")
    cleaner_role = await role_id(client, owner, "CLEANER")
    duty = await make_duty(
        client, owner, "Legacy scrub", category="CLEANING", assigned_user_id=worker_id
    )
    assert (await complete_duty(client, worker, duty["id"])).status_code == 200
    # Only the constraint's own DDL can produce the pre-D9 shape; it is put
    # back verbatim so the row rejected below is validated against production
    # semantics.
    async with get_sessionmaker()() as db:
        await db.execute(
            text("ALTER TABLE tasks DROP CONSTRAINT ck_tasks_user_assignment_has_role")
        )
        await db.execute(update(Task).where(Task.id == duty["id"]).values(assigned_role_id=None))
        await db.execute(
            text(
                "ALTER TABLE tasks ADD CONSTRAINT ck_tasks_user_assignment_has_role "
                "CHECK (status <> 'PENDING' OR assigned_user_id IS NULL "
                "OR assigned_role_id IS NOT NULL) NOT VALID"
            )
        )
        await db.commit()

    response = await client.post(
        f"/api/tasks/{duty['id']}/reject", json={"note": "redo"}, headers=owner
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "PENDING"
    assert response.json()["assigned_role_id"] == cleaner_role
    assert response.json()["assigned_role_name"] == "Cleaner"
    async with get_sessionmaker()() as db:
        await db.execute(
            text("ALTER TABLE tasks VALIDATE CONSTRAINT ck_tasks_user_assignment_has_role")
        )
        await db.commit()


async def test_manual_queue_lock_does_not_deadlock_with_an_animal_first_sale(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The per-farm duty-queue mutex must not be a Farm ROW lock.

    A sale holds ANIMAL and then needs FOR KEY SHARE on farms to insert its
    Transaction, while a recurring completion took the farm mutex before the
    ANIMAL lock. With `SELECT farms.id ... FOR UPDATE` those two orders invert
    and PostgreSQL aborts one of them with a 500.
    """
    owner = await owner_with_farm(client)
    animal_id = await make_animal(client, owner, "DEADLOCK-1")
    created = await post_duty(
        client,
        owner,
        title="Recurring animal check",
        due_date=iso(today()),
        category="OTHER",
        animal_id=animal_id,
        recur_days=1,
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]

    parked = asyncio.Event()
    release = asyncio.Event()
    real_skip = animals_api.skip_pending_tasks_for_animal

    async def parking_skip(*args: object, **kwargs: object) -> None:
        # Reached with the ANIMAL row already locked and immediately before the
        # sale Transaction insert that needs farm KEY SHARE.
        parked.set()
        await release.wait()
        await real_skip(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(animals_api, "skip_pending_tasks_for_animal", parking_skip)

    async with lock_race_client() as sale_client, lock_race_client() as complete_client:
        sale_request = asyncio.create_task(
            sale_client.post(
                f"/api/animals/{animal_id}/status",
                json={"new_status": "SOLD", "sale_price": 5000},
                headers=owner,
            )
        )
        complete_request: asyncio.Task[httpx.Response] | None = None
        try:
            await asyncio.wait_for(parked.wait(), timeout=10)
            complete_request = asyncio.create_task(
                complete_client.post(f"/api/tasks/{task_id}/complete", headers=owner)
            )
            # Completion now holds the farm mutex and queues on the animal.
            await wait_for_lock_waiters(1)
            release.set()
            sale_response, complete_response = await asyncio.gather(sale_request, complete_request)
        finally:
            release.set()
            if not sale_request.done():
                sale_request.cancel()
            if complete_request is not None and not complete_request.done():
                complete_request.cancel()

    assert sale_response.status_code == 200, sale_response.text
    # The sale committed first and swept the duty to SKIPPED, so completion
    # loses on its own state re-check — a domain 400, never the deadlock 500
    # the farm ROW lock produced.
    assert complete_response.status_code == 400, complete_response.text
    assert complete_response.json()["detail"] == "Task is not pending"
