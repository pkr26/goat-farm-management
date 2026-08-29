"""Model-logic tests (one file per SPEC): expected kidding date, breeding-ready
check, conception rate, quarantine schedule generation — ported from v1
tests/test_logic.py to the async JSON API.

Pure domain functions (`app.models` / `recipe_for_animal`) are still unit-tested
directly, as in v1's Phase 1. The DB-backed flow tests (v1 Phases 2–3, sync
SQLite + direct service calls) now drive the API: Bearer JWT auth, X-Farm-Id
farm scoping, JSON responses instead of ORM rows.

Date adjustments vs v1 (API guards the old suite didn't have):
- POST /api/kidding rejects future dates, so breedings are backdated far
  enough that the expected kidding date (breeding + 150d) is already past.
- An auto-generated duty cannot be completed before its due date, so the
  weaning test backdates further to make the +60d weaning duty due.
"""

from datetime import date, datetime, timedelta
from itertools import pairwise

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import (
    SHIFT_SPLIT,
    Animal,
    AnimalSource,
    AnimalStatus,
    BreedingOutcome,
    BreedingRecord,
    Bucket,
    BucketMove,
    FeedingShift,
    PurchaseBatch,
    Sex,
    Task,
    TaskCategory,
    TaskStatus,
    WeightRecord,
    conception_rate,
    expected_kidding_date,
    planned_ultrasound_date,
    quarantine_schedule,
)
from app.services import recipe_for_animal
from app.utils import today, utcnow

from .conftest import owner_with_farm

# ---------------------------------------------------------------------------
# Shared setup helpers (API-driven)
# ---------------------------------------------------------------------------


