"""Target-stable bulk health writes and auditable restriction episodes."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy import event as sa_event
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db import get_engine, get_sessionmaker
from app.main import create_app
from app.models import (
    Animal,
    HealthEvent,
    MovementRestrictionAction,
    Task,
    Transaction,
    VaccineTemplate,
)
from app.services.health import (
    _legacy_event_matches,
    protocol_phrase_of,
    target_matches_template,
    template_name_for_task,
)
from app.utils import today, utcnow

from .conftest import create_farm, owner_with_farm, register
from .test_health_extended import iso, make_animal, make_batch, post_event, record_event


async def wait_for_lock_waiter(minimum: int = 1, timeout_seconds: float = 10.0) -> None:
    for _ in range(int(timeout_seconds / 0.01)):
        async with get_sessionmaker()() as db:
            blocked = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() "
                        "AND pid <> pg_backend_pid() AND wait_event_type = 'Lock'"
                    )
                )
            ).scalar_one()
        if blocked >= minimum:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"expected {minimum} lock-waiting requests")


async def preview(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    **target: object,
) -> httpx.Response:
    return await client.post("/api/health/events/preview", json=target, headers=headers)


async def test_bulk_preview_is_sorted_and_new_entrants_are_not_silently_included(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    first = await make_animal(client, owner, tag="SNAPSHOT-1", name="First goat")
    second = await make_animal(client, owner, tag="SNAPSHOT-2", name="Second goat")

    reviewed = await preview(client, owner, scope="bucket", bucket="FOUNDATION")
    assert reviewed.status_code == 200, reviewed.text
    body = reviewed.json()
    expected_ids = body["target_animal_ids"]
    assert expected_ids == sorted([first["id"], second["id"]])
    assert body["target_animals"] == [
        {"id": first["id"], "tag_number": "SNAPSHOT-1", "name": "First goat"},
        {"id": second["id"], "tag_number": "SNAPSHOT-2", "name": "Second goat"},
    ]
    assert [row["id"] for row in body["target_animals"]] == expected_ids
    assert body["target_count"] == 2
    assert body["max_targets"] == 250

    entrant = await make_animal(client, owner, tag="SNAPSHOT-LATE")
    recorded = await client.post(
        "/api/health/events",
        json={
            "scope": "bucket",
            "bucket": "FOUNDATION",
            "expected_animal_ids": expected_ids,
            "type": "FOOTBATH",
        },
        headers=owner,
    )
    assert recorded.status_code == 201, recorded.text
    assert [event["animal_id"] for event in recorded.json()] == expected_ids
    assert entrant["id"] not in {event["animal_id"] for event in recorded.json()}

    refreshed = await preview(client, owner, scope="bucket", bucket="FOUNDATION")
    assert refreshed.status_code == 200
    assert refreshed.json()["target_count"] == 3


async def test_bulk_write_rejects_missing_duplicate_and_stale_reviewed_sets(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    first = await make_animal(client, owner, tag="STALE-1")
    second = await make_animal(client, owner, tag="STALE-2")
    reviewed = await preview(client, owner, scope="bucket", bucket="FOUNDATION")
    expected = reviewed.json()["target_animal_ids"]

    missing_snapshot = await client.post(
        "/api/health/events",
        json={"scope": "bucket", "bucket": "FOUNDATION", "type": "VACCINE"},
        headers=owner,
    )
    assert missing_snapshot.status_code == 409

    duplicate = await client.post(
        "/api/health/events",
        json={
            "scope": "bucket",
            "bucket": "FOUNDATION",
            "expected_animal_ids": [first["id"], first["id"]],
            "type": "VACCINE",
        },
        headers=owner,
    )
    assert duplicate.status_code == 409
    assert "duplicate" in duplicate.json()["detail"].lower()

    nonexistent = await client.post(
        "/api/health/events",
        json={
            "scope": "bucket",
            "bucket": "FOUNDATION",
            "expected_animal_ids": [*expected, 2_147_483_647],
            "type": "VACCINE",
        },
        headers=owner,
    )
    assert nonexistent.status_code == 409

    died = await client.post(
        f"/api/animals/{second['id']}/status",
        json={"new_status": "DEAD"},
        headers=owner,
    )
    assert died.status_code == 200, died.text
    stale = await client.post(
        "/api/health/events",
        json={
            "scope": "bucket",
            "bucket": "FOUNDATION",
            "expected_animal_ids": expected,
            "type": "VACCINE",
        },
        headers=owner,
    )
    assert stale.status_code == 409
    assert "stale" in stale.json()["detail"].lower()
    ledger = await client.get("/api/health/events", headers=owner)
    assert ledger.status_code == 200
    assert ledger.json()["total"] == 0


async def test_stale_reviewed_snapshot_states_its_conflict_verbatim(
    client: httpx.AsyncClient,
) -> None:
    """Pins the operator-facing 409 copy raised once a reviewed target went stale."""
    owner = await owner_with_farm(client)
    await make_animal(client, owner, tag="COPY-1")
    second = await make_animal(client, owner, tag="COPY-2")
    reviewed = await preview(client, owner, scope="bucket", bucket="FOUNDATION")
    expected = reviewed.json()["target_animal_ids"]

    died = await client.post(
        f"/api/animals/{second['id']}/status",
        json={"new_status": "DEAD"},
        headers=owner,
    )
    assert died.status_code == 200, died.text
    stale = await client.post(
        "/api/health/events",
        json={
            "scope": "bucket",
            "bucket": "FOUNDATION",
            "expected_animal_ids": expected,
            "type": "VACCINE",
        },
        headers=owner,
    )

    # The locked ids still match the reviewed set, so it is the stability guard
    # that speaks here rather than the id-mismatch guard. Its wording is what
    # the operator is shown, so it is pinned exactly rather than case-folded.
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"] == "Reviewed target snapshot is stale"


async def test_bulk_snapshot_never_locks_or_doses_a_foreign_farm_animal(
    client: httpx.AsyncClient,
) -> None:
    """A reviewed set naming another farm's animal is stale, never a cross-tenant lock."""
    victim = await owner_with_farm(client, email="victim@farm.in", farm_name="Victim")
    outsider = await make_animal(client, victim, tag="VICTIM-1")
    attacker = await owner_with_farm(client, email="attacker@farm.in", farm_name="Attacker")
    mine = await make_animal(client, attacker, tag="MINE-1")

    stolen = await client.post(
        "/api/health/events",
        json={
            "scope": "bucket",
            "bucket": "FOUNDATION",
            "expected_animal_ids": sorted([mine["id"], outsider["id"]]),
            "type": "FOOTBATH",
        },
        headers=attacker,
    )

    # The target lock is farm-scoped, so the foreign id cannot come back, the
    # reviewed set cannot match, and no row outside the tenant is ever locked
    # or dosed — the composite health-event foreign key is not the last line.
    assert stolen.status_code == 409, stolen.text
    assert stolen.json()["detail"] == "Reviewed target snapshot is stale"
    victim_ledger = await client.get("/api/health/events", headers=victim)
    assert victim_ledger.status_code == 200, victim_ledger.text
    assert victim_ledger.json()["events"] == []
    assert victim_ledger.json()["total"] == 0


