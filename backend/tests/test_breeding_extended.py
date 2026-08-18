"""Extended coverage for the breeding & kidding modules (SPEC: breed constants,
bucket system, BreedingRecord/KiddingRecord entities, task auto-generation).

Covers, through the async JSON API only:
- GET /api/breeding (paged records + bounded candidate availability counts)
- GET /api/breeding/candidates (SQL-filtered/paged eligible doe and buck identities)
- POST /api/breeding (eligibility guards: female, >=10 months, >=22 kg, not
  pregnant, correct bucket; ultrasound task at breeding + 32d)
- GET /api/breeding/{id}
- POST /api/breeding/{id}/ultrasound (gestation 150d, follow-up tasks,
  heat-cycle re-breeding, two-failed-cycles cull candidate)
- POST /api/breeding/{id}/abort
- GET /api/kidding (upcoming/overdue due list, history)
- POST /api/kidding (kids auto-created as Animals, doe -> RECOVERY,
  weaning task at +60d)
- Weaning completion (kids -> MALE_KIDS/FEMALE_KIDS by sex, doe -> RESTING)
- Auth / farm-tenancy / RBAC / malformed-input guards for all of the above.

Dates are driven through API payload fields (the codebase has no freezegun
hook): breedings are backdated so that expected kidding dates, weaning duties,
etc. fall where the API guards require them.
"""

import inspect
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError

from app.db import get_engine, get_sessionmaker
from app.models import (
    Animal,
    BreedingRecord,
    BucketMove,
    KiddingRecord,
    KidEntry,
    User,
    WeightRecord,
)
from app.services.breeding import mark_unassessed, record_ultrasound_result
from app.utils import add_months, today

from .conftest import owner_with_farm, register

WORKER_PW = "workerpass123"
GESTATION_DAYS = 150
ULTRASOUND_AFTER_BREEDING_DAYS = 32
WEANING_DAYS = 60
POSTPARTUM_RECOVERY_DAYS = 14


def iso(d: date) -> str:
    return d.isoformat()


