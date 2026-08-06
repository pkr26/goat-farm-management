"""Extended coverage for the breeding & kidding modules (SPEC: breed constants,
bucket system, BreedingRecord/KiddingRecord entities, task auto-generation).

Covers, through the async JSON API only:
- GET /api/breeding (records + breeding-ready candidate picker + active bucks)
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

from datetime import date, timedelta

import httpx

from app.utils import add_months, today

from .conftest import owner_with_farm, register

WORKER_PW = "workerpass123"
GESTATION_DAYS = 150
ULTRASOUND_AFTER_BREEDING_DAYS = 32
WEANING_DAYS = 60


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
    resp = await client.post("/api/animals", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def make_doe(
    client: httpx.AsyncClient,
    headers: dict,
    tag: str = "D-1",
    bucket: str = "FOUNDATION",
    age_days: int = 400,
    weight_kg: float | None = 26.0,
) -> dict:
    """A breeding-ready doe (13 months old, 26 kg entry weight, FOUNDATION)."""
    overrides: dict = {"date_of_birth": iso(today() - timedelta(days=age_days))}
    if weight_kg is not None:
        overrides["weight_kg"] = weight_kg
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
    return await make_animal(client, headers, tag, sex="F", **overrides)


async def make_buck(
    client: httpx.AsyncClient, headers: dict, tag: str = "B-1", **overrides: object
) -> dict:
    return await make_animal(client, headers, tag, sex="M", bucket="BREEDING", **overrides)


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
        breeding_date=iso(breeding_date or today()),
    )
    return doe, buck, br


async def ultrasound(
    client: httpx.AsyncClient, headers: dict, br_id: int, **payload: object
) -> httpx.Response:
    body = {"pregnant": True, "kid_count": 2} | payload
    return await client.post(f"/api/breeding/{br_id}/ultrasound", json=body, headers=headers)


async def confirm(
    client: httpx.AsyncClient, headers: dict, br_id: int, kid_count: int | None = 2
) -> dict:
    resp = await ultrasound(client, headers, br_id, pregnant=True, kid_count=kid_count)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def fail_cycle(client: httpx.AsyncClient, headers: dict, br_id: int) -> dict:
    resp = await ultrasound(client, headers, br_id, pregnant=False, kid_count=None)
    assert resp.status_code == 200, resp.text
    return resp.json()


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


async def kidding_list(client: httpx.AsyncClient, headers: dict) -> dict:
    resp = await client.get("/api/kidding", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def move_to(client: httpx.AsyncClient, headers: dict, animal_id: int, bucket: str) -> dict:
    resp = await client.post(
        f"/api/animals/{animal_id}/move", json={"to_bucket": bucket}, headers=headers
    )
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


# ---------------------------------------------------------------------------
# GET /api/breeding — list, candidate does, active bucks
# ---------------------------------------------------------------------------
async def test_breeding_list_empty_farm(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    body = await breeding_list(client, headers)
    assert body["records"] == []
    assert body["candidate_doe_ids"] == []
    assert body["active_buck_ids"] == []


async def test_candidate_doe_in_foundation_is_listed(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers)
    body = await breeding_list(client, headers)
    assert body["candidate_doe_ids"] == [doe["id"]]


async def test_candidate_excludes_males(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_buck(client, headers)
    body = await breeding_list(client, headers)
    assert body["candidate_doe_ids"] == []


async def test_candidate_excludes_young_doe(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_doe_aged_months(client, headers, "D-YOUNG", months=9)
    body = await breeding_list(client, headers)
    assert body["candidate_doe_ids"] == []


async def test_candidate_age_boundary_ten_months(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe_aged_months(client, headers, "D-10MO", months=10)
    assert doe["age_months"] == 10
    body = await breeding_list(client, headers)
    assert body["candidate_doe_ids"] == [doe["id"]]


async def test_candidate_excludes_light_doe(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_doe(client, headers, weight_kg=21.99)
    body = await breeding_list(client, headers)
    assert body["candidate_doe_ids"] == []


async def test_candidate_weight_boundary_22kg(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers, weight_kg=22.0)
    body = await breeding_list(client, headers)
    assert body["candidate_doe_ids"] == [doe["id"]]


async def test_candidate_excludes_doe_without_weight(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_doe(client, headers, weight_kg=None)
    body = await breeding_list(client, headers)
    assert body["candidate_doe_ids"] == []


async def test_candidate_excludes_quarantine_doe(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_doe(client, headers, bucket="QUARANTINE")
    body = await breeding_list(client, headers)
    assert body["candidate_doe_ids"] == []


async def test_candidate_includes_female_kids_bucket(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers, bucket="FEMALE_KIDS")
    body = await breeding_list(client, headers)
    assert body["candidate_doe_ids"] == [doe["id"]]


async def test_candidate_includes_resting_doe(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers, bucket="RESTING")
    body = await breeding_list(client, headers)
    assert body["candidate_doe_ids"] == [doe["id"]]


async def test_candidate_includes_breeding_bucket_doe_without_weight(
    client: httpx.AsyncClient,
) -> None:
    """Re-breeding path: a doe already in BREEDING (not pregnant, >=10 mo) is
    eligible even with no weight record — the 22 kg rule applies to the
    first-breeding buckets only."""
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers, bucket="BREEDING", weight_kg=None)
    body = await breeding_list(client, headers)
    assert body["candidate_doe_ids"] == [doe["id"]]


async def test_candidate_excludes_young_doe_in_breeding_bucket(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    await make_doe_aged_months(client, headers, "D-YB", months=9)
    await move_to(
        client,
        headers,
        (await list_animals(client, headers, q="D-YB"))[0]["id"],
        "BREEDING",
    )
    body = await breeding_list(client, headers)
    assert body["candidate_doe_ids"] == []


async def test_candidate_excludes_pregnant_doe(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, _br = await pregnant_doe(client, headers)
    body = await breeding_list(client, headers)
    assert doe["id"] not in body["candidate_doe_ids"]


async def test_candidate_excludes_doe_with_pending_breeding(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, _br = await bred_doe(client, headers)
    body = await breeding_list(client, headers)
    assert doe["id"] not in body["candidate_doe_ids"]


async def test_candidate_excludes_sold_doe(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers)
    await set_status(client, headers, doe["id"], "SOLD")
    body = await breeding_list(client, headers)
    assert body["candidate_doe_ids"] == []


async def test_candidate_excludes_dead_doe(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers)
    await set_status(client, headers, doe["id"], "DEAD")
    body = await breeding_list(client, headers)
    assert body["candidate_doe_ids"] == []


async def test_candidate_excludes_culled_doe(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers)
    await set_status(client, headers, doe["id"], "CULLED")
    body = await breeding_list(client, headers)
    assert body["candidate_doe_ids"] == []


async def test_active_buck_ids_include_active_males(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    buck = await make_buck(client, headers)
    body = await breeding_list(client, headers)
    assert body["active_buck_ids"] == [buck["id"]]


async def test_active_buck_ids_exclude_sold_male(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    buck = await make_buck(client, headers)
    await set_status(client, headers, buck["id"], "SOLD")
    body = await breeding_list(client, headers)
    assert body["active_buck_ids"] == []


async def test_active_buck_ids_exclude_females(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_doe(client, headers)
    body = await breeding_list(client, headers)
    assert body["active_buck_ids"] == []


async def test_breeding_list_records_newest_date_first(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe1, buck1, _br = await bred_doe(client, headers, "D-1", today() - timedelta(days=60))
    _doe2, _buck2, br2 = await bred_doe(client, headers, "D-2", today() - timedelta(days=10))
    body = await breeding_list(client, headers)
    assert [r["id"] for r in body["records"]] == [br2["id"], _br["id"]]
    assert {r["doe_id"] for r in body["records"]} == {doe1["id"], _doe2["id"]}
    assert buck1["id"] in body["active_buck_ids"]


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
    assert resp.status_code == 400


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


async def test_breeding_list_non_member_farm_forbidden(client: httpx.AsyncClient) -> None:
    other = await owner_with_farm(client, email="other@farm.in", farm_name="Beta Farm")
    stranger = await register(client, email="stranger@farm.in")
    resp = await client.get("/api/breeding", headers=stranger | {"X-Farm-Id": other["X-Farm-Id"]})
    assert resp.status_code == 403


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


async def test_create_breeding_explicit_heat_cycle_number(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers)
    buck = await make_buck(client, headers)
    br = await make_breeding(client, headers, doe["id"], buck["id"], heat_cycle_number=3)
    assert br["heat_cycle_number"] == 3


async def test_create_breeding_date_today(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers)
    buck = await make_buck(client, headers)
    br = await make_breeding(client, headers, doe["id"], buck["id"])
    assert br["breeding_date"] == iso(today())


async def test_create_breeding_date_far_past(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers)
    buck = await make_buck(client, headers)
    br = await make_breeding(client, headers, doe["id"], buck["id"], breeding_date="1990-01-01")
    assert br["breeding_date"] == "1990-01-01"
    assert br["ultrasound_date"] == "1990-02-02"


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
    doe, buck, br1 = await bred_doe(client, headers, breeding_date=today() - timedelta(days=42))
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


async def test_create_breeding_rebreed_after_kidding_full_cycle(client: httpx.AsyncClient) -> None:
    """Full reproductive cycle: kid, rest, breed again."""
    headers = await owner_with_farm(client)
    doe, buck, br = await pregnant_doe(client, headers, gestation_days=160)
    await kid_on_ekd(client, headers, br)
    doe_after = await get_animal(client, headers, doe["id"])
    assert doe_after["current_bucket"] == "RECOVERY"
    # Not eligible from RECOVERY
    resp = await post_breeding(client, headers, doe["id"], buck["id"])
    assert resp.status_code == 400
    # After the move to RESTING she is a breeding candidate again
    await move_to(client, headers, doe["id"], "RESTING")
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


async def test_create_breeding_heat_cycle_string(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers)
    buck = await make_buck(client, headers)
    resp = await post_breeding(client, headers, doe["id"], buck["id"], heat_cycle_number="two")
    assert resp.status_code == 422


async def test_create_breeding_extra_field_ignored(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers)
    buck = await make_buck(client, headers)
    resp = await post_breeding(client, headers, doe["id"], buck["id"], bogus_field="x")
    assert resp.status_code == 201, resp.text


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
    assert resp.status_code == 400


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


async def test_ultrasound_failed_ignores_kid_count(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await bred_doe(client, headers)
    resp = await ultrasound(client, headers, br["id"], pregnant=False, kid_count=3)
    assert resp.status_code == 200, resp.text
    assert resp.json()["kid_count_detected"] is None


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
    body = await breeding_list(client, headers)
    assert doe["id"] in body["candidate_doe_ids"]


async def test_ultrasound_pregnant_removes_doe_from_candidates(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await bred_doe(client, headers)
    await confirm(client, headers, br["id"])
    body = await breeding_list(client, headers)
    assert doe["id"] not in body["candidate_doe_ids"]


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
    resp = await client.post(f"/api/breeding/{br['id']}/abort", headers=headers)
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


async def test_success_between_failures_breaks_the_streak(client: httpx.AsyncClient) -> None:
    """fail → conceive → kid → fail: not consecutive, no cull flag."""
    headers = await owner_with_farm(client)
    doe, buck, br1 = await bred_doe(client, headers, breeding_date=today() - timedelta(days=400))
    await fail_cycle(client, headers, br1["id"])
    br2 = await make_breeding(
        client, headers, doe["id"], buck["id"], breeding_date=iso(today() - timedelta(days=160))
    )
    br2 = await confirm(client, headers, br2["id"])
    doe_mid = await get_animal(client, headers, doe["id"])
    assert doe_mid["cull_candidate"] is False
    await kid_on_ekd(client, headers, br2)
    await move_to(client, headers, doe["id"], "RESTING")
    br3 = await make_breeding(
        client, headers, doe["id"], buck["id"], breeding_date=iso(today() - timedelta(days=10))
    )
    await fail_cycle(client, headers, br3["id"])
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
    await confirm(client, headers, br2["id"])
    resp = await client.post(f"/api/breeding/{br2['id']}/abort", headers=headers)
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
    resp = await client.post(f"/api/breeding/{br['id']}/abort", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "ABORTED"
    assert body["pregnant"] is False
    doe_after = await get_animal(client, headers, doe["id"])
    assert doe_after["current_bucket"] == "RESTING"
    assert doe_after["is_currently_pregnant"] is False


async def test_abort_skips_open_pregnancy_tasks(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=35)
    resp = await client.post(f"/api/breeding/{br['id']}/abort", headers=headers)
    assert resp.status_code == 200, resp.text
    tasks = await all_tasks(client, headers)
    for category in ("VACCINE", "BUCKET_MOVE", "KIDDING_DUE"):
        statuses = [t["status"] for t in tasks_by_category(tasks, category)]
        assert statuses == ["SKIPPED"]


async def test_abort_makes_doe_candidate_again(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await pregnant_doe(client, headers)
    resp = await client.post(f"/api/breeding/{br['id']}/abort", headers=headers)
    assert resp.status_code == 200, resp.text
    body = await breeding_list(client, headers)
    assert doe["id"] in body["candidate_doe_ids"]


async def test_abort_on_pending_breeding(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await bred_doe(client, headers)
    resp = await client.post(f"/api/breeding/{br['id']}/abort", headers=headers)
    assert resp.status_code == 409


async def test_abort_on_failed_breeding(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await bred_doe(client, headers)
    await fail_cycle(client, headers, br["id"])
    resp = await client.post(f"/api/breeding/{br['id']}/abort", headers=headers)
    assert resp.status_code == 409


async def test_abort_after_kidding_recorded(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    await kid_on_ekd(client, headers, br)
    resp = await client.post(f"/api/breeding/{br['id']}/abort", headers=headers)
    assert resp.status_code == 409


async def test_abort_replay_conflict(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers)
    resp = await client.post(f"/api/breeding/{br['id']}/abort", headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await client.post(f"/api/breeding/{br['id']}/abort", headers=headers)
    assert resp.status_code == 409


async def test_abort_nonexistent_record(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.post("/api/breeding/999999/abort", headers=headers)
    assert resp.status_code == 404


async def test_abort_huge_id_no_500(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.post(f"/api/breeding/{2**63}/abort", headers=headers)
    assert resp.status_code == 404


async def test_abort_cross_farm_record(client: httpx.AsyncClient) -> None:
    headers_a = await owner_with_farm(client)
    headers_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    _doe, _buck, br_b = await pregnant_doe(client, headers_b, "D-B")
    resp = await client.post(f"/api/breeding/{br_b['id']}/abort", headers=headers_a)
    assert resp.status_code == 404


async def test_abort_requires_auth(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/breeding/1/abort")
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
    assert resp.status_code == 400


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


async def test_kidding_four_alive_birth_type_null(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    await kid_on_ekd(client, headers, br, kids=[{"sex": "M"}] * 4)
    born = [a for a in await list_animals(client, headers) if a["source"] == "BORN"]
    assert len(born) == 4
    assert [a["birth_type"] for a in born] == [None] * 4


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
    assert born[0]["birth_type"] == "SINGLE"  # only one alive kid


async def test_kidding_died_creates_no_animal(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    record = await kid_on_ekd(
        client,
        headers,
        br,
        kids=[{"sex": "M", "status": "DIED"}, {"sex": "F"}],
    )
    died = next(k for k in record["kids"] if k["status"] == "DIED")
    assert died["animal_id"] is None
    born = [a for a in await list_animals(client, headers) if a["source"] == "BORN"]
    assert len(born) == 1


async def test_kidding_all_stillborn_no_animals_doe_recovers(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    record = await kid_on_ekd(client, headers, br, kids=[{"sex": "M", "status": "STILLBORN"}])
    assert record["kids"][0]["animal_id"] is None
    born = [a for a in await list_animals(client, headers) if a["source"] == "BORN"]
    assert born == []
    doe_after = await get_animal(client, headers, doe["id"])
    assert doe_after["current_bucket"] == "RECOVERY"


async def test_kidding_auto_tags(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    record = await kid_on_ekd(client, headers, br, kids=[{"sex": "M"}, {"sex": "F"}])
    tags = [k["tag"] for k in record["kids"]]
    assert tags == ["D-1-K1", "D-1-K2"]
    born = {a["tag_number"] for a in await list_animals(client, headers) if a["source"] == "BORN"}
    assert born == {"D-1-K1", "D-1-K2"}


async def test_kidding_auto_tags_uniquified_on_second_kidding(client: httpx.AsyncClient) -> None:
    """A doe's second kidding must not crash on the '<doe>-K1' tag collision —
    the service uniquifies ('-2' suffix)."""
    headers = await owner_with_farm(client)
    doe, buck, br1 = await pregnant_doe(client, headers, "D-2ND", gestation_days=310)
    record1 = await kid_on_ekd(client, headers, br1, kids=[{"sex": "M"}, {"sex": "F"}])
    assert [k["tag"] for k in record1["kids"]] == ["D-2ND-K1", "D-2ND-K2"]
    await move_to(client, headers, doe["id"], "RESTING")
    br2 = await make_breeding(
        client,
        headers,
        doe["id"],
        buck["id"],
        breeding_date=iso(today() - timedelta(days=160)),
    )
    br2 = await confirm(client, headers, br2["id"])
    record2 = await kid_on_ekd(client, headers, br2, kids=[{"sex": "M"}, {"sex": "F"}])
    assert [k["tag"] for k in record2["kids"]] == ["D-2ND-K1-2", "D-2ND-K2-2"]
    tags = {a["tag_number"] for a in await list_animals(client, headers)}
    assert {"D-2ND-K1", "D-2ND-K2", "D-2ND-K1-2", "D-2ND-K2-2"} <= tags


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
    resp = await client.post(f"/api/breeding/{br['id']}/abort", headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await post_kidding(client, headers, br["id"])
    assert resp.status_code == 400


async def test_kidding_on_sold_doe_conflict(client: httpx.AsyncClient) -> None:
    """A sold doe must not 'deliver' new stock — service-level guard → 409."""
    headers = await owner_with_farm(client)
    doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    await set_status(client, headers, doe["id"], "SOLD")
    resp = await kid_on_ekd_raw(client, headers, br)
    assert resp.status_code == 409
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
    resp = await post_kidding(client, headers, br["id"], date=iso(today() + timedelta(days=1)))
    assert resp.status_code == 400


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


async def test_kidding_long_notes_accepted(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    notes = "x" * 10_000
    record = await kid_on_ekd(client, headers, br, notes=notes)
    assert record["notes"] == notes


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


async def test_kidding_extra_field_ignored(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    resp = await kid_on_ekd_raw(client, headers, br, bogus_field="x")
    assert resp.status_code == 201, resp.text


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
    assert resp.status_code == 400


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
    body = await breeding_list(client, headers)
    assert doe["id"] in body["candidate_doe_ids"]


# ---------------------------------------------------------------------------
# RBAC: breeding perms (VET) vs kidding perms (owner-only among presets)
# ---------------------------------------------------------------------------
async def test_vet_worker_can_view_and_manage_breeding(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner)
    buck = await make_buck(client, owner)
    vet = await worker_headers(client, owner, "VET", "vet@farm.in")
    resp = await client.get("/api/breeding", headers=vet)
    assert resp.status_code == 200, resp.text
    resp = await post_breeding(client, vet, doe["id"], buck["id"])
    assert resp.status_code == 201, resp.text
    resp = await ultrasound(client, vet, resp.json()["id"], pregnant=False, kid_count=None)
    assert resp.status_code == 200, resp.text


async def test_vet_worker_cannot_record_kidding(client: httpx.AsyncClient) -> None:
    """No preset role holds kidding.view/kidding.manage — owner-only."""
    owner = await owner_with_farm(client)
    _doe, _buck, br = await pregnant_doe(client, owner, gestation_days=160)
    vet = await worker_headers(client, owner, "VET", "vet@farm.in")
    resp = await client.get("/api/kidding", headers=vet)
    assert resp.status_code == 403
    resp = await kid_on_ekd_raw(client, vet, br)
    assert resp.status_code == 403


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
    resp = await client.post("/api/breeding/1/abort", headers=mover)
    assert resp.status_code == 403