async def test_bulk_lock_walks_targets_in_canonical_id_order(
    client: httpx.AsyncClient,
) -> None:
    """Canonical ascending-id lock order holds even when the heap disagrees."""
    owner = await owner_with_farm(client, email="order@farm.in", farm_name="Order")
    first = await make_animal(client, owner, tag="ORDER-1")
    second = await make_animal(client, owner, tag="ORDER-2")
    third = await make_animal(client, owner, tag="ORDER-3")
    expected = sorted([first["id"], second["id"], third["id"]])

    async with get_sessionmaker()() as db:
        # Rewriting the lowest id writes a new live tuple at the end of the
        # heap, so physical order and id order now genuinely disagree.
        await db.execute(update(Animal).where(Animal.id == first["id"]).values(name="Moved goat"))
        await db.commit()
        heap_order = list(
            (
                await db.execute(
                    select(Animal.id).where(Animal.id.in_(expected)).order_by(text("ctid"))
                )
            ).scalars()
        )
    assert heap_order == [second["id"], third["id"], first["id"]]

    recorded = await client.post(
        "/api/health/events",
        json={
            "scope": "bucket",
            "bucket": "FOUNDATION",
            "expected_animal_ids": expected,
            "type": "FOOTBATH",
        },
        headers=owner,
    )
    # An unordered FOR UPDATE would lock out of canonical order and then read
    # the ids back in heap order, failing the freshness comparison and telling
    # the operator a perfectly current snapshot is stale.
    assert recorded.status_code == 201, recorded.text
    assert [event["animal_id"] for event in recorded.json()] == expected


async def test_bucket_bulk_stays_at_250_while_valid_purchase_batch_remains_treatable(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="BOUND-1")

    oversized_write = await client.post(
        "/api/health/events",
        json={
            "scope": "bucket",
            "bucket": "FOUNDATION",
            "expected_animal_ids": [animal["id"]] * 251,
            "type": "VACCINE",
        },
        headers=owner,
    )
    assert oversized_write.status_code == 409
    assert "250" in oversized_write.json()["detail"]

    # Purchase creation legitimately supports larger batches. The linked
    # quarantine protocol must therefore preview and treat all 251 targets,
    # rather than creating an unfinishable duty at the old 250 boundary.
    batch = await make_batch(
        client,
        owner,
        count=251,
        date=iso(today() - timedelta(days=3)),
    )
    batch_preview = await preview(
        client,
        owner,
        scope="batch",
        purchase_batch_id=batch["id"],
    )
    assert batch_preview.status_code == 200, batch_preview.text
    preview_body = batch_preview.json()
    assert preview_body["target_count"] == 251
    assert preview_body["max_targets"] == 1000

    detail = await client.get(f"/api/purchases/{batch['id']}", headers=owner)
    assert detail.status_code == 200, detail.text
    deworming = next(task for task in detail.json()["tasks"] if task["category"] == "DEWORMING")
    treated = await client.post(
        "/api/health/events",
        json={
            "scope": "batch",
            "purchase_batch_id": batch["id"],
            "expected_animal_ids": preview_body["target_animal_ids"],
            "type": "DEWORMING",
            "disease_target": "Deworming",
            "schedule_template_name": "Deworming",
            "task_id": deworming["id"],
        },
        headers=owner,
    )
    assert treated.status_code == 201, treated.text
    assert len(treated.json()) == 251


async def test_largest_int32_purchase_batch_id_is_still_treatable(
    client: httpx.AsyncClient,
) -> None:
    """The int32 ceiling is a legal batch id, not a permanently stale snapshot."""
    owner = await owner_with_farm(client, email="edge@farm.in", farm_name="Edge")
    async with get_sessionmaker()() as db:
        await db.execute(
            text(
                "SELECT setval(pg_get_serial_sequence('purchase_batches', 'id'), :ceiling, false)"
            ),
            {"ceiling": 2_147_483_647},
        )
        await db.commit()

    batch = await make_batch(client, owner, count=2)
    assert batch["id"] == 2_147_483_647
    detail = await client.get(f"/api/purchases/{batch['id']}", headers=owner)
    assert detail.status_code == 200, detail.text
    animal_ids = sorted(animal["id"] for animal in detail.json()["animals"])

    recorded = await client.post(
        "/api/health/events",
        json={
            "scope": "batch",
            "purchase_batch_id": batch["id"],
            "expected_animal_ids": animal_ids,
            "type": "VACCINE",
        },
        headers=owner,
    )
    # The batch lookup gate covers the whole int32 range; excluding its top
    # value would leave this batch resolvable nowhere and untreatable forever.
    assert recorded.status_code == 201, recorded.text
    assert [event["animal_id"] for event in recorded.json()] == animal_ids
    assert {event["purchase_batch_id"] for event in recorded.json()} == {batch["id"]}


async def test_linked_batch_health_prelocks_active_animals_outside_quarantine(
    client: httpx.AsyncClient,
) -> None:
    """An outsider cannot enter the linked task's cohort behind its snapshot."""
    owner = await owner_with_farm(client)
    batch = await make_batch(client, owner, count=2, create_animals=True)
    detail = await client.get(f"/api/purchases/{batch['id']}", headers=owner)
    assert detail.status_code == 200, detail.text
    animals = sorted(detail.json()["animals"], key=lambda animal: animal["id"])
    task = next(row for row in detail.json()["tasks"] if row["category"] == "VACCINE")

    outsider = animals[0]
    moved = await client.post(
        f"/api/animals/{outsider['id']}/move",
        json={
            "to_bucket": "FOUNDATION",
            "history_override": True,
            "reason": "Correct imported quarantine history",
        },
        headers=owner,
    )
    assert moved.status_code == 200, moved.text
    reviewed = await preview(
        client,
        owner,
        scope="batch",
        purchase_batch_id=batch["id"],
        task_id=task["id"],
    )
    assert reviewed.status_code == 200, reviewed.text
    expected_ids = reviewed.json()["target_animal_ids"]
    assert expected_ids == [animals[1]["id"]]

    holder = get_sessionmaker()()
    await holder.execute(select(Task.id).where(Task.id == task["id"]).with_for_update())
    request = asyncio.create_task(
        client.post(
            "/api/health/events",
            json={
                "scope": "batch",
                "purchase_batch_id": batch["id"],
                "expected_animal_ids": expected_ids,
                "type": task["category"],
                "task_id": task["id"],
            },
            headers=owner,
        )
    )
    try:
        await wait_for_lock_waiter()
        async with get_sessionmaker()() as probe:
            with pytest.raises(DBAPIError) as locked:
                await probe.execute(
                    select(Animal.id)
                    .where(Animal.id == outsider["id"])
                    .with_for_update(nowait=True)
                )
            assert getattr(locked.value.orig, "sqlstate", None) == "55P03"
            await probe.rollback()
        await holder.rollback()
        response = await asyncio.wait_for(request, timeout=10)
    finally:
        await holder.rollback()
        await holder.close()
        if not request.done():
            request.cancel()

    # The protocol item is future-dated; the point of this request is the
    # pre-lock snapshot, which happens before the due-date guard.
    assert response.status_code == 409, response.text


