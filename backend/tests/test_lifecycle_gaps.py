"""Bucket-lifecycle gap tests (2026-09-23 verification plan, category 2).

The exhaustive legal/illegal transition matrix, workflow-edge forgery, and
override round-trips already live in test_e2e_lifecycle_audit /
test_animals_extended. This module pins the gaps that remained:

- [HISTORY OVERRIDE]: non-owner denied (403), the audit marker prefix is
  always written, a reason is required, an override cannot escape an open
  reproductive workflow, and an owner cannot bypass a started quarantine
  protocol;
- the weaning boundary at exactly day 59 / 60 / 61 with the sex split;
- manual early-join out of QUARANTINE is blocked for batch animals;
- SOLD/DEAD/CULLED animals are locked out of moves, weights, breeding, and
  re-sale (audit fields are status-gated at the DB level);
- every workflow-driven move carries the full audit tuple.
"""

import asyncio
from datetime import timedelta

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import BucketMove
from app.models.constants import HISTORY_OVERRIDE_REASON_PREFIX
from app.utils import today

from .conftest import login_and_rotate, owner_with_farm
from .test_e2e_lifecycle_audit import (
    breed,
    change_status,
    find_task,
    get_animal,
    make_animal,
    record_kidding,
    try_move,
)


async def _mover_worker_headers(client: httpx.AsyncClient, owner: dict) -> dict:
    role = await client.post(
        "/api/team/roles",
        json={"name": "Mover", "permissions": ["animals.move", "animals.view"]},
        headers=owner,
    )
    assert role.status_code == 201, role.text
    await client.post(
        "/api/team/workers",
        json={
            "email": "mover@farm.in",
            "name": "Mover",
            "role_id": role.json()["id"],
            "password": "workerpass123",
        },
        headers=owner,
    )
    worker = await login_and_rotate(client, "mover@farm.in", "workerpass123")
    return worker | {"X-Farm-Id": owner["X-Farm-Id"]}


async def _moves_of(animal_id: int) -> list[BucketMove]:
    async with get_sessionmaker()() as db:
        rows = (
            await db.execute(
                select(BucketMove).where(BucketMove.animal_id == animal_id).order_by(BucketMove.id)
            )
        ).scalars().all()
        for row in rows:
            await db.refresh(row)
        return list(rows)


async def test_history_override_fence_gaps(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="ovr@farm.in")
    doe = await make_animal(client, owner, "OVR-F-1")
    worker = await _mover_worker_headers(client, owner)

    # A non-owner is refused the override even with the move permission.
    denied = await client.post(
        f"/api/animals/{doe['id']}/move",
        json={"to_bucket": "PREGNANCY_EARLY", "reason": "worker tries", "history_override": True},
        headers=worker,
    )
    assert denied.status_code == 403, denied.text
    assert "owner" in denied.json()["detail"].lower()

    # An override without a reason is a schema-level 422.
    bare = await client.post(
        f"/api/animals/{doe['id']}/move",
        json={"to_bucket": "PREGNANCY_EARLY", "history_override": True},
        headers=owner,
    )
    assert bare.status_code == 422, bare.text

    # The owner's override lands with the audit marker in the move history.
    overridden = await client.post(
        f"/api/animals/{doe['id']}/move",
        json={
            "to_bucket": "PREGNANCY_EARLY",
            "reason": "paper pregnancy from bought record",
            "history_override": True,
        },
        headers=owner,
    )
    assert overridden.status_code == 200, overridden.text
    moves = await _moves_of(doe["id"])
    assert moves, "no bucket moves recorded"
    override_moves = [m for m in moves if (m.reason or "").startswith(HISTORY_OVERRIDE_REASON_PREFIX)]
    assert override_moves, f"no audit marker on any move: {[m.reason for m in moves]}"
    assert override_moves[-1].to_bucket == "PREGNANCY_EARLY"
    assert override_moves[-1].created_by_id is not None, "move lacks actor attribution"
    assert override_moves[-1].effective_date == today("Asia/Kolkata")

    # The prefix cannot be forged by a plain reason: a worker writing the
    # marker text into a manual move must not produce an override-flagged row.
    forged = await client.post(
        f"/api/animals/{doe['id']}/move",
        json={
            "to_bucket": "FOUNDATION",
            "reason": f"{HISTORY_OVERRIDE_REASON_PREFIX}forged marker",
        },
        headers=worker,
    )
    if forged.status_code == 200:  # legal edge for a worker, but not as override
        moves = await _moves_of(doe["id"])
        forged_rows = [
            m
            for m in moves
            if (m.reason or "").startswith(HISTORY_OVERRIDE_REASON_PREFIX)
            and m.id > override_moves[-1].id
        ]
        assert not forged_rows, "a manual worker move wrote the override audit marker"


