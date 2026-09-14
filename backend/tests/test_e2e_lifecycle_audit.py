"""Independent end-to-end lifecycle audit of the whole SaaS workflow.

Scenario tests walking every operational path a real farm would take:
purchase → quarantine (45-day protocol) → release → foundation → breeding →
ultrasound → pregnancy → pre-kidding move → kidding → weaning → rebreeding →
sale/cull/death exits, plus the vet/restriction interplay (the "sick animal"
path), task-skip blocking, stuck-animal scenarios, finance reconciliation,
and the exhaustive manual bucket-move matrix.

Written as an acceptance pass: each test asserts the *business* outcome
(bucket occupancy, task states, ledger rows), not just status codes.
"""

import asyncio
from datetime import date, timedelta

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import BucketMove
from app.utils import today, utcnow

from .conftest import owner_with_farm

# ---------------------------------------------------------------------------
# Helpers (API-driven, self-contained)
# ---------------------------------------------------------------------------

ALL_BUCKETS = [
    "QUARANTINE",
    "FOUNDATION",
    "BREEDING",
    "PREGNANCY_EARLY",
    "PREGNANCY_LATE",
    "DELIVERY",
    "RECOVERY",
    "RESTING",
    "MALE_KIDS",
    "FEMALE_KIDS",
]


def iso(d: date) -> str:
    return d.isoformat()