async def test_batch_animal_can_reenter_only_before_protocol_work_starts(
    client: httpx.AsyncClient,
) -> None:
    """History correction stays possible only while the full protocol is pristine."""
    owner = await owner_with_farm(client)
    batch = await make_batch(client, owner, count=1, create_animals=True)
    detail = await client.get(f"/api/purchases/{batch['id']}", headers=owner)
    assert detail.status_code == 200, detail.text
    animal = detail.json()["animals"][0]

    move_out = {
        "to_bucket": "FOUNDATION",
        "history_override": True,
        "reason": "Correct imported quarantine history",
    }
    moved = await client.post(
        f"/api/animals/{animal['id']}/move",
        json=move_out,
        headers=owner,
    )
    assert moved.status_code == 200, moved.text
    pristine_reentry = await client.post(
        f"/api/animals/{animal['id']}/move",
        json={
            "to_bucket": "QUARANTINE",
            "history_override": True,
            "reason": "Restore the corrected purchase classification",
        },
        headers=owner,
    )
    assert pristine_reentry.status_code == 200, pristine_reentry.text

    moved_again = await client.post(
        f"/api/animals/{animal['id']}/move",
        json=move_out,
        headers=owner,
    )
    assert moved_again.status_code == 200, moved_again.text
    first_protocol_task = detail.json()["tasks"][0]
    completed = await client.post(
        f"/api/tasks/{first_protocol_task['id']}/complete",
        headers=owner,
    )
    assert completed.status_code == 200, completed.text

    refused = await client.post(
        f"/api/animals/{animal['id']}/move",
        json={
            "to_bucket": "QUARANTINE",
            "history_override": True,
            "reason": "Late correction must not evade completed protocol work",
        },
        headers=owner,
    )
    assert refused.status_code == 409, refused.text
    assert "protocol has started, ended, or is incomplete" in refused.json()["detail"]


async def test_batch_animal_cannot_reenter_an_incomplete_protocol(
    client: httpx.AsyncClient,
) -> None:
    """Missing legacy protocol rows fail closed instead of bypassing the gate."""
    owner = await owner_with_farm(client)
    batch = await make_batch(client, owner, count=1, create_animals=True)
    detail = await client.get(f"/api/purchases/{batch['id']}", headers=owner)
    assert detail.status_code == 200, detail.text
    animal = detail.json()["animals"][0]
    moved = await client.post(
        f"/api/animals/{animal['id']}/move",
        json={
            "to_bucket": "FOUNDATION",
            "history_override": True,
            "reason": "Correct imported quarantine history",
        },
        headers=owner,
    )
    assert moved.status_code == 200, moved.text

    async with get_sessionmaker()() as db:
        await db.execute(delete(Task).where(Task.id == detail.json()["tasks"][0]["id"]))
        await db.commit()

    refused = await client.post(
        f"/api/animals/{animal['id']}/move",
        json={
            "to_bucket": "QUARANTINE",
            "history_override": True,
            "reason": "Incomplete legacy schedule",
        },
        headers=owner,
    )
    assert refused.status_code == 409, refused.text
    assert "protocol has started, ended, or is incomplete" in refused.json()["detail"]


async def test_batch_animal_cannot_reenter_after_recorded_protocol_facts(
    client: httpx.AsyncClient,
) -> None:
    """Completion or rejection facts on a still-PENDING protocol row refuse re-entry."""
    owner = await owner_with_farm(client)
    identity = await client.get("/api/auth/me", headers=owner)
    assert identity.status_code == 200, identity.text
    owner_id = identity.json()["id"]
    batch = await make_batch(client, owner, count=1, create_animals=True)
    detail = await client.get(f"/api/purchases/{batch['id']}", headers=owner)
    assert detail.status_code == 200, detail.text
    animal = detail.json()["animals"][0]
    protocol_task_id = detail.json()["tasks"][0]["id"]
    moved = await client.post(
        f"/api/animals/{animal['id']}/move",
        json={
            "to_bucket": "FOUNDATION",
            "history_override": True,
            "reason": "Correct imported quarantine history",
        },
        headers=owner,
    )
    assert moved.status_code == 200, moved.text
    reentry = {
        "to_bucket": "QUARANTINE",
        "history_override": True,
        "reason": "Late correction must not evade recorded protocol facts",
    }

    # A duty sent back to its worker is PENDING again but already carries a fact.
    async with get_sessionmaker()() as db:
        await db.execute(
            update(Task)
            .where(Task.id == protocol_task_id)
            .values(
                rejected_by_id=owner_id,
                rejected_at=utcnow(),
                verification_note="sent back",
            )
        )
        await db.commit()
    after_rejection = await client.post(
        f"/api/animals/{animal['id']}/move", json=reentry, headers=owner
    )
    assert after_rejection.status_code == 409, after_rejection.text
    assert after_rejection.json()["detail"] == (
        "This animal cannot leave quarantine by override because its "
        "purchase-batch protocol has started, ended, or is incomplete."
    )

    async with get_sessionmaker()() as db:
        await db.execute(
            update(Task)
            .where(Task.id == protocol_task_id)
            .values(
                rejected_by_id=None,
                rejected_at=None,
                verification_note=None,
                completed_by_id=owner_id,
                completed_at=utcnow(),
            )
        )
        await db.commit()
        stamped_status = (
            await db.execute(select(Task.status).where(Task.id == protocol_task_id))
        ).scalar_one()
    assert stamped_status == "PENDING"
    after_completion = await client.post(
        f"/api/animals/{animal['id']}/move", json=reentry, headers=owner
    )
    assert after_completion.status_code == 409, after_completion.text
    assert after_completion.json()["detail"] == (
        "This animal cannot leave quarantine by override because its "
        "purchase-batch protocol has started, ended, or is incomplete."
    )

    stored = await client.get(f"/api/animals/{animal['id']}", headers=owner)
    assert stored.status_code == 200, stored.text
    assert stored.json()["animal"]["current_bucket"] == "FOUNDATION"


async def test_batch_reentry_locks_only_its_own_batch(client: httpx.AsyncClient) -> None:
    """A sibling purchase batch in the same farm must not widen the re-entry lock."""
    owner = await owner_with_farm(client)
    batch = await make_batch(client, owner, count=1, create_animals=True)
    sibling = await make_batch(
        client, owner, count=1, create_animals=True, supplier="Nandyal Traders"
    )
    assert sibling["id"] != batch["id"]
    detail = await client.get(f"/api/purchases/{batch['id']}", headers=owner)
    assert detail.status_code == 200, detail.text
    animal = detail.json()["animals"][0]
    moved = await client.post(
        f"/api/animals/{animal['id']}/move",
        json={
            "to_bucket": "FOUNDATION",
            "history_override": True,
            "reason": "Correct imported quarantine history",
        },
        headers=owner,
    )
    assert moved.status_code == 200, moved.text

    reentry = await client.post(
        f"/api/animals/{animal['id']}/move",
        json={
            "to_bucket": "QUARANTINE",
            "history_override": True,
            "reason": "Restore the corrected purchase classification",
        },
        headers=owner,
    )
    assert reentry.status_code == 200, reentry.text
    assert reentry.json()["current_bucket"] == "QUARANTINE"