# ---------------------------------------------------------------------------
# Setup helpers (everything goes through the API)
# ---------------------------------------------------------------------------
async def make_animal(
    client: httpx.AsyncClient,
    headers: dict,
    tag: str,
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


async def make_doe(
    client: httpx.AsyncClient,
    headers: dict,
    tag: str = "D-1",
    bucket: str = "FOUNDATION",
    age_days: int = 800,
    weight_kg: float | None = 26.0,
) -> dict:
    """A mature doe with enough dated history for backdated flow tests."""
    dob = today() - timedelta(days=age_days)
    overrides: dict = {"date_of_birth": iso(dob)}
    if weight_kg is not None:
        overrides["weight_kg"] = weight_kg
        overrides["weight_date"] = iso(dob)
    return await make_animal(client, headers, tag, sex="F", bucket=bucket, **overrides)


async def make_doe_aged_months(
    client: httpx.AsyncClient,
    headers: dict,
    tag: str,
    months: int,
    weight_kg: float | None = 26.0,
) -> dict:
    overrides: dict = {"date_of_birth": iso(add_months(today(), -months))}
    if weight_kg is not None:
        overrides["weight_kg"] = weight_kg
        overrides["weight_date"] = overrides["date_of_birth"]
    return await make_animal(client, headers, tag, sex="F", **overrides)


async def make_buck(
    client: httpx.AsyncClient, headers: dict, tag: str = "B-1", **overrides: object
) -> dict:
    defaults: dict[str, object] = {
        "date_of_birth": iso(today() - timedelta(days=800)),
        "weight_kg": 30.0,
        "weight_date": iso(today() - timedelta(days=800)),
    }
    return await make_animal(
        client, headers, tag, sex="M", bucket="BREEDING", **(defaults | overrides)
    )


async def place_legacy_animal_in_breeding(animal_id: int) -> None:
    """Model a pre-hardening row that bypassed today's import/move guards.

    The public API correctly refuses immature, underweight, or unweighed
    animals in BREEDING.  Candidate and service guards still need coverage
    for legacy data that predates that invariant, so only those tests use a
    direct fixture mutation.
    """
    async with get_sessionmaker()() as db:
        animal = await db.get(Animal, animal_id)
        assert animal is not None
        animal.current_bucket = "BREEDING"
        await db.commit()


async def backdate_latest_bucket_move(animal_id: int, effective_date: date) -> None:
    """Age an API-created move for a coherent multi-cycle history fixture."""
    async with get_sessionmaker()() as db:
        move = (
            await db.execute(
                select(BucketMove)
                .where(BucketMove.animal_id == animal_id)
                .order_by(BucketMove.moved_at.desc(), BucketMove.id.desc())
                .limit(1)
            )
        ).scalar_one()
        move.effective_date = effective_date
        await db.commit()


async def post_breeding(
    client: httpx.AsyncClient, headers: dict, doe_id: int, buck_id: int, **overrides: object
) -> httpx.Response:
    payload = {
        "doe_id": doe_id,
        "buck_id": buck_id,
        "breeding_date": iso(today()),
    } | overrides
    return await client.post("/api/breeding", json=payload, headers=headers)


async def make_breeding(
    client: httpx.AsyncClient, headers: dict, doe_id: int, buck_id: int, **overrides: object
) -> dict:
    resp = await post_breeding(client, headers, doe_id, buck_id, **overrides)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def bred_doe(
    client: httpx.AsyncClient,
    headers: dict,
    tag: str = "D-1",
    breeding_date: date | None = None,
) -> tuple[dict, dict, dict]:
    """Breeding-ready doe + buck and one PENDING breeding. Returns (doe, buck, br)."""
    doe = await make_doe(client, headers, tag)
    buck = await make_buck(client, headers, f"{tag}-BUCK")
    br = await make_breeding(
        client,
        headers,
        doe["id"],
        buck["id"],
        # Most callers subsequently record an ultrasound.  Keep the default
        # at least the scheduled +32-day check interval in the past so that
        # the implicit, farm-local observation date is chronologically valid.
        breeding_date=iso(
            breeding_date or today() - timedelta(days=ULTRASOUND_AFTER_BREEDING_DAYS + 3)
        ),
    )
    return doe, buck, br


async def ultrasound(
    client: httpx.AsyncClient, headers: dict, br_id: int, **payload: object
) -> httpx.Response:
    body = {"pregnant": True, "kid_count": 2} | payload
    return await client.post(f"/api/breeding/{br_id}/ultrasound", json=body, headers=headers)


async def place_health_hold(client: httpx.AsyncClient, headers: dict, animal_id: int) -> None:
    response = await client.post(
        "/api/health/events",
        json={
            "scope": "animal",
            "animal_id": animal_id,
            "type": "TREATMENT",
            "disease_target": "Reportable-condition concern",
            "suspected_scheduled_disease": True,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text


async def confirm(
    client: httpx.AsyncClient, headers: dict, br_id: int, kid_count: int | None = 2
) -> dict:
    # Same rationale as fail_cycle: the result is observed on the scheduled
    # check, not "today". Confirming today and then recording the kidding on
    # the (earlier) expected kidding date would be a delivery predating its own
    # confirmation, which record_kidding rejects.
    breeding = await get_breeding(client, headers, br_id)
    resp = await ultrasound(
        client,
        headers,
        br_id,
        pregnant=True,
        kid_count=kid_count,
        date=breeding["ultrasound_date"],
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def fail_cycle(client: httpx.AsyncClient, headers: dict, br_id: int) -> dict:
    # Outcome-flow fixtures represent a result observed on the scheduled
    # check, not "today". This preserves an honest boundary when later tests
    # record a subsequent historical service between that check and today.
    breeding = await get_breeding(client, headers, br_id)
    resp = await ultrasound(
        client,
        headers,
        br_id,
        pregnant=False,
        kid_count=None,
        date=breeding["ultrasound_date"],
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def post_abort(
    client: httpx.AsyncClient, headers: dict, br_id: int, **overrides: object
) -> httpx.Response:
    payload = {
        "loss_date": iso(today()),
        "cause": "UNKNOWN",
        "notes": "Pregnancy loss recorded in test",
    } | overrides
    return await client.post(f"/api/breeding/{br_id}/abort", json=payload, headers=headers)


async def pregnant_doe(
    client: httpx.AsyncClient,
    headers: dict,
    tag: str = "D-1",
    gestation_days: int = 35,
) -> tuple[dict, dict, dict]:
    """Confirmed pregnancy bred `gestation_days` ago (EKD = breeding + 150)."""
    doe, buck, br = await bred_doe(client, headers, tag, today() - timedelta(days=gestation_days))
    br = await confirm(client, headers, br["id"])
    return doe, buck, br


async def post_kidding(
    client: httpx.AsyncClient, headers: dict, br_id: int, **overrides: object
) -> httpx.Response:
    payload = {
        "breeding_record_id": br_id,
        "date": iso(today()),
        "ease": "NORMAL",
        "kids": [{"sex": "M"}, {"sex": "F"}],
    } | overrides
    return await client.post("/api/kidding", json=payload, headers=headers)


async def make_kidding(
    client: httpx.AsyncClient, headers: dict, br_id: int, **overrides: object
) -> dict:
    resp = await post_kidding(client, headers, br_id, **overrides)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def kid_on_ekd(
    client: httpx.AsyncClient, headers: dict, br: dict, **overrides: object
) -> dict:
    """Record a kidding exactly on the expected kidding date (always <= today
    when the breeding was backdated >= 150 days)."""
    return await make_kidding(
        client, headers, br["id"], date=br["expected_kidding_date"], **overrides
    )


async def get_animal(client: httpx.AsyncClient, headers: dict, animal_id: int) -> dict:
    resp = await client.get(f"/api/animals/{animal_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["animal"]


async def list_animals(client: httpx.AsyncClient, headers: dict, **params: str) -> list[dict]:
    resp = await client.get("/api/animals", headers=headers, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()["animals"]


async def get_breeding(client: httpx.AsyncClient, headers: dict, br_id: int) -> dict:
    resp = await client.get(f"/api/breeding/{br_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def breeding_list(client: httpx.AsyncClient, headers: dict) -> dict:
    resp = await client.get("/api/breeding", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def candidate_list(
    client: httpx.AsyncClient,
    headers: dict,
    kind: str = "doe",
    **params: object,
) -> dict:
    resp = await client.get(
        "/api/breeding/candidates",
        params={"kind": kind} | params,
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def candidate_ids(
    client: httpx.AsyncClient,
    headers: dict,
    kind: str = "doe",
    **params: object,
) -> list[int]:
    page = await candidate_list(client, headers, kind, **params)
    return [row["id"] for row in page["candidates"]]


async def kidding_list(client: httpx.AsyncClient, headers: dict) -> dict:
    resp = await client.get("/api/kidding", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def move_to(
    client: httpx.AsyncClient,
    headers: dict,
    animal_id: int,
    bucket: str,
    *,
    history_override: bool = False,
) -> dict:
    body: dict[str, object] = {"to_bucket": bucket}
    if history_override:
        body |= {
            "history_override": True,
            "reason": "Test fixture: imported lifecycle completion",
        }
    resp = await client.post(f"/api/animals/{animal_id}/move", json=body, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def set_status(client: httpx.AsyncClient, headers: dict, animal_id: int, status: str) -> dict:
    resp = await client.post(
        f"/api/animals/{animal_id}/status", json={"new_status": status}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def all_tasks(client: httpx.AsyncClient, headers: dict) -> list[dict]:
    """Every duty across the five tabs of GET /api/tasks."""
    resp = await client.get("/api/tasks", headers=headers)
    assert resp.status_code == 200, resp.text
    tabs = resp.json()
    return tabs["today"] + tabs["overdue"] + tabs["upcoming"] + tabs["awaiting"] + tabs["completed"]


def tasks_by_category(tasks: list[dict], category: str) -> list[dict]:
    return [t for t in tasks if t["category"] == category]


async def worker_headers(
    client: httpx.AsyncClient, owner: dict, role_code: str, email: str
) -> dict:
    """Owner adds a worker with a preset role; returns that worker's farm headers."""
    resp = await client.get("/api/team", headers=owner)
    assert resp.status_code == 200, resp.text
    role_id = next(r["id"] for r in resp.json()["roles"] if r["code"] == role_code)
    resp = await client.post(
        "/api/team/workers",
        json={"name": "Worker", "email": email, "password": WORKER_PW, "role_id": role_id},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    resp = await client.post("/api/auth/login", json={"email": email, "password": WORKER_PW})
    assert resp.status_code == 200, resp.text
    return {
        "Authorization": f"Bearer {resp.json()['access_token']}",
        "X-Farm-Id": owner["X-Farm-Id"],
    }


async def custom_breeding_viewer_headers(
    client: httpx.AsyncClient, owner: dict, email: str
) -> dict:
    role = await client.post(
        "/api/team/roles",
        json={"name": "Breeding Records Reader", "permissions": ["breeding.view"]},
        headers=owner,
    )
    assert role.status_code == 201, role.text
    worker = await client.post(
        "/api/team/workers",
        json={
            "name": "Breeding Reader",
            "email": email,
            "password": WORKER_PW,
            "role_id": role.json()["id"],
        },
        headers=owner,
    )
    assert worker.status_code == 201, worker.text
    login = await client.post("/api/auth/login", json={"email": email, "password": WORKER_PW})
    assert login.status_code == 200, login.text
    return {
        "Authorization": f"Bearer {login.json()['access_token']}",
        "X-Farm-Id": owner["X-Farm-Id"],
    }


# ---------------------------------------------------------------------------
# GET /api/breeding history and the independently paginated candidate picker
# ---------------------------------------------------------------------------
async def test_breeding_list_empty_farm(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    body = await breeding_list(client, headers)
    assert body["records"] == []
    assert body["candidate_availability"] == {
        "eligible_doe_count": 0,
        "eligible_buck_count": 0,
    }


async def test_candidate_doe_in_foundation_is_listed(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers)
    assert await candidate_ids(client, headers) == [doe["id"]]


async def test_candidate_search_treats_backslash_as_literal(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    literal = await make_doe(client, headers, "D-\\MATCH")
    await make_doe(client, headers, "D-PLAIN")
    page = await candidate_list(client, headers, q="\\MATCH")
    assert page["total"] == 1
    assert [row["id"] for row in page["candidates"]] == [literal["id"]]


async def test_candidate_excludes_males(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_buck(client, headers)
    assert await candidate_ids(client, headers) == []


async def test_candidate_excludes_young_doe(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_doe_aged_months(client, headers, "D-YOUNG", months=9)
    assert await candidate_ids(client, headers) == []


async def test_candidate_age_boundary_ten_months(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe_aged_months(client, headers, "D-10MO", months=10)
    assert doe["age_months"] == 10
    assert await candidate_ids(client, headers) == [doe["id"]]


async def test_candidate_age_uses_exact_whole_month_boundary(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    cutoff = add_months(today(), -10)
    ready = await make_animal(
        client,
        headers,
        "D-AGE-EXACT",
        date_of_birth=iso(cutoff),
        weight_kg=26.0,
        weight_date=iso(cutoff),
    )
    too_young = await make_animal(
        client,
        headers,
        "D-AGE-DAY-YOUNG",
        date_of_birth=iso(cutoff + timedelta(days=1)),
        weight_kg=26.0,
        weight_date=iso(cutoff + timedelta(days=1)),
    )
    ids = await candidate_ids(client, headers, q="D-AGE")
    assert ready["id"] in ids
    assert too_young["id"] not in ids


async def test_candidate_excludes_light_doe(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_doe(client, headers, weight_kg=21.99)
    assert await candidate_ids(client, headers) == []


async def test_candidate_weight_boundary_22kg(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers, weight_kg=22.0)
    assert await candidate_ids(client, headers) == [doe["id"]]


async def test_candidate_uses_latest_weight_as_of_farm_today(
    client: httpx.AsyncClient,
) -> None:
    """Future measurements neither qualify a light doe nor hide today's valid weight."""
    headers = await owner_with_farm(client)
    eligible = await make_doe(client, headers, "D-FUTURE-LOW", weight_kg=22.0)
    light = await make_doe(client, headers, "D-FUTURE-HIGH", weight_kg=21.0)
    async with get_sessionmaker()() as db:
        db.add_all(
            [
                WeightRecord(
                    animal_id=eligible["id"],
                    date=today() + timedelta(days=1),
                    weight_kg=10.0,
                ),
                WeightRecord(
                    animal_id=light["id"],
                    date=today() + timedelta(days=1),
                    weight_kg=30.0,
                ),
            ]
        )
        await db.commit()

    page = await candidate_list(client, headers, q="D-FUTURE")
    assert page["total"] == 1
    assert [(row["id"], row["latest_weight_kg"]) for row in page["candidates"]] == [
        (eligible["id"], 22.0)
    ]


async def test_candidate_excludes_doe_without_weight(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_doe(client, headers, weight_kg=None)
    assert await candidate_ids(client, headers) == []


async def test_candidate_excludes_quarantine_doe(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_doe(client, headers, bucket="QUARANTINE")
    assert await candidate_ids(client, headers) == []


async def test_candidate_excludes_both_operational_health_holds(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    ready = await make_doe(client, headers, "D-HOLD-READY")
    restricted = await make_doe(client, headers, "D-HOLD-RESTRICTED")
    suspected = await make_doe(client, headers, "D-HOLD-SUSPECTED")
    async with get_sessionmaker()() as db:
        restricted_row = await db.get(Animal, restricted["id"])
        suspected_row = await db.get(Animal, suspected["id"])
        assert restricted_row is not None and suspected_row is not None
        restricted_row.movement_restricted = True
        restricted_row.restriction_reason = "Regulatory movement hold"
        suspected_row.movement_restricted = True
        suspected_row.restriction_reason = "Suspected scheduled disease"
        suspected_row.suspected_scheduled_disease = True
        suspected_row.suspected_disease = "PPR"
        await db.commit()

    assert await candidate_ids(client, headers, q="D-HOLD") == [ready["id"]]


async def test_candidate_includes_female_kids_bucket(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers, bucket="FEMALE_KIDS")
    assert await candidate_ids(client, headers) == [doe["id"]]


async def test_candidate_includes_resting_doe(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers, bucket="RESTING")
    assert await candidate_ids(client, headers) == [doe["id"]]


async def test_candidate_excludes_breeding_bucket_doe_without_current_weight(
    client: httpx.AsyncClient,
) -> None:
    """A re-service keeps the same current-weight guard as first service."""
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers, bucket="FOUNDATION", weight_kg=None)
    await place_legacy_animal_in_breeding(doe["id"])
    assert await candidate_ids(client, headers) == []


async def test_candidate_excludes_young_doe_in_breeding_bucket(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    await make_doe_aged_months(client, headers, "D-YB", months=9)
    doe_id = (await list_animals(client, headers, q="D-YB"))[0]["id"]
    response = await client.post(
        f"/api/animals/{doe_id}/move",
        json={"to_bucket": "BREEDING"},
        headers=headers,
    )
    assert response.status_code == 409, response.text
    assert await candidate_ids(client, headers) == []


async def test_candidate_excludes_pregnant_doe(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, _br = await pregnant_doe(client, headers)
    assert doe["id"] not in await candidate_ids(client, headers)


async def test_candidate_excludes_doe_with_pending_breeding(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, _br = await bred_doe(client, headers)
    assert doe["id"] not in await candidate_ids(client, headers)


async def test_candidate_excludes_sold_doe(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers)
    await set_status(client, headers, doe["id"], "SOLD")
    assert await candidate_ids(client, headers) == []


async def test_candidate_excludes_dead_doe(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers)
    await set_status(client, headers, doe["id"], "DEAD")
    assert await candidate_ids(client, headers) == []


async def test_candidate_excludes_culled_doe(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers)
    await set_status(client, headers, doe["id"], "CULLED")
    assert await candidate_ids(client, headers) == []


async def test_buck_candidates_include_ready_active_male(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    buck = await make_buck(client, headers)
    assert await candidate_ids(client, headers, "buck") == [buck["id"]]


async def test_buck_candidates_exclude_sold_male(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    buck = await make_buck(client, headers)
    await set_status(client, headers, buck["id"], "SOLD")
    assert await candidate_ids(client, headers, "buck") == []


async def test_buck_candidates_exclude_females(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_doe(client, headers)
    assert await candidate_ids(client, headers, "buck") == []


async def test_buck_minimums_and_candidate_contract_are_consistent(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    age_cutoff = add_months(today(), -12)
    young_dob = age_cutoff + timedelta(days=1)
    young = await make_animal(
        client,
        headers,
        "B-YOUNG",
        sex="M",
        bucket="FOUNDATION",
        date_of_birth=iso(young_dob),
        weight_kg=30.0,
        weight_date=iso(young_dob),
    )
    old_dob = today() - timedelta(days=800)
    light = await make_animal(
        client,
        headers,
        "B-LIGHT",
        sex="M",
        bucket="FOUNDATION",
        date_of_birth=iso(old_dob),
        weight_kg=24.99,
        weight_date=iso(old_dob),
    )
    await place_legacy_animal_in_breeding(young["id"])
    await place_legacy_animal_in_breeding(light["id"])
    eligible = await make_buck(
        client,
        headers,
        "B-READY",
        date_of_birth=iso(age_cutoff),
        weight_kg=25.0,
        weight_date=iso(age_cutoff),
    )

    ids = await candidate_ids(client, headers, "buck")
    assert ids == [eligible["id"]]
    assert young["id"] not in ids
    assert light["id"] not in ids
    assert (await breeding_list(client, headers))["candidate_availability"] == {
        "eligible_doe_count": 0,
        "eligible_buck_count": 1,
    }


async def test_create_breeding_rejects_immature_or_light_buck(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers)
    young_dob = today() - timedelta(days=200)
    young = await make_animal(
        client,
        headers,
        "B-YOUNG",
        sex="M",
        bucket="FOUNDATION",
        date_of_birth=iso(young_dob),
        weight_kg=30.0,
        weight_date=iso(young_dob),
    )
    old_dob = today() - timedelta(days=800)
    light = await make_animal(
        client,
        headers,
        "B-LIGHT",
        sex="M",
        bucket="FOUNDATION",
        date_of_birth=iso(old_dob),
        weight_kg=24.99,
        weight_date=iso(old_dob),
    )
    await place_legacy_animal_in_breeding(young["id"])
    await place_legacy_animal_in_breeding(light["id"])
    for buck in (young, light):
        response = await post_breeding(client, headers, doe["id"], buck["id"])
        assert response.status_code == 400, response.text


async def test_breeding_list_records_newest_date_first(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe1, buck1, _br = await bred_doe(client, headers, "D-1", today() - timedelta(days=60))
    _doe2, _buck2, br2 = await bred_doe(client, headers, "D-2", today() - timedelta(days=10))
    body = await breeding_list(client, headers)
    assert [r["id"] for r in body["records"]] == [br2["id"], _br["id"]]
    assert {r["doe_id"] for r in body["records"]} == {doe1["id"], _doe2["id"]}
    assert buck1["id"] in await candidate_ids(client, headers, "buck")


async def test_breeding_history_pagination_reports_full_count(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    created = []
    for index, days_ago in enumerate((60, 40, 20), start=1):
        _doe, _buck, breeding = await bred_doe(
            client,
            headers,
            f"PAGE-D-{index}",
            today() - timedelta(days=days_ago),
        )
        created.append(breeding["id"])
    response = await client.get("/api/breeding?limit=2&offset=1", headers=headers)
    assert response.status_code == 200, response.text
    page = response.json()
    assert (page["total"], page["limit"], page["offset"]) == (3, 2, 1)
    assert [record["id"] for record in page["records"]] == [created[1], created[0]]


async def test_breeding_list_record_shape_pending(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    breeding_date = today() - timedelta(days=5)
    doe, buck, br = await bred_doe(client, headers, breeding_date=breeding_date)
    body = await breeding_list(client, headers)
    record = body["records"][0]
    assert record["id"] == br["id"]
    assert record["doe_id"] == doe["id"] and record["buck_id"] == buck["id"]
    assert record["doe_tag"] == "D-1" and record["buck_tag"] == "D-1-BUCK"
    assert record["breeding_date"] == iso(breeding_date)
    assert record["method"] == "NATURAL"
    assert record["heat_cycle_number"] == 1
    assert record["ultrasound_date"] == iso(
        breeding_date + timedelta(days=ULTRASOUND_AFTER_BREEDING_DAYS)
    )
    assert record["ultrasound_done"] is False
    assert record["pregnant"] is None
    assert record["kid_count_detected"] is None
    assert record["expected_kidding_date"] is None
    assert record["outcome"] == "PENDING"
    assert record["has_kidding"] is False


async def test_breeding_list_requires_auth(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/breeding")
    assert resp.status_code == 401


async def test_breeding_list_requires_farm_header(client: httpx.AsyncClient) -> None:
    headers = await register(client)
    resp = await client.get("/api/breeding", headers=headers)
    # Required contract header: missing fails validation (422).
    assert resp.status_code == 422


async def test_breeding_list_rejects_non_integer_farm_header(client: httpx.AsyncClient) -> None:
    headers = await register(client)
    resp = await client.get("/api/breeding", headers=headers | {"X-Farm-Id": "abc"})
    assert resp.status_code == 400


async def test_breeding_list_rejects_out_of_range_farm_header(client: httpx.AsyncClient) -> None:
    headers = await register(client)
    for bad in ("0", "-3", str(2**62)):
        resp = await client.get("/api/breeding", headers=headers | {"X-Farm-Id": bad})
        assert resp.status_code == 400


async def test_breeding_list_nonexistent_farm(client: httpx.AsyncClient) -> None:
    headers = await register(client)
    resp = await client.get("/api/breeding", headers=headers | {"X-Farm-Id": "999999"})
    assert resp.status_code == 404


async def test_breeding_list_non_member_farm_not_found(client: httpx.AsyncClient) -> None:
    other = await owner_with_farm(client, email="other@farm.in", farm_name="Beta Farm")
    stranger = await register(client, email="stranger@farm.in")
    resp = await client.get("/api/breeding", headers=stranger | {"X-Farm-Id": other["X-Farm-Id"]})
    # Forbidden farms answer 404, same as nonexistent ones.
    assert resp.status_code == 404


async def test_breeding_list_isolated_across_farms(client: httpx.AsyncClient) -> None:
    headers_a = await owner_with_farm(client)
    headers_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    await bred_doe(client, headers_a, "D-A")
    body_b = await breeding_list(client, headers_b)
    assert body_b["records"] == []
    body_a = await breeding_list(client, headers_a)
    assert len(body_a["records"]) == 1


# ---------------------------------------------------------------------------
# POST /api/breeding — creation, eligibility guards, validation
# ---------------------------------------------------------------------------
async def test_create_breeding_happy_path(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    breeding_date = today() - timedelta(days=3)
    doe, buck, br = await bred_doe(client, headers, breeding_date=breeding_date)
    assert br["doe_id"] == doe["id"] and br["buck_id"] == buck["id"]
    assert br["breeding_date"] == iso(breeding_date)
    assert br["method"] == "NATURAL"
    assert br["outcome"] == "PENDING"
    assert br["heat_cycle_number"] == 1
    assert br["ultrasound_date"] == iso(breeding_date + timedelta(days=32))
    assert br["ultrasound_done"] is False
    assert br["pregnant"] is None
    assert br["expected_kidding_date"] is None
    assert br["has_kidding"] is False
    assert br["doe_tag"] == "D-1" and br["buck_tag"] == "D-1-BUCK"


async def test_create_breeding_moves_doe_to_breeding_bucket(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, _br = await bred_doe(client, headers)
    doe_after = await get_animal(client, headers, doe["id"])
    assert doe_after["current_bucket"] == "BREEDING"


async def test_create_breeding_creates_ultrasound_task_at_plus_32(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    breeding_date = today() - timedelta(days=1)
    doe, _buck, br = await bred_doe(client, headers, breeding_date=breeding_date)
    us_tasks = tasks_by_category(await all_tasks(client, headers), "ULTRASOUND")
    assert len(us_tasks) == 1
    assert us_tasks[0]["due_date"] == iso(breeding_date + timedelta(days=32))
    assert us_tasks[0]["due_date"] == br["ultrasound_date"]
    assert us_tasks[0]["status"] == "PENDING"
    assert us_tasks[0]["animal_id"] == doe["id"]
    assert us_tasks[0]["auto_generated"] is True


async def test_create_breeding_ignores_client_supplied_heat_cycle_number(
    client: httpx.AsyncClient,
) -> None:
    """The field stays on the wire for compatibility but is never stored: the
    cycle index is derived from the doe's own consecutive failed cycles."""
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers)
    buck = await make_buck(client, headers)
    br = await make_breeding(client, headers, doe["id"], buck["id"], heat_cycle_number=3)
    assert br["heat_cycle_number"] == 1


async def test_create_breeding_date_today(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers)
    buck = await make_buck(client, headers)
    br = await make_breeding(client, headers, doe["id"], buck["id"])
    assert br["breeding_date"] == iso(today())


async def test_create_breeding_date_cannot_predate_recorded_doe_birth(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers)
    buck = await make_buck(client, headers)
    response = await post_breeding(
        client, headers, doe["id"], buck["id"], breeding_date="1990-01-01"
    )
    # The canonical eligibility predicate is the earliest deterministic
    # guard: at this date neither animal is old enough to breed. The deeper
    # chronology guard remains defence-in-depth for service/import callers.
    assert response.status_code == 400
    assert response.json()["detail"] == "Doe or buck is not eligible for breeding"


async def test_create_breeding_rejects_male_as_doe(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    male = await make_buck(client, headers, "M-1")
    buck = await make_buck(client, headers, "M-2")
    resp = await post_breeding(client, headers, male["id"], buck["id"])
    assert resp.status_code == 400


async def test_create_breeding_rejects_female_as_buck(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers, "D-1")
    other_doe = await make_doe(client, headers, "D-2")
    resp = await post_breeding(client, headers, doe["id"], other_doe["id"])
    assert resp.status_code == 400


async def test_create_breeding_rejects_sold_buck(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers)
    buck = await make_buck(client, headers)
    await set_status(client, headers, buck["id"], "SOLD")
    resp = await post_breeding(client, headers, doe["id"], buck["id"])
    assert resp.status_code == 400


async def test_create_breeding_rejects_dead_buck(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers)
    buck = await make_buck(client, headers)
    await set_status(client, headers, buck["id"], "DEAD")
    resp = await post_breeding(client, headers, doe["id"], buck["id"])
    assert resp.status_code == 400


async def test_create_breeding_rejects_young_doe(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe_aged_months(client, headers, "D-Y", months=9)
    buck = await make_buck(client, headers)
    resp = await post_breeding(client, headers, doe["id"], buck["id"])
    assert resp.status_code == 400


async def test_create_breeding_rejects_light_doe(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers, weight_kg=20.0)
    buck = await make_buck(client, headers)
    resp = await post_breeding(client, headers, doe["id"], buck["id"])
    assert resp.status_code == 400


async def test_create_breeding_rejects_quarantine_doe(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers, bucket="QUARANTINE")
    buck = await make_buck(client, headers)
    resp = await post_breeding(client, headers, doe["id"], buck["id"])
    assert resp.status_code == 400


async def test_create_breeding_rejects_pregnant_doe(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, buck, _br = await pregnant_doe(client, headers)
    resp = await post_breeding(client, headers, doe["id"], buck["id"])
    assert resp.status_code == 400


async def test_create_breeding_rejects_doe_with_pending_breeding(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    doe, buck, _br = await bred_doe(client, headers)
    resp = await post_breeding(client, headers, doe["id"], buck["id"])
    assert resp.status_code == 400


async def test_create_breeding_rebreed_after_failed_cycle(client: httpx.AsyncClient) -> None:
    """Heat-cycle re-breeding: after a FAILED cycle the doe (still in BREEDING)
    is eligible again — heat_cycle_number 2."""
    headers = await owner_with_farm(client)
    doe, buck, br1 = await bred_doe(client, headers, breeding_date=today() - timedelta(days=60))
    await fail_cycle(client, headers, br1["id"])
    br2 = await make_breeding(
        client,
        headers,
        doe["id"],
        buck["id"],
        breeding_date=iso(today() - timedelta(days=21)),
        heat_cycle_number=2,
    )
    assert br2["heat_cycle_number"] == 2
    assert br2["outcome"] == "PENDING"


async def test_open_breeding_cannot_be_bypassed_by_manual_resting_move(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, _br = await bred_doe(client, headers)
    response = await client.post(
        f"/api/animals/{doe['id']}/move",
        json={"to_bucket": "RESTING", "reason": "skip outcome"},
        headers=headers,
    )
    assert response.status_code == 409, response.text
    assert "open breeding" in response.json()["detail"]
    assert (await get_animal(client, headers, doe["id"]))["current_bucket"] == "BREEDING"


async def test_backdated_rebreed_uses_weight_known_on_breeding_date(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    doe, buck, first = await bred_doe(client, headers, breeding_date=today() - timedelta(days=60))
    await fail_cycle(client, headers, first["id"])
    low_weight = await client.post(
        f"/api/animals/{doe['id']}/weight",
        json={"weight_kg": 21.9},
        headers=headers,
    )
    assert low_weight.status_code == 201, low_weight.text
    retry = await post_breeding(
        client,
        headers,
        doe["id"],
        buck["id"],
        breeding_date=iso(today() - timedelta(days=21)),
        heat_cycle_number=2,
    )
    # A later measurement must not rewrite eligibility for an event recorded
    # 21 days earlier; latest_weight_kg_on deliberately ignores future facts.
    assert retry.status_code == 201, retry.text


async def test_backdated_rebreed_blocks_low_weight_on_or_before_breeding_date(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    doe, buck, first = await bred_doe(client, headers, breeding_date=today() - timedelta(days=60))
    await fail_cycle(client, headers, first["id"])
    low_weight = await client.post(
        f"/api/animals/{doe['id']}/weight",
        json={"date": iso(today() - timedelta(days=22)), "weight_kg": 21.9},
        headers=headers,
    )
    assert low_weight.status_code == 201, low_weight.text
    retry = await post_breeding(
        client,
        headers,
        doe["id"],
        buck["id"],
        breeding_date=iso(today() - timedelta(days=21)),
        heat_cycle_number=2,
    )
    assert retry.status_code == 400


async def test_create_breeding_rebreed_after_kidding_full_cycle(client: httpx.AsyncClient) -> None:
    """Full reproductive cycle: kid, rest, breed again."""
    headers = await owner_with_farm(client)
    doe, buck, br = await bred_doe(client, headers, breeding_date=today() - timedelta(days=160))
    confirmation = await ultrasound(
        client,
        headers,
        br["id"],
        pregnant=True,
        kid_count=2,
        date=br["ultrasound_date"],
    )
    assert confirmation.status_code == 200, confirmation.text
    br = confirmation.json()
    await kid_on_ekd(client, headers, br)
    doe_after = await get_animal(client, headers, doe["id"])
    assert doe_after["current_bucket"] == "RECOVERY"
    # Not eligible from RECOVERY
    resp = await post_breeding(client, headers, doe["id"], buck["id"])
    assert resp.status_code == 400
    # After the move to RESTING she is a breeding candidate again
    await move_to(client, headers, doe["id"], "RESTING", history_override=True)
    resp = await post_breeding(client, headers, doe["id"], buck["id"], breeding_date=iso(today()))
    assert resp.status_code == 201, resp.text


async def test_create_breeding_nonexistent_doe(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    buck = await make_buck(client, headers)
    resp = await post_breeding(client, headers, 999999, buck["id"])
    assert resp.status_code == 404


async def test_create_breeding_nonexistent_buck(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers)
    resp = await post_breeding(client, headers, doe["id"], 999999)
    assert resp.status_code == 404


async def test_create_breeding_cross_farm_doe(client: httpx.AsyncClient) -> None:
    headers_a = await owner_with_farm(client)
    headers_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    doe_b = await make_doe(client, headers_b, "D-B")
    buck_a = await make_buck(client, headers_a, "B-A")
    resp = await post_breeding(client, headers_a, doe_b["id"], buck_a["id"])
    assert resp.status_code == 404


async def test_create_breeding_cross_farm_buck(client: httpx.AsyncClient) -> None:
    headers_a = await owner_with_farm(client)
    headers_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    doe_a = await make_doe(client, headers_a, "D-A")
    buck_b = await make_buck(client, headers_b, "B-B")
    resp = await post_breeding(client, headers_a, doe_a["id"], buck_b["id"])
    assert resp.status_code == 404


async def test_create_breeding_doe_id_zero(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    buck = await make_buck(client, headers)
    resp = await post_breeding(client, headers, 0, buck["id"])
    assert resp.status_code == 422


async def test_create_breeding_doe_id_negative(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    buck = await make_buck(client, headers)
    resp = await post_breeding(client, headers, -5, buck["id"])
    assert resp.status_code == 422


async def test_create_breeding_doe_id_above_max(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    buck = await make_buck(client, headers)
    resp = await post_breeding(client, headers, 2**62 + 1, buck["id"])
    assert resp.status_code == 422


async def test_create_breeding_doe_id_string(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    buck = await make_buck(client, headers)
    resp = await post_breeding(client, headers, "abc", buck["id"])  # type: ignore[arg-type]
    assert resp.status_code == 422


async def test_create_breeding_missing_doe_id(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    buck = await make_buck(client, headers)
    resp = await client.post(
        "/api/breeding",
        json={"buck_id": buck["id"], "breeding_date": iso(today())},
        headers=headers,
    )
    assert resp.status_code == 422


async def test_create_breeding_missing_buck_id(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers)
    resp = await client.post(
        "/api/breeding",
        json={"doe_id": doe["id"], "breeding_date": iso(today())},
        headers=headers,
    )
    assert resp.status_code == 422


async def test_create_breeding_missing_breeding_date(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers)
    buck = await make_buck(client, headers)
    resp = await client.post(
        "/api/breeding",
        json={"doe_id": doe["id"], "buck_id": buck["id"]},
        headers=headers,
    )
    assert resp.status_code == 422


async def test_create_breeding_future_date(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers)
    buck = await make_buck(client, headers)
    resp = await post_breeding(
        client,
        headers,
        doe["id"],
        buck["id"],
        # The schema permits UTC/local-date headroom; the endpoint enforces
        # the selected farm's exact business date.
        breeding_date=iso(today() + timedelta(days=1)),
    )
    assert resp.status_code == 422


async def test_create_breeding_malformed_date(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers)
    buck = await make_buck(client, headers)
    for bad in ("not-a-date", "2026-13-40", "32-01-2026"):
        resp = await post_breeding(client, headers, doe["id"], buck["id"], breeding_date=bad)
        assert resp.status_code == 422


async def test_create_breeding_heat_cycle_zero(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers)
    buck = await make_buck(client, headers)
    resp = await post_breeding(client, headers, doe["id"], buck["id"], heat_cycle_number=0)
    assert resp.status_code == 422


async def test_create_breeding_heat_cycle_100(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers)
    buck = await make_buck(client, headers)
    resp = await post_breeding(client, headers, doe["id"], buck["id"], heat_cycle_number=100)
    assert resp.status_code == 422


async def test_create_breeding_heat_cycle_rejects_coercible_wire_types(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers)
    buck = await make_buck(client, headers)
    for value in ("2", True):
        resp = await post_breeding(client, headers, doe["id"], buck["id"], heat_cycle_number=value)
        assert resp.status_code == 422
    assert (await breeding_list(client, headers))["records"] == []


async def test_create_breeding_extra_field_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers)
    buck = await make_buck(client, headers)
    resp = await post_breeding(client, headers, doe["id"], buck["id"], bogus_field="x")
    assert resp.status_code == 422, resp.text
    assert (await breeding_list(client, headers))["records"] == []


async def test_create_breeding_requires_auth(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/breeding",
        json={"doe_id": 1, "buck_id": 2, "breeding_date": iso(today())},
    )
    assert resp.status_code == 401


async def test_create_breeding_requires_farm_header(client: httpx.AsyncClient) -> None:
    headers = await register(client)
    resp = await client.post(
        "/api/breeding",
        json={"doe_id": 1, "buck_id": 2, "breeding_date": iso(today())},
        headers=headers,
    )
    # Required contract header: missing fails validation (422).
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/breeding/{record_id}
# ---------------------------------------------------------------------------
async def test_get_breeding_record_happy(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await bred_doe(client, headers)
    fetched = await get_breeding(client, headers, br["id"])
    assert fetched == br


async def test_get_breeding_record_nonexistent(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.get("/api/breeding/999999", headers=headers)
    assert resp.status_code == 404


async def test_get_breeding_record_id_zero(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.get("/api/breeding/0", headers=headers)
    assert resp.status_code == 404


async def test_get_breeding_record_id_negative(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.get("/api/breeding/-5", headers=headers)
    assert resp.status_code == 404


async def test_get_breeding_record_huge_id_no_500(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.get(f"/api/breeding/{2**63}", headers=headers)
    assert resp.status_code == 404


async def test_get_breeding_record_malformed_id(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.get("/api/breeding/abc", headers=headers)
    assert resp.status_code == 422


async def test_get_breeding_record_cross_farm(client: httpx.AsyncClient) -> None:
    headers_a = await owner_with_farm(client)
    headers_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    _doe, _buck, br_b = await bred_doe(client, headers_b, "D-B")
    resp = await client.get(f"/api/breeding/{br_b['id']}", headers=headers_a)
    assert resp.status_code == 404


async def test_get_breeding_record_requires_auth(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/breeding/1")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/breeding/{record_id}/ultrasound
# ---------------------------------------------------------------------------
def test_ultrasound_service_requires_an_explicit_business_date() -> None:
    parameter = inspect.signature(record_ultrasound_result).parameters["result_date"]
    assert parameter.default is inspect.Parameter.empty


async def test_ultrasound_pregnant_happy_path(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    breeding_date = today() - timedelta(days=35)
    _doe, _buck, br = await bred_doe(client, headers, breeding_date=breeding_date)
    br = await confirm(client, headers, br["id"], kid_count=2)
    assert br["outcome"] == "CONFIRMED_PREGNANT"
    assert br["ultrasound_done"] is True
    assert br["pregnant"] is True
    assert br["kid_count_detected"] == 2
    assert br["expected_kidding_date"] == iso(breeding_date + timedelta(days=GESTATION_DAYS))
    assert br["ultrasound_result_date"] == iso(
        breeding_date + timedelta(days=ULTRASOUND_AFTER_BREEDING_DAYS)
    )


async def test_ultrasound_result_date_defaults_to_the_farms_today(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await bred_doe(client, headers, breeding_date=today() - timedelta(days=40))
    response = await ultrasound(client, headers, br["id"], pregnant=True, kid_count=2)
    assert response.status_code == 200, response.text
    assert response.json()["ultrasound_result_date"] == iso(today())


async def test_ultrasound_explicit_result_date_cannot_predate_planned_check(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, breeding = await bred_doe(
        client, headers, breeding_date=today() - timedelta(days=10)
    )
    response = await ultrasound(
        client,
        headers,
        breeding["id"],
        pregnant=True,
        kid_count=1,
        date=iso(today()),
    )
    assert response.status_code == 409


async def test_ultrasound_records_explicit_result_date(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    breeding_date = today() - timedelta(days=40)
    _doe, _buck, breeding = await bred_doe(client, headers, breeding_date=breeding_date)
    response = await ultrasound(
        client,
        headers,
        breeding["id"],
        pregnant=True,
        kid_count=1,
        date=iso(today()),
    )
    assert response.status_code == 200, response.text
    assert response.json()["ultrasound_result_date"] == iso(today())


async def test_ultrasound_expected_kidding_date_exactly_150_days(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    breeding_date = today() - timedelta(days=35)
    _doe, _buck, br = await bred_doe(client, headers, breeding_date=breeding_date)
    br = await confirm(client, headers, br["id"])
    delta = date.fromisoformat(br["expected_kidding_date"]) - breeding_date
    assert delta.days == 150


async def test_ultrasound_pregnant_moves_doe_to_pregnancy_early(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await bred_doe(client, headers)
    await confirm(client, headers, br["id"])
    doe_after = await get_animal(client, headers, doe["id"])
    assert doe_after["current_bucket"] == "PREGNANCY_EARLY"
    assert doe_after["is_currently_pregnant"] is True


async def test_held_doe_pregnancy_confirmation_reclassifies_atomically(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, breeding = await bred_doe(client, headers, "HELD-CONFIRM")
    await place_health_hold(client, headers, doe["id"])

    confirmed = await confirm(client, headers, breeding["id"])
    assert confirmed["outcome"] == "CONFIRMED_PREGNANT"
    after = await get_animal(client, headers, doe["id"])
    assert after["current_bucket"] == "PREGNANCY_EARLY"
    assert after["movement_restricted"] is True
    assert after["suspected_scheduled_disease"] is True


async def test_ultrasound_pregnant_creates_three_followup_tasks(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    breeding_date = today() - timedelta(days=35)
    doe, _buck, br = await bred_doe(client, headers, breeding_date=breeding_date)
    br = await confirm(client, headers, br["id"])
    tasks = await all_tasks(client, headers)
    ekd = date.fromisoformat(br["expected_kidding_date"])
    vaccine = tasks_by_category(tasks, "VACCINE")
    moves = tasks_by_category(tasks, "BUCKET_MOVE")
    kidding = tasks_by_category(tasks, "KIDDING_DUE")
    assert [t["due_date"] for t in vaccine] == [iso(ekd - timedelta(days=40))]
    assert [t["due_date"] for t in moves] == [iso(ekd - timedelta(days=15))]
    assert [t["due_date"] for t in kidding] == [iso(ekd)]
    assert all(t["animal_id"] == doe["id"] for t in vaccine + moves + kidding)
    assert all(t["status"] == "PENDING" for t in vaccine + moves + kidding)


async def test_ultrasound_marks_ultrasound_task_done(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await bred_doe(client, headers)
    await confirm(client, headers, br["id"])
    us_tasks = tasks_by_category(await all_tasks(client, headers), "ULTRASOUND")
    assert [t["status"] for t in us_tasks] == ["DONE"]


async def test_ultrasound_pregnant_without_kid_count(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await bred_doe(client, headers)
    br = await confirm(client, headers, br["id"], kid_count=None)
    assert br["outcome"] == "CONFIRMED_PREGNANT"
    assert br["kid_count_detected"] is None


async def test_ultrasound_kid_count_boundaries(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br1 = await bred_doe(client, headers, "D-1")
    resp = await ultrasound(client, headers, br1["id"], pregnant=True, kid_count=1)
    assert resp.status_code == 200, resp.text
    assert resp.json()["kid_count_detected"] == 1
    _doe2, _buck2, br2 = await bred_doe(client, headers, "D-2")
    # SPEC: kid_count_detected is 1/2/3 nullable (BirthType SINGLE/TWIN/TRIPLET).
    resp = await ultrasound(client, headers, br2["id"], pregnant=True, kid_count=3)
    assert resp.status_code == 200, resp.text
    assert resp.json()["kid_count_detected"] == 3


async def test_ultrasound_failed_happy_path(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await bred_doe(client, headers)
    br = await fail_cycle(client, headers, br["id"])
    assert br["outcome"] == "FAILED"
    assert br["ultrasound_done"] is True
    assert br["pregnant"] is False
    assert br["kid_count_detected"] is None
    assert br["expected_kidding_date"] is None


async def test_ultrasound_failed_keeps_doe_in_breeding_bucket(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await bred_doe(client, headers)
    await fail_cycle(client, headers, br["id"])
    doe_after = await get_animal(client, headers, doe["id"])
    assert doe_after["current_bucket"] == "BREEDING"
    assert doe_after["is_currently_pregnant"] is False


async def test_ultrasound_failed_rejects_kid_count_without_mutation(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await bred_doe(client, headers)
    resp = await ultrasound(client, headers, br["id"], pregnant=False, kid_count=3)
    assert resp.status_code == 422, resp.text
    unchanged = await get_breeding(client, headers, br["id"])
    assert unchanged["outcome"] == "PENDING"
    assert unchanged["ultrasound_done"] is False
    assert unchanged["pregnant"] is None
    assert unchanged["kid_count_detected"] is None


async def test_ultrasound_failed_marks_ultrasound_task_done(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await bred_doe(client, headers)
    await fail_cycle(client, headers, br["id"])
    us_tasks = tasks_by_category(await all_tasks(client, headers), "ULTRASOUND")
    assert [t["status"] for t in us_tasks] == ["DONE"]


async def test_ultrasound_failed_makes_doe_candidate_again(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await bred_doe(client, headers)
    await fail_cycle(client, headers, br["id"])
    assert doe["id"] in await candidate_ids(client, headers)


async def test_ultrasound_pregnant_removes_doe_from_candidates(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await bred_doe(client, headers)
    await confirm(client, headers, br["id"])
    assert doe["id"] not in await candidate_ids(client, headers)


async def test_ultrasound_replay_conflict(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await bred_doe(client, headers)
    await confirm(client, headers, br["id"])
    resp = await ultrasound(client, headers, br["id"], pregnant=True)
    assert resp.status_code == 409


async def test_ultrasound_replay_after_failure(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await bred_doe(client, headers)
    await fail_cycle(client, headers, br["id"])
    resp = await ultrasound(client, headers, br["id"], pregnant=True)
    assert resp.status_code == 409


async def test_ultrasound_on_aborted_record(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers)
    resp = await post_abort(client, headers, br["id"])
    assert resp.status_code == 200, resp.text
    resp = await ultrasound(client, headers, br["id"], pregnant=True)
    assert resp.status_code == 409


async def test_ultrasound_kid_count_zero(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await bred_doe(client, headers)
    resp = await ultrasound(client, headers, br["id"], pregnant=True, kid_count=0)
    assert resp.status_code == 422


async def test_ultrasound_kid_count_six(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await bred_doe(client, headers)
    resp = await ultrasound(client, headers, br["id"], pregnant=True, kid_count=6)
    assert resp.status_code == 422


async def test_ultrasound_missing_pregnant_field(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await bred_doe(client, headers)
    resp = await client.post(
        f"/api/breeding/{br['id']}/ultrasound", json={"kid_count": 2}, headers=headers
    )
    assert resp.status_code == 422


async def test_ultrasound_pregnant_wrong_type(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await bred_doe(client, headers)
    resp = await ultrasound(client, headers, br["id"], pregnant="maybe")
    assert resp.status_code == 422


async def test_ultrasound_rejects_coercible_boolean_and_integer_types(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await bred_doe(client, headers)
    for payload in (
        {"pregnant": 1, "kid_count": 2},
        {"pregnant": "false", "kid_count": 2},
        {"pregnant": True, "kid_count": "2"},
    ):
        resp = await client.post(
            f"/api/breeding/{br['id']}/ultrasound", json=payload, headers=headers
        )
        assert resp.status_code == 422
    assert (await get_breeding(client, headers, br["id"]))["outcome"] == "PENDING"


async def test_ultrasound_nonexistent_record(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await ultrasound(client, headers, 999999)
    assert resp.status_code == 404


async def test_ultrasound_huge_id_no_500(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await ultrasound(client, headers, 2**63)
    assert resp.status_code == 404


async def test_ultrasound_cross_farm_record(client: httpx.AsyncClient) -> None:
    headers_a = await owner_with_farm(client)
    headers_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    _doe, _buck, br_b = await bred_doe(client, headers_b, "D-B")
    resp = await ultrasound(client, headers_a, br_b["id"])
    assert resp.status_code == 404


async def test_ultrasound_requires_auth(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/breeding/1/ultrasound", json={"pregnant": True})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Cull candidate: 2 consecutive FAILED cycles (SPEC breed constants)
# ---------------------------------------------------------------------------
async def test_one_failure_not_cull_candidate(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await bred_doe(client, headers)
    await fail_cycle(client, headers, br["id"])
    doe_after = await get_animal(client, headers, doe["id"])
    assert doe_after["cull_candidate"] is False


async def test_two_consecutive_failures_flag_cull_candidate(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, buck, br1 = await bred_doe(client, headers, breeding_date=today() - timedelta(days=120))
    await fail_cycle(client, headers, br1["id"])
    br2 = await make_breeding(
        client, headers, doe["id"], buck["id"], breeding_date=iso(today() - timedelta(days=60))
    )
    await fail_cycle(client, headers, br2["id"])
    doe_after = await get_animal(client, headers, doe["id"])
    assert doe_after["cull_candidate"] is True


async def test_three_consecutive_failures_stay_flagged(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, buck, br1 = await bred_doe(client, headers, breeding_date=today() - timedelta(days=180))
    await fail_cycle(client, headers, br1["id"])
    br2 = await make_breeding(
        client, headers, doe["id"], buck["id"], breeding_date=iso(today() - timedelta(days=120))
    )
    await fail_cycle(client, headers, br2["id"])
    br3 = await make_breeding(
        client, headers, doe["id"], buck["id"], breeding_date=iso(today() - timedelta(days=60))
    )
    await fail_cycle(client, headers, br3["id"])
    doe_after = await get_animal(client, headers, doe["id"])
    assert doe_after["cull_candidate"] is True


async def test_successful_kidding_resets_cull_streak_before_a_new_cycle(
    client: httpx.AsyncClient,
) -> None:
    """A completed pregnancy clears the failed-cycle worklist before rebreeding.

    A confirmation still needs its planned +32-day check, so the fresh service
    below can only be closed as a not-held (return-to-heat) cycle.
    """
    headers = await owner_with_farm(client)
    doe, buck, br1 = await bred_doe(client, headers, breeding_date=today() - timedelta(days=400))
    await fail_cycle(client, headers, br1["id"])
    br2 = await make_breeding(
        client, headers, doe["id"], buck["id"], breeding_date=iso(today() - timedelta(days=160))
    )
    confirmation = await ultrasound(
        client,
        headers,
        br2["id"],
        pregnant=True,
        kid_count=2,
        date=br2["ultrasound_date"],
    )
    assert confirmation.status_code == 200, confirmation.text
    br2 = confirmation.json()
    doe_mid = await get_animal(client, headers, doe["id"])
    assert doe_mid["cull_candidate"] is False
    await kid_on_ekd(client, headers, br2)
    await move_to(client, headers, doe["id"], "RESTING", history_override=True)
    br3 = await make_breeding(client, headers, doe["id"], buck["id"], breeding_date=iso(today()))
    early_confirmation = await ultrasound(client, headers, br3["id"], pregnant=True, kid_count=2)
    assert early_confirmation.status_code == 409
    early_result = await ultrasound(client, headers, br3["id"], pregnant=False, kid_count=None)
    assert early_result.status_code == 200, early_result.text
    # One failure after a delivered pregnancy is not a two-cycle streak.
    doe_after = await get_animal(client, headers, doe["id"])
    assert doe_after["cull_candidate"] is False


async def test_conception_clears_cull_candidate_flag(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, buck, br1 = await bred_doe(client, headers, breeding_date=today() - timedelta(days=180))
    await fail_cycle(client, headers, br1["id"])
    br2 = await make_breeding(
        client, headers, doe["id"], buck["id"], breeding_date=iso(today() - timedelta(days=120))
    )
    await fail_cycle(client, headers, br2["id"])
    assert (await get_animal(client, headers, doe["id"]))["cull_candidate"] is True
    br3 = await make_breeding(
        client, headers, doe["id"], buck["id"], breeding_date=iso(today() - timedelta(days=60))
    )
    await confirm(client, headers, br3["id"])
    doe_after = await get_animal(client, headers, doe["id"])
    assert doe_after["cull_candidate"] is False


async def test_abort_between_failures_breaks_the_streak(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, buck, br1 = await bred_doe(client, headers, breeding_date=today() - timedelta(days=200))
    await fail_cycle(client, headers, br1["id"])
    br2 = await make_breeding(
        client, headers, doe["id"], buck["id"], breeding_date=iso(today() - timedelta(days=120))
    )
    confirmation = await ultrasound(
        client,
        headers,
        br2["id"],
        pregnant=True,
        kid_count=2,
        date=br2["ultrasound_date"],
    )
    assert confirmation.status_code == 200, confirmation.text
    resp = await post_abort(
        client,
        headers,
        br2["id"],
        loss_date=iso(today() - timedelta(days=70)),
    )
    assert resp.status_code == 200, resp.text
    br3 = await make_breeding(
        client, headers, doe["id"], buck["id"], breeding_date=iso(today() - timedelta(days=60))
    )
    await fail_cycle(client, headers, br3["id"])
    doe_after = await get_animal(client, headers, doe["id"])
    assert doe_after["cull_candidate"] is False


async def test_cull_flag_cleared_when_doe_marked_culled(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, buck, br1 = await bred_doe(client, headers, breeding_date=today() - timedelta(days=120))
    await fail_cycle(client, headers, br1["id"])
    br2 = await make_breeding(
        client, headers, doe["id"], buck["id"], breeding_date=iso(today() - timedelta(days=60))
    )
    await fail_cycle(client, headers, br2["id"])
    assert (await get_animal(client, headers, doe["id"]))["cull_candidate"] is True
    culled = await set_status(client, headers, doe["id"], "CULLED")
    assert culled["status"] == "CULLED"
    assert culled["cull_candidate"] is False


# ---------------------------------------------------------------------------
# POST /api/breeding/{record_id}/abort
# ---------------------------------------------------------------------------
async def test_abort_happy_path(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await pregnant_doe(client, headers)
    resp = await post_abort(client, headers, br["id"], cause="DISEASE", notes="Confirmed loss")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "ABORTED"
    assert body["pregnant"] is False
    assert body["loss_date"] == iso(today())
    assert body["loss_cause"] == "DISEASE"
    assert body["loss_notes"] == "Confirmed loss"
    assert body["loss_recorded_at"] is not None
    async with get_sessionmaker()() as db:
        owner_id = (
            (await db.execute(select(User).where(User.email == "owner@farm.in"))).scalar_one().id
        )
    assert body["loss_recorded_by_id"] == owner_id
    doe_after = await get_animal(client, headers, doe["id"])
    assert doe_after["current_bucket"] == "RESTING"
    assert doe_after["is_currently_pregnant"] is False


async def test_abort_requires_explicit_loss_payload(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers)
    resp = await client.post(f"/api/breeding/{br['id']}/abort", headers=headers)
    assert resp.status_code == 422
    assert (await get_breeding(client, headers, br["id"]))["outcome"] == "CONFIRMED_PREGNANT"


async def test_held_doe_pregnancy_loss_reclassifies_atomically(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, breeding = await pregnant_doe(client, headers, "HELD-LOSS")
    await place_health_hold(client, headers, doe["id"])

    aborted = await post_abort(client, headers, breeding["id"])
    assert aborted.status_code == 200, aborted.text
    assert aborted.json()["outcome"] == "ABORTED"
    after = await get_animal(client, headers, doe["id"])
    assert after["current_bucket"] == "RESTING"
    assert after["movement_restricted"] is True
    assert after["suspected_scheduled_disease"] is True


async def test_abort_rejects_future_and_pre_confirmation_dates(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers)
    future = await post_abort(client, headers, br["id"], loss_date=iso(today() + timedelta(days=1)))
    assert future.status_code == 422
    confirmed_on = date.fromisoformat(br["ultrasound_result_date"])
    before_confirmation = await post_abort(
        client, headers, br["id"], loss_date=iso(confirmed_on - timedelta(days=1))
    )
    assert before_confirmation.status_code == 422
    assert (await get_breeding(client, headers, br["id"]))["outcome"] == "CONFIRMED_PREGNANT"


async def test_abort_rejects_loss_after_maximum_gestation(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    breeding_date = today() - timedelta(days=201)
    _doe, _buck, breeding = await bred_doe(client, headers, breeding_date=breeding_date)
    confirmed = await ultrasound(
        client,
        headers,
        breeding["id"],
        pregnant=True,
        kid_count=1,
        date=iso(breeding_date + timedelta(days=32)),
    )
    assert confirmed.status_code == 200, confirmed.text

    late = await post_abort(client, headers, breeding["id"], loss_date=iso(today()))
    assert late.status_code == 422, late.text
    assert "200-day gestation window" in late.json()["detail"]
    assert (await get_breeding(client, headers, breeding["id"]))["outcome"] == "CONFIRMED_PREGNANT"


async def test_public_abort_cannot_forge_status_change_cause(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, breeding = await pregnant_doe(client, headers)
    forged = await post_abort(client, headers, breeding["id"], cause="ANIMAL_STATUS_CHANGE")
    assert forged.status_code == 422, forged.text
    assert (await get_breeding(client, headers, breeding["id"]))["outcome"] == "CONFIRMED_PREGNANT"


async def test_late_status_change_retains_internal_administrative_close(
    client: httpx.AsyncClient,
) -> None:
    """A legacy over-window confirmed record must not make sale/death impossible."""
    headers = await owner_with_farm(client)
    breeding_date = today() - timedelta(days=201)
    doe, _buck, breeding = await bred_doe(client, headers, breeding_date=breeding_date)
    confirmed = await ultrasound(
        client,
        headers,
        breeding["id"],
        pregnant=True,
        kid_count=1,
        date=iso(breeding_date + timedelta(days=32)),
    )
    assert confirmed.status_code == 200, confirmed.text

    sold = await client.post(
        f"/api/animals/{doe['id']}/status",
        json={"new_status": "SOLD", "date": iso(today())},
        headers=headers,
    )
    assert sold.status_code == 200, sold.text
    closed = await get_breeding(client, headers, breeding["id"])
    assert closed["outcome"] == "ABORTED"
    assert closed["loss_cause"] == "ANIMAL_STATUS_CHANGE"


async def test_abort_payload_cause_and_notes_are_bounded(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers)
    bad_cause = await post_abort(client, headers, br["id"], cause="FREE_FORM")
    assert bad_cause.status_code == 422
    long_notes = await post_abort(client, headers, br["id"], notes="x" * 4_001)
    assert long_notes.status_code == 422


async def test_status_change_auto_loss_uses_same_audit_contract(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await pregnant_doe(client, headers)
    sold = await client.post(
        f"/api/animals/{doe['id']}/status",
        json={"new_status": "SOLD", "date": iso(today())},
        headers=headers,
    )
    assert sold.status_code == 200, sold.text
    closed = await get_breeding(client, headers, br["id"])
    assert closed["outcome"] == "ABORTED"
    assert closed["loss_date"] == iso(today())
    assert closed["loss_cause"] == "ANIMAL_STATUS_CHANGE"
    assert closed["loss_notes"] == "Pregnancy auto-resolved when doe was marked SOLD"
    assert closed["loss_recorded_by_id"] is not None
    assert closed["loss_recorded_at"] is not None


async def test_status_change_cannot_auto_resolve_before_confirmation_date(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await pregnant_doe(client, headers)
    confirmed_on = date.fromisoformat(br["ultrasound_result_date"])
    resp = await client.post(
        f"/api/animals/{doe['id']}/status",
        json={"new_status": "SOLD", "date": iso(confirmed_on - timedelta(days=1))},
        headers=headers,
    )
    assert resp.status_code == 422
    assert (await get_animal(client, headers, doe["id"]))["status"] == "ACTIVE"
    assert (await get_breeding(client, headers, br["id"]))["outcome"] == "CONFIRMED_PREGNANT"


# ---------------------------------------------------------------------------
# A never-scanned service is closed when its doe leaves the herd
# ---------------------------------------------------------------------------
# Selling/culling/killing a doe auto-resolved a CONFIRMED_PREGNANT service as
# ABORTED, but a PENDING one was left open forever: the only exit from PENDING
# is an ultrasound result, and record_ultrasound_result refuses a non-ACTIVE
# doe. The row sat in her history for good while the UI kept offering an
# "Ultrasound result" action that could only ever 409.
@pytest.mark.parametrize("new_status", ["SOLD", "DEAD", "CULLED"])
async def test_herd_exit_closes_a_never_scanned_service_as_unassessed(
    client: httpx.AsyncClient, new_status: str
) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await bred_doe(client, headers)
    assert br["outcome"] == "PENDING"
    await set_status(client, headers, doe["id"], new_status)

    closed = await get_breeding(client, headers, br["id"])
    assert closed["outcome"] == "UNASSESSED"
    # Nothing is invented about the pregnancy: no scan ever happened, so the
    # record is neither a conception nor a recorded failure to conceive, and
    # it carries no pregnancy-loss audit (there was no pregnancy to lose).
    assert closed["ultrasound_done"] is False
    assert closed["pregnant"] is None
    assert closed["kid_count_detected"] is None
    assert closed["ultrasound_result_date"] is None
    assert closed["expected_kidding_date"] is None
    assert closed["loss_date"] is None
    assert closed["loss_cause"] is None
    assert closed["loss_recorded_at"] is None


async def test_unassessed_service_refuses_the_dead_end_ultrasound_action(
    client: httpx.AsyncClient,
) -> None:
    """The 409 the UI used to hit forever now names the real reason, and the
    terminal state is what stops the action being offered at all."""
    headers = await owner_with_farm(client)
    doe, _buck, br = await bred_doe(client, headers)
    await set_status(client, headers, doe["id"], "SOLD")

    resp = await ultrasound(client, headers, br["id"], pregnant=True, kid_count=2)
    assert resp.status_code == 409, resp.text
    assert "sold" in resp.json()["detail"]
    unchanged = await get_breeding(client, headers, br["id"])
    assert unchanged["outcome"] == "UNASSESSED"
    assert unchanged["ultrasound_done"] is False
    # Its ultrasound duty is closed too — no work left pointing at the record.
    related = [t for t in await all_tasks(client, headers) if t["breeding_record_id"] == br["id"]]
    assert {t["category"] for t in related} == {"ULTRASOUND"}
    assert {t["status"] for t in related} == {"SKIPPED"}


async def test_herd_exit_leaves_other_does_services_pending(client: httpx.AsyncClient) -> None:
    """The sweep is scoped to the departing doe: a herd-mate's open service is
    still an open question and must keep its ultrasound action."""
    headers = await owner_with_farm(client)
    sold_doe, _buck, sold_br = await bred_doe(client, headers, "D-SOLD")
    kept_doe, _buck2, kept_br = await bred_doe(client, headers, "D-KEPT")
    await set_status(client, headers, sold_doe["id"], "SOLD")

    assert (await get_breeding(client, headers, sold_br["id"]))["outcome"] == "UNASSESSED"
    assert (await get_breeding(client, headers, kept_br["id"]))["outcome"] == "PENDING"
    assert kept_doe["id"] not in await candidate_ids(client, headers)
    resp = await ultrasound(client, headers, kept_br["id"], pregnant=False, kid_count=None)
    assert resp.status_code == 200, resp.text
    assert (await get_breeding(client, headers, kept_br["id"]))["outcome"] == "FAILED"


async def test_mark_unassessed_refuses_a_doe_still_in_the_herd(
    client: httpx.AsyncClient,
) -> None:
    """UNASSESSED means "she left before the check". It must never become a
    shortcut for closing a live service without scanning her."""
    headers = await owner_with_farm(client)
    _doe, _buck, br = await bred_doe(client, headers)
    async with get_sessionmaker()() as db:
        record = await db.get(BreedingRecord, br["id"])
        assert record is not None
        with pytest.raises(ValueError, match="still in the herd"):
            await mark_unassessed(db, record, closed_by_id=1)


async def test_abort_skips_open_pregnancy_tasks(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=35)
    resp = await post_abort(client, headers, br["id"])
    assert resp.status_code == 200, resp.text
    tasks = await all_tasks(client, headers)
    for category in ("VACCINE", "BUCKET_MOVE", "KIDDING_DUE"):
        statuses = [t["status"] for t in tasks_by_category(tasks, category)]
        assert statuses == ["SKIPPED"]


async def test_abort_makes_doe_candidate_again(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await pregnant_doe(client, headers)
    resp = await post_abort(client, headers, br["id"])
    assert resp.status_code == 200, resp.text
    assert doe["id"] in await candidate_ids(client, headers)


async def test_abort_on_pending_breeding(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await bred_doe(client, headers)
    resp = await post_abort(client, headers, br["id"])
    assert resp.status_code == 409


async def test_abort_on_failed_breeding(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await bred_doe(client, headers)
    await fail_cycle(client, headers, br["id"])
    resp = await post_abort(client, headers, br["id"])
    assert resp.status_code == 409


async def test_abort_after_kidding_recorded(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    await kid_on_ekd(client, headers, br)
    resp = await post_abort(client, headers, br["id"])
    assert resp.status_code == 409


async def test_abort_replay_conflict(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers)
    resp = await post_abort(client, headers, br["id"])
    assert resp.status_code == 200, resp.text
    resp = await post_abort(client, headers, br["id"])
    assert resp.status_code == 409


async def test_aborted_metadata_is_immutable_at_database_boundary(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers)
    assert (await post_abort(client, headers, br["id"])).status_code == 200
    async with get_sessionmaker()() as db:
        record = await db.get(BreedingRecord, br["id"])
        assert record is not None
        record.loss_notes = "Attempted rewrite"
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()


async def test_aborted_pregnancy_cannot_gain_kidding_record_at_database_boundary(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await pregnant_doe(client, headers)
    assert (await post_abort(client, headers, br["id"])).status_code == 200
    async with get_sessionmaker()() as db:
        db.add(
            KiddingRecord(
                farm_id=int(headers["X-Farm-Id"]),
                doe_id=doe["id"],
                date=today(),
                breeding_record_id=br["id"],
                ease="NORMAL",
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()


async def test_abort_nonexistent_record(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await post_abort(client, headers, 999999)
    assert resp.status_code == 404


async def test_abort_huge_id_no_500(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await post_abort(client, headers, 2**63)
    assert resp.status_code == 404


async def test_abort_cross_farm_record(client: httpx.AsyncClient) -> None:
    headers_a = await owner_with_farm(client)
    headers_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    _doe, _buck, br_b = await pregnant_doe(client, headers_b, "D-B")
    resp = await post_abort(client, headers_a, br_b["id"])
    assert resp.status_code == 404


async def test_abort_requires_auth(client: httpx.AsyncClient) -> None:
    resp = await post_abort(client, {}, 1)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/kidding — upcoming/overdue due list, history
# ---------------------------------------------------------------------------
async def test_kidding_list_empty(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    body = await kidding_list(client, headers)
    assert body["records"] == []
    assert body["upcoming"] == []
    assert body["overdue"] == []


async def test_kidding_upcoming_includes_due_within_30_days(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    # EKD = today + 10 → inside the 30-day horizon
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=140)
    body = await kidding_list(client, headers)
    assert [r["id"] for r in body["upcoming"]] == [br["id"]]
    assert body["overdue"] == []


async def test_kidding_upcoming_excludes_due_beyond_30_days(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    # EKD = today + 50 → neither upcoming nor overdue
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=100)
    body = await kidding_list(client, headers)
    assert body["upcoming"] == []
    assert body["overdue"] == []
    assert br["id"] not in [r["id"] for r in body["records"]]


async def test_kidding_upcoming_includes_due_today(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    # EKD = today exactly → upcoming boundary (now <= ekd <= horizon)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=150)
    body = await kidding_list(client, headers)
    assert [r["id"] for r in body["upcoming"]] == [br["id"]]
    assert body["overdue"] == []


async def test_kidding_overdue_includes_past_due(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    # EKD = today - 10 → overdue
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    body = await kidding_list(client, headers)
    assert body["upcoming"] == []
    assert [r["id"] for r in body["overdue"]] == [br["id"]]


async def test_kidded_pregnancy_leaves_upcoming_and_overdue(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    await kid_on_ekd(client, headers, br)
    body = await kidding_list(client, headers)
    assert body["upcoming"] == []
    assert body["overdue"] == []
    assert len(body["records"]) == 1


async def test_kidding_history_pagination_reports_full_count(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe_one, _buck_one, breeding_one = await pregnant_doe(
        client, headers, "PAGE-K-1", gestation_days=160
    )
    _doe_two, _buck_two, breeding_two = await pregnant_doe(
        client, headers, "PAGE-K-2", gestation_days=160
    )
    record_one = await kid_on_ekd(client, headers, breeding_one)
    record_two = await kid_on_ekd(client, headers, breeding_two)
    response = await client.get("/api/kidding?limit=1&offset=1", headers=headers)
    assert response.status_code == 200, response.text
    page = response.json()
    assert (page["total"], page["limit"], page["offset"]) == (2, 1, 1)
    assert [record["id"] for record in page["records"]] == [record_one["id"]]
    assert record_two["id"] != record_one["id"]


async def test_kidding_history_lists_record_with_kids(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    record = await kid_on_ekd(client, headers, br)
    body = await kidding_list(client, headers)
    assert len(body["records"]) == 1
    entry = body["records"][0]
    assert entry["id"] == record["id"]
    assert entry["doe_id"] == doe["id"]
    assert entry["doe_tag"] == "D-1"
    assert entry["breeding_record_id"] == br["id"]
    assert entry["date"] == br["expected_kidding_date"]
    assert entry["ease"] == "NORMAL"
    assert len(entry["kids"]) == 2


async def test_kidding_history_newest_first(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe_a, _buck_a, br_a = await pregnant_doe(client, headers, "D-A", gestation_days=200)
    record_a = await kid_on_ekd(client, headers, br_a)
    _doe_b, _buck_b, br_b = await pregnant_doe(client, headers, "D-B", gestation_days=160)
    record_b = await kid_on_ekd(client, headers, br_b)
    body = await kidding_list(client, headers)
    assert [r["id"] for r in body["records"]] == [record_b["id"], record_a["id"]]


async def test_kidding_list_requires_auth(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/kidding")
    assert resp.status_code == 401


async def test_kidding_list_requires_farm_header(client: httpx.AsyncClient) -> None:
    headers = await register(client)
    resp = await client.get("/api/kidding", headers=headers)
    # Required contract header: missing fails validation (422).
    assert resp.status_code == 422


async def test_kidding_list_isolated_across_farms(client: httpx.AsyncClient) -> None:
    headers_a = await owner_with_farm(client)
    headers_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    _doe, _buck, br = await pregnant_doe(client, headers_a, "D-A", gestation_days=160)
    await kid_on_ekd(client, headers_a, br)
    body_b = await kidding_list(client, headers_b)
    assert body_b["records"] == [] and body_b["upcoming"] == [] and body_b["overdue"] == []
    body_a = await kidding_list(client, headers_a)
    assert len(body_a["records"]) == 1


# ---------------------------------------------------------------------------
# POST /api/kidding — recording, kid Animals, validation, state guards
# ---------------------------------------------------------------------------
async def test_kidding_happy_path(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    record = await kid_on_ekd(client, headers, br)
    assert record["doe_id"] == doe["id"]
    assert record["breeding_record_id"] == br["id"]
    assert record["date"] == br["expected_kidding_date"]
    assert record["ease"] == "NORMAL"
    assert record["doe_tag"] == "D-1"
    assert len(record["kids"]) == 2
    assert all(k["animal_id"] is not None for k in record["kids"])
    # the breeding record now reports has_kidding
    fetched = await get_breeding(client, headers, br["id"])
    assert fetched["has_kidding"] is True
    assert fetched["outcome"] == "CONFIRMED_PREGNANT"


async def test_kidding_creates_born_animals(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, buck, br = await pregnant_doe(client, headers, gestation_days=160)
    record = await kid_on_ekd(
        client,
        headers,
        br,
        kids=[
            {"tag": "K-1", "sex": "M", "birth_weight": 2.8, "status": "ALIVE"},
            {"tag": "K-2", "sex": "F", "birth_weight": 2.5, "status": "ALIVE"},
        ],
    )
    born = [a for a in await list_animals(client, headers) if a["source"] == "BORN"]
    assert len(born) == 2
    by_tag = {a["tag_number"]: a for a in born}
    assert set(by_tag) == {"K-1", "K-2"}
    for kid_animal in born:
        assert kid_animal["dam_id"] == doe["id"]
        assert kid_animal["sire_id"] == buck["id"]
        assert kid_animal["current_bucket"] == "RECOVERY"
        assert kid_animal["status"] == "ACTIVE"
        assert kid_animal["date_of_birth"] == record["date"]
        assert kid_animal["breed"] == doe["breed"]
    assert by_tag["K-1"]["birth_weight"] == 2.8
    assert by_tag["K-2"]["sex"] == "F"


async def test_kidding_birth_type_single(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    await kid_on_ekd(client, headers, br, kids=[{"tag": "K-S", "sex": "M"}])
    born = [a for a in await list_animals(client, headers) if a["source"] == "BORN"]
    assert [a["birth_type"] for a in born] == ["SINGLE"]


async def test_kidding_birth_type_twin(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    await kid_on_ekd(client, headers, br, kids=[{"sex": "M"}, {"sex": "F"}])
    born = [a for a in await list_animals(client, headers) if a["source"] == "BORN"]
    assert [a["birth_type"] for a in born] == ["TWIN", "TWIN"]


async def test_kidding_birth_type_triplet(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    await kid_on_ekd(client, headers, br, kids=[{"sex": "M"}, {"sex": "F"}, {"sex": "M"}])
    born = [a for a in await list_animals(client, headers) if a["source"] == "BORN"]
    assert [a["birth_type"] for a in born] == ["TRIPLET"] * 3


async def test_kidding_four_alive_birth_type_quadruplet(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    await kid_on_ekd(client, headers, br, kids=[{"sex": "M"}] * 4)
    born = [a for a in await list_animals(client, headers) if a["source"] == "BORN"]
    assert len(born) == 4
    assert [a["birth_type"] for a in born] == ["QUADRUPLET"] * 4


async def test_kidding_ten_alive_birth_type_multiplet(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    await kid_on_ekd(client, headers, br, kids=[{"sex": "M"}] * 10)  # schema caps kids at 10
    born = [a for a in await list_animals(client, headers) if a["source"] == "BORN"]
    assert len(born) == 10
    assert [a["birth_type"] for a in born] == ["MULTIPLET"] * 10


async def test_kidding_born_kid_bucket_moves_are_attributed(client: httpx.AsyncClient) -> None:
    """Each born kid's initial RECOVERY BucketMove carries the recording
    user's id, matching every other attributed move in the system."""
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    record = await kid_on_ekd(client, headers, br, kids=[{"sex": "M"}, {"sex": "F"}])
    kid_animal_ids = [k["animal_id"] for k in record["kids"]]
    async with get_sessionmaker()() as db:
        owner_id = (
            (await db.execute(select(User).where(User.email == "owner@farm.in"))).scalar_one().id
        )
        moves = list(
            (
                await db.execute(select(BucketMove).where(BucketMove.animal_id.in_(kid_animal_ids)))
            ).scalars()
        )
    assert len(moves) == 2
    assert all(m.to_bucket == "RECOVERY" and m.created_by_id == owner_id for m in moves)


async def test_kidding_stillborn_creates_no_animal(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    record = await kid_on_ekd(
        client,
        headers,
        br,
        kids=[{"sex": "M"}, {"sex": "F", "status": "STILLBORN"}],
    )
    statuses = {k["sex"]: (k["status"], k["animal_id"]) for k in record["kids"]}
    assert statuses["F"] == ("STILLBORN", None)
    born = [a for a in await list_animals(client, headers) if a["source"] == "BORN"]
    assert len(born) == 1
    assert born[0]["birth_type"] == "TWIN"  # birth type counts the full delivered litter
    pending_moves = [
        task
        for task in tasks_by_category(await all_tasks(client, headers), "BUCKET_MOVE")
        if task["status"] == "PENDING"
    ]
    assert pending_moves == []  # the surviving kid retains the normal weaning workflow


async def test_kidding_died_creates_dead_animal_for_mortality_traceability(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    record = await kid_on_ekd(
        client,
        headers,
        br,
        kids=[
            {"sex": "M", "status": "DIED", "mortality_reported_at": iso(today())},
            {"sex": "F"},
        ],
    )
    died = next(k for k in record["kids"] if k["status"] == "DIED")
    assert died["animal_id"] is not None
    assert died["mortality_reported_at"] == iso(today())
    born = [a for a in await list_animals(client, headers) if a["source"] == "BORN"]
    assert len(born) == 1  # the default herd list intentionally excludes DEAD animals
    dead_animal = await get_animal(client, headers, died["animal_id"])
    assert dead_animal["status"] == "DEAD"
    assert dead_animal["status_date"] == iso(today())


async def test_kidding_died_kid_requires_explicit_mortality_date(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    resp = await kid_on_ekd_raw(client, headers, br, kids=[{"sex": "M", "status": "DIED"}])
    assert resp.status_code == 422
    assert (await get_breeding(client, headers, br["id"]))["has_kidding"] is False


async def test_died_kid_date_is_required_at_database_boundary(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    record = await kid_on_ekd(client, headers, br, kids=[{"sex": "F"}])
    async with get_sessionmaker()() as db:
        db.add(
            KidEntry(
                farm_id=int(headers["X-Farm-Id"]),
                kidding_record_id=record["id"],
                sex="M",
                status="DIED",
                mortality_reported_at=None,
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()


async def test_kidding_date_cannot_be_moved_after_recorded_kid_mortality(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    mortality_date = today() - timedelta(days=5)
    record = await kid_on_ekd(
        client,
        headers,
        br,
        kids=[
            {
                "sex": "M",
                "status": "DIED",
                "mortality_reported_at": iso(mortality_date),
            }
        ],
    )
    async with get_sessionmaker()() as db:
        kidding = await db.get(KiddingRecord, record["id"])
        assert kidding is not None
        kidding.date = mortality_date + timedelta(days=1)
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()


async def test_kidding_all_died_creates_no_weaning_task(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, breeding = await pregnant_doe(client, headers, gestation_days=160)
    record = await kid_on_ekd(
        client,
        headers,
        breeding,
        kids=[{"sex": "M", "status": "DIED", "mortality_reported_at": iso(today())}],
    )
    died = record["kids"][0]
    assert died["animal_id"] is not None
    tasks = await all_tasks(client, headers)
    assert not any(task["category"] == "WEANING" for task in tasks)
    postpartum = [
        task for task in tasks_by_category(tasks, "BUCKET_MOVE") if task["status"] == "PENDING"
    ]
    assert len(postpartum) == 1
    assert postpartum[0]["due_date"] == iso(today() + timedelta(days=POSTPARTUM_RECOVERY_DAYS))
    assert postpartum[0]["breeding_record_id"] == breeding["id"]


async def test_kidding_all_stillborn_no_animals_doe_recovers(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    record = await kid_on_ekd(client, headers, br, kids=[{"sex": "M", "status": "STILLBORN"}])
    assert record["kids"][0]["animal_id"] is None
    born = [a for a in await list_animals(client, headers) if a["source"] == "BORN"]
    assert born == []
    doe_after = await get_animal(client, headers, doe["id"])
    assert doe_after["current_bucket"] == "RECOVERY"
    tasks = await all_tasks(client, headers)
    assert not any(task["category"] == "WEANING" for task in tasks)
    postpartum = [
        task for task in tasks_by_category(tasks, "BUCKET_MOVE") if task["status"] == "PENDING"
    ]
    assert len(postpartum) == 1
    assert postpartum[0]["due_date"] == iso(
        date.fromisoformat(record["date"]) + timedelta(days=POSTPARTUM_RECOVERY_DAYS)
    )


async def test_no_survivor_postpartum_task_completes_once_and_rests_doe(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await pregnant_doe(client, headers, gestation_days=220)
    await kid_on_ekd(client, headers, br, kids=[{"sex": "F", "status": "STILLBORN"}])
    postpartum = next(
        task
        for task in tasks_by_category(await all_tasks(client, headers), "BUCKET_MOVE")
        if task["status"] == "PENDING"
    )
    completed = await client.post(f"/api/tasks/{postpartum['id']}/complete", headers=headers)
    assert completed.status_code == 200, completed.text
    assert (await get_animal(client, headers, doe["id"]))["current_bucket"] == "RESTING"
    replay = await client.post(f"/api/tasks/{postpartum['id']}/complete", headers=headers)
    assert replay.status_code == 400
    async with get_sessionmaker()() as db:
        moves = list(
            (
                await db.execute(
                    select(BucketMove).where(
                        BucketMove.animal_id == doe["id"],
                        BucketMove.reason == "Postpartum recovery complete; no surviving kids",
                    )
                )
            ).scalars()
        )
    assert len(moves) == 1


async def test_kidding_auto_tags(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    record = await kid_on_ekd(client, headers, br, kids=[{"sex": "M"}, {"sex": "F"}])
    tags = [k["tag"] for k in record["kids"]]
    assert tags == ["D-1-K1", "D-1-K2"]
    born = {a["tag_number"] for a in await list_animals(client, headers) if a["source"] == "BORN"}
    assert born == {"D-1-K1", "D-1-K2"}


async def test_kidding_auto_tags_and_collision_suffixes_fit_database_limit(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers, "D" * 50)
    buck = await make_buck(client, headers, "B-LONG-TAG")
    br = await make_breeding(
        client,
        headers,
        doe["id"],
        buck["id"],
        breeding_date=iso(today() - timedelta(days=160)),
    )
    br = await confirm(client, headers, br["id"])
    record = await kid_on_ekd(client, headers, br, kids=[{"sex": "M"}, {"sex": "F"}])
    tags = [kid["tag"] for kid in record["kids"]]
    assert len(set(tags)) == 2
    assert all(len(tag) <= 50 for tag in tags)
    assert doe["tag_number"] not in tags


async def test_kidding_explicit_tag_is_trimmed_before_length_limit(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    tag = "K" * 50
    record = await kid_on_ekd(client, headers, br, kids=[{"tag": f"  {tag}  ", "sex": "M"}])
    assert record["kids"][0]["tag"] == tag


async def test_kidding_explicit_tag_over_database_limit_rejected(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    resp = await kid_on_ekd_raw(client, headers, br, kids=[{"tag": "K" * 51, "sex": "M"}])
    assert resp.status_code == 422
    assert (await get_breeding(client, headers, br["id"]))["has_kidding"] is False


async def test_kidding_auto_tags_uniquified_on_second_kidding(client: httpx.AsyncClient) -> None:
    """A doe's second kidding must not crash on the '<doe>-K1' tag collision —
    the service uses a bounded unpredictable fallback."""
    headers = await owner_with_farm(client)
    doe, buck, br1 = await bred_doe(
        client, headers, "D-2ND", breeding_date=today() - timedelta(days=310)
    )
    first_ultrasound = await ultrasound(client, headers, br1["id"], date=br1["ultrasound_date"])
    assert first_ultrasound.status_code == 200, first_ultrasound.text
    br1 = first_ultrasound.json()
    record1 = await kid_on_ekd(client, headers, br1, kids=[{"sex": "M"}, {"sex": "F"}])
    assert [k["tag"] for k in record1["kids"]] == ["D-2ND-K1", "D-2ND-K2"]
    await move_to(client, headers, doe["id"], "RESTING", history_override=True)
    await backdate_latest_bucket_move(doe["id"], today() - timedelta(days=160))
    br2 = await make_breeding(
        client,
        headers,
        doe["id"],
        buck["id"],
        breeding_date=iso(today() - timedelta(days=159)),
    )
    second_ultrasound = await ultrasound(client, headers, br2["id"], date=br2["ultrasound_date"])
    assert second_ultrasound.status_code == 200, second_ultrasound.text
    br2 = second_ultrasound.json()
    record2 = await kid_on_ekd(client, headers, br2, kids=[{"sex": "M"}, {"sex": "F"}])
    second_tags = [k["tag"] for k in record2["kids"]]
    assert second_tags[0].startswith("D-2ND-K1-A")
    assert second_tags[1].startswith("D-2ND-K2-A")
    assert all(len(tag) <= 50 for tag in second_tags)
    tags = {a["tag_number"] for a in await list_animals(client, headers)}
    assert {"D-2ND-K1", "D-2ND-K2", *second_tags} <= tags


async def test_kidding_auto_tag_collision_work_is_strictly_bounded(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await pregnant_doe(client, headers, "D-PROBE", gestation_days=160)
    farm_id = int(headers["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        occupied = ["D-PROBE-K1", *[f"D-PROBE-K1-{n}" for n in range(2, 502)]]
        db.add_all(
            [
                Animal(
                    farm_id=farm_id,
                    tag_number=tag,
                    breed="Osmanabadi",
                    sex="F",
                    source="PURCHASED",
                    current_bucket="FOUNDATION",
                )
                for tag in occupied
            ]
        )
        await db.commit()

    statements: list[str] = []

    def capture(
        _conn: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        statements.append(statement)

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = await kid_on_ekd_raw(client, headers, br, kids=[{"sex": "M"}])
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 201, response.text
    assert response.json()["kids"][0]["tag"].startswith(f"{doe['tag_number']}-K1-A")
    tag_probes = [
        statement
        for statement in statements
        if "FROM animals" in statement and "animals.tag_number =" in statement
    ]
    assert len(tag_probes) <= 4


async def test_kidding_explicit_duplicate_tags(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    resp = await kid_on_ekd_raw(
        client, headers, br, kids=[{"tag": "K-X", "sex": "M"}, {"tag": "K-X", "sex": "F"}]
    )
    assert resp.status_code == 400


async def kid_on_ekd_raw(
    client: httpx.AsyncClient, headers: dict, br: dict, **overrides: object
) -> httpx.Response:
    kidding_date = overrides.pop("date", br["expected_kidding_date"])
    return await post_kidding(client, headers, br["id"], date=kidding_date, **overrides)


async def test_kidding_tag_clash_with_existing_animal(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "K-EXISTING", sex="M", bucket="MALE_KIDS")
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    resp = await kid_on_ekd_raw(client, headers, br, kids=[{"tag": "K-EXISTING", "sex": "M"}])
    assert resp.status_code == 400


async def test_kidding_tag_clash_with_does_own_tag(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    resp = await kid_on_ekd_raw(client, headers, br, kids=[{"tag": doe["tag_number"], "sex": "M"}])
    assert resp.status_code == 400


async def test_kidding_whitespace_only_tag_becomes_auto(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    record = await kid_on_ekd(client, headers, br, kids=[{"tag": "   ", "sex": "M"}])
    assert record["kids"][0]["tag"] == "D-1-K1"


async def test_kidding_tag_is_stripped(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    record = await kid_on_ekd(client, headers, br, kids=[{"tag": "  K-77  ", "sex": "M"}])
    assert record["kids"][0]["tag"] == "K-77"
    born = {a["tag_number"] for a in await list_animals(client, headers)}
    assert "K-77" in born


async def test_kidding_moves_doe_to_recovery(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    await kid_on_ekd(client, headers, br)
    doe_after = await get_animal(client, headers, doe["id"])
    assert doe_after["current_bucket"] == "RECOVERY"
    assert doe_after["is_currently_pregnant"] is False


async def test_held_doe_kidding_reclassifies_atomically(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, breeding = await pregnant_doe(client, headers, "HELD-KIDDING", gestation_days=160)
    await place_health_hold(client, headers, doe["id"])

    recorded = await make_kidding(client, headers, breeding["id"], date=iso(today()))
    assert recorded["breeding_record_id"] == breeding["id"]
    after = await get_animal(client, headers, doe["id"])
    assert after["current_bucket"] == "RECOVERY"
    assert after["movement_restricted"] is True
    assert after["suspected_scheduled_disease"] is True


async def test_held_orphan_kid_can_be_weaned_after_clearance_with_real_provenance(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, breeding = await pregnant_doe(client, headers, "ORPHAN-DAM", gestation_days=160)
    recorded = await make_kidding(
        client,
        headers,
        breeding["id"],
        date=iso(today()),
        kids=[{"tag": "ORPHAN-KID", "sex": "M", "status": "ALIVE"}],
    )
    kid_id = recorded["kids"][0]["animal_id"]
    assert kid_id is not None
    await place_health_hold(client, headers, kid_id)

    removed = await client.post(
        f"/api/animals/{doe['id']}/status",
        json={"new_status": "DEAD", "date": iso(today())},
        headers=headers,
    )
    assert removed.status_code == 200, removed.text
    assert (await get_animal(client, headers, kid_id))["current_bucket"] == "RECOVERY"

    still_held = await client.post(
        f"/api/animals/{kid_id}/move",
        json={"to_bucket": "MALE_KIDS"},
        headers=headers,
    )
    assert still_held.status_code == 409
    assert "restriction" in still_held.json()["detail"].lower()

    cleared = await client.post(
        f"/api/health/restrictions/{kid_id}/clear",
        json={
            "clearance_reference": "District AHD orphan clearance",
            "expected_restriction_version": 1,
        },
        headers=headers,
    )
    assert cleared.status_code == 204, cleared.text

    wrong_sex = await client.post(
        f"/api/animals/{kid_id}/move",
        json={"to_bucket": "FEMALE_KIDS"},
        headers=headers,
    )
    assert wrong_sex.status_code == 409

    moved = await client.post(
        f"/api/animals/{kid_id}/move",
        json={"to_bucket": "MALE_KIDS"},
        headers=headers,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["current_bucket"] == "MALE_KIDS"
    profile = await client.get(f"/api/animals/{kid_id}", headers=headers)
    assert profile.status_code == 200
    assert profile.json()["moves"][0]["reason"] == (
        "Dam no longer active — deferred early wean after hold clearance"
    )


async def test_kidding_creates_weaning_task_at_plus_60(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    record = await kid_on_ekd(client, headers, br)
    weaning = tasks_by_category(await all_tasks(client, headers), "WEANING")
    assert len(weaning) == 1
    kidding_date = date.fromisoformat(record["date"])
    assert weaning[0]["due_date"] == iso(kidding_date + timedelta(days=WEANING_DAYS))
    assert weaning[0]["animal_id"] == doe["id"]
    assert weaning[0]["status"] == "PENDING"


async def test_kidding_closes_pregnancy_tasks(client: httpx.AsyncClient) -> None:
    """KIDDING_DUE is auto-completed; leftover pre-kidding duties are skipped."""
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    await kid_on_ekd(client, headers, br)
    tasks = await all_tasks(client, headers)
    assert [t["status"] for t in tasks_by_category(tasks, "KIDDING_DUE")] == ["DONE"]
    assert [t["status"] for t in tasks_by_category(tasks, "VACCINE")] == ["SKIPPED"]
    assert [t["status"] for t in tasks_by_category(tasks, "BUCKET_MOVE")] == ["SKIPPED"]


async def test_double_kidding_conflict(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    await kid_on_ekd(client, headers, br)
    resp = await kid_on_ekd_raw(client, headers, br, kids=[{"sex": "M"}])
    assert resp.status_code == 409
    # no extra animals were created by the replay
    born = [a for a in await list_animals(client, headers) if a["source"] == "BORN"]
    assert len(born) == 2


async def test_kidding_on_pending_breeding(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await bred_doe(client, headers)
    resp = await post_kidding(client, headers, br["id"])
    assert resp.status_code == 400


async def test_kidding_on_failed_breeding(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await bred_doe(client, headers)
    await fail_cycle(client, headers, br["id"])
    resp = await post_kidding(client, headers, br["id"])
    assert resp.status_code == 400


async def test_kidding_on_aborted_breeding(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers)
    resp = await post_abort(client, headers, br["id"])
    assert resp.status_code == 200, resp.text
    resp = await post_kidding(client, headers, br["id"])
    assert resp.status_code == 400


async def test_kidding_on_sold_doe_conflict(client: httpx.AsyncClient) -> None:
    """A sold doe must not 'deliver' new stock. Since the sale auto-resolves
    her confirmed pregnancy as ABORTED, the kidding attempt now
    fails the same confirmed-pregnancy state guard as any aborted record
    (400, like test_kidding_on_aborted_breeding) rather than the service's
    non-ACTIVE-doe guard."""
    headers = await owner_with_farm(client)
    doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    await set_status(client, headers, doe["id"], "SOLD")
    assert (await get_breeding(client, headers, br["id"]))["outcome"] == "ABORTED"
    resp = await kid_on_ekd_raw(client, headers, br)
    assert resp.status_code == 400
    born = [
        a for a in await list_animals(client, headers, status="ACTIVE") if a["source"] == "BORN"
    ]
    assert born == []


async def test_kidding_nonexistent_breeding_record(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await post_kidding(client, headers, 999999)
    assert resp.status_code == 404


async def test_kidding_cross_farm_breeding_record(client: httpx.AsyncClient) -> None:
    headers_a = await owner_with_farm(client)
    headers_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    _doe, _buck, br_b = await pregnant_doe(client, headers_b, "D-B", gestation_days=160)
    resp = await kid_on_ekd_raw(client, headers_a, br_b)
    assert resp.status_code == 404


async def test_kidding_future_date(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    # Pydantic keeps one day of UTC/local-date parsing headroom, but the
    # endpoint rejects a date beyond the selected farm's exact local date.
    resp = await post_kidding(client, headers, br["id"], date=iso(today() + timedelta(days=1)))
    assert resp.status_code == 422


async def test_kidding_date_before_breeding_date(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    breeding_date = today() - timedelta(days=160)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    resp = await post_kidding(
        client, headers, br["id"], date=iso(breeding_date - timedelta(days=1))
    )
    assert resp.status_code == 400


async def test_kidding_empty_kids_list(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    resp = await post_kidding(client, headers, br["id"], kids=[])
    assert resp.status_code == 422


async def test_kidding_eleven_kids_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    resp = await post_kidding(client, headers, br["id"], kids=[{"sex": "M"}] * 11)
    assert resp.status_code == 422


async def test_kidding_ten_kids_boundary_ok(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    resp = await kid_on_ekd_raw(client, headers, br, kids=[{"sex": "F"}] * 10)
    assert resp.status_code == 201, resp.text
    assert len(resp.json()["kids"]) == 10


async def test_kidding_invalid_kid_sex(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    resp = await post_kidding(client, headers, br["id"], kids=[{"sex": "X"}])
    assert resp.status_code == 422


async def test_kidding_invalid_kid_status(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    resp = await post_kidding(client, headers, br["id"], kids=[{"sex": "M", "status": "GHOST"}])
    assert resp.status_code == 422


async def test_kidding_negative_birth_weight(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    resp = await post_kidding(client, headers, br["id"], kids=[{"sex": "M", "birth_weight": -0.5}])
    assert resp.status_code == 422


async def test_kidding_zero_birth_weight_ok(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    resp = await kid_on_ekd_raw(client, headers, br, kids=[{"sex": "M", "birth_weight": 0.0}])
    assert resp.status_code == 201, resp.text
    assert resp.json()["kids"][0]["birth_weight"] == 0.0


async def test_kidding_tag_too_long(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    resp = await post_kidding(client, headers, br["id"], kids=[{"sex": "M", "tag": "K" * 51}])
    assert resp.status_code == 422


async def test_kidding_tag_max_length_ok(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    tag = "K" * 50
    resp = await kid_on_ekd_raw(client, headers, br, kids=[{"sex": "M", "tag": tag}])
    assert resp.status_code == 201, resp.text
    assert resp.json()["kids"][0]["tag"] == tag


async def test_kidding_invalid_ease(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    resp = await post_kidding(client, headers, br["id"], ease="BRUTAL")
    assert resp.status_code == 422


async def test_kidding_caesarean_rejected(client: httpx.AsyncClient) -> None:
    """SPEC §KiddingRecord defines ease as NORMAL | ASSISTED | DIFFICULT (and
    models.KiddingEase has exactly those three) — 'CAESAREAN' is out of domain
    and the schema now rejects it with 422 instead of coercing to NORMAL."""
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    resp = await post_kidding(client, headers, br["id"], ease="CAESAREAN")
    assert resp.status_code == 422


async def test_kidding_ease_assisted_and_difficult(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe_a, _buck_a, br_a = await pregnant_doe(client, headers, "D-A", gestation_days=160)
    record_a = await kid_on_ekd(client, headers, br_a, ease="ASSISTED")
    assert record_a["ease"] == "ASSISTED"
    _doe_b, _buck_b, br_b = await pregnant_doe(client, headers, "D-B", gestation_days=160)
    record_b = await kid_on_ekd(client, headers, br_b, ease="DIFFICULT")
    assert record_b["ease"] == "DIFFICULT"


async def test_kidding_unicode_notes_and_tag(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    record = await kid_on_ekd(
        client,
        headers,
        br,
        notes="ఎమే జెడ్డు 🐐 — बकरी ने दो बच्चे दिए",
        kids=[{"sex": "F", "tag": "మేక-01 🐐"}],
    )
    assert record["notes"] == "ఎమే జెడ్డు 🐐 — बकरी ने दो बच्चे दिए"
    assert record["kids"][0]["tag"] == "మేక-01 🐐"
    born = {a["tag_number"] for a in await list_animals(client, headers)}
    assert "మేక-01 🐐" in born


async def test_kidding_sql_injection_tag_stored_verbatim(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    evil = "K-1'; DROP TABLE animals;--"
    record = await kid_on_ekd(client, headers, br, kids=[{"sex": "M", "tag": evil}])
    assert record["kids"][0]["tag"] == evil
    # the herd is intact and the tag round-trips
    born = {a["tag_number"] for a in await list_animals(client, headers)}
    assert evil in born


async def test_kidding_notes_length_boundary(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    accepted = "x" * 4_000
    record = await kid_on_ekd(client, headers, br, notes=accepted)
    assert record["notes"] == accepted

    _doe, _buck, br = await pregnant_doe(client, headers, "D-NOTES", gestation_days=160)
    rejected = await kid_on_ekd_raw(client, headers, br, notes="x" * 4_001)
    assert rejected.status_code == 422
    assert (await get_breeding(client, headers, br["id"]))["has_kidding"] is False


async def test_kidding_notes_null_when_omitted(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    record = await kid_on_ekd(client, headers, br)
    assert record["notes"] is None


async def test_kidding_missing_date(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    resp = await client.post(
        "/api/kidding",
        json={"breeding_record_id": br["id"], "kids": [{"sex": "M"}]},
        headers=headers,
    )
    assert resp.status_code == 422


async def test_kidding_missing_kids(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    resp = await client.post(
        "/api/kidding",
        json={"breeding_record_id": br["id"], "date": iso(today())},
        headers=headers,
    )
    assert resp.status_code == 422


async def test_kidding_malformed_date(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    resp = await post_kidding(client, headers, br["id"], date="not-a-date")
    assert resp.status_code == 422


async def test_kidding_breeding_id_zero(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await post_kidding(client, headers, 0)
    assert resp.status_code == 422


async def test_kidding_breeding_id_above_max(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await post_kidding(client, headers, 2**62 + 1)
    assert resp.status_code == 422


async def test_kidding_breeding_id_string(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.post(
        "/api/kidding",
        json={"breeding_record_id": "abc", "date": iso(today()), "kids": [{"sex": "M"}]},
        headers=headers,
    )
    assert resp.status_code == 422


async def test_kidding_extra_field_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    resp = await kid_on_ekd_raw(client, headers, br, bogus_field="x")
    assert resp.status_code == 422, resp.text
    assert (await get_breeding(client, headers, br["id"]))["has_kidding"] is False


async def test_kidding_requires_auth(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/kidding",
        json={"breeding_record_id": 1, "date": iso(today()), "kids": [{"sex": "M"}]},
    )
    assert resp.status_code == 401


async def test_kidding_requires_farm_header(client: httpx.AsyncClient) -> None:
    headers = await register(client)
    resp = await client.post(
        "/api/kidding",
        json={"breeding_record_id": 1, "date": iso(today()), "kids": [{"sex": "M"}]},
        headers=headers,
    )
    # Required contract header: missing fails validation (422).
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Weaning at day 60: kids → MALE_KIDS/FEMALE_KIDS, doe → RESTING
# ---------------------------------------------------------------------------
async def _kidded_doe_with_due_weaning(
    client: httpx.AsyncClient, headers: dict, tag: str = "D-1"
) -> tuple[dict, dict, dict]:
    """Doe kidded 70 days ago → her +60d weaning duty fell due 10 days ago and
    can be completed via the API. Returns (doe, buck, kidding_record)."""
    doe, buck, br = await pregnant_doe(client, headers, tag, gestation_days=220)
    record = await kid_on_ekd(client, headers, br)
    return doe, buck, record


async def test_weaning_moves_kids_by_sex_and_doe_to_resting(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, _record = await _kidded_doe_with_due_weaning(client, headers)
    weaning = tasks_by_category(await all_tasks(client, headers), "WEANING")[0]
    resp = await client.post(f"/api/tasks/{weaning['id']}/complete", headers=headers)
    assert resp.status_code == 200, resp.text
    born = {
        a["tag_number"]: a for a in await list_animals(client, headers) if a["source"] == "BORN"
    }
    assert born["D-1-K1"]["current_bucket"] == "MALE_KIDS"  # sex M
    assert born["D-1-K2"]["current_bucket"] == "FEMALE_KIDS"  # sex F
    doe_after = await get_animal(client, headers, doe["id"])
    assert doe_after["current_bucket"] == "RESTING"


async def test_weaning_not_due_yet_conflict(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    # kidded on the EKD 10 days ago → weaning due in 50 days
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    await kid_on_ekd(client, headers, br)
    weaning = tasks_by_category(await all_tasks(client, headers), "WEANING")[0]
    resp = await client.post(f"/api/tasks/{weaning['id']}/complete", headers=headers)
    assert resp.status_code == 409


async def test_weaning_only_moves_this_does_kids(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe_a, _buck_a, _rec_a = await _kidded_doe_with_due_weaning(client, headers, "D-A")
    # Second doe kidded 10 days ago — her kids must stay in RECOVERY
    _doe_b, _buck_b, br_b = await pregnant_doe(client, headers, "D-B", gestation_days=160)
    await kid_on_ekd(client, headers, br_b)
    weaning = next(
        t
        for t in tasks_by_category(await all_tasks(client, headers), "WEANING")
        if t["animal_id"] == doe_a["id"]
    )
    resp = await client.post(f"/api/tasks/{weaning['id']}/complete", headers=headers)
    assert resp.status_code == 200, resp.text
    born = {
        a["tag_number"]: a for a in await list_animals(client, headers) if a["source"] == "BORN"
    }
    assert born["D-A-K1"]["current_bucket"] == "MALE_KIDS"
    assert born["D-B-K1"]["current_bucket"] == "RECOVERY"
    assert born["D-B-K2"]["current_bucket"] == "RECOVERY"


async def test_weaning_complete_twice_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, _record = await _kidded_doe_with_due_weaning(client, headers)
    weaning = tasks_by_category(await all_tasks(client, headers), "WEANING")[0]
    resp = await client.post(f"/api/tasks/{weaning['id']}/complete", headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await client.post(f"/api/tasks/{weaning['id']}/complete", headers=headers)
    assert resp.status_code == 400


async def test_after_weaning_doe_is_breeding_candidate(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, _record = await _kidded_doe_with_due_weaning(client, headers)
    weaning = tasks_by_category(await all_tasks(client, headers), "WEANING")[0]
    resp = await client.post(f"/api/tasks/{weaning['id']}/complete", headers=headers)
    assert resp.status_code == 200, resp.text
    assert doe["id"] in await candidate_ids(client, headers)


# ---------------------------------------------------------------------------
# RBAC: breeding and kidding perms (VET) vs the roles that hold neither
# ---------------------------------------------------------------------------
async def test_breeding_view_only_does_not_receive_mutation_candidates(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    await make_doe(client, owner)
    await make_buck(client, owner)
    viewer = await custom_breeding_viewer_headers(client, owner, "breeding-reader@farm.in")

    response = await client.get("/api/breeding", headers=viewer)
    assert response.status_code == 200, response.text
    assert response.json()["candidate_availability"] is None
    assert "candidate_doe_ids" not in response.json()
    assert "active_buck_ids" not in response.json()

    candidates = await client.get(
        "/api/breeding/candidates", params={"kind": "doe"}, headers=viewer
    )
    assert candidates.status_code == 403
    assert candidates.json()["detail"] == "Missing permission: breeding.manage"


async def test_vet_worker_can_view_and_manage_breeding(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner)
    buck = await make_buck(client, owner)
    vet = await worker_headers(client, owner, "VET", "vet@farm.in")
    resp = await client.get("/api/breeding", headers=vet)
    assert resp.status_code == 200, resp.text
    resp = await post_breeding(
        client,
        vet,
        doe["id"],
        buck["id"],
        breeding_date=iso(today() - timedelta(days=ULTRASOUND_AFTER_BREEDING_DAYS + 3)),
    )
    assert resp.status_code == 201, resp.text
    resp = await ultrasound(client, vet, resp.json()["id"], pregnant=False, kid_count=None)
    assert resp.status_code == 200, resp.text


async def test_vet_worker_can_record_kidding(client: httpx.AsyncClient) -> None:
    """The vet attends the difficult deliveries, and KIDDING_DUE duties are
    auto-assigned to her role (permissions.TASK_CATEGORY_ROLE_MAP), so the
    preset carries kidding.view/kidding.manage. It previously carried neither,
    which left those duties on a role that could not open the kidding form."""
    owner = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, owner, gestation_days=160)
    vet = await worker_headers(client, owner, "VET", "vet@farm.in")
    resp = await client.get("/api/kidding", headers=vet)
    assert resp.status_code == 200, resp.text
    resp = await kid_on_ekd_raw(client, vet, br)
    assert resp.status_code == 201, resp.text


async def test_mover_worker_cannot_record_kidding(client: httpx.AsyncClient) -> None:
    """Kidding stays behind its own permission: a role that only moves animals
    between buckets holds neither kidding.view nor kidding.manage."""
    owner = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, owner, gestation_days=160)
    mover = await worker_headers(client, owner, "MOVER", "mover@farm.in")
    resp = await client.get("/api/kidding", headers=mover)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: kidding.view"
    resp = await kid_on_ekd_raw(client, mover, br)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: kidding.manage"


async def test_mover_worker_cannot_access_breeding(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner)
    buck = await make_buck(client, owner)
    mover = await worker_headers(client, owner, "MOVER", "mover@farm.in")
    resp = await client.get("/api/breeding", headers=mover)
    assert resp.status_code == 403
    resp = await post_breeding(client, mover, doe["id"], buck["id"])
    assert resp.status_code == 403
    resp = await ultrasound(client, mover, 1, pregnant=True)
    assert resp.status_code == 403
    resp = await post_abort(client, mover, 1)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Audit 2026-08-09 regressions
# ---------------------------------------------------------------------------
async def test_return_to_heat_closes_the_cycle_on_the_day_it_was_observed(
    client: httpx.AsyncClient,
) -> None:
    """The heat cycle is ~21 days: a doe seen back in standing heat is factual
    evidence the service did not hold, so a NEGATIVE result may predate the
    planned day-32 scan and brings the cycle's check forward with it."""
    headers = await owner_with_farm(client)
    breeding_date = today() - timedelta(days=30)
    doe = await make_doe(client, headers, "HEAT-1")
    buck = await make_buck(client, headers, "HEAT-1-BUCK")
    br = await make_breeding(
        client, headers, doe["id"], buck["id"], breeding_date=iso(breeding_date)
    )
    observed = breeding_date + timedelta(days=21)
    response = await ultrasound(
        client, headers, br["id"], pregnant=False, kid_count=None, date=iso(observed)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "FAILED"
    assert body["ultrasound_result_date"] == iso(observed)
    # The planned check never happened; the record must not keep claiming a
    # later check date than its own result (ck_breeding_records_result_after_plan).
    assert body["ultrasound_date"] == iso(observed)


async def test_doe_returning_to_heat_can_be_reserved_without_falsifying_dates(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    breeding_date = today() - timedelta(days=30)
    doe = await make_doe(client, headers, "HEAT-2")
    buck = await make_buck(client, headers, "HEAT-2-BUCK")
    first = await make_breeding(
        client, headers, doe["id"], buck["id"], breeding_date=iso(breeding_date)
    )
    closed = await ultrasound(
        client,
        headers,
        first["id"],
        pregnant=False,
        kid_count=None,
        date=iso(breeding_date + timedelta(days=20)),
    )
    assert closed.status_code == 200, closed.text
    second = await post_breeding(
        client,
        headers,
        doe["id"],
        buck["id"],
        breeding_date=iso(breeding_date + timedelta(days=21)),
    )
    assert second.status_code == 201, second.text
    assert second.json()["breeding_date"] == iso(breeding_date + timedelta(days=21))
    assert second.json()["heat_cycle_number"] == 2


async def test_early_pregnancy_confirmation_still_requires_the_planned_check(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    breeding_date = today() - timedelta(days=21)
    doe = await make_doe(client, headers, "HEAT-3")
    buck = await make_buck(client, headers, "HEAT-3-BUCK")
    br = await make_breeding(
        client, headers, doe["id"], buck["id"], breeding_date=iso(breeding_date)
    )
    response = await ultrasound(
        client, headers, br["id"], pregnant=True, kid_count=2, date=iso(today())
    )
    assert response.status_code == 409, response.text
    assert "planned check date" in response.json()["detail"]


async def test_pregnancy_check_result_cannot_predate_the_breeding_date(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    breeding_date = today() - timedelta(days=30)
    doe = await make_doe(client, headers, "HEAT-4")
    buck = await make_buck(client, headers, "HEAT-4-BUCK")
    br = await make_breeding(
        client, headers, doe["id"], buck["id"], breeding_date=iso(breeding_date)
    )
    response = await ultrasound(
        client,
        headers,
        br["id"],
        pregnant=False,
        kid_count=None,
        date=iso(breeding_date - timedelta(days=1)),
    )
    assert response.status_code == 409, response.text
    assert "predate the breeding date" in response.json()["detail"]


async def test_heat_cycle_number_is_derived_from_the_does_failed_cycles(
    client: httpx.AsyncClient,
) -> None:
    """No client ever sent the field, so every record claimed cycle 1 and the
    reports' first-cycle metric degenerated into the conception rate."""
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers, "CYCLE-1")
    buck = await make_buck(client, headers, "CYCLE-1-BUCK")
    first = await make_breeding(
        client, headers, doe["id"], buck["id"], breeding_date=iso(today() - timedelta(days=120))
    )
    assert first["heat_cycle_number"] == 1
    await fail_cycle(client, headers, first["id"])
    second = await make_breeding(
        client, headers, doe["id"], buck["id"], breeding_date=iso(today() - timedelta(days=80))
    )
    assert second["heat_cycle_number"] == 2
    await fail_cycle(client, headers, second["id"])
    # A forged/stale client value is ignored: the doe's own history decides.
    third = await make_breeding(
        client,
        headers,
        doe["id"],
        buck["id"],
        breeding_date=iso(today() - timedelta(days=40)),
        heat_cycle_number=1,
    )
    assert third["heat_cycle_number"] == 3


async def test_heat_cycle_number_restarts_after_a_resolved_pregnancy(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers, "CYCLE-2")
    buck = await make_buck(client, headers, "CYCLE-2-BUCK")
    first = await make_breeding(
        client, headers, doe["id"], buck["id"], breeding_date=iso(today() - timedelta(days=120))
    )
    await fail_cycle(client, headers, first["id"])
    second = await make_breeding(
        client, headers, doe["id"], buck["id"], breeding_date=iso(today() - timedelta(days=80))
    )
    assert second["heat_cycle_number"] == 2
    await confirm(client, headers, second["id"])
    aborted = await post_abort(
        client, headers, second["id"], loss_date=iso(today() - timedelta(days=40))
    )
    assert aborted.status_code == 200, aborted.text
    third = await make_breeding(
        client, headers, doe["id"], buck["id"], breeding_date=iso(today() - timedelta(days=30))
    )
    assert third["heat_cycle_number"] == 1


async def test_kidding_cannot_predate_the_pregnancy_confirmation(
    client: httpx.AsyncClient,
) -> None:
    """A late-entered scan date used to let a delivery be recorded months
    before the pregnancy it belongs to was confirmed."""
    headers = await owner_with_farm(client)
    breeding_date = today() - timedelta(days=160)
    doe = await make_doe(client, headers, "CONFIRM-ORDER")
    buck = await make_buck(client, headers, "CONFIRM-ORDER-BUCK")
    br = await make_breeding(
        client, headers, doe["id"], buck["id"], breeding_date=iso(breeding_date)
    )
    # The vet's report is entered late, dated today rather than the scan day.
    late = await ultrasound(
        client, headers, br["id"], pregnant=True, kid_count=1, date=iso(today())
    )
    assert late.status_code == 200, late.text
    assert late.json()["ultrasound_result_date"] == iso(today())

    response = await post_kidding(
        client,
        headers,
        br["id"],
        date=late.json()["expected_kidding_date"],
        kids=[{"sex": "F"}],
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Kidding date cannot predate the pregnancy confirmation"


async def test_sold_sibling_does_not_strand_the_dam_in_recovery(
    client: httpx.AsyncClient,
) -> None:
    """A litter-mate that left the herd alive must not deadlock the dam.

    ``replan_dam_after_last_kid_death`` decides survivorship from live herd
    status (a SOLD kid is not a survivor) and therefore schedules the dam's
    postpartum duty. The completion guard used to read ``KidEntry.status``
    instead — an immutable birth fact that selling never rewrites — so it saw
    the sold sibling as still ALIVE and refused the very duty the other half
    of the workflow had just created, leaving the doe in RECOVERY forever.
    """
    headers = await owner_with_farm(client)
    doe, _buck, br = await pregnant_doe(client, headers, gestation_days=220)
    record = await kid_on_ekd(client, headers, br, kids=[{"sex": "F"}, {"sex": "M"}])
    first, second = record["kids"][0]["animal_id"], record["kids"][1]["animal_id"]
    assert first is not None and second is not None

    sold = await client.post(
        f"/api/animals/{first}/status",
        json={"new_status": "SOLD", "sale_price": 3000.0},
        headers=headers,
    )
    assert sold.status_code == 200, sold.text
    # The sold kid keeps its ALIVE birth record — that is the whole point.
    async with get_sessionmaker()() as db:
        entries = list(
            (
                await db.execute(select(KidEntry).where(KidEntry.kidding_record_id == record["id"]))
            ).scalars()
        )
    assert any(entry.animal_id == first and entry.status == "ALIVE" for entry in entries)

    # Backdate the death so the postpartum duty it schedules is already due.
    death_date = today() - timedelta(days=POSTPARTUM_RECOVERY_DAYS + 1)
    died = await client.post(
        f"/api/animals/{second}/status",
        json={
            "new_status": "DEAD",
            "mortality_cause": "illness",
            "date": iso(death_date),
            "mortality_reported_at": iso(death_date),
        },
        headers=headers,
    )
    assert died.status_code == 200, died.text

    postpartum = next(
        task
        for task in tasks_by_category(await all_tasks(client, headers), "BUCKET_MOVE")
        if task["status"] == "PENDING" and task["animal_id"] == doe["id"]
    )
    completed = await client.post(f"/api/tasks/{postpartum['id']}/complete", headers=headers)
    assert completed.status_code == 200, completed.text
    assert (await get_animal(client, headers, doe["id"]))["current_bucket"] == "RESTING"