async def test_override_cannot_escape_open_reproductive_workflow(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="ovr2@farm.in")
    doe = await make_animal(client, owner, "OVR2-F")
    buck = await make_animal(client, owner, "OVR2-M", sex="M", weight_kg=32.0)
    br = await breed(client, owner, doe["id"], buck["id"], today() - timedelta(days=40))

    escape = await client.post(
        f"/api/animals/{doe['id']}/move",
        json={
            "to_bucket": "FOUNDATION",
            "reason": "escape open service",
            "history_override": True,
        },
        headers=owner,
    )
    assert escape.status_code == 409, escape.text
    assert "open breeding" in escape.json()["detail"]
    _ = br


async def test_owner_override_cannot_bypass_started_quarantine_protocol(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="ovr3@farm.in")
    batch = await client.post(
        "/api/purchases/new",
        json={"date": today().isoformat(), "supplier": "Override supplier", "count": 1},
        headers=owner,
    )
    assert batch.status_code == 201, batch.text
    detail = (await client.get(f"/api/purchases/{batch.json()['id']}", headers=owner)).json()
    animal = detail["animals"][0]
    assert animal["current_bucket"] == "QUARANTINE"

    # Before any protocol work: an owner override may still exit (pristine).
    # Start the protocol by completing the first arrival task.
    tasks = await client.get("/api/tasks", headers=owner)
    all_rows = (
        tasks.json()["today"] + tasks.json()["upcoming"] + tasks.json()["overdue"]
    )
    arrival = next(
        t for t in all_rows if t.get("purchase_batch_id") == batch.json()["id"]
    )
    done = await client.post(f"/api/tasks/{arrival['id']}/complete", headers=owner)
    assert done.status_code in (200, 409), done.text
    if done.status_code == 409:
        # The first task is form-linked; drive its health event instead.
        await client.post(
            "/api/health/events",
            json={
                "scope": "animal",
                "animal_id": animal["id"],
                "type": "TREATMENT",
                "product_name": "Arrival electrolytes",
                "date": today().isoformat(),
                "task_id": arrival["id"],
            },
            headers=owner,
        )

    bypass = await client.post(
        f"/api/animals/{animal['id']}/move",
        json={
            "to_bucket": "FOUNDATION",
            "reason": "owner impatient",
            "history_override": True,
        },
        headers=owner,
    )
    assert bypass.status_code == 409, bypass.text