async def test_batch_reentry_ignores_tasks_outside_its_batch(client: httpx.AsyncClient) -> None:
    """Unrelated farm duties are not counted as this batch's protocol rows."""
    owner = await owner_with_farm(client)
    batch = await make_batch(client, owner, count=1, create_animals=True)
    duty = await client.post(
        "/api/tasks",
        json={
            "title": "Unrelated cleaning duty",
            "due_date": iso(today()),
            "category": "CLEANING",
        },
        headers=owner,
    )
    assert duty.status_code == 201, duty.text
    detail = await client.get(f"/api/purchases/{batch['id']}", headers=owner)
    assert detail.status_code == 200, detail.text
    assert duty.json()["id"] not in {task["id"] for task in detail.json()["tasks"]}
    animal = detail.json()["animals"][0]
    moved = await client.post(
        f"/api/animals/{animal['id']}/move",
        json={
            "to_bucket": "FOUNDATION",
            "history_override": True,
            "reason": "Correct imported quarantine history",
        },
        headers=owner,
    )
    assert moved.status_code == 200, moved.text

    reentry = await client.post(
        f"/api/animals/{animal['id']}/move",
        json={
            "to_bucket": "QUARANTINE",
            "history_override": True,
            "reason": "Restore the corrected purchase classification",
        },
        headers=owner,
    )
    assert reentry.status_code == 200, reentry.text
    assert reentry.json()["current_bucket"] == "QUARANTINE"
    untouched = await client.get(f"/api/tasks/{duty.json()['id']}", headers=owner)
    assert untouched.status_code == 200, untouched.text
    assert untouched.json()["status"] == "PENDING"


