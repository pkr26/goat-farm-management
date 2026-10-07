"""Health rounds retain task/authorization pins and compatible native parent locks."""

import asyncio
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import health as health_api
from app.api import tasks as tasks_api
from app.db import get_sessionmaker
from app.models import (
    Animal,
    BreedingRecord,
    BucketMove,
    Farm,
    FarmMembership,
    HealthRound,
    HealthRoundTarget,
    PurchaseBatch,
    Task,
    User,
)
from app.schemas.health import HealthEventIn
from app.services.health_rounds import ensure_round_snapshot
from app.services.tasks import complete_task, skip_task
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import all_tasks, make_animal, pregnant_doe
from .test_tasks_extended import worker_headers


async def _herd_duty(farm_id: int, *, worker_id: int | None = None) -> int:
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        role_id = None
        if worker_id is not None:
            membership = (
                await db.execute(
                    select(FarmMembership).where(
                        FarmMembership.farm_id == farm_id, FarmMembership.user_id == worker_id
                    )
                )
            ).scalar_one()
            assert membership.is_active is True
            role_id = membership.role_id
        # The retained unlinked nonrecurring programme is a documented herd
        # duty shape. All FK/assignment and clinical constraints remain enabled.
        task = Task(
            farm_id=farm_id,
            title="Vaccinate PPR",
            due_date=today(farm.timezone),
            category="VACCINE",
            auto_generated=True,
            status="PENDING",
            title_args={},
            assigned_user_id=worker_id,
            assigned_role_id=role_id,
            created_by_id=farm.owner_id,
        )
        db.add(task)
        await db.commit()
        return task.id


async def _blocked_by(pid: int) -> bool:
    async with get_sessionmaker()() as observer:
        return bool(
            await observer.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                    "WHERE datname=current_database() "
                    "AND :holder=ANY(pg_blocking_pids(pid)))"
                ),
                {"holder": pid},
            )
        )


async def _wait_for_writer_dependency(pid: int) -> None:
    for _ in range(1000):
        if await _blocked_by(pid):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("The actual request never reached the prepared PostgreSQL writer")