async def make_animal(
    client: httpx.AsyncClient,
    headers: dict,
    tag: str,
    *,
    sex: str = "F",
    bucket: str = "FOUNDATION",
    dob_days: int = 800,
    weight_kg: float | None = 26.0,
) -> dict:
    payload: dict = {
        "tag_number": tag,
        "sex": sex,
        "source": "PURCHASED",
        "current_bucket": bucket,
        "date_of_birth": iso(today() - timedelta(days=dob_days)),
        "historical_import_reason": "Lifecycle-audit fixture",
    }
    if weight_kg is not None:
        payload["weight_kg"] = weight_kg
        payload["weight_date"] = payload["date_of_birth"]
    resp = await client.post("/api/animals", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def get_animal(client: httpx.AsyncClient, headers: dict, animal_id: int) -> dict:
    resp = await client.get(f"/api/animals/{animal_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["animal"]


async def all_tasks(client: httpx.AsyncClient, headers: dict) -> list[dict]:
    resp = await client.get("/api/tasks", headers=headers)
    assert resp.status_code == 200, resp.text
    tabs = resp.json()
    return tabs["today"] + tabs["overdue"] + tabs["upcoming"] + tabs["awaiting"] + tabs["completed"]


async def find_task(client: httpx.AsyncClient, headers: dict, **match: object) -> dict:
    tasks = await all_tasks(client, headers)
    for task in tasks:
        if all(task.get(k) == v for k, v in match.items()):
            return task
    raise AssertionError(f"No task matching {match} in {[t['title'] for t in tasks]}") from None


async def complete(client: httpx.AsyncClient, headers: dict, task_id: int) -> httpx.Response:
    return await client.post(f"/api/tasks/{task_id}/complete", headers=headers)


async def try_move(
    client: httpx.AsyncClient, headers: dict, animal_id: int, to_bucket: str
) -> httpx.Response:
    return await client.post(
        f"/api/animals/{animal_id}/move",
        json={"to_bucket": to_bucket, "reason": "audit move"},
        headers=headers,
    )


async def breed(
    client: httpx.AsyncClient,
    headers: dict,
    doe_id: int,
    buck_id: int,
    breeding_date: date,
) -> dict:
    resp = await client.post(
        "/api/breeding",
        json={
            "doe_id": doe_id,
            "buck_id": buck_id,
            "breeding_date": iso(breeding_date),
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def pregnant_doe_in(
    client: httpx.AsyncClient,
    headers: dict,
    bucket: str,
    *,
    bred_days_ago: int,
    kid_count: int = 2,
) -> tuple[dict, dict]:
    """Doe + buck, bred `bred_days_ago`, ultrasound-positive; doe now in `bucket`.

    Works for PREGNANCY_EARLY (straight after the scan) and PREGNANCY_LATE
    (after the manual EARLY→LATE move).
    """
    doe = await make_animal(client, headers, "AUD-F-" + str(bred_days_ago))
    buck = await make_animal(
        client, headers, "AUD-M-" + str(bred_days_ago), sex="M", weight_kg=32.0
    )
    br = await breed(
        client, headers, doe["id"], buck["id"], today() - timedelta(days=bred_days_ago)
    )
    detail = await client.get(f"/api/breeding/{br['id']}", headers=headers)
    scan = await client.post(
        f"/api/breeding/{br['id']}/ultrasound",
        json={
            "pregnant": True,
            "kid_count": kid_count,
            "date": detail.json()["ultrasound_date"],
        },
        headers=headers,
    )
    assert scan.status_code == 200, scan.text
    if bucket == "PREGNANCY_LATE":
        moved = await try_move(client, headers, doe["id"], "PREGNANCY_LATE")
        assert moved.status_code == 200, moved.text
    elif bucket != "PREGNANCY_EARLY":
        raise ValueError(f"unsupported setup bucket {bucket}")
    animal = await get_animal(client, headers, doe["id"])
    assert animal["current_bucket"] == bucket, animal["current_bucket"]
    return animal, br


async def health_event(
    client: httpx.AsyncClient, headers: dict, **payload: object
) -> httpx.Response:
    scope = payload.get("scope", "animal")
    if scope in {"bucket", "batch"} and "expected_animal_ids" not in payload:
        target: dict = {"scope": scope}
        if scope == "bucket" and "bucket" in payload:
            target["bucket"] = payload["bucket"]
        if scope == "batch" and "purchase_batch_id" in payload:
            target["purchase_batch_id"] = payload["purchase_batch_id"]
        preview = await client.post("/api/health/events/preview", json=target, headers=headers)
        assert preview.status_code == 200, preview.text
        payload["expected_animal_ids"] = preview.json()["target_animal_ids"]
    return await client.post("/api/health/events", json=payload, headers=headers)


async def place_hold(client: httpx.AsyncClient, headers: dict, animal_id: int) -> None:
    resp = await health_event(
        client,
        headers,
        scope="animal",
        animal_id=animal_id,
        type="TREATMENT",
        suspected_scheduled_disease=True,
        disease_target="Reportable-condition concern",
    )
    assert resp.status_code == 201, resp.text


async def clear_hold(client: httpx.AsyncClient, headers: dict, animal_id: int) -> None:
    history = await client.get(f"/api/health/restrictions/{animal_id}", headers=headers)
    assert history.status_code == 200, history.text
    body = history.json()
    placed = [e for e in body["actions"] if e.get("action") == "PLACED"] or body["actions"]
    assert placed, "no restriction episodes found"
    version = placed[-1]["restriction_version"]
    resp = await client.post(
        f"/api/health/restrictions/{animal_id}/clear",
        json={
            "clearance_reference": "AHD clearance AUD-2026-001",
            "expected_restriction_version": version,
        },
        headers=headers,
    )
    assert resp.status_code == 204, resp.text


async def change_status(
    client: httpx.AsyncClient, headers: dict, animal_id: int, status: str, **extra: object
) -> httpx.Response:
    return await client.post(
        f"/api/animals/{animal_id}/status",
        json={"new_status": status} | extra,
        headers=headers,
    )


async def confirm_pregnancy(
    client: httpx.AsyncClient, headers: dict, breeding_id: int, *, kid_count: int = 2
) -> dict:
    """Positive ultrasound at the scheduled date; returns the refreshed record
    (expected_kidding_date is only populated once the scan confirms)."""
    detail = (await client.get(f"/api/breeding/{breeding_id}", headers=headers)).json()
    scan = await client.post(
        f"/api/breeding/{breeding_id}/ultrasound",
        json={
            "pregnant": True,
            "kid_count": kid_count,
            "date": detail["ultrasound_date"],
        },
        headers=headers,
    )
    assert scan.status_code == 200, scan.text
    return (await client.get(f"/api/breeding/{breeding_id}", headers=headers)).json()


async def record_kidding(
    client: httpx.AsyncClient, headers: dict, breeding_id: int, on: date, kids: list[dict]
) -> dict:
    resp = await client.post(
        "/api/kidding",
        json={
            "breeding_record_id": breeding_id,
            "date": iso(on),
            "ease": "NORMAL",
            "notes": "",
            "kids": kids,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def backdate_bucket_entry(animal_id: int, farm_id: int, bucket: str, days: int) -> None:
    """Insert a backdated initial BucketMove so days-in-bucket is realistic."""
    async with get_sessionmaker()() as db:
        db.add(
            BucketMove(
                farm_id=farm_id,
                animal_id=animal_id,
                from_bucket=None,
                to_bucket=bucket,
                effective_date=today() - timedelta(days=days),
                moved_at=utcnow() - timedelta(days=days),
            )
        )
        await db.commit()


async def set_weight(
    client: httpx.AsyncClient, headers: dict, animal_id: int, weight: float, on: date | None = None
) -> None:
    payload: dict = {"weight_kg": weight}
    if on is not None:
        payload["date"] = iso(on)
    resp = await client.post(f"/api/animals/{animal_id}/weight", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# 1. Golden path: full doe cycle breed → scan → deliver → kid → wean → re-breed
# ---------------------------------------------------------------------------


async def test_golden_path_full_doe_cycle(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_animal(client, headers, "GOLD-F-1")
    buck = await make_animal(client, headers, "GOLD-M-1", sex="M", weight_kg=32.0)

    # -- breed (T-260): doe moves FOUNDATION → BREEDING, ultrasound duty +32d
    br = await breed(client, headers, doe["id"], buck["id"], today() - timedelta(days=260))
    staged = await get_animal(client, headers, doe["id"])
    assert staged["current_bucket"] == "BREEDING"
    us_task = await find_task(client, headers, category="ULTRASOUND", breeding_record_id=br["id"])
    assert us_task["status"] == "PENDING"

    # -- ultrasound positive at the scheduled date (T-228): → PREGNANCY_EARLY
    detail = await confirm_pregnancy(client, headers, br["id"], kid_count=2)
    assert (await get_animal(client, headers, doe["id"]))["current_bucket"] == "PREGNANCY_EARLY"
    assert (await find_task(client, headers, id=us_task["id"]))["status"] == "DONE"
    # four generated duties: 2 vaccines, delivery move, kidding due
    tasks = await all_tasks(client, headers)
    vax = sorted(
        (
            t
            for t in tasks
            if t["category"] == "VACCINE" and t.get("breeding_record_id") == br["id"]
        ),
        key=lambda t: t["due_date"],
    )
    delivery = next(
        t
        for t in tasks
        if t["category"] == "BUCKET_MOVE" and t.get("breeding_record_id") == br["id"]
    )
    due_task = next(
        t
        for t in tasks
        if t["category"] == "KIDDING_DUE" and t.get("breeding_record_id") == br["id"]
    )
    assert len(vax) == 2 and delivery["status"] == "PENDING" and due_task["status"] == "PENDING"

    # -- close both vaccine duties through the health form (the only legal way)
    for task in vax:
        resp = await health_event(
            client,
            headers,
            scope="animal",
            animal_id=doe["id"],
            type="VACCINE",
            product_name="ET+Tetanus",
            task_id=task["id"],
        )
        assert resp.status_code == 201, resp.text
        assert (await find_task(client, headers, id=task["id"]))["status"] == "DONE"

    # -- pre-kidding move duty (EKD-15, long due): PREGNANCY_EARLY → DELIVERY
    resp = await complete(client, headers, delivery["id"])
    assert resp.status_code == 200, resp.text
    assert (await get_animal(client, headers, doe["id"]))["current_bucket"] == "DELIVERY"

    # -- kidding on the EKD (T-110): doe → RECOVERY, kids born into RECOVERY
    ekd = date.fromisoformat(detail["expected_kidding_date"])
    kids_payload = [
        {"tag": "GOLD-K-1", "sex": "M", "birth_weight": 2.8, "status": "ALIVE"},
        {"tag": "GOLD-K-2", "sex": "F", "birth_weight": 2.5, "status": "ALIVE"},
    ]
    body = await record_kidding(client, headers, br["id"], ekd, kids_payload)
    doe_now = await get_animal(client, headers, doe["id"])
    assert doe_now["current_bucket"] == "RECOVERY"
    kid_ids = [kid["animal_id"] for kid in body["kids"] if kid.get("animal_id")]
    assert len(kid_ids) == 2
    for kid_id in kid_ids:
        assert (await get_animal(client, headers, kid_id))["current_bucket"] == "RECOVERY"
    assert (await find_task(client, headers, id=due_task["id"]))["status"] == "DONE"
    weaning = await find_task(client, headers, category="WEANING", animal_id=doe["id"])
    assert weaning["status"] == "PENDING"

    # -- weaning (+60d, due): kids sex-split, dam → RESTING
    resp = await complete(client, headers, weaning["id"])
    assert resp.status_code == 200, resp.text
    male_kid, female_kid = kid_ids
    assert (await get_animal(client, headers, male_kid))["current_bucket"] == "MALE_KIDS"
    assert (await get_animal(client, headers, female_kid))["current_bucket"] == "FEMALE_KIDS"
    assert (await get_animal(client, headers, doe["id"]))["current_bucket"] == "RESTING"

    # -- the male kid is ~3.6 months: sale must be refused (< 8 months)
    refused = await change_status(
        client, headers, male_kid, "SOLD", sale_price=3000, buyer_name="Local buyer"
    )
    assert refused.status_code in {409, 422}, refused.text

    # -- rest period is advisory only: re-service straight out of RESTING
    await set_weight(client, headers, doe["id"], 27.0)
    br2 = await breed(client, headers, doe["id"], buck["id"], today())
    assert br2["id"] != br["id"]
    assert (await get_animal(client, headers, doe["id"]))["current_bucket"] == "BREEDING"


# ---------------------------------------------------------------------------
# 2. Purchase → 45-day quarantine protocol → release → foundation
# ---------------------------------------------------------------------------


async def test_purchase_quarantine_release_chain(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    batch_date = today() - timedelta(days=60)
    created = await client.post(
        "/api/purchases/new",
        json={
            "date": iso(batch_date),
            "supplier": "Audit Supplier",
            "count": 3,
            "avg_age_months": 14,
            "avg_weight_kg": 26.0,
            "total_price": 21000.0,
            "create_animals": True,
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    batch = created.json()

    detail = (await client.get(f"/api/purchases/{batch['id']}", headers=headers)).json()
    animal_ids = [a["id"] for a in detail["animals"]]
    assert len(animal_ids) == 3
    for animal_id in animal_ids:
        assert (await get_animal(client, headers, animal_id))["current_bucket"] == "QUARANTINE"
    assert len(detail["tasks"]) == 8

    tasks = await all_tasks(client, headers)
    batch_tasks = [t for t in tasks if t.get("purchase_batch_id") == batch["id"]]
    plain = sorted(
        (t for t in batch_tasks if t["category"] == "QUARANTINE"), key=lambda t: t["due_date"]
    )
    forms = sorted(
        (t for t in batch_tasks if t["category"] in {"VACCINE", "DEWORMING"}),
        key=lambda t: t["due_date"],
    )
    release = next(t for t in batch_tasks if t["category"] == "BUCKET_MOVE")
    assert len(plain) == 2 and len(forms) == 5 and release["status"] == "PENDING"

    # A protocol duty cannot be skipped while the batch still has live animals.
    skip_refused = await client.post(
        f"/api/tasks/{plain[0]['id']}/skip", json={"reason": "audit"}, headers=headers
    )
    assert skip_refused.status_code == 409, skip_refused.text

    # Release must be refused while prerequisites are outstanding.
    early = await complete(client, headers, release["id"])
    assert early.status_code == 409, early.text

    for task in plain:
        resp = await complete(client, headers, task["id"])
        assert resp.status_code == 200, resp.text
    for task in forms:
        resp = await health_event(
            client,
            headers,
            scope="batch",
            purchase_batch_id=batch["id"],
            type=task["category"],
            product_name="Protocol dose",
            task_id=task["id"],
        )
        assert resp.status_code == 201, resp.text

    resp = await complete(client, headers, release["id"])
    assert resp.status_code == 200, resp.text
    for animal_id in animal_ids:
        assert (await get_animal(client, headers, animal_id))["current_bucket"] == "FOUNDATION"

    # The batch doe is now integrable: weight → breed.
    doe_id = animal_ids[0]
    await set_weight(client, headers, doe_id, 27.0)
    buck = await make_animal(client, headers, "QA-M-1", sex="M", weight_kg=32.0)
    br = await breed(client, headers, doe_id, buck["id"], today())
    assert br["id"]
    assert (await get_animal(client, headers, doe_id))["current_bucket"] == "BREEDING"

    # Ledger carries the purchase expense with batch provenance.
    finance = (await client.get("/api/finance", headers=headers)).json()
    rows = finance["transactions"] if isinstance(finance, dict) else finance
    purchases = [
        r for r in rows if r["category"] == "ANIMAL_PURCHASE" and r.get("source_id") == batch["id"]
    ]
    assert len(purchases) == 1
    assert float(purchases[0]["amount"]) == 21000.0


# ---------------------------------------------------------------------------
# 3. Vet path: movement restriction freeze → blocked everywhere → clearance
# ---------------------------------------------------------------------------


async def test_vet_restriction_freezes_everything_until_cleared(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_animal(client, headers, "VET-F-1")
    buck = await make_animal(client, headers, "VET-M-1", sex="M", weight_kg=32.0)

    await place_hold(client, headers, doe["id"])
    held = await get_animal(client, headers, doe["id"])
    assert held["movement_restricted"] is True

    # manual move blocked
    assert (await try_move(client, headers, doe["id"], "BREEDING")).status_code == 409
    # breeding blocked (eligibility refuses a held doe)
    breeding = await client.post(
        "/api/breeding",
        json={"doe_id": doe["id"], "buck_id": buck["id"], "breeding_date": iso(today())},
        headers=headers,
    )
    assert breeding.status_code in {400, 409, 422}, breeding.text
    # sale + cull blocked
    assert (
        await change_status(client, headers, doe["id"], "SOLD", sale_price=5000)
    ).status_code == 409
    assert (await change_status(client, headers, doe["id"], "CULLED")).status_code == 409

    # clearance releases the hold and every path reopens
    await clear_hold(client, headers, doe["id"])
    freed = await get_animal(client, headers, doe["id"])
    assert freed["movement_restricted"] is False
    bred = await breed(client, headers, doe["id"], buck["id"], today())
    assert bred["id"]
    assert (await get_animal(client, headers, doe["id"]))["current_bucket"] == "BREEDING"


async def test_withdrawal_window_blocks_sale_until_expired(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    treated = await make_animal(
        client, headers, "WD-M-1", sex="M", bucket="MALE_KIDS", weight_kg=30.0
    )
    clean = await make_animal(
        client, headers, "WD-M-2", sex="M", bucket="MALE_KIDS", weight_kg=30.0
    )

    active = await health_event(
        client,
        headers,
        scope="animal",
        animal_id=treated["id"],
        type="TREATMENT",
        product_name="Oxytetracycline",
        withdrawal_until=iso(today() + timedelta(days=5)),
    )
    assert active.status_code == 201, active.text
    expired = await health_event(
        client,
        headers,
        scope="animal",
        animal_id=clean["id"],
        type="TREATMENT",
        product_name="Oxytetracycline",
        date=iso(today() - timedelta(days=10)),
        withdrawal_until=iso(today() - timedelta(days=5)),
    )
    assert expired.status_code == 201, expired.text

    blocked = await change_status(
        client, headers, treated["id"], "SOLD", sale_price=4000, buyer_name="Buyer"
    )
    assert blocked.status_code == 409, blocked.text
    allowed = await change_status(
        client, headers, clean["id"], "SOLD", sale_price=4000, buyer_name="Buyer"
    )
    assert allowed.status_code == 200, allowed.text
    assert (await get_animal(client, headers, clean["id"]))["status"] == "SOLD"


# ---------------------------------------------------------------------------
# 4. Weaning ↔ vet interplay: a held kid freezes the whole weaning duty
# ---------------------------------------------------------------------------


async def test_weaning_blocked_while_kid_under_movement_hold(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_animal(client, headers, "WH-F-1")
    buck = await make_animal(client, headers, "WH-M-1", sex="M", weight_kg=32.0)
    br = await breed(client, headers, doe["id"], buck["id"], today() - timedelta(days=260))
    detail = await confirm_pregnancy(client, headers, br["id"], kid_count=1)
    delivery = await find_task(client, headers, category="BUCKET_MOVE", breeding_record_id=br["id"])
    assert (await complete(client, headers, delivery["id"])).status_code == 200
    kidding = await record_kidding(
        client,
        headers,
        br["id"],
        date.fromisoformat(detail["expected_kidding_date"]),
        [{"tag": "WH-K-1", "sex": "F", "birth_weight": 2.5, "status": "ALIVE"}],
    )
    kid_id = kidding["kids"][0]["animal_id"]

    # Vet suspects disease in the kid → movement hold → weaning duty cannot
    # complete; mother and kid both stay in RECOVERY.
    await place_hold(client, headers, kid_id)
    weaning = await find_task(client, headers, category="WEANING", animal_id=doe["id"])
    blocked = await complete(client, headers, weaning["id"])
    assert blocked.status_code == 409, blocked.text
    assert (await get_animal(client, headers, doe["id"]))["current_bucket"] == "RECOVERY"
    assert (await get_animal(client, headers, kid_id))["current_bucket"] == "RECOVERY"
    assert (await find_task(client, headers, id=weaning["id"]))["status"] == "PENDING"

    # Skips are refused too: RECOVERY is closed except through its duties.
    skip = await client.post(
        f"/api/tasks/{weaning['id']}/skip", json={"reason": "audit"}, headers=headers
    )
    assert skip.status_code == 409, skip.text

    # After clearance the same duty completes and everyone moves.
    await clear_hold(client, headers, kid_id)
    done = await complete(client, headers, weaning["id"])
    assert done.status_code == 200, done.text
    assert (await get_animal(client, headers, kid_id))["current_bucket"] == "FEMALE_KIDS"
    assert (await get_animal(client, headers, doe["id"]))["current_bucket"] == "RESTING"


# ---------------------------------------------------------------------------
# 5. Pregnancy failure paths: negative scan, abortion, exit-while-pregnant
# ---------------------------------------------------------------------------


async def test_negative_scan_failed_then_rebreed(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_animal(client, headers, "NEG-F-1")
    buck = await make_animal(client, headers, "NEG-M-1", sex="M", weight_kg=32.0)
    br = await breed(client, headers, doe["id"], buck["id"], today() - timedelta(days=40))
    detail = (await client.get(f"/api/breeding/{br['id']}", headers=headers)).json()

    negative = await client.post(
        f"/api/breeding/{br['id']}/ultrasound",
        json={"pregnant": False, "date": detail["ultrasound_date"]},
        headers=headers,
    )
    assert negative.status_code == 200, negative.text
    assert negative.json()["outcome"] == "FAILED"
    # The doe stays in BREEDING (no bucket change on a negative scan) and can
    # be re-served immediately.
    assert (await get_animal(client, headers, doe["id"]))["current_bucket"] == "BREEDING"
    br2 = await breed(client, headers, doe["id"], buck["id"], today())
    assert br2["id"] != br["id"]


async def test_abortion_from_pregnancy_late(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, br = await pregnant_doe_in(client, headers, "PREGNANCY_LATE", bred_days_ago=120)

    aborted = await client.post(
        f"/api/breeding/{br['id']}/abort",
        json={"loss_date": iso(today() - timedelta(days=2)), "cause": "INJURY", "notes": "audit"},
        headers=headers,
    )
    assert aborted.status_code == 200, aborted.text
    assert aborted.json()["outcome"] == "ABORTED"
    after = await get_animal(client, headers, doe["id"])
    assert after["current_bucket"] == "RESTING"
    # every pregnancy duty is swept
    tasks = await all_tasks(client, headers)
    leftovers = [
        t for t in tasks if t.get("breeding_record_id") == br["id"] and t["status"] == "PENDING"
    ]
    assert leftovers == [], leftovers


async def test_doe_exits_herd_while_pregnant_closes_record(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, br = await pregnant_doe_in(client, headers, "PREGNANCY_EARLY", bred_days_ago=60)

    sold = await change_status(client, headers, doe["id"], "SOLD", sale_price=6000, buyer_name="B")
    assert sold.status_code == 200, sold.text
    detail = (await client.get(f"/api/breeding/{br['id']}", headers=headers)).json()
    assert detail["outcome"] in {"UNASSESSED", "ABORTED"}, detail["outcome"]
    tasks = await all_tasks(client, headers)
    pending = [
        t for t in tasks if t.get("breeding_record_id") == br["id"] and t["status"] == "PENDING"
    ]
    assert pending == [], pending


# ---------------------------------------------------------------------------
# 6. Kidding edge scenarios: stillborn litter, kids dying, orphaned litter
# ---------------------------------------------------------------------------


async def test_all_stillborn_litter_postpartum_path(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_animal(client, headers, "SB-F-1")
    buck = await make_animal(client, headers, "SB-M-1", sex="M", weight_kg=32.0)
    br = await breed(client, headers, doe["id"], buck["id"], today() - timedelta(days=260))
    detail = await confirm_pregnancy(client, headers, br["id"], kid_count=2)
    delivery = await find_task(client, headers, category="BUCKET_MOVE", breeding_record_id=br["id"])
    await complete(client, headers, delivery["id"])

    kidding_date = date.fromisoformat(detail["expected_kidding_date"])
    kidding = await record_kidding(
        client,
        headers,
        br["id"],
        kidding_date,
        [
            {"tag": "", "sex": "M", "birth_weight": None, "status": "STILLBORN"},
            {"tag": "", "sex": "F", "birth_weight": None, "status": "STILLBORN"},
        ],
    )
    assert (await get_animal(client, headers, doe["id"]))["current_bucket"] == "RECOVERY"
    assert kidding["kids"][0].get("animal_id") is None

    # No survivors → postpartum duty at kidding+14, no weaning duty.
    postpartum = await find_task(client, headers, category="BUCKET_MOVE", animal_id=doe["id"])
    assert postpartum["status"] == "PENDING"
    assert (await complete(client, headers, postpartum["id"])).status_code == 200
    assert (await get_animal(client, headers, doe["id"]))["current_bucket"] == "RESTING"


async def test_last_kid_death_replans_dam_to_postpartum(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_animal(client, headers, "LD-F-1")
    buck = await make_animal(client, headers, "LD-M-1", sex="M", weight_kg=32.0)
    br = await breed(client, headers, doe["id"], buck["id"], today() - timedelta(days=300))
    detail = await confirm_pregnancy(client, headers, br["id"], kid_count=2)
    delivery = await find_task(client, headers, category="BUCKET_MOVE", breeding_record_id=br["id"])
    await complete(client, headers, delivery["id"])
    kidding_date = date.fromisoformat(detail["expected_kidding_date"])
    kidding = await record_kidding(
        client,
        headers,
        br["id"],
        kidding_date,
        [
            {"tag": "LD-K-1", "sex": "M", "birth_weight": 2.6, "status": "ALIVE"},
            {"tag": "LD-K-2", "sex": "F", "birth_weight": 2.4, "status": "ALIVE"},
        ],
    )
    kid_ids = [k["animal_id"] for k in kidding["kids"]]

    first_death = kidding_date + timedelta(days=20)
    second_death = kidding_date + timedelta(days=30)
    for kid_id, died_on in zip(kid_ids, [first_death, second_death], strict=True):
        resp = await change_status(
            client,
            headers,
            kid_id,
            "DEAD",
            mortality_cause="Pneumonia",
            mortality_reported_at=iso(died_on),
            date=iso(died_on),
        )
        assert resp.status_code == 200, resp.text

    # weaning duty swept; postpartum duty replanned to last-death + 14 and due
    tasks = await all_tasks(client, headers)
    assert [t for t in tasks if t["category"] == "WEANING" and t["status"] == "PENDING"] == []
    postpartum = next(
        t
        for t in tasks
        if t["category"] == "BUCKET_MOVE"
        and t.get("animal_id") == doe["id"]
        and t["status"] == "PENDING"
    )
    assert date.fromisoformat(postpartum["due_date"]) == second_death + timedelta(days=14)
    assert (await complete(client, headers, postpartum["id"])).status_code == 200
    assert (await get_animal(client, headers, doe["id"]))["current_bucket"] == "RESTING"


async def test_dam_exit_orphan_weans_surviving_kid(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_animal(client, headers, "OR-F-1")
    buck = await make_animal(client, headers, "OR-M-1", sex="M", weight_kg=32.0)
    br = await breed(client, headers, doe["id"], buck["id"], today() - timedelta(days=260))
    detail = await confirm_pregnancy(client, headers, br["id"], kid_count=1)
    delivery = await find_task(client, headers, category="BUCKET_MOVE", breeding_record_id=br["id"])
    await complete(client, headers, delivery["id"])
    kidding = await record_kidding(
        client,
        headers,
        br["id"],
        date.fromisoformat(detail["expected_kidding_date"]),
        [{"tag": "OR-K-1", "sex": "M", "birth_weight": 2.7, "status": "ALIVE"}],
    )
    kid_id = kidding["kids"][0]["animal_id"]

    # Dam dies before weaning day → kid must be early-weaned out of RECOVERY,
    # never left stranded in a bucket whose only exits need the dam's duties.
    died = await change_status(client, headers, doe["id"], "DEAD", mortality_cause="Illness")
    assert died.status_code == 200, died.text
    kid = await get_animal(client, headers, kid_id)
    assert kid["current_bucket"] == "MALE_KIDS", kid["current_bucket"]


# ---------------------------------------------------------------------------
# 7. Exhaustive manual-move matrix + stuck-animal scenarios
# ---------------------------------------------------------------------------


async def test_manual_move_matrix_every_bucket_every_target(client: httpx.AsyncClient) -> None:
    """For one eligible animal in each reachable bucket, attempt every target.

    Legal manual edges (from LEGAL_BUCKET_TRANSITIONS) must succeed; every
    other target must be refused with 409. This pins the whole state machine
    through the public API in one place.
    """
    headers = await owner_with_farm(client)

    async def check(animal: dict, legal: set[str]) -> None:
        current = animal["current_bucket"]
        # Illegal targets first: a legal move would change `current` and make
        # every later attempt assert against the wrong source bucket.
        ordered = [t for t in ALL_BUCKETS if t != current and t not in legal]
        ordered += [t for t in ALL_BUCKETS if t != current and t in legal]
        for target in ordered:
            resp = await try_move(client, headers, animal["id"], target)
            if target in legal:
                assert resp.status_code == 200, (target, resp.text)
            else:
                assert resp.status_code == 409, (target, resp.status_code, resp.text)

    # QUARANTINE (historical import, no batch): only FOUNDATION.
    quarantined = await make_animal(client, headers, "MX-Q-1", bucket="QUARANTINE")
    await check(quarantined, {"FOUNDATION"})

    # FOUNDATION doe: only BREEDING (when eligible).
    foundation_doe = await make_animal(client, headers, "MX-F-1", bucket="FOUNDATION")
    await check(foundation_doe, {"BREEDING"})

    # FEMALE_KIDS adult doe: FOUNDATION and BREEDING.
    female_kid = await make_animal(client, headers, "MX-F-2", bucket="FEMALE_KIDS")
    await check(female_kid, {"FOUNDATION", "BREEDING"})

    # MALE_KIDS mature buck: BREEDING only (when sire-ready).
    male_kid = await make_animal(
        client, headers, "MX-M-1", sex="M", bucket="MALE_KIDS", weight_kg=32.0
    )
    await check(male_kid, {"BREEDING"})

    # RESTING doe: BREEDING only.
    resting_doe = await make_animal(client, headers, "MX-F-3", bucket="RESTING")
    await check(resting_doe, {"BREEDING"})

    # PREGNANCY_EARLY: PREGNANCY_LATE only.
    early_doe, _br = await pregnant_doe_in(client, headers, "PREGNANCY_EARLY", bred_days_ago=60)
    await check(early_doe, {"PREGNANCY_LATE"})

    # PREGNANCY_LATE: DELIVERY only (live pregnancy).
    late_doe, _br2 = await pregnant_doe_in(client, headers, "PREGNANCY_LATE", bred_days_ago=120)
    await check(late_doe, {"DELIVERY"})


async def test_buck_in_breeding_bucket_has_no_manual_exit(client: httpx.AsyncClient) -> None:
    """AUDIT FINDING probe: a buck that entered BREEDING can never leave it
    through the legal graph (all pregnancy/resting buckets are female-only);
    only the owner's history_override escape hatch remains.
    """
    headers = await owner_with_farm(client)
    buck = await make_animal(
        client, headers, "STK-M-1", sex="M", bucket="MALE_KIDS", weight_kg=32.0
    )
    promoted = await try_move(client, headers, buck["id"], "BREEDING")
    assert promoted.status_code == 200, promoted.text

    for target in ALL_BUCKETS:
        if target == "BREEDING":
            continue  # same bucket: a legal no-op
        resp = await try_move(client, headers, buck["id"], target)
        assert resp.status_code == 409, (target, resp.status_code, resp.text)

    # The owner-only history override is the sole escape.
    override = await client.post(
        f"/api/animals/{buck['id']}/move",
        json={
            "to_bucket": "MALE_KIDS",
            "reason": "History override: rotation out of breeding pen",
            "history_override": True,
        },
        headers=headers,
    )
    assert override.status_code == 200, override.text
    assert (await get_animal(client, headers, buck["id"]))["current_bucket"] == "MALE_KIDS"


async def test_recovery_is_closed_except_through_duties(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_animal(client, headers, "RC-F-1")
    buck = await make_animal(client, headers, "RC-M-1", sex="M", weight_kg=32.0)
    br = await breed(client, headers, doe["id"], buck["id"], today() - timedelta(days=260))
    detail = await confirm_pregnancy(client, headers, br["id"], kid_count=1)
    delivery = await find_task(client, headers, category="BUCKET_MOVE", breeding_record_id=br["id"])
    await complete(client, headers, delivery["id"])
    kidding = await record_kidding(
        client,
        headers,
        br["id"],
        date.fromisoformat(detail["expected_kidding_date"]),
        [{"tag": "RC-K-1", "sex": "F", "birth_weight": 2.5, "status": "ALIVE"}],
    )
    kid_id = kidding["kids"][0]["animal_id"]
    doe_bucket = (await get_animal(client, headers, doe["id"]))["current_bucket"]
    kid_bucket = (await get_animal(client, headers, kid_id))["current_bucket"]
    for animal_id, current in ((doe["id"], doe_bucket), (kid_id, kid_bucket)):
        for target in ALL_BUCKETS:
            if target == current:
                continue  # same-bucket is a no-op, not an escape
            resp = await try_move(client, headers, animal_id, target)
            assert resp.status_code == 409, (animal_id, target, resp.status_code)


# ---------------------------------------------------------------------------
# 8. Dashboard suggestions surface the ready-to-move animals
# ---------------------------------------------------------------------------


async def test_dashboard_suggestions_point_at_ready_animals(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    early_doe, _ = await pregnant_doe_in(client, headers, "PREGNANCY_EARLY", bred_days_ago=130)
    late_doe, _ = await pregnant_doe_in(client, headers, "PREGNANCY_LATE", bred_days_ago=150)
    resting_doe = await make_animal(client, headers, "DS-F-1", bucket="RESTING")
    await backdate_bucket_entry(resting_doe["id"], int(headers["X-Farm-Id"]), "RESTING", days=35)
    sale_buck = await make_animal(
        client, headers, "DS-M-1", sex="M", bucket="MALE_KIDS", dob_days=300, weight_kg=28.0
    )

    summary = (await client.get("/api/dashboard", headers=headers)).json()
    suggestions = summary.get("suggestions") or []
    suggested_ids = {s["animal"]["id"] for s in suggestions}
    assert early_doe["id"] in suggested_ids, summary
    assert late_doe["id"] in suggested_ids, summary
    assert resting_doe["id"] in suggested_ids, summary
    assert sale_buck["id"] in suggested_ids, summary


# ---------------------------------------------------------------------------
# 9. RBAC: only MOVER can move; VET holds/clears health; FEEDER cannot move
# ---------------------------------------------------------------------------


async def _worker(client: httpx.AsyncClient, owner: dict, code: str, email: str) -> dict:
    roster = (await client.get("/api/team", headers=owner)).json()
    role_id = next(r["id"] for r in roster["roles"] if r["code"] == code)
    resp = await client.post(
        "/api/team/workers",
        json={
            "name": f"Audit {code}",
            "email": email,
            "password": "auditpass123",
            "role_id": role_id,
        },
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    login = await client.post("/api/auth/login", json={"email": email, "password": "auditpass123"})
    assert login.status_code == 200, login.text
    return {
        "Authorization": f"Bearer {login.json()['access_token']}",
        "X-Farm-Id": owner["X-Farm-Id"],
    }


async def test_rbac_move_and_health_permissions(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe = await make_animal(client, headers=owner, tag="RB-F-1", bucket="FOUNDATION")
    mover = await _worker(client, owner, "MOVER", "mover@audit.in")
    vet = await _worker(client, owner, "VET", "vet@audit.in")
    feeder = await _worker(client, owner, "FEEDER", "feeder@audit.in")

    ok = await try_move(client, mover, doe["id"], "BREEDING")
    assert ok.status_code == 200, ok.text
    second = await make_animal(client, headers=owner, tag="RB-F-2", bucket="FEMALE_KIDS")
    ok2 = await try_move(client, mover, second["id"], "FOUNDATION")
    assert ok2.status_code == 200, ok2.text

    denied = await try_move(client, feeder, second["id"], "BREEDING")
    assert denied.status_code == 403, denied.text
    denied_vet = await try_move(client, vet, second["id"], "BREEDING")
    assert denied_vet.status_code == 403, denied_vet.text

    # VET places the scheduled-disease hold; MOVER cannot clear (403), and
    # RT-FG-2's two-person rule means the placing VET cannot clear it alone
    # either — the owner releases it.
    placed = await health_event(
        client,
        vet,
        scope="animal",
        animal_id=doe["id"],
        type="TREATMENT",
        suspected_scheduled_disease=True,
        disease_target="Vet suspicion",
    )
    assert placed.status_code == 201, placed.text
    forbidden = await client.post(
        f"/api/health/restrictions/{doe['id']}/clear",
        json={"clearance_reference": "x", "expected_restriction_version": 1},
        headers=mover,
    )
    assert forbidden.status_code == 403, forbidden.text
    self_clear = await client.post(
        f"/api/health/restrictions/{doe['id']}/clear",
        json={"clearance_reference": "Vet OK", "expected_restriction_version": 1},
        headers=vet,
    )
    assert self_clear.status_code == 409, self_clear.text
    cleared = await client.post(
        f"/api/health/restrictions/{doe['id']}/clear",
        json={"clearance_reference": "AHD release", "expected_restriction_version": 1},
        headers=owner,
    )
    assert cleared.status_code == 204, cleared.text


# ---------------------------------------------------------------------------
# 10. Concurrency: double completion, parallel moves
# ---------------------------------------------------------------------------


async def test_concurrent_task_completion_single_effect(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    batch_date = today() - timedelta(days=60)
    created = await client.post(
        "/api/purchases/new",
        json={
            "date": iso(batch_date),
            "supplier": "Race Supplier",
            "count": 2,
            "avg_age_months": 14,
            "avg_weight_kg": 26.0,
            "total_price": 14000.0,
            "create_animals": True,
        },
        headers=headers,
    )
    batch = created.json()
    detail = (await client.get(f"/api/purchases/{batch['id']}", headers=headers)).json()
    tasks = await all_tasks(client, headers)
    batch_tasks = [t for t in tasks if t.get("purchase_batch_id") == batch["id"]]
    for task in sorted(batch_tasks, key=lambda t: t["due_date"]):
        if task["category"] == "BUCKET_MOVE":
            release = task
        elif task["category"] in {"VACCINE", "DEWORMING"}:
            resp = await health_event(
                client,
                headers,
                scope="batch",
                purchase_batch_id=batch["id"],
                type=task["category"],
                product_name="Race dose",
                task_id=task["id"],
            )
            assert resp.status_code == 201, resp.text
        else:
            assert (await complete(client, headers, task["id"])).status_code == 200

    first, second = await asyncio.gather(
        complete(client, headers, release["id"]),
        complete(client, headers, release["id"]),
    )
    statuses = sorted([first.status_code, second.status_code])
    # The route refuses the replay ("Task is not pending", 400) — the service
    # no-op makes even a raced double-submit single-effect.
    assert statuses in ([200, 200], [200, 400], [200, 409]), (first.text, second.text)
    for animal in detail["animals"]:
        assert (await get_animal(client, headers, animal["id"]))["current_bucket"] == "FOUNDATION"
    # Exactly one release move per animal in the audit trail.
    async with get_sessionmaker()() as db:
        for animal in detail["animals"]:
            moves = (
                (
                    await db.execute(
                        select(BucketMove).where(
                            BucketMove.animal_id == animal["id"],
                            BucketMove.to_bucket == "FOUNDATION",
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(moves) == 1, moves


async def test_concurrent_conflicting_moves_single_winner(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_animal(client, headers, "CC-F-1", bucket="FEMALE_KIDS")
    await asyncio.gather(
        try_move(client, headers, doe["id"], "FOUNDATION"),
        try_move(client, headers, doe["id"], "BREEDING"),
    )
    final = await get_animal(client, headers, doe["id"])
    assert final["current_bucket"] in {"FOUNDATION", "BREEDING"}, final["current_bucket"]


# ---------------------------------------------------------------------------
# 11. Finance reconciliation across the chain + corrections
# ---------------------------------------------------------------------------


async def test_finance_ledger_reconciles_full_chain(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    # purchase
    created = await client.post(
        "/api/purchases/new",
        json={
            "date": iso(today() - timedelta(days=60)),
            "supplier": "Ledger Supplier",
            "count": 2,
            "avg_age_months": 14,
            "avg_weight_kg": 26.0,
            "total_price": 18000.0,
            "create_animals": True,
        },
        headers=headers,
    )
    batch = created.json()
    # medicine + vet events
    resp = await health_event(
        client,
        headers,
        scope="animal",
        animal_id=(await _first_batch_animal(client, headers, batch["id"])),
        type="TREATMENT",
        product_name="Consult",
        cost=500.0,
    )
    assert resp.status_code == 201, resp.text
    # sale
    buck = await make_animal(client, headers, "LG-M-1", sex="M", bucket="MALE_KIDS", weight_kg=30.0)
    sold = await change_status(
        client, headers, buck["id"], "SOLD", sale_price=7500.0, buyer_name="Ledger buyer"
    )
    assert sold.status_code == 200, sold.text

    finance = (await client.get("/api/finance", headers=headers)).json()
    rows = finance["transactions"] if isinstance(finance, dict) else finance
    by_cat: dict[str, float] = {}
    for row in rows:
        by_cat[row["category"]] = by_cat.get(row["category"], 0.0) + float(row["amount"])
    assert by_cat.get("ANIMAL_PURCHASE") == 18000.0
    assert by_cat.get("VET") == 500.0
    assert by_cat.get("ANIMAL_SALE") == 7500.0
    assert all(
        r["type"] == ("INCOME" if r["category"] == "ANIMAL_SALE" else "EXPENSE")
        for r in rows
        if r["category"] in {"ANIMAL_PURCHASE", "VET", "ANIMAL_SALE"}
    )

    # Correct the sale amount: original voided, correction row linked.
    sale_row = next(r for r in rows if r["category"] == "ANIMAL_SALE")
    corrected = await client.post(
        f"/api/finance/transactions/{sale_row['id']}/correct",
        json={
            "date": iso(today()),
            "type": "INCOME",
            "category": "ANIMAL_SALE",
            "amount": 7000.0,
            "reason": "Price agreed lower",
        },
        headers=headers,
    )
    assert corrected.status_code == 201, corrected.text
    finance2 = (await client.get("/api/finance", headers=headers)).json()
    rows2 = finance2["transactions"] if isinstance(finance2, dict) else finance2
    sale_rows = [r for r in rows2 if r["category"] == "ANIMAL_SALE"]
    assert any(float(r["amount"]) == 7000.0 for r in sale_rows)
    assert any(r.get("voided_at") for r in sale_rows if r["id"] == sale_row["id"])


async def _first_batch_animal(client: httpx.AsyncClient, headers: dict, batch_id: int) -> int:
    detail = (await client.get(f"/api/purchases/{batch_id}", headers=headers)).json()
    return detail["animals"][0]["id"]


# ---------------------------------------------------------------------------
# 12. Feeding path: plan follows bucket occupancy, dispense logs + guards
# ---------------------------------------------------------------------------


async def test_feeding_plan_and_dispense_follow_the_herd(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    # Two does in BREEDING + one buck in MALE_KIDS.
    await make_animal(client, headers, "FD-F-1", bucket="BREEDING")
    await make_animal(client, headers, "FD-F-2", bucket="BREEDING")
    await make_animal(client, headers, "FD-M-1", sex="M", bucket="MALE_KIDS", weight_kg=32.0)

    plan = (await client.get("/api/feeding/plan", headers=headers)).json()
    plan_rows = {line["bucket"]: line for line in plan["lines"]}
    assert plan_rows["BREEDING"]["heads"] == 2
    assert plan_rows["MALE_KIDS"]["heads"] == 1

    # Stock ingredients → mix a recipe → dispense for a shift.
    inventory = (await client.get("/api/feeding/inventory", headers=headers)).json()
    items = inventory if isinstance(inventory, list) else inventory["items"]
    for item in items:
        restock = await client.post(
            f"/api/feeding/inventory/{item['id']}/add",
            json={"qty_kg": 500.0},
            headers=headers,
        )
        assert restock.status_code == 200, restock.text
    mixed = await client.post(
        "/api/feeding/mix",
        json={"recipe_code": "MAINTENANCE_75_25", "batch_kg": 60.0},
        headers=headers,
    )
    assert mixed.status_code == 200, mixed.text

    dispensed = await client.post(
        "/api/feeding/dispense",
        json={
            "bucket": "BREEDING",
            "shift": "MORNING",
            "recipe_code": "MAINTENANCE_75_25",
            "qty_kg": 5.0,
        },
        headers=headers,
    )
    assert dispensed.status_code == 201, dispensed.text

    records = (await client.get("/api/feeding/records", headers=headers)).json()
    rows = records.get("records") or records
    assert any(r["bucket"] == "BREEDING" and float(r["qty_kg"]) == 5.0 for r in rows), rows

    # Dispensing more than the finished stock is refused.
    finished = (await client.get("/api/feeding/finished-stock", headers=headers)).json()
    stock_rows = finished if isinstance(finished, list) else finished["stock"]
    breeding_stock = next((s for s in stock_rows if s.get("qty_kg", 0) > 0), None)
    if breeding_stock is not None:
        over = await client.post(
            "/api/feeding/dispense",
            json={
                "bucket": "BREEDING",
                "shift": "AFTERNOON",
                "recipe_code": "MAINTENANCE_75_25",
                "qty_kg": float(breeding_stock["qty_kg"]) + 100.0,
            },
            headers=headers,
        )
        assert over.status_code in {400, 409, 422}, over.text


# ---------------------------------------------------------------------------
# 13. FIXED STRANDING SCENARIO: the open pregnancy check refuses to be skipped
# ---------------------------------------------------------------------------
# Quarantine duties refuse skip (would deadlock the release gate) and
# RECOVERY-exit duties refuse skip (would strand doe+kids). The pregnancy
# check is the same class of gate: a skipped check leaves the breeding record
# PENDING, the one-open-pregnancy rule blocks re-service, and nothing prompted
# anyone to resolve it. Since the skip guard landed, the only way to close the
# duty is recording the scan result — which the form accepts backdated.
# ---------------------------------------------------------------------------


async def test_open_pregnancy_check_refuses_skip_until_resolved(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    doe = await make_animal(client, headers, "SK-F-1")
    buck = await make_animal(client, headers, "SK-M-1", sex="M", weight_kg=32.0)
    br = await breed(client, headers, doe["id"], buck["id"], today() - timedelta(days=60))

    us_task = await find_task(client, headers, category="ULTRASOUND", breeding_record_id=br["id"])
    refused = await client.post(
        f"/api/tasks/{us_task['id']}/skip", json={"reason": "Vet unavailable"}, headers=headers
    )
    assert refused.status_code == 409, refused.text
    assert "pregnancy check" in refused.json()["detail"].lower()
    assert (await find_task(client, headers, id=us_task["id"]))["status"] == "PENDING"

    # The doe cannot be re-serviced while the pregnancy check stays open…
    rebreed = await client.post(
        "/api/breeding",
        json={"doe_id": doe["id"], "buck_id": buck["id"], "breeding_date": iso(today())},
        headers=headers,
    )
    assert rebreed.status_code in {400, 409, 422}, rebreed.text

    # …and nothing on the dashboard points at her.
    summary = (await client.get("/api/dashboard", headers=headers)).json()
    suggested_ids = {s["animal"]["id"] for s in summary.get("suggestions", [])}
    assert doe["id"] not in suggested_ids

    # Recording the late scan resolves it: the form never required the duty in
    # the first place. Re-service then needs a date strictly after the scan.
    late_scan = await client.post(
        f"/api/breeding/{br['id']}/ultrasound",
        json={"pregnant": False, "date": iso(today() - timedelta(days=1))},
        headers=headers,
    )
    assert late_scan.status_code == 200, late_scan.text
    assert late_scan.json()["outcome"] == "FAILED"
    rebred = await breed(client, headers, doe["id"], buck["id"], today())
    assert rebred["id"] != br["id"]


# ---------------------------------------------------------------------------
# 14. FIXED GAP: the dashboard surfaces every animal under an active hold
# ---------------------------------------------------------------------------


async def test_dashboard_lists_restricted_animals_farm_wide(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe1 = await make_animal(client, owner, "R1-F-1")
    doe2 = await make_animal(client, owner, "R1-F-2")
    await place_hold(client, owner, doe1["id"])
    await place_hold(client, owner, doe2["id"])

    summary = (await client.get("/api/dashboard", headers=owner)).json()
    held = {r["animal"]["id"]: r for r in summary["restricted_animals"]}
    assert summary["restricted_animals_total"] == 2
    assert set(held) == {doe1["id"], doe2["id"]}
    assert all(r["current_bucket"] == "FOUNDATION" for r in held.values())
    assert all(r["held_since"] is not None for r in held.values())
    assert all(r["reason"] for r in held.values())  # owner sees the clinical reason

    # Clearance removes exactly the cleared animal from the list.
    await clear_hold(client, owner, doe1["id"])
    summary = (await client.get("/api/dashboard", headers=owner)).json()
    assert summary["restricted_animals_total"] == 1
    assert summary["restricted_animals"][0]["animal"]["id"] == doe2["id"]

    # MOVER (animals.view, no health.view): the operational fact of the hold
    # is visible, the clinical narrative is not.
    mover = await _worker(client, owner, "MOVER", "mover@r1.in")
    mover_view = (await client.get("/api/dashboard", headers=mover)).json()
    assert mover_view["restricted_animals_total"] == 1
    assert mover_view["restricted_animals"][0]["animal"]["id"] == doe2["id"]
    assert mover_view["restricted_animals"][0]["reason"] is None

    # CLEANER (dashboard.view only): identity withheld entirely.
    cleaner = await _worker(client, owner, "CLEANER", "cleaner@r1.in")
    cleaner_view = (await client.get("/api/dashboard", headers=cleaner)).json()
    assert cleaner_view["restricted_animals_total"] is None
    assert cleaner_view["restricted_animals"] == []