async def test_batch_reentry_refusal_states_both_halves_of_its_reason(
    client: httpx.AsyncClient,
) -> None:
    """The refusal body names the animal, the batch and the protocol, verbatim."""
    owner = await owner_with_farm(client)
    batch = await make_batch(client, owner, count=1, create_animals=True)
    detail = await client.get(f"/api/purchases/{batch['id']}", headers=owner)
    assert detail.status_code == 200, detail.text
    animal = detail.json()["animals"][0]
    moved = await client.post(
        f"/api/animals/{animal['id']}/move",
        json={
            "to_bucket": "FOUNDATION",
            "history_override": True,
            "reason": "Correct imported quarantine history",
        },
        headers=owner,
    )
    assert moved.status_code == 200, moved.text
    completed = await client.post(
        f"/api/tasks/{detail.json()['tasks'][0]['id']}/complete",
        headers=owner,
    )
    assert completed.status_code == 200, completed.text

    refused = await client.post(
        f"/api/animals/{animal['id']}/move",
        json={
            "to_bucket": "QUARANTINE",
            "history_override": True,
            "reason": "Late correction must not evade completed protocol work",
        },
        headers=owner,
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == (
        "This animal cannot leave quarantine by override because its "
        "purchase-batch protocol has started, ended, or is incomplete."
    )


async def test_linked_health_winner_prevents_waiting_animal_quarantine_reentry(
    client: httpx.AsyncClient,
) -> None:
    """If the protocol fact wins, a blocked outsider cannot enter behind it."""
    owner = await owner_with_farm(client)
    batch = await make_batch(
        client,
        owner,
        count=2,
        create_animals=True,
        date=iso(today() - timedelta(days=9)),
    )
    detail = await client.get(f"/api/purchases/{batch['id']}", headers=owner)
    assert detail.status_code == 200, detail.text
    animals = sorted(detail.json()["animals"], key=lambda animal: animal["id"])
    task = next(row for row in detail.json()["tasks"] if row["category"] == "VACCINE")
    outsider = animals[0]
    moved = await client.post(
        f"/api/animals/{outsider['id']}/move",
        json={
            "to_bucket": "FOUNDATION",
            "history_override": True,
            "reason": "Correct imported quarantine history",
        },
        headers=owner,
    )
    assert moved.status_code == 200, moved.text
    reviewed = await preview(
        client,
        owner,
        scope="batch",
        purchase_batch_id=batch["id"],
        task_id=task["id"],
    )
    assert reviewed.status_code == 200, reviewed.text
    expected_ids = reviewed.json()["target_animal_ids"]
    assert expected_ids == [animals[1]["id"]]

    holder = get_sessionmaker()()
    await holder.execute(select(Task.id).where(Task.id == task["id"]).with_for_update())
    health_request = asyncio.create_task(
        client.post(
            "/api/health/events",
            json={
                "scope": "batch",
                "purchase_batch_id": batch["id"],
                "expected_animal_ids": expected_ids,
                "type": task["category"],
                "task_id": task["id"],
            },
            headers=owner,
        )
    )
    reentry_request: asyncio.Task[httpx.Response] | None = None
    try:
        await wait_for_lock_waiter()
        reentry_request = asyncio.create_task(
            client.post(
                f"/api/animals/{outsider['id']}/move",
                json={
                    "to_bucket": "QUARANTINE",
                    "history_override": True,
                    "reason": "Concurrent late quarantine correction",
                },
                headers=owner,
            )
        )
        await wait_for_lock_waiter(2)
        await holder.rollback()
        async with asyncio.timeout(10):
            health_response, reentry_response = await asyncio.gather(
                health_request,
                reentry_request,
            )
    finally:
        await holder.rollback()
        await holder.close()
        if not health_request.done():
            health_request.cancel()
        if reentry_request is not None and not reentry_request.done():
            reentry_request.cancel()

    assert health_response.status_code == 201, health_response.text
    assert reentry_response.status_code == 409, reentry_response.text
    stored = await client.get(f"/api/animals/{outsider['id']}", headers=owner)
    assert stored.status_code == 200, stored.text
    assert stored.json()["animal"]["current_bucket"] == "FOUNDATION"


async def test_restriction_versions_history_and_explicit_supersession(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="EPISODE-1")
    first = await record_event(
        client,
        owner,
        animal_id=animal["id"],
        type="TREATMENT",
        disease_target="PPR concern",
        suspected_scheduled_disease=True,
    )
    second = await record_event(
        client,
        owner,
        animal_id=animal["id"],
        type="TREATMENT",
        disease_target="Anthrax concern",
        suspected_scheduled_disease=True,
    )

    history = await client.get(f"/api/health/restrictions/{animal['id']}", headers=owner)
    assert history.status_code == 200, history.text
    body = history.json()
    assert body["restriction_version"] == 2
    assert body["active"] is True
    assert (body["total"], body["limit"], body["offset"]) == (2, 25, 0)
    assert [(row["restriction_version"], row["action"]) for row in body["actions"]] == [
        (2, "PLACED"),
        (1, "PLACED"),
    ]
    assert [row["disease_target"] for row in body["actions"]] == [
        "Anthrax concern",
        "PPR concern",
    ]
    assert [row["health_event_id"] for row in body["actions"]] == [
        second[0]["id"],
        first[0]["id"],
    ]
    assert all(row["acted_by_id"] is not None for row in body["actions"])
    assert all(row["acted_at"] for row in body["actions"])

    stale_clear = await client.post(
        f"/api/health/restrictions/{animal['id']}/clear",
        json={"clearance_reference": "Old episode clearance", "expected_restriction_version": 1},
        headers=owner,
    )
    assert stale_clear.status_code == 409
    current_clear = await client.post(
        f"/api/health/restrictions/{animal['id']}/clear",
        json={"clearance_reference": "AHD current clearance", "expected_restriction_version": 2},
        headers=owner,
    )
    assert current_clear.status_code == 204, current_clear.text

    closed = await client.get(f"/api/health/restrictions/{animal['id']}", headers=owner)
    assert closed.json()["active"] is False
    # Episode one is deliberately superseded, not falsely reported cleared.
    assert [(row["restriction_version"], row["action"]) for row in closed.json()["actions"]] == [
        (2, "CLEARED"),
        (2, "PLACED"),
        (1, "PLACED"),
    ]


async def test_restriction_history_is_bounded_exact_and_stably_newest_first(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="RESTRICTION-PAGE")
    acted_at = utcnow()
    async with get_sessionmaker()() as db:
        actions = [
            MovementRestrictionAction(
                farm_id=int(owner["X-Farm-Id"]),
                animal_id=animal["id"],
                restriction_version=version,
                action="PLACED",
                acted_at=acted_at,
                acted_by_id=None,
                action_reference=f"Historical placement {version}",
                disease_target=f"Concern {version}",
                health_event_id=None,
            )
            for version in range(1, 131)
        ]
        db.add_all(actions)
        await db.flush()
        inserted_ids = [action.id for action in actions]
        await db.commit()

    newest_ids = list(reversed(inserted_ids))
    default_page = await client.get(
        f"/api/health/restrictions/{animal['id']}",
        headers=owner,
    )
    assert default_page.status_code == 200, default_page.text
    body = default_page.json()
    assert set(body) == {
        "animal_id",
        "restriction_version",
        "active",
        "actions",
        "total",
        "limit",
        "offset",
    }
    assert (body["total"], body["limit"], body["offset"]) == (130, 25, 0)
    assert [row["id"] for row in body["actions"]] == newest_ids[:25]

    middle_page = await client.get(
        f"/api/health/restrictions/{animal['id']}?limit=100&offset=25",
        headers=owner,
    )
    assert middle_page.status_code == 200, middle_page.text
    middle = middle_page.json()
    assert (middle["total"], middle["limit"], middle["offset"]) == (130, 100, 25)
    assert [row["id"] for row in middle["actions"]] == newest_ids[25:125]

    tail_page = await client.get(
        f"/api/health/restrictions/{animal['id']}?limit=100&offset=125",
        headers=owner,
    )
    assert tail_page.status_code == 200, tail_page.text
    assert [row["id"] for row in tail_page.json()["actions"]] == newest_ids[125:]
    too_large = await client.get(
        f"/api/health/restrictions/{animal['id']}?limit=101",
        headers=owner,
    )
    assert too_large.status_code == 422


async def test_schedule_query_work_is_bounded_by_templates_not_event_history(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="SCHEDULE-VOLUME")

    async def captured_schedule() -> tuple[httpx.Response, list[str]]:
        statements: list[str] = []

        def capture_statement(
            _conn: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: object,
        ) -> None:
            statements.append(statement)

        engine = get_engine().sync_engine
        sa_event.listen(engine, "before_cursor_execute", capture_statement)
        try:
            response = await client.get(
                f"/api/health/schedule/{animal['id']}",
                headers=owner,
            )
        finally:
            sa_event.remove(engine, "before_cursor_execute", capture_statement)
        return response, statements

    baseline, baseline_statements = await captured_schedule()
    assert baseline.status_code == 200, baseline.text

    event_date = today()
    async with get_sessionmaker()() as db:
        ppr_id = (
            await db.execute(select(VaccineTemplate.id).where(VaccineTemplate.name == "PPR"))
        ).scalar_one()
        db.add_all(
            [
                HealthEvent(
                    farm_id=int(owner["X-Farm-Id"]),
                    animal_id=animal["id"],
                    purchase_batch_id=None,
                    date=event_date - timedelta(days=1_199 - index),
                    type="VACCINE",
                    product_name="PPR vaccine",
                    schedule_template_name="PPR",
                    schedule_template_id=ppr_id,
                )
                for index in range(1_200)
            ]
            + [
                HealthEvent(
                    farm_id=int(owner["X-Farm-Id"]),
                    animal_id=animal["id"],
                    purchase_batch_id=None,
                    date=event_date - timedelta(days=1_199 - index),
                    type="VACCINE",
                    product_name=f"Unclassified legacy product {index}",
                    schedule_template_name=None,
                    schedule_template_id=None,
                )
                for index in range(1_200)
            ]
        )
        await db.commit()

    expanded, expanded_statements = await captured_schedule()
    assert expanded.status_code == 200, expanded.text
    assert len(expanded_statements) == len(baseline_statements)
    ppr = next(row for row in expanded.json()["rows"] if row["template_name"] == "PPR")
    assert ppr["last_done"] == event_date.isoformat()

    health_event_queries = [
        statement for statement in expanded_statements if "health_events" in statement
    ]
    assert len(health_event_queries) == 2
    canonical_query = next(
        statement for statement in health_event_queries if "UNION ALL" in statement
    )
    legacy_query = next(
        statement
        for statement in health_event_queries
        if "health_events.schedule_template_id IS NULL" in statement
    )
    # Each template uses two bounded probes: newest-two for current status and
    # earliest-one for the permanent primary-dose booster anchor. Work scales
    # with template count, never with the animal's lifetime event volume.
    expected_template_probes = 2 * len(expanded.json()["rows"])
    assert canonical_query.count("LIMIT") == expected_template_probes
    assert canonical_query.count("health_events.schedule_template_id =") == expected_template_probes
    assert legacy_query.count("LIMIT") == 1


async def test_new_schedule_links_are_canonical_immutable_and_indexed(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="SCHEDULE-LINK")
    recorded = await record_event(
        client,
        owner,
        animal_id=animal["id"],
        type="VACCINE",
        product_name="PPR vaccine",
    )
    # Inference is an internal canonical link; it does not rewrite what the
    # farmer actually supplied into the public audit-name field.
    assert recorded[0]["schedule_template_name"] is None

    async with get_sessionmaker()() as db:
        template_ids = dict(
            (
                await db.execute(
                    select(VaccineTemplate.name, VaccineTemplate.id).where(
                        VaccineTemplate.name.in_(["PPR", "FMD"])
                    )
                )
            ).all()
        )
        event_row = await db.get(HealthEvent, recorded[0]["id"])
        assert event_row is not None
        assert event_row.schedule_template_id == template_ids["PPR"]

        with pytest.raises(DBAPIError, match="schedule template link is immutable"):
            await db.execute(
                update(HealthEvent)
                .where(HealthEvent.id == event_row.id)
                .values(schedule_template_id=template_ids["FMD"])
            )
            await db.commit()
        await db.rollback()

        index_definition = (
            await db.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE indexname = 'ix_health_events_animal_template_latest'"
                )
            )
        ).scalar_one()
    assert "(animal_id, schedule_template_id, date DESC, id DESC)" in index_definition