async def test_round_start_rechecks_a_genuine_concurrent_public_skip(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    await make_animal(client, owner, "ROUND-SKIP-ANIMAL")
    task_id = await _herd_duty(farm_id)
    skipped = asyncio.Event()
    release = asyncio.Event()
    writer_pids: list[int] = []

    async def pause_real_skip(
        db: AsyncSession, task: Task, user: User, reason: str | None = None
    ) -> Task:
        result = await skip_task(db, task, user, reason)
        if task.id == task_id:
            pid = await db.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(pid, int)
            writer_pids.append(pid)
            skipped.set()
            await release.wait()
        return result

    monkeypatch.setattr(tasks_api, "skip_task", pause_real_skip)
    skip = asyncio.create_task(
        client.post(
            f"/api/tasks/{task_id}/skip",
            headers=owner,
            json={"reason": "Programme cancelled by owner"},
        )
    )
    start: asyncio.Task[httpx.Response] | None = None
    try:
        await asyncio.wait_for(skipped.wait(), timeout=10)
        start = asyncio.create_task(
            client.post(f"/api/health/rounds/{task_id}/start", headers=owner)
        )
        await _wait_for_writer_dependency(writer_pids[0])
        release.set()
        skip_response = await asyncio.wait_for(skip, timeout=15)
        response = await asyncio.wait_for(start, timeout=15)
    finally:
        release.set()
        for request in (skip, start):
            if request is not None and not request.done():
                request.cancel()
        await asyncio.gather(
            *(request for request in (skip, start) if request is not None), return_exceptions=True
        )
    assert skip_response.status_code == 200, skip_response.text
    assert response.status_code == 409, response.text
    async with get_sessionmaker()() as db:
        task = await db.get(Task, task_id)
        assert task is not None and task.status == "SKIPPED"
        assert task.skip_reason == "Programme cancelled by owner"
        assert await db.get(HealthRound, task_id) is None


async def test_round_start_pins_the_inactive_named_assignee_during_peer_fallback(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    peer, _peer_id = await worker_headers(client, owner, "MANAGER", "round-peer@farm.in")
    _assignee_headers, assignee_id = await worker_headers(
        client, owner, "MANAGER", "round-assignee@farm.in"
    )
    await make_animal(client, owner, "ROUND-FALLBACK-ANIMAL")
    task_id = await _herd_duty(farm_id, worker_id=assignee_id)
    async with get_sessionmaker()() as db:
        membership_id = (
            await db.execute(
                select(FarmMembership.id).where(
                    FarmMembership.farm_id == farm_id, FarmMembership.user_id == assignee_id
                )
            )
        ).scalar_one()
    deactivated = await client.put(
        f"/api/team/workers/{membership_id}/status", headers=owner, json={"is_active": False}
    )
    assert deactivated.status_code == 200, deactivated.text
    ready = asyncio.Event()
    release = asyncio.Event()
    starter_pids: list[int] = []

    async def pause_snapshot(db: AsyncSession, task: Task) -> HealthRound:
        if task.id == task_id:
            pid = await db.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(pid, int)
            starter_pids.append(pid)
            ready.set()
            await release.wait()
        return await ensure_round_snapshot(db, task)

    monkeypatch.setattr(health_api, "ensure_round_snapshot", pause_snapshot)
    start = asyncio.create_task(client.post(f"/api/health/rounds/{task_id}/start", headers=peer))
    activate: asyncio.Task[httpx.Response] | None = None
    try:
        await asyncio.wait_for(ready.wait(), timeout=10)
        activate = asyncio.create_task(
            client.put(
                f"/api/team/workers/{membership_id}/status", headers=owner, json={"is_active": True}
            )
        )
        for _ in range(1000):
            if await _blocked_by(starter_pids[0]):
                break
            if activate.done():
                result = await activate
                assert result.status_code == 200, result.text
                raise AssertionError(
                    "A named assignee reactivated before the fallback round decision committed"
                )
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("The real reactivation did not reach the pinned assignee")
        release.set()
        response = await asyncio.wait_for(start, timeout=15)
        activated = await asyncio.wait_for(activate, timeout=15)
    finally:
        release.set()
        for request in (start, activate):
            if request is not None and not request.done():
                request.cancel()
        await asyncio.gather(
            *(request for request in (start, activate) if request is not None),
            return_exceptions=True,
        )
    assert response.status_code == 200, response.text
    assert activated.status_code == 200, activated.text
    async with get_sessionmaker()() as db:
        membership = await db.get(FarmMembership, membership_id)
        assert membership is not None and membership.is_active is True
        assert await db.get(HealthRound, task_id) is not None


async def test_round_start_keeps_animal_before_task_order_against_real_health_evidence(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    animal = await make_animal(client, owner, "ROUND-EVIDENCE-ANIMAL")
    task_id = await _herd_duty(farm_id)
    initialized = await client.post(f"/api/health/rounds/{task_id}/start", headers=owner)
    assert initialized.status_code == 200, initialized.text
    real_lock_targets = health_api._lock_event_targets
    animals_locked = asyncio.Event()
    release = asyncio.Event()
    writer_pids: list[int] = []

    async def pause_real_targets(
        db: AsyncSession, farm: Farm, payload: HealthEventIn, *, linked_batch_id: int | None = None
    ) -> tuple[list[Animal], PurchaseBatch | None, int | None]:
        result = await real_lock_targets(db, farm, payload, linked_batch_id=linked_batch_id)
        if payload.task_id == task_id:
            pid = await db.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(pid, int)
            writer_pids.append(pid)
            animals_locked.set()
            await release.wait()
        return result

    monkeypatch.setattr(health_api, "_lock_event_targets", pause_real_targets)
    evidence = asyncio.create_task(
        client.post(
            "/api/health/events",
            headers=owner,
            json={
                "scope": "bucket",
                "bucket": "FOUNDATION",
                "expected_animal_ids": [animal["id"]],
                "type": "VACCINE",
                "disease_target": "PPR",
                "task_id": task_id,
            },
        )
    )
    start: asyncio.Task[httpx.Response] | None = None
    try:
        await asyncio.wait_for(animals_locked.wait(), timeout=10)
        start = asyncio.create_task(
            client.post(f"/api/health/rounds/{task_id}/start", headers=owner)
        )
        await _wait_for_writer_dependency(writer_pids[0])
        release.set()
        evidence_response = await asyncio.wait_for(evidence, timeout=15)
        response = await asyncio.wait_for(start, timeout=15)
    finally:
        release.set()
        for request in (evidence, start):
            if request is not None and not request.done():
                request.cancel()
        await asyncio.gather(
            *(request for request in (evidence, start) if request is not None),
            return_exceptions=True,
        )
    assert evidence_response.status_code == 201, evidence_response.text
    assert response.status_code == 409, response.text
    async with get_sessionmaker()() as db:
        task = await db.get(Task, task_id)
        assert task is not None and task.status == "DONE" and task.completed_by_id is not None
        assert await db.get(HealthRound, task_id) is not None


async def test_snapshot_remains_available_during_a_native_nonkey_prepartum_move(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    doe, buck, breeding = await pregnant_doe(
        client, owner, "ROUND-NONKEY-DELIVERY", gestation_days=140
    )
    delivery = next(
        task
        for task in await all_tasks(client, owner)
        if task["breeding_record_id"] == breeding["id"]
        and task["category"] == "BUCKET_MOVE"
        and date.fromisoformat(task["due_date"])
        == date.fromisoformat(breeding["expected_kidding_date"]) - timedelta(days=15)
    )
    task_id = await _herd_duty(farm_id)
    request: asyncio.Task[httpx.Response] | None = None
    async with get_sessionmaker()() as writer:
        farm = await writer.get(Farm, farm_id)
        assert farm is not None
        user = await writer.get(User, farm.owner_id)
        assert user is not None
        # NO KEY UPDATE is a real canonical prelock protecting every nonkey
        # field this movement changes. Referenced id/farm_id stay fixed.
        animal = (
            await writer.execute(
                select(Animal)
                .where(Animal.id == doe["id"], Animal.farm_id == farm_id)
                .with_for_update(key_share=True)
            )
        ).scalar_one()
        identity = (animal.id, animal.farm_id)
        duty = (
            await writer.execute(select(Task).where(Task.id == delivery["id"]).with_for_update())
        ).scalar_one()
        await complete_task(
            writer, duty, user, locked_animals=[animal], reference_date=today(farm.timezone)
        )
        assert animal.current_bucket == "DELIVERY" and (animal.id, animal.farm_id) == identity
        assert duty.status == "DONE" and duty.completed_by_id == user.id
        assert duty.completed_at is not None
        completion_audit = (duty.completed_by_id, duty.completed_at)
        pid = await writer.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(pid, int)
        request = asyncio.create_task(
            client.post(f"/api/health/rounds/{task_id}/start", headers=owner)
        )
        try:
            try:
                response = await asyncio.wait_for(asyncio.shield(request), timeout=5)
            except TimeoutError:
                if await _blocked_by(pid):
                    pytest.fail(
                        "A health snapshot was blocked by a real compatible native nonkey movement"
                    )
                raise
            assert response.status_code == 200, response.text
            assert {target["animal_id"] for target in response.json()["targets"]} == {
                doe["id"],
                buck["id"],
            }
            await writer.commit()
        finally:
            await writer.rollback()
            if request is not None and not request.done():
                request.cancel()
            if request is not None:
                await asyncio.gather(request, return_exceptions=True)
    async with get_sessionmaker()() as db:
        animal = (await db.execute(select(Animal).where(Animal.id == doe["id"]))).scalar_one()
        duty = (await db.execute(select(Task).where(Task.id == delivery["id"]))).scalar_one()
        assert (animal.id, animal.farm_id) == identity and animal.current_bucket == "DELIVERY"
        assert duty.status == "DONE"
        assert (duty.completed_by_id, duty.completed_at) == completion_audit
        assert await db.get(HealthRoundTarget, (task_id, animal.id)) is not None
        clinical = await db.get(BreedingRecord, breeding["id"])
        assert clinical is not None and clinical.outcome == "CONFIRMED_PREGNANT"
        assert clinical.pregnant is True and clinical.loss_date is None
        move = (
            await db.execute(
                select(BucketMove).where(
                    BucketMove.animal_id == animal.id,
                    BucketMove.to_bucket == "DELIVERY",
                )
            )
        ).scalar_one()
        assert move.created_by_id == completion_audit[0]
        assert move.reason == "~2 weeks before due date"