async def make_doe(
    client: httpx.AsyncClient,
    headers: dict,
    tag: str = "D-101",
    bucket: str = "BREEDING",
    dob_days: int = 800,
    weight_kg: float | None = 26.0,
) -> dict:
    """Create a doe via POST /api/animals (entry weight creates a WeightRecord)."""
    payload: dict = {
        "tag_number": tag,
        "sex": "F",
        "source": "PURCHASED",
        "current_bucket": bucket,
        "date_of_birth": (today() - timedelta(days=dob_days)).isoformat(),
        "historical_import_reason": "Existing-herd test fixture",
    }
    if weight_kg is not None:
        payload["weight_kg"] = weight_kg
        payload["weight_date"] = payload["date_of_birth"]
    resp = await client.post("/api/animals", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def make_buck(client: httpx.AsyncClient, headers: dict, tag: str = "B-01") -> dict:
    dob = today() - timedelta(days=800)
    payload = {
        "tag_number": tag,
        "sex": "M",
        "source": "PURCHASED",
        "current_bucket": "BREEDING",
        "date_of_birth": dob.isoformat(),
        "weight_kg": 30.0,
        "weight_date": dob.isoformat(),
        "historical_import_reason": "Existing-herd test fixture",
    }
    resp = await client.post("/api/animals", json=payload, headers=headers)
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
            "breeding_date": breeding_date.isoformat(),
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def make_bred_doe(
    client: httpx.AsyncClient, headers: dict, breeding_date: date | None = None
) -> tuple[dict, dict, dict]:
    """Doe + buck + breeding record; returns (doe, buck, br)."""
    doe = await make_doe(client, headers)
    buck = await make_buck(client, headers)
    br = await make_breeding(
        client, headers, doe, buck, breeding_date or (today() - timedelta(days=35))
    )
    return doe, buck, br


async def submit_ultrasound(
    client: httpx.AsyncClient,
    headers: dict,
    breeding_id: int,
    pregnant: bool,
    kid_count: int = 2,
    result_date: date | None = None,
) -> dict:
    payload: dict[str, object] = {"pregnant": pregnant}
    if pregnant:
        payload["kid_count"] = kid_count
    if result_date is not None:
        payload["date"] = result_date.isoformat()
    else:
        # Default to the scheduled check rather than "today": a pregnancy
        # confirmed today cannot be followed by a kidding recorded on the
        # earlier expected kidding date.
        detail = await client.get(f"/api/breeding/{breeding_id}", headers=headers)
        assert detail.status_code == 200, detail.text
        payload["date"] = detail.json()["ultrasound_date"]
    resp = await client.post(
        f"/api/breeding/{breeding_id}/ultrasound",
        json=payload,
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


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
            "date": kidding_date.isoformat(),
            "ease": "NORMAL",
            "notes": "",
            "kids": kids,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def all_tasks(client: httpx.AsyncClient, headers: dict) -> list[dict]:
    """Every duty across the five tabs of GET /api/tasks."""
    resp = await client.get("/api/tasks", headers=headers)
    assert resp.status_code == 200, resp.text
    tabs = resp.json()
    return tabs["today"] + tabs["overdue"] + tabs["upcoming"] + tabs["awaiting"] + tabs["completed"]


def tasks_by_category(tasks: list[dict], category: str) -> list[dict]:
    return [t for t in tasks if t["category"] == category]


async def get_animal(client: httpx.AsyncClient, headers: dict, animal_id: int) -> dict:
    resp = await client.get(f"/api/animals/{animal_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["animal"]


async def find_animal_by_tag(client: httpx.AsyncClient, headers: dict, tag: str) -> dict:
    resp = await client.get("/api/animals", params={"q": tag}, headers=headers)
    assert resp.status_code == 200, resp.text
    animals = resp.json()["animals"]
    assert len(animals) == 1
    return animals[0]


async def complete_quarantine_prerequisites(
    client: httpx.AsyncClient, headers: dict, batch_id: int, tasks: list[dict]
) -> None:
    """Close each prerequisite through its matching auditable workflow."""
    for task in tasks:
        if task["category"] == "BUCKET_MOVE":
            continue
        if task["category"] in {"VACCINE", "DEWORMING"}:
            preview = await client.post(
                "/api/health/events/preview",
                json={"scope": "batch", "purchase_batch_id": batch_id},
                headers=headers,
            )
            assert preview.status_code == 200, preview.text
            response = await client.post(
                "/api/health/events",
                json={
                    "scope": "batch",
                    "purchase_batch_id": batch_id,
                    "expected_animal_ids": preview.json()["target_animal_ids"],
                    "type": task["category"],
                    "task_id": task["id"],
                },
                headers=headers,
            )
            assert response.status_code == 201, response.text
        else:
            response = await client.post(f"/api/tasks/{task['id']}/complete", headers=headers)
            assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Phase 1: pure domain-logic tests (unchanged from v1 — the functions live in
# app.models / app.services and need no database)
# ---------------------------------------------------------------------------


def make_doe_object(**overrides) -> Animal:
    """v1's make_doe: an in-memory (never persisted) breeding-ready doe."""
    dob = today() - timedelta(days=365)  # 12 months old
    animal = Animal(
        farm_id=1,
        tag_number="D-001",
        sex=Sex.F.value,
        source=AnimalSource.PURCHASED.value,
        status=AnimalStatus.ACTIVE.value,
        current_bucket=Bucket.FOUNDATION.value,
        date_of_birth=dob,
        breedings_as_doe=[],
        weight_records=[WeightRecord(date=today(), weight_kg=25.0)],
    )
    for key, value in overrides.items():
        setattr(animal, key, value)
    return animal


def test_expected_kidding_date_is_150_days() -> None:
    breeding_date = date(2026, 1, 10)
    assert expected_kidding_date(breeding_date) == date(2026, 6, 9)
    assert planned_ultrasound_date(breeding_date) == breeding_date + timedelta(days=32)


def test_breeding_ready_doe_passes() -> None:
    assert make_doe_object().is_breeding_ready is True


def test_breeding_ready_rejects_young_light_male_pregnant_wrong_bucket() -> None:
    # too young (6 months)
    assert make_doe_object(date_of_birth=today() - timedelta(days=180)).is_breeding_ready is False
    # too light (<22 kg)
    light = make_doe_object()
    light.weight_records = [WeightRecord(date=today(), weight_kg=20.0)]
    assert light.is_breeding_ready is False
    # male
    assert make_doe_object(sex=Sex.M.value).is_breeding_ready is False
    # currently pregnant
    pregnant = make_doe_object()
    pregnant.breedings_as_doe = [
        BreedingRecord(
            farm_id=1,
            doe_id=1,
            buck_id=2,
            breeding_date=today() - timedelta(days=60),
            outcome=BreedingOutcome.CONFIRMED_PREGNANT.value,
        )
    ]
    assert pregnant.is_breeding_ready is False
    # wrong bucket (already in BREEDING / pregnancy buckets)
    assert make_doe_object(current_bucket=Bucket.BREEDING.value).is_breeding_ready is False
    assert make_doe_object(current_bucket=Bucket.PREGNANCY_EARLY.value).is_breeding_ready is False
    # resting bucket is eligible
    assert make_doe_object(current_bucket=Bucket.RESTING.value).is_breeding_ready is True
    # not active
    assert make_doe_object(status=AnimalStatus.SOLD.value).is_breeding_ready is False


def test_age_months_uses_estimated_dob_when_no_dob() -> None:
    animal = make_doe_object(date_of_birth=None, estimated_dob=today() - timedelta(days=305))
    assert animal.age_months == 10


def test_conception_rate() -> None:
    records = [
        BreedingRecord(
            farm_id=1,
            doe_id=1,
            buck_id=2,
            breeding_date=today(),
            outcome=BreedingOutcome.CONFIRMED_PREGNANT.value,
        ),
        BreedingRecord(
            farm_id=1,
            doe_id=1,
            buck_id=2,
            breeding_date=today(),
            outcome=BreedingOutcome.CONFIRMED_PREGNANT.value,
        ),
        BreedingRecord(
            farm_id=1,
            doe_id=1,
            buck_id=2,
            breeding_date=today(),
            outcome=BreedingOutcome.FAILED.value,
        ),
        BreedingRecord(
            farm_id=1,
            doe_id=1,
            buck_id=2,
            breeding_date=today(),
            outcome=BreedingOutcome.PENDING.value,
        ),
    ]
    assert conception_rate(records) == 66.7  # 2 of 3 completed
    assert conception_rate([]) is None
    assert conception_rate([records[3]]) is None  # only pending


def test_quarantine_schedule_covers_45_days() -> None:
    batch = PurchaseBatch(farm_id=1, date=date(2026, 3, 1), supplier="Kurnool Traders", count=50)
    batch.id = 7
    schedule = quarantine_schedule(batch)
    assert len(schedule) == 8
    # Day 4 deworming is due 3 days after arrival (offset day N -> date + N-1)
    deworm = next(t for t in schedule if t["category"] == "DEWORMING")
    assert deworm["due_date"] == date(2026, 3, 4)
    # Day 45 footbath + release is the final task
    last = schedule[-1]
    assert last["due_date"] == date(2026, 4, 14)  # batch.date + 44
    assert "FOUNDATION" in last["title"]
    # Live viral vaccines separated by >= 10 days (PPR day 10, Goat Pox day 30)
    vaccines = [t for t in schedule if t["category"] == "VACCINE"]
    gaps = [(b["due_date"] - a["due_date"]).days for a, b in pairwise(vaccines)]
    assert all(g >= 10 for g in gaps)


def test_resting_flush_switch_at_day_10() -> None:
    """recipe_for_animal is pure python over the loaded object graph — the API
    cannot backdate BucketMove.moved_at, so this stays a model-level test (v1
    manipulated moved_at directly in the DB; here it is set in memory)."""
    animal = make_doe_object(
        tag_number="D-R1",
        current_bucket=Bucket.RESTING.value,
        date_of_birth=today() - timedelta(days=900),
    )
    move = BucketMove(animal_id=1, from_bucket=None, to_bucket=Bucket.RESTING.value, reason="test")
    animal.bucket_moves = [move]
    move.moved_at = datetime.combine(today() - timedelta(days=5), datetime.min.time())
    assert recipe_for_animal(animal) == "MAINTENANCE_75_25"
    move.moved_at = datetime.combine(today() - timedelta(days=12), datetime.min.time())
    assert recipe_for_animal(animal) == "FLUSH_70_30"


# ---------------------------------------------------------------------------
# Phase 2: API-backed flow tests
# ---------------------------------------------------------------------------


async def test_ultrasound_pregnant_creates_three_followup_tasks(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    breeding_date = today() - timedelta(days=35)
    doe, _buck, br = await make_bred_doe(client, headers, breeding_date)

    # breeding auto-created the ultrasound task at +32d
    tasks = await all_tasks(client, headers)
    us_tasks = tasks_by_category(tasks, "ULTRASOUND")
    assert len(us_tasks) == 1
    assert us_tasks[0]["due_date"] == br["ultrasound_date"]
    assert us_tasks[0]["due_date"] == (breeding_date + timedelta(days=32)).isoformat()
    assert us_tasks[0]["status"] == "PENDING"

    br = await submit_ultrasound(client, headers, br["id"], pregnant=True, kid_count=2)

    assert br["outcome"] == "CONFIRMED_PREGNANT"
    assert br["expected_kidding_date"] == expected_kidding_date(breeding_date).isoformat()
    doe_after = await get_animal(client, headers, doe["id"])
    assert doe_after["current_bucket"] == "PREGNANCY_EARLY"
    tasks = await all_tasks(client, headers)
    assert [t["status"] for t in tasks_by_category(tasks, "ULTRASOUND")] == ["DONE"]

    ekd = date.fromisoformat(br["expected_kidding_date"])
    vaccine = tasks_by_category(tasks, "VACCINE")
    moves = tasks_by_category(tasks, "BUCKET_MOVE")
    kidding = tasks_by_category(tasks, "KIDDING_DUE")
    assert [t["due_date"] for t in vaccine] == [(ekd - timedelta(days=40)).isoformat()]
    assert [t["due_date"] for t in moves] == [(ekd - timedelta(days=15)).isoformat()]
    assert [t["due_date"] for t in kidding] == [ekd.isoformat()]
    assert all(t["animal_id"] == doe["id"] for t in vaccine + moves + kidding)


async def test_two_failed_cycles_flag_cull_candidate(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, buck, br1 = await make_bred_doe(client, headers, today() - timedelta(days=120))
    br1 = await submit_ultrasound(
        client,
        headers,
        br1["id"],
        pregnant=False,
        result_date=today() - timedelta(days=80),
    )
    assert br1["outcome"] == "FAILED"
    doe_after = await get_animal(client, headers, doe["id"])
    assert doe_after["cull_candidate"] is False

    br2 = await make_breeding(client, headers, doe, buck, today() - timedelta(days=60))
    br2 = await submit_ultrasound(client, headers, br2["id"], pregnant=False)
    assert br2["outcome"] == "FAILED"
    doe_after = await get_animal(client, headers, doe["id"])
    assert doe_after["cull_candidate"] is True


async def test_kidding_creates_kid_animals_and_weaning_task(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    # bred 160 days ago → expected kidding date is 10 days in the past, so the
    # API's "kidding date cannot be in the future" guard lets us kid on the EKD.
    breeding_date = today() - timedelta(days=160)
    doe, buck, br = await make_bred_doe(client, headers, breeding_date)
    br = await submit_ultrasound(client, headers, br["id"], pregnant=True, kid_count=2)
    kidding_date = date.fromisoformat(br["expected_kidding_date"])

    kids = [
        {"tag": "K-201", "sex": "M", "birth_weight": 2.8, "status": "ALIVE"},
        {"tag": "K-202", "sex": "F", "birth_weight": 2.5, "status": "ALIVE"},
        {"tag": "", "sex": "M", "birth_weight": None, "status": "STILLBORN"},
    ]
    record = await record_kidding(client, headers, br, kidding_date, kids)
    assert record["doe_id"] == doe["id"]
    assert len(record["kids"]) == 3

    resp = await client.get("/api/animals", headers=headers)
    assert resp.status_code == 200, resp.text
    born = [a for a in resp.json()["animals"] if a["source"] == "BORN"]
    assert len(born) == 2  # stillborn does not create an Animal
    assert {a["tag_number"] for a in born} == {"K-201", "K-202"}
    assert all(a["dam_id"] == doe["id"] and a["sire_id"] == buck["id"] for a in born)
    assert all(a["current_bucket"] == "RECOVERY" for a in born)
    assert all(a["date_of_birth"] == kidding_date.isoformat() for a in born)
    triplets = [a for a in born if a["birth_type"] == "TRIPLET"]
    assert len(triplets) == 2  # litter size includes the stillborn delivery

    doe_after = await get_animal(client, headers, doe["id"])
    assert doe_after["current_bucket"] == "RECOVERY"

    tasks = await all_tasks(client, headers)
    weaning = tasks_by_category(tasks, "WEANING")
    assert len(weaning) == 1
    assert weaning[0]["due_date"] == (kidding_date + timedelta(days=60)).isoformat()
    assert weaning[0]["animal_id"] == doe["id"]


# Regression guard: record_kidding marks KIDDING_DUE DONE, then flushes before
# the "skip leftovers" PENDING query (sessions run autoflush=False) so the DONE
# row is not re-selected and clobbered to SKIPPED.
async def test_kidding_marks_kidding_due_task_done(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    breeding_date = today() - timedelta(days=160)
    _doe, _buck, br = await make_bred_doe(client, headers, breeding_date)
    br = await submit_ultrasound(client, headers, br["id"], pregnant=True, kid_count=1)
    kidding_date = date.fromisoformat(br["expected_kidding_date"])
    await record_kidding(
        client,
        headers,
        br,
        kidding_date,
        [{"tag": "K-401", "sex": "M", "birth_weight": 2.7, "status": "ALIVE"}],
    )
    tasks = await all_tasks(client, headers)
    # the KIDDING_DUE task is auto-completed
    assert tasks_by_category(tasks, "KIDDING_DUE")[0]["status"] == "DONE"


async def test_quarantine_batch_creates_45_day_task_set(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.post(
        "/api/purchases/new",
        json={
            "date": "2026-03-01",
            "supplier": "Kurnool Traders",
            "count": 3,
            "avg_age_months": 7,
            "avg_weight_kg": 15.0,
            "total_price": 30000.0,
            "notes": "",
            "create_animals": True,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    batch_id = resp.json()["id"]

    resp = await client.get(f"/api/purchases/{batch_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    detail = resp.json()
    stubbed = detail["animals"]
    assert len(stubbed) == 3
    tag_prefix = stubbed[0]["tag_number"].rsplit("-", 1)[0]
    assert tag_prefix.startswith(f"B{batch_id}-")
    assert len(tag_prefix.removeprefix(f"B{batch_id}-")) == 12
    assert [a["tag_number"] for a in stubbed] == [
        f"{tag_prefix}-0001",
        f"{tag_prefix}-0002",
        f"{tag_prefix}-0003",
    ]
    assert all(a["current_bucket"] == "QUARANTINE" for a in stubbed)
    assert all(a["purchase_price"] == 10000.0 for a in stubbed)  # total split evenly

    tasks = detail["tasks"]  # ordered by due_date
    assert len(tasks) == 8
    assert tasks[0]["due_date"] == "2026-03-01"  # days 1–3 rest
    assert tasks[-1]["category"] == "BUCKET_MOVE"
    assert tasks[-1]["due_date"] == "2026-04-14"

    await complete_quarantine_prerequisites(client, headers, batch_id, tasks)
    # The guarded release follows completed, recorded prerequisites.
    resp = await client.post(f"/api/tasks/{tasks[-1]['id']}/complete", headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await client.get(f"/api/purchases/{batch_id}", headers=headers)
    stubbed = resp.json()["animals"]
    assert all(a["current_bucket"] == "FOUNDATION" for a in stubbed)


async def test_weaning_completion_moves_kids_by_sex(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    # bred 220 days ago → kidded 70 days ago (on the EKD) → the +60d weaning
    # duty fell due 10 days ago and can actually be completed via the API.
    breeding_date = today() - timedelta(days=220)
    doe, _buck, br = await make_bred_doe(client, headers, breeding_date)
    br = await submit_ultrasound(client, headers, br["id"], pregnant=True, kid_count=2)
    kidding_date = date.fromisoformat(br["expected_kidding_date"])
    await record_kidding(
        client,
        headers,
        br,
        kidding_date,
        [
            {"tag": "K-301", "sex": "M", "birth_weight": 2.6, "status": "ALIVE"},
            {"tag": "K-302", "sex": "F", "birth_weight": 2.4, "status": "ALIVE"},
        ],
    )

    tasks = await all_tasks(client, headers)
    weaning = tasks_by_category(tasks, "WEANING")[0]
    resp = await client.post(f"/api/tasks/{weaning['id']}/complete", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "DONE"

    male = await find_animal_by_tag(client, headers, "K-301")
    female = await find_animal_by_tag(client, headers, "K-302")
    assert male["current_bucket"] == "MALE_KIDS"
    assert female["current_bucket"] == "FEMALE_KIDS"
    doe_after = await get_animal(client, headers, doe["id"])
    assert doe_after["current_bucket"] == "RESTING"


async def test_final_kid_death_leaves_a_closed_weaning_duty_alone(
    client: httpx.AsyncClient,
) -> None:
    """Replanning after the last kid dies may only touch PENDING weaning duties.

    The replan loop rewrites every row it is handed to SKIPPED without
    re-checking the status, so the PENDING filter in the helper query is its
    only guard: dropping it silently un-completes an already-DONE legacy
    weaning row (breeding_record_id NULL, due_date == kidding + 60d) that the
    due-date fallback also matches.
    """
    headers = await owner_with_farm(client)
    breeding_date = today() - timedelta(days=160)
    doe, _buck, br = await make_bred_doe(client, headers, breeding_date)
    br = await submit_ultrasound(client, headers, br["id"], pregnant=True, kid_count=1)
    kidding_date = date.fromisoformat(br["expected_kidding_date"])
    record = await record_kidding(
        client,
        headers,
        br,
        kidding_date,
        [{"tag": "K-601", "sex": "F", "birth_weight": 2.5, "status": "ALIVE"}],
    )
    kid_id = record["kids"][0]["animal_id"]

    # A pre-link legacy duty for this dam: no breeding link, day-60 due date
    # (so the fallback matches it), and already closed as DONE.
    async with get_sessionmaker()() as db:
        legacy = Task(
            farm_id=int(headers["X-Farm-Id"]),
            title="Legacy weaning plan",
            due_date=kidding_date + timedelta(days=60),
            category=TaskCategory.WEANING.value,
            animal_id=doe["id"],
            breeding_record_id=None,
            auto_generated=True,
            status=TaskStatus.DONE.value,
            completed_at=utcnow(),
        )
        db.add(legacy)
        await db.commit()
        legacy_id = legacy.id

    resp = await client.post(
        f"/api/animals/{kid_id}/status", json={"new_status": "DEAD"}, headers=headers
    )
    assert resp.status_code == 200, resp.text

    async with get_sessionmaker()() as db:
        stored = await db.get(Task, legacy_id)
        assert stored is not None
        assert stored.status == TaskStatus.DONE.value
        assert stored.skip_reason is None
        assert stored.skipped_at is None
        # …while the live duty for this litter is the one the replan cancels,
        # proving the replan path really ran against this dam.
        linked = (
            await db.execute(
                select(Task.status, Task.skip_reason).where(
                    Task.breeding_record_id == br["id"],
                    Task.category == TaskCategory.WEANING.value,
                )
            )
        ).all()
    assert [tuple(row) for row in linked] == [
        (TaskStatus.SKIPPED.value, "Final surviving kid died; weaning no longer applies")
    ]


async def test_purchase_batch_sex_defaults_female_and_male_stays_out_of_doe_lists(
    client: httpx.AsyncClient,
) -> None:
    """A bought buck batch must stub MALE animals — hardcoded sex="F" used to
    turn purchased bucks into breeding-candidate does."""
    headers = await owner_with_farm(client)
    batch_date = (today() - timedelta(days=50)).isoformat()  # all protocol tasks due
    resp = await client.post(
        "/api/purchases/new",
        json={"date": batch_date, "count": 2, "create_animals": True},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["sex"] == "F"
    default_batch_id = resp.json()["id"]
    resp = await client.post(
        "/api/purchases/new",
        json={"date": batch_date, "count": 2, "create_animals": True, "sex": "M"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["sex"] == "M"
    buck_batch_id = resp.json()["id"]

    resp = await client.get(f"/api/purchases/{default_batch_id}", headers=headers)
    assert {a["sex"] for a in resp.json()["animals"]} == {"F"}  # default unchanged
    resp = await client.get(f"/api/purchases/{buck_batch_id}", headers=headers)
    buck_stubs = resp.json()["animals"]
    assert {a["sex"] for a in buck_stubs} == {"M"}

    # Release the buck batch from quarantine (day-45 task) and confirm the
    # stubs remain male, but do not enter either readiness picker until they
    # have explicit mature-age and adult-weight evidence.
    detail = resp.json()
    release = next(t for t in detail["tasks"] if t["category"] == "BUCKET_MOVE")
    await complete_quarantine_prerequisites(client, headers, buck_batch_id, detail["tasks"])
    resp = await client.post(f"/api/tasks/{release['id']}/complete", headers=headers)
    assert resp.status_code == 200, resp.text
    assert all(
        a["current_bucket"] == "FOUNDATION"
        for a in (await client.get(f"/api/purchases/{buck_batch_id}", headers=headers)).json()[
            "animals"
        ]
    )
    stub_ids = {a["id"] for a in buck_stubs}
    for kind in ("doe", "buck"):
        resp = await client.get(
            "/api/breeding/candidates",
            params={"kind": kind},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert stub_ids.isdisjoint(row["id"] for row in resp.json()["candidates"])


async def test_purchase_batch_explicit_zero_price_books_zero_expense(
    client: httpx.AsyncClient,
) -> None:
    """An explicit ₹0 batch books a ₹0 ANIMAL_PURCHASE expense; omitting
    total_price books nothing (previously `if total_price:` skipped both)."""
    headers = await owner_with_farm(client)
    resp = await client.post(
        "/api/purchases/new",
        json={
            "date": today().isoformat(),
            "count": 2,
            "total_price": 0,
            "create_animals": False,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    resp = await client.get("/api/finance", headers=headers)
    purchases = [t for t in resp.json()["transactions"] if t["category"] == "ANIMAL_PURCHASE"]
    assert [t["amount"] for t in purchases] == [0.0]

    resp = await client.post(
        "/api/purchases/new",
        json={"date": today().isoformat(), "count": 1, "create_animals": False},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    resp = await client.get("/api/finance", headers=headers)
    purchases = [t for t in resp.json()["transactions"] if t["category"] == "ANIMAL_PURCHASE"]
    assert len(purchases) == 1  # still only the explicit-zero one


# ---------------------------------------------------------------------------
# Phase 3: feeding + finance tests
# ---------------------------------------------------------------------------


async def test_feeding_plan_split_math(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for i in range(4):
        await make_doe(
            client,
            headers,
            tag=f"D-{i}",
            bucket="BREEDING",
            dob_days=800,
            weight_kg=26.0,
        )
    resp = await client.get("/api/feeding/plan", headers=headers)
    assert resp.status_code == 200, resp.text
    plan = resp.json()["lines"]
    assert len(plan) == 1
    line = plan[0]
    assert line["heads"] == 4
    assert line["recipe_code"] == "MAINTENANCE_75_25"
    assert line["daily_kg"] == round(4 * line["kg_per_head"], 2)
    # 40 / 20 / 40 split, sums back to the daily total
    pcts = {s["shift"]: s["pct"] for s in line["shifts"]}
    assert pcts == {"MORNING": 40, "AFTERNOON": 20, "NIGHT": 40}
    shift_total = sum(s["kg"] for s in line["shifts"])
    assert abs(shift_total - line["daily_kg"]) < 0.05
    assert set(SHIFT_SPLIT) == set(FeedingShift)


async def _inventory_item(client: httpx.AsyncClient, headers: dict, ingredient: str) -> dict:
    resp = await client.get("/api/feeding/inventory", headers=headers)
    assert resp.status_code == 200, resp.text
    return next(i for i in resp.json() if i["ingredient"] == ingredient)


async def test_mix_batch_decrements_and_refuses_when_short(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    # Farm creation seeded zero-stock inventory rows; stock everything to
    # 100 kg except Mineral mix (1.0 kg) via the add-stock endpoint.
    resp = await client.get("/api/feeding/inventory", headers=headers)
    assert resp.status_code == 200, resp.text
    for item in resp.json():
        qty = 1.0 if item["ingredient"] == "Mineral mix" else 100.0
        resp = await client.post(
            f"/api/feeding/inventory/{item['id']}/add", json={"qty_kg": qty}, headers=headers
        )
        assert resp.status_code == 200, resp.text

    # 100 kg of FATTENING_50_50 needs 1.5 kg mineral mix — only 1.0 on hand
    resp = await client.post(
        "/api/feeding/mix",
        json={"recipe_code": "FATTENING_50_50", "batch_kg": 100.0},
        headers=headers,
    )
    assert resp.status_code == 400, resp.text
    assert "Mineral mix" in resp.json()["detail"]
    # refusal is all-or-nothing: green fodder untouched
    green = await _inventory_item(client, headers, "Super Napier green fodder")
    assert green["qty_on_hand"] == 100.0

    # top up and mix: every line decrements
    mineral = await _inventory_item(client, headers, "Mineral mix")
    resp = await client.post(
        f"/api/feeding/inventory/{mineral['id']}/add", json={"qty_kg": 9.0}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    resp = await client.post(
        "/api/feeding/mix",
        json={"recipe_code": "FATTENING_50_50", "batch_kg": 100.0},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    mineral = await _inventory_item(client, headers, "Mineral mix")
    assert mineral["qty_on_hand"] == 8.5
    green = await _inventory_item(client, headers, "Super Napier green fodder")
    assert green["qty_on_hand"] == 70.0  # 100 - 30

    # refusal is all-or-nothing: a 1000 kg mix is short on everything —
    # mineral and green fodder stay exactly where the successful mix left them
    resp = await client.post(
        "/api/feeding/mix",
        json={"recipe_code": "FATTENING_50_50", "batch_kg": 1000.0},
        headers=headers,
    )
    assert resp.status_code == 400, resp.text
    mineral = await _inventory_item(client, headers, "Mineral mix")
    assert mineral["qty_on_hand"] == 8.5
    green = await _inventory_item(client, headers, "Super Napier green fodder")
    assert green["qty_on_hand"] == 70.0


async def test_monthly_pnl_aggregation(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    current_month = today().replace(day=1)
    previous_month = (current_month - timedelta(days=1)).replace(day=1)
    txns = [
        (previous_month, "INCOME", "ANIMAL_SALE", 23000.0),
        (previous_month, "EXPENSE", "FEED", 8000.0),
        (previous_month, "EXPENSE", "VET", 1500.0),
        (current_month, "INCOME", "MANURE", 1500.0),
        (current_month, "EXPENSE", "LABOUR", 9000.0),
    ]
    for d, txn_type, category, amount in txns:
        resp = await client.post(
            "/api/finance/new",
            json={
                "date": d.isoformat(),
                "type": txn_type,
                "category": category,
                "amount": amount,
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text

    resp = await client.get("/api/finance", headers=headers)
    assert resp.status_code == 200, resp.text
    pnl = resp.json()["pnl"]
    assert len(pnl) == 12
    assert [r["month"] for r in pnl[:2]] == [
        current_month.strftime("%Y-%m"),
        previous_month.strftime("%Y-%m"),
    ]
    current, previous = pnl[0], pnl[1]
    assert (
        previous["income"] == 23000.0
        and previous["expense"] == 9500.0
        and previous["net"] == 13500.0
    )
    assert current["income"] == 1500.0
    assert current["expense"] == 9000.0
    assert current["net"] == -7500.0
    assert previous["categories"]["FEED"]["expense"] == 8000.0


# ---------------------------------------------------------------------------
# — a 0-month average age means newborn, not "unknown age"
# ---------------------------------------------------------------------------
# create_purchase_batch used `if avg_age_months`, so purchased newborn kids
# (avg 0 months) got estimated_dob=None instead of the batch date, breaking
# their age-based vaccine schedule.
async def test_purchase_batch_zero_age_months_sets_dob_to_batch_date(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    batch_date = today() - timedelta(days=5)
    resp = await client.post(
        "/api/purchases/new",
        json={
            "date": batch_date.isoformat(),
            "count": 2,
            "create_animals": True,
            "avg_age_months": 0,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    resp = await client.get(f"/api/purchases/{resp.json()['id']}", headers=headers)
    assert [a["estimated_dob"] for a in resp.json()["animals"]] == [batch_date.isoformat()] * 2


# ---------------------------------------------------------------------------
# — per-head purchase prices sum back to the booked total
# ---------------------------------------------------------------------------
# total/count rounded to 2dp per animal could drift from the ledger by up to
# count × ₹0.005; the first animal now absorbs the paise remainder (same
# trick as record_health_event's cost split).
async def test_purchase_batch_per_head_prices_sum_to_total(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.post(
        "/api/purchases/new",
        json={
            "date": today().isoformat(),
            "count": 3,
            "create_animals": True,
            "total_price": 1000,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    resp = await client.get(f"/api/purchases/{resp.json()['id']}", headers=headers)
    prices = [a["purchase_price"] for a in resp.json()["animals"]]
    assert prices == [333.34, 333.33, 333.33]
    assert round(sum(prices), 2) == 1000.00


async def test_purchase_batch_tiny_total_never_creates_a_negative_head_price(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    resp = await client.post(
        "/api/purchases/new",
        json={
            "date": today().isoformat(),
            "count": 4,
            "create_animals": True,
            "total_price": 0.02,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    detail = await client.get(f"/api/purchases/{resp.json()['id']}", headers=headers)
    prices = sorted(animal["purchase_price"] for animal in detail.json()["animals"])
    assert prices == [0.0, 0.0, 0.01, 0.01]
    assert round(sum(prices), 2) == 0.02