async def test_disease_target_alone_earns_the_canonical_schedule_link(
    client: httpx.AsyncClient,
) -> None:
    """A product-less vaccine entry is linked by its disease target alone."""
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="SCHEDULE-TARGET")
    recorded = await record_event(
        client,
        owner,
        animal_id=animal["id"],
        type="VACCINE",
        disease_target="PPR",
    )
    assert recorded[0]["schedule_template_name"] is None

    async with get_sessionmaker()() as db:
        ppr_id = (
            await db.execute(select(VaccineTemplate.id).where(VaccineTemplate.name == "PPR"))
        ).scalar_one()
        event_row = await db.get(HealthEvent, recorded[0]["id"])
        assert event_row is not None
        # Dropping the target from the inference haystack leaves this row
        # unlinked: still visible through the bounded legacy text window, but
        # off ix_health_events_animal_template_latest and its canonical probes.
        assert event_row.schedule_template_id == ppr_id


async def test_bounded_legacy_schedule_window_still_matches_unlinked_history(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="SCHEDULE-LEGACY")
    legacy_date = today() - timedelta(days=10)
    async with get_sessionmaker()() as db:
        db.add(
            HealthEvent(
                farm_id=int(owner["X-Farm-Id"]),
                animal_id=animal["id"],
                purchase_batch_id=None,
                date=legacy_date,
                type="VACCINE",
                product_name="PPR vaccine",
                schedule_template_name=None,
                schedule_template_id=None,
            )
        )
        await db.commit()

    schedule = await client.get(f"/api/health/schedule/{animal['id']}", headers=owner)
    assert schedule.status_code == 200, schedule.text
    ppr = next(row for row in schedule.json()["rows"] if row["template_name"] == "PPR")
    assert ppr["last_done"] == legacy_date.isoformat()


async def test_mortality_suspicion_uses_the_same_audited_episode_path(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="MORTALITY-HOLD")
    response = await client.post(
        f"/api/animals/{animal['id']}/status",
        json={
            "new_status": "DEAD",
            "date": iso(today()),
            "mortality_cause": "Sudden death",
            "suspected_scheduled_disease": True,
            "suspected_disease": "Anthrax",
            "authority_notified_at": iso(today()),
        },
        headers=owner,
    )
    assert response.status_code == 200, response.text
    assert response.json()["restriction_version"] == 1
    history = await client.get(f"/api/health/restrictions/{animal['id']}", headers=owner)
    assert history.status_code == 200
    assert history.json()["restriction_version"] == 1
    [action] = history.json()["actions"]
    assert action["action"] == "PLACED"
    assert action["disease_target"] == "Anthrax"
    assert action["health_event_id"] is None
    assert "Mortality status change effective" in action["action_reference"]
    assert action["acted_by_id"] is not None


async def test_clear_racing_a_new_hold_cannot_clear_the_new_episode(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="CLEAR-RACE")
    await record_event(
        client,
        owner,
        animal_id=animal["id"],
        type="TREATMENT",
        disease_target="Initial concern",
        suspected_scheduled_disease=True,
    )

    app = create_app()
    async with (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as one,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as two,
    ):
        clear, placement = await asyncio.gather(
            one.post(
                f"/api/health/restrictions/{animal['id']}/clear",
                json={"clearance_reference": "Race clearance", "expected_restriction_version": 1},
                headers=owner,
            ),
            two.post(
                "/api/health/events",
                json={
                    "animal_id": animal["id"],
                    "type": "TREATMENT",
                    "disease_target": "Newer concern",
                    "suspected_scheduled_disease": True,
                },
                headers=owner,
            ),
        )
    assert placement.status_code == 201, placement.text
    assert clear.status_code in {204, 409}, clear.text
    history = await client.get(f"/api/health/restrictions/{animal['id']}", headers=owner)
    assert history.status_code == 200
    assert history.json()["restriction_version"] == 2
    assert history.json()["active"] is True
    assert not any(
        row["restriction_version"] == 2 and row["action"] == "CLEARED"
        for row in history.json()["actions"]
    )


async def test_audit_rows_are_immutable_and_tenant_foreign_keys_fail_closed(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="audit-one@farm.in", farm_name="Audit One")
    other = await create_farm(
        client,
        {"Authorization": owner["Authorization"]},
        name="Audit Two",
    )
    animal_one = await make_animal(client, owner, tag="AUDIT-ONE")
    animal_two = await make_animal(client, other, tag="AUDIT-TWO")
    await record_event(
        client,
        owner,
        animal_id=animal_one["id"],
        type="TREATMENT",
        disease_target="Audit concern",
        suspected_scheduled_disease=True,
    )

    async with get_sessionmaker()() as db:
        action = (await db.execute(select(MovementRestrictionAction))).scalar_one()
        with pytest.raises(DBAPIError, match="immutable"):
            await db.execute(
                update(MovementRestrictionAction)
                .where(MovementRestrictionAction.id == action.id)
                .values(action_reference="rewritten")
            )
            await db.commit()
        await db.rollback()

        db.add(
            MovementRestrictionAction(
                farm_id=int(owner["X-Farm-Id"]),
                animal_id=animal_two["id"],
                restriction_version=1,
                action="PLACED",
                action_reference="Cross-tenant attempt",
                disease_target="Concern",
            )
        )
        with pytest.raises(IntegrityError, match="fk_movement_restriction_actions_farm_animal"):
            await db.commit()


async def test_next_due_provenance_is_enforced_for_direct_database_writes(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="NEXT-DUE-DB")
    async with get_sessionmaker()() as db:
        db.add(
            HealthEvent(
                farm_id=int(owner["X-Farm-Id"]),
                animal_id=animal["id"],
                date=today(),
                type="VACCINE",
                next_due_date=today() + timedelta(days=30),
            )
        )
        with pytest.raises(IntegrityError, match="ck_health_events_next_due_provenance"):
            await db.commit()


@pytest.mark.parametrize("field", ["authority_notified_at", "isolation_started_at"])
async def test_compliance_dates_require_a_suspected_scheduled_disease(
    client: httpx.AsyncClient,
    field: str,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag=f"ORPHAN-{field[:3]}")

    response = await client.post(
        "/api/health/events",
        json={
            "scope": "animal",
            "animal_id": animal["id"],
            "type": "TREATMENT",
            field: today().isoformat(),
        },
        headers=owner,
    )

    assert response.status_code == 422, response.text