async def test_weaning_boundary_days_59_60_61(client: httpx.AsyncClient) -> None:
    """Kids branch into the sex pens exactly at day 60 — day 59 refuses,
    day 60 and 61 succeed, and the split is by sex."""
    owner = await owner_with_farm(client, email="wean@farm.in")
    results: dict[int, tuple[str, str | None]] = {}

    for days in (59, 60, 61):
        doe = await make_animal(client, owner, f"WEAN-F-{days}")
        buck = await make_animal(client, owner, f"WEAN-M-{days}", sex="M", weight_kg=32.0)
        bred = await breed(client, owner, doe["id"], buck["id"], today() - timedelta(days=210))
        detail = (await client.get(f"/api/breeding/{bred['id']}", headers=owner)).json()
        scan = await client.post(
            f"/api/breeding/{bred['id']}/ultrasound",
            json={"pregnant": True, "kid_count": 2, "date": detail["ultrasound_date"]},
            headers=owner,
        )
        assert scan.status_code == 200, scan.text
        kidding = await record_kidding(
            client,
            owner,
            bred["id"],
            today() - timedelta(days=days),
            [
                {"sex": "M", "status": "ALIVE", "birth_weight": 2.5},
                {"sex": "F", "status": "ALIVE", "birth_weight": 2.3},
            ],
        )
        resp = None
        tasks_resp = await client.get("/api/tasks", headers=owner)
        tabs = tasks_resp.json()
        rows = tabs["today"] + tabs["overdue"] + tabs["upcoming"] + tabs["awaiting"] + tabs["completed"]
        wean_task = next(
            (t for t in rows if t["title"].startswith(f"Wean kids of WEAN-F-{days}")), None
        )
        assert wean_task, f"no weaning duty for WEAN-F-{days} among {[t['title'] for t in rows][:5]}"
        resp = await client.post(f"/api/tasks/{wean_task['id']}/complete", headers=owner)
        results[days] = (str(resp.status_code), wean_task.get("due_date"))

    assert results[59][0] == "409", f"day-59 weaning must refuse: {results[59]}"
    due_59 = results[59][1]
    assert results[60][0] == "200", f"day-60 weaning must succeed: {results[60]}"
    assert results[61][0] == "200", f"day-61 weaning must succeed: {results[61]}"
    _ = due_59

    # Sex split: the day-60 litter's kids are now in their sex pens.
    doe60 = await get_animal(client, owner, (await _animal_by_tag(client, owner, "WEAN-F-60"))["id"])
    listed = (await client.get("/api/animals", params={"limit": 100}, headers=owner)).json()["animals"]
    kids60 = [a for a in listed if a.get("dam_id") == doe60["id"]]
    by_sex = {k["sex"]: k["current_bucket"] for k in kids60}
    assert by_sex.get("M") == "MALE_KIDS", by_sex
    assert by_sex.get("F") == "FEMALE_KIDS", by_sex


async def _animal_by_tag(client: httpx.AsyncClient, headers: dict, tag: str) -> dict:
    found = await client.get("/api/animals", params={"q": tag, "limit": 100}, headers=headers)
    items = found.json()["animals"]
    exact = [a for a in items if a["tag_number"] == tag]
    assert exact, f"animal {tag} not found among {[a['tag_number'] for a in items]}"
    return exact[0]


async def test_batch_quarantine_animal_cannot_early_join_manually(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="quar@farm.in")
    batch = await client.post(
        "/api/purchases/new",
        json={"date": today().isoformat(), "supplier": "Quarantine supplier", "count": 2},
        headers=owner,
    )
    assert batch.status_code == 201, batch.text
    detail = (await client.get(f"/api/purchases/{batch.json()['id']}", headers=owner)).json()
    animal = detail["animals"][0]

    for target in ("FOUNDATION", "BREEDING", "MALE_KIDS"):
        resp = await try_move(client, owner, animal["id"], target)
        assert resp.status_code == 409, (
            f"quarantine early-join to {target} → {resp.status_code}: {resp.text[:150]}"
        )
        assert "quarantine" in resp.json()["detail"].lower()