async def test_compliance_dates_are_enforced_for_direct_database_writes(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="ORPHAN-DB")
    async with get_sessionmaker()() as db:
        db.add(
            HealthEvent(
                farm_id=int(owner["X-Farm-Id"]),
                animal_id=animal["id"],
                date=today(),
                type="TREATMENT",
                suspected_scheduled_disease=False,
                authority_notified_at=today(),
            )
        )
        with pytest.raises(
            IntegrityError,
            match="ck_health_events_compliance_requires_suspicion",
        ):
            await db.commit()


@pytest.mark.parametrize("field", ["authority_notified_at", "isolation_started_at"])
async def test_compliance_dates_are_checked_against_their_own_value_and_label(
    client: httpx.AsyncClient,
    field: str,
) -> None:
    """Each compliance date is chronology-checked with its own value and its own label."""
    owner = await owner_with_farm(client, email="comp@farm.in")
    animal = await make_animal(
        client,
        owner,
        tag="COMP-1",
        estimated_dob=iso(today() - timedelta(days=100)),
    )

    response = await post_event(
        client,
        owner,
        animal_id=animal["id"],
        type="TREATMENT",
        date=iso(today()),
        disease_target="Anthrax",
        suspected_scheduled_disease=True,
        **{field: iso(today() - timedelta(days=200))},
    )

    # Checking anything other than this field's own value turns a domain 422
    # into an unhandled comparison against None, and a wrong label leaves the
    # operator guessing which of the two compliance dates was rejected.
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == f"{field} cannot predate COMP-1's recorded birth date"


async def test_event_dates_in_the_farms_future_name_the_offending_field(
    client: httpx.AsyncClient,
) -> None:
    """Every farm-future 422 names the field it rejected, in the farm's own timezone."""
    account = await register(client, "tz-owner@farm.in")
    created = await client.post(
        "/api/auth/farms",
        json={"name": "West farm", "timezone": "Pacific/Honolulu"},
        headers=account,
    )
    assert created.status_code == 201, created.text
    # A farm west of UTC makes today() + 1 unambiguously future for the farm
    # while still inside the schema's UTC+1-day ceiling, so these rejections
    # come from the farm-local guard rather than from input validation.
    west = account | {"X-Farm-Id": str(created.json()["id"])}
    animal = await make_animal(client, west, tag="TZ-1")
    tomorrow = iso(today() + timedelta(days=1))

    future_event = await post_event(
        client,
        west,
        animal_id=animal["id"],
        type="VACCINE",
        date=tomorrow,
    )
    assert future_event.status_code == 422, future_event.text
    assert (
        future_event.json()["detail"] == "health event date cannot be in the future for this farm"
    )

    future_manufacture = await post_event(
        client,
        west,
        animal_id=animal["id"],
        type="VACCINE",
        date=iso(today() - timedelta(days=1)),
        product_manufactured_on=tomorrow,
    )
    assert future_manufacture.status_code == 422, future_manufacture.text
    assert future_manufacture.json()["detail"] == (
        "product_manufactured_on cannot be in the future for this farm"
    )

    for field in ("authority_notified_at", "isolation_started_at"):
        future_compliance = await post_event(
            client,
            west,
            animal_id=animal["id"],
            type="TREATMENT",
            disease_target="Anthrax",
            suspected_scheduled_disease=True,
            **{field: tomorrow},
        )
        assert future_compliance.status_code == 422, future_compliance.text
        assert future_compliance.json()["detail"] == (
            f"{field} cannot be in the future for this farm"
        )


def test_health_template_matching_uses_words_and_requires_both_combined_components() -> None:
    assert not target_matches_template("diet review", "Enterotoxaemia (ET)")
    assert not target_matches_template("ET vaccine", "ET + TT pre-kidding")
    assert not target_matches_template("Tetanus toxoid", "ET + TT pre-kidding")
    assert target_matches_template("ET + TT vaccine", "ET + TT pre-kidding")
    assert target_matches_template("Enterotoxaemia and tetanus", "ET + TT pre-kidding")

    assert template_name_for_task("Diet review plus tetanus", "VACCINE") is None
    assert template_name_for_task("Day 20: vaccinate ET + Tetanus", "VACCINE") == (
        "Enterotoxaemia (ET)"
    )
    assert template_name_for_task("Pre-kidding ET+TT vaccine", "VACCINE") == ("ET + TT pre-kidding")


def test_legacy_schedule_matching_rejects_incompatible_event_types() -> None:
    def matches(
        template_name: str,
        event_type: str,
        *,
        schedule_template_name: str | None = None,
        product_name: str | None = None,
    ) -> bool:
        return _legacy_event_matches(
            template_name,
            event_type=event_type,
            schedule_template_name=schedule_template_name,
            product_name=product_name,
            disease_target=None,
        )

    # Exact legacy labels are not enough when the recorded clinical action is
    # from the other programme family.
    assert not matches("PPR", "DEWORMING", schedule_template_name="PPR")
    assert not matches("Deworming", "VACCINE", schedule_template_name="Deworming")
    # Nor may free text or a known trade-name alias override the event type.
    assert not matches("PPR", "DEWORMING", product_name="Raksha-PPR")
    assert not matches("Deworming", "VACCINE", product_name="routine deworming treatment")

    assert matches("PPR", "VACCINE", schedule_template_name="PPR")
    assert matches("PPR", "VACCINE", product_name="Raksha-PPR")
    assert matches("Deworming", "DEWORMING", product_name="routine treatment")


def test_template_inference_ignores_operator_supplied_labels() -> None:
    """Only the protocol phrase decides the programme item: the quarantine
    supplier prefix and the pre-kidding tag suffix are display text."""
    assert protocol_phrase_of("[PPR Traders #1] Day 20: vaccinate ET + Tetanus (toxoid, SC)") == (
        "Day 20: vaccinate ET + Tetanus (toxoid, SC)"
    )
    # A supplier may itself contain "]"; the generated prefix ends at the last.
    assert protocol_phrase_of("[A] Day 10: PPR #1] Day 40: vaccinate FMD (killed, SC)") == (
        "Day 40: vaccinate FMD (killed, SC)"
    )
    assert protocol_phrase_of("Pre-kidding ET+TT vaccine: PPR-01") == "Pre-kidding ET+TT vaccine"

    for supplier in ("PPR Traders", "Goat Pox Agro", "FMD Exports"):
        title = f"[{supplier} #7] Day 20: vaccinate ET + Tetanus (toxoid, SC)"
        assert template_name_for_task(title, "VACCINE") == "Enterotoxaemia (ET)"
        pox = f"[{supplier} #7] Day 30: vaccinate Goat Pox (live viral, SC)"
        assert template_name_for_task(pox, "VACCINE") == "Goat Pox"
    assert template_name_for_task("Pre-kidding ET+TT vaccine: PPR-01", "VACCINE") == (
        "ET + TT pre-kidding"
    )


def test_template_abbreviation_matches_its_own_target() -> None:
    """The read/inference path accepts the advertised abbreviation, so the
    stricter write path must not reject the more precise entry."""
    assert target_matches_template("HS", "Haemorrhagic Septicaemia (HS)")
    assert target_matches_template("Haemorrhagic Septicaemia", "Haemorrhagic Septicaemia (HS)")
    assert not target_matches_template("Anthrax", "Haemorrhagic Septicaemia (HS)")


async def test_linked_duty_without_a_target_cannot_be_closed(client: httpx.AsyncClient) -> None:
    """A generated VACCINE duty carrying neither an animal nor a batch has no
    scope to validate against, so it must never close from a health record."""
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="NO-TARGET")
    async with get_sessionmaker()() as db:
        orphan = Task(
            farm_id=int(owner["X-Farm-Id"]),
            title="Day 10: vaccinate PPR (live viral, SC)",
            due_date=today(),
            status="PENDING",
            category="VACCINE",
            auto_generated=True,
        )
        db.add(orphan)
        await db.commit()
        task_id = orphan.id

    response = await client.post(
        "/api/health/events",
        json={
            "scope": "animal",
            "animal_id": animal["id"],
            "type": "VACCINE",
            "disease_target": "PPR",
            "task_id": task_id,
        },
        headers=owner,
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "Linked health task has no supported target"
    async with get_sessionmaker()() as db:
        stored = await db.get(Task, task_id)
        assert stored is not None and stored.status == "PENDING"


async def test_a_new_suspicion_episode_restates_its_own_authority_notification(
    client: httpx.AsyncClient,
) -> None:
    """place_movement_restriction opens a NEW episode, so the animal-level
    restriction columns describe the current concern only. Carrying a previous
    notification date forward would falsely assert the authority was told about
    the new suspicion; the fact itself stays on the event that recorded it."""
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="NOTIFY-1")
    notified_on = today() - timedelta(days=3)
    await record_event(
        client,
        owner,
        scope="animal",
        animal_id=animal["id"],
        type="TREATMENT",
        disease_target="Anthrax",
        suspected_scheduled_disease=True,
        authority_notified_at=notified_on.isoformat(),
    )
    profile = await client.get(f"/api/animals/{animal['id']}", headers=owner)
    assert profile.json()["animal"]["authority_notified_at"] == notified_on.isoformat()

    await record_event(
        client,
        owner,
        scope="animal",
        animal_id=animal["id"],
        type="TREATMENT",
        disease_target="Anthrax",
        suspected_scheduled_disease=True,
    )
    profile = await client.get(f"/api/animals/{animal['id']}", headers=owner)
    assert profile.json()["animal"]["restriction_version"] == 2
    assert profile.json()["animal"]["authority_notified_at"] is None
    ledger = await client.get("/api/health/events", headers=owner)
    assert ledger.status_code == 200, ledger.text
    assert notified_on.isoformat() in {
        event["authority_notified_at"] for event in ledger.json()["events"]
    }


async def test_manual_diet_task_does_not_gain_an_et_template_by_substring(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    batch = await make_batch(
        client,
        owner,
        count=1,
        date=iso(today() - timedelta(days=21)),
        create_animals=True,
    )
    detail = await client.get(f"/api/purchases/{batch['id']}", headers=owner)
    assert detail.status_code == 200, detail.text
    task = next(
        row
        for row in detail.json()["tasks"]
        if row["category"] == "VACCINE" and row["due_date"] <= iso(today())
    )
    # Manual workflow categories are intentionally forbidden.  Model a
    # legacy/system-generated duty whose free-text title contains "diet" so
    # the route-level provenance/template matching remains covered.
    async with get_sessionmaker()() as db:
        task_row = await db.get(Task, task["id"])
        assert task_row is not None
        task_row.title = "Diet review plus tetanus note"
        await db.commit()
    event = await record_event(
        client,
        owner,
        scope="batch",
        purchase_batch_id=batch["id"],
        type="VACCINE",
        task_id=task["id"],
    )
    assert event[0]["schedule_template_name"] is None


async def test_health_cost_and_audit_counts_are_one_per_actual_mutation(
    client: httpx.AsyncClient,
) -> None:
    """A local sanity check used by the keyed concurrency test as well."""
    owner = await owner_with_farm(client)
    animals = [
        await make_animal(client, owner, tag="COUNT-1"),
        await make_animal(client, owner, tag="COUNT-2"),
    ]
    reviewed = await preview(client, owner, scope="bucket", bucket="FOUNDATION")
    response = await client.post(
        "/api/health/events",
        json={
            "scope": "bucket",
            "bucket": "FOUNDATION",
            "expected_animal_ids": reviewed.json()["target_animal_ids"],
            "type": "TREATMENT",
            "cost": 25,
            "disease_target": "Count concern",
            "suspected_scheduled_disease": True,
        },
        headers=owner,
    )
    assert response.status_code == 201, response.text
    async with get_sessionmaker()() as db:
        assert (await db.execute(select(func.count(HealthEvent.id)))).scalar_one() == len(animals)
        assert (
            await db.execute(select(func.count(MovementRestrictionAction.id)))
        ).scalar_one() == len(animals)
        assert (await db.execute(select(func.count(Transaction.id)))).scalar_one() == 1


async def test_clearing_a_mortality_hold_keeps_the_authority_notification_date(
    client: httpx.AsyncClient,
) -> None:
    """The mortality path writes no HealthEvent, so the animal column is the
    only copy of a statutorily mandated notification date.

    ``clear_movement_restriction`` justified nulling it by saying the fact
    "stays on the HealthEvent that recorded the suspicion" — true for
    ``record_health_event``, false for ``POST /api/animals/{id}/status`` with
    ``suspected_scheduled_disease``, which places the hold with
    ``health_event_id=None``. There the clearance erased the date for good.
    """
    headers = await owner_with_farm(client)
    created = await client.post(
        "/api/animals",
        json={
            "tag_number": "NOTIFY-1",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "BREEDING",
            "date_of_birth": (today() - timedelta(days=700)).isoformat(),
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    animal_id = created.json()["id"]

    notified_on = today()
    dead = await client.post(
        f"/api/animals/{animal_id}/status",
        json={
            "new_status": "DEAD",
            "mortality_cause": "Sudden death",
            "suspected_scheduled_disease": True,
            "suspected_disease": "Anthrax",
            "authority_notified_at": notified_on.isoformat(),
        },
        headers=headers,
    )
    assert dead.status_code == 200, dead.text
    assert dead.json()["authority_notified_at"] == notified_on.isoformat()
    version = dead.json()["restriction_version"]

    cleared = await client.post(
        f"/api/health/restrictions/{animal_id}/clear",
        json={"clearance_reference": "AHD-2026-9", "expected_restriction_version": version},
        headers=headers,
    )
    assert cleared.status_code in (200, 204), cleared.text

    after = await client.get(f"/api/animals/{animal_id}", headers=headers)
    assert after.status_code == 200, after.text
    # GET /api/animals/{id} returns the profile, with the animal nested.
    body = after.json()["animal"]
    # The hold itself is closed...
    assert body["movement_restricted"] is False
    assert body["suspected_scheduled_disease"] is False
    assert body["suspected_disease"] is None
    # ...but the notification date, whose only copy this is, survives.
    assert body["authority_notified_at"] == notified_on.isoformat()