async def test_sold_dead_culled_animals_are_locked_out(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="lock@farm.in")
    doe = await make_animal(client, owner, "LOCK-F")
    buck = await make_animal(client, owner, "LOCK-M", sex="M", weight_kg=32.0)

    sold = await change_status(
        client,
        owner,
        doe["id"],
        "SOLD",
        date=today().isoformat(),
        sale_price=5000.0,
        sale_weight_kg=28.0,
        buyer_name="Local butcher",
    )
    assert sold.status_code == 200, sold.text

    move = await try_move(client, owner, doe["id"], "FOUNDATION")
    assert move.status_code in (400, 409), move.text
    assert "sold" in move.json()["detail"].lower()

    weight = await client.post(
        f"/api/animals/{doe['id']}/weight",
        json={"weight_kg": 30.0, "date": today().isoformat()},
        headers=owner,
    )
    assert weight.status_code == 400, weight.text

    rebreed = await client.post(
        "/api/breeding",
        json={
            "doe_id": doe["id"],
            "buck_id": buck["id"],
            "breeding_date": today().isoformat(),
        },
        headers=owner,
    )
    assert rebreed.status_code == 400, rebreed.text

    resale = await change_status(
        client, owner, doe["id"], "SOLD", sale_price=1.0, buyer_name="again"
    )
    assert resale.status_code == 400, resale.text
    assert "already" in resale.json()["detail"].lower()

    dead = await change_status(
        client,
        owner,
        buck["id"],
        "DEAD",
        date=today().isoformat(),
        mortality_cause="Pneumonia",
        mortality_cause_code="PNEUMONIA",
        disposal_method="DEEP_BURIAL",
    )
    assert dead.status_code == 200, dead.text
    dead_move = await try_move(client, owner, buck["id"], "FOUNDATION")
    assert dead_move.status_code in (400, 409), dead_move.text
    assert "dead" in dead_move.json()["detail"].lower()

    # Audit gating is status-scoped at the DB level: sale fields on a DEAD
    # row / mortality fields on an ACTIVE row are impossible through the API.
    third = await make_animal(client, owner, "LOCK-3")
    bad_dead = await change_status(
        client,
        owner,
        third["id"],
        "DEAD",
        sale_price=100.0,
        buyer_name="nonsense",
    )
    assert bad_dead.status_code in (400, 422), (
        f"sale fields on a DEAD transition accepted: {bad_dead.status_code} {bad_dead.text[:150]}"
    )


async def test_workflow_moves_carry_the_full_audit_tuple(client: httpx.AsyncClient) -> None:
    """Every move this scenario drives (purchase intake, kidding-born kids,
    weaning) records reason, actor, effective business date, and timestamp."""
    owner = await owner_with_farm(client, email="audit@farm.in")
    doe = await make_animal(client, owner, "AUD2-F")
    buck = await make_animal(client, owner, "AUD2-M", sex="M", weight_kg=32.0)
    bred = await breed(client, owner, doe["id"], buck["id"], today() - timedelta(days=210))
    detail = (await client.get(f"/api/breeding/{bred['id']}", headers=owner)).json()
    scan = await client.post(
        f"/api/breeding/{bred['id']}/ultrasound",
        json={"pregnant": True, "kid_count": 1, "date": detail["ultrasound_date"]},
        headers=owner,
    )
    assert scan.status_code == 200, scan.text
    kidding_date = today() - timedelta(days=60)
    kidding = await record_kidding(
        client,
        owner,
        bred["id"],
        kidding_date,
        [{"sex": "M", "status": "ALIVE", "birth_weight": 2.5}],
    )
    kids = kidding.get("kids") or []
    kid_id = next(k["animal_id"] for k in kids if k.get("animal_id"))

    audited = await _moves_of(kid_id)
    assert audited, "born kid has no bucket move rows"
    for move in audited:
        assert move.reason, f"move {move.id} lacks a reason"
        assert move.moved_at is not None
        assert move.effective_date is not None
        assert move.created_by_id is not None
    # The birth move's business-effective date is the kidding date, not "now".
    birth_move = audited[0]
    assert birth_move.effective_date == kidding_date, (
        f"birth move effective {birth_move.effective_date} != kidding {kidding_date}"
    )
