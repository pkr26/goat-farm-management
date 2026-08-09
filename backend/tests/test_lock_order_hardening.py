"""Deterministic PostgreSQL regressions for cross-domain lock ordering.

These tests deliberately park one transaction at the dangerous boundary,
wait until PostgreSQL reports the competing request as lock-blocked, and only
then start the inverse lifecycle operation. They therefore exercise the exact
interleavings that used to deadlock instead of relying on timing or repeated
``asyncio.gather`` luck.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

import app.api.tasks as tasks_api
import app.api.team as team_api
import app.services.tasks as task_service
from app.db import get_sessionmaker
from app.main import create_app
from app.models import (
    WEANING_DAYS,
    Animal,
    BreedingRecord,
    FarmMembership,
    HealthEvent,
    KiddingRecord,
    KidEntry,
    Role,
    Task,
    TaskStatus,
    User,
    WeightRecord,
)
from app.utils import today, utcnow

from .conftest import owner_with_farm

WORKER_PASSWORD = "workerpass123"


def second_client() -> httpx.AsyncClient:
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


async def role_id(client: httpx.AsyncClient, owner: dict[str, str], code: str) -> int:
    response = await client.get("/api/team", headers=owner)
    assert response.status_code == 200, response.text
    return int(next(role["id"] for role in response.json()["roles"] if role["code"] == code))


async def custom_role(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    name: str,
    permissions: list[str] | None = None,
) -> int:
    response = await client.post(
        "/api/team/roles",
        json={"name": name, "permissions": permissions or []},
        headers=owner,
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


async def make_animal(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    tag: str,
) -> int:
    response = await client.post(
        "/api/animals",
        json={
            "tag_number": tag,
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "historical_import_reason": "Lock-order regression fixture",
        },
        headers=owner,
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


async def create_worker(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    role: int,
    email: str,
) -> tuple[int, int]:
    response = await client.post(
        "/api/team/workers",
        json={
            "email": email,
            "name": "Race Worker",
            "password": WORKER_PASSWORD,
            "role_id": role,
        },
        headers=owner,
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"]), int(response.json()["user_id"])


async def login_worker(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    email: str,
) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login",
        json={"email": email, "password": WORKER_PASSWORD},
    )
    assert response.status_code == 200, response.text
    return {
        "Authorization": f"Bearer {response.json()['access_token']}",
        "X-Farm-Id": owner["X-Farm-Id"],
    }


async def finish_pair(
    first: asyncio.Task[httpx.Response],
    second: asyncio.Task[httpx.Response],
) -> tuple[httpx.Response, httpx.Response]:
    """Bound every race so a lock regression fails quickly, never hangs CI."""
    async with asyncio.timeout(10):
        return await asyncio.gather(first, second)


async def assert_animal_row_locked(animal_id: int) -> None:
    """Assert another transaction cannot acquire this animal row NOWAIT."""
    async with get_sessionmaker()() as probe:
        try:
            await probe.execute(
                select(Animal.id).where(Animal.id == animal_id).with_for_update(nowait=True)
            )
        except DBAPIError:
            await probe.rollback()
            return
        await probe.rollback()
    raise AssertionError(f"animal {animal_id} was not pre-locked")


async def test_linked_health_locks_animal_before_task_status_change(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    purchase = await client.post(
        "/api/purchases/new",
        json={
            "date": (today() - timedelta(days=9)).isoformat(),
            "supplier": "Health lock-order fixture",
            "count": 1,
            "avg_age_months": 7,
            "avg_weight_kg": 15,
            "total_price": 100,
            "create_animals": True,
        },
        headers=owner,
    )
    assert purchase.status_code == 201, purchase.text
    batch_id = purchase.json()["id"]
    detail = await client.get(f"/api/purchases/{batch_id}", headers=owner)
    assert detail.status_code == 200, detail.text
    animal_id = detail.json()["animals"][0]["id"]
    task_id = next(task["id"] for task in detail.json()["tasks"] if "PPR" in task["title"])

    holder = get_sessionmaker()()
    await holder.execute(select(Task.id).where(Task.id == task_id).with_for_update())
    async with second_client() as health_client, second_client() as status_client:
        health_request = asyncio.create_task(
            health_client.post(
                "/api/health/events",
                json={
                    "scope": "batch",
                    "purchase_batch_id": batch_id,
                    "expected_animal_ids": [animal_id],
                    "type": "VACCINE",
                    "product_name": "PPR vaccine",
                    "task_id": task_id,
                },
                headers=owner,
            )
        )
        status_request: asyncio.Task[httpx.Response] | None = None
        try:
            # The health request is blocked on TASK only after it has acquired
            # the animal row. The status request must therefore block on that
            # animal instead of creating TASK -> ANIMAL / ANIMAL -> TASK.
            await wait_for_lock_waiters(1)
            status_request = asyncio.create_task(
                status_client.post(
                    f"/api/animals/{animal_id}/status",
                    json={"new_status": "SOLD"},
                    headers=owner,
                )
            )
            await wait_for_lock_waiters(2)
            await holder.rollback()
            health_response, status_response = await finish_pair(
                health_request,
                status_request,
            )
        finally:
            await holder.rollback()
            await holder.close()
            if not health_request.done():
                health_request.cancel()
            if status_request is not None and not status_request.done():
                status_request.cancel()

    assert health_response.status_code == 201, health_response.text
    assert status_response.status_code == 200, status_response.text
    async with get_sessionmaker()() as db:
        task = await db.get(Task, task_id)
        animal = await db.get(Animal, animal_id)
        event_count = (
            await db.execute(
                select(func.count())
                .select_from(HealthEvent)
                .where(HealthEvent.animal_id == animal_id)
            )
        ).scalar_one()
    assert task is not None and task.status == TaskStatus.DONE.value
    assert animal is not None and animal.status == "SOLD"
    assert event_count == 1


async def test_recurring_skip_locks_animal_before_task_status_change(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal_id = await make_animal(client, owner, "SKIP-LOCK")
    created = await client.post(
        "/api/tasks",
        json={
            "title": "Recurring animal check",
            "due_date": today().isoformat(),
            "category": "OTHER",
            "animal_id": animal_id,
            "recur_days": 1,
        },
        headers=owner,
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]
    series_id = created.json()["recurring_series_id"]

    holder = get_sessionmaker()()
    await holder.execute(select(Task.id).where(Task.id == task_id).with_for_update())
    async with second_client() as skip_client, second_client() as status_client:
        skip_request = asyncio.create_task(
            skip_client.post(
                f"/api/tasks/{task_id}/skip",
                json={"reason": "Not needed today"},
                headers=owner,
            )
        )
        status_request: asyncio.Task[httpx.Response] | None = None
        try:
            # Skip must already own Animal when it queues behind the held Task.
            await wait_for_lock_waiters(1)
            await assert_animal_row_locked(animal_id)
            status_request = asyncio.create_task(
                status_client.post(
                    f"/api/animals/{animal_id}/status",
                    json={"new_status": "SOLD"},
                    headers=owner,
                )
            )
            await wait_for_lock_waiters(2)
            await holder.rollback()
            skip_response, status_response = await finish_pair(skip_request, status_request)
        finally:
            await holder.rollback()
            await holder.close()
            if not skip_request.done():
                skip_request.cancel()
            if status_request is not None and not status_request.done():
                status_request.cancel()

    assert skip_response.status_code == 200, skip_response.text
    assert status_response.status_code == 200, status_response.text
    async with get_sessionmaker()() as db:
        tasks = list(
            (
                await db.execute(
                    select(Task)
                    .where(Task.recurring_series_id == series_id)
                    .order_by(Task.due_date, Task.id)
                )
            ).scalars()
        )
    assert len(tasks) == 2
    assert all(task.status == TaskStatus.SKIPPED.value for task in tasks)


async def test_linked_batch_health_rejects_subset_and_stale_full_snapshot(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    purchase = await client.post(
        "/api/purchases/new",
        json={
            "date": today().isoformat(),
            "supplier": "Lock-order supplier",
            "count": 2,
            "avg_age_months": 7,
            "avg_weight_kg": 15,
            "total_price": 200,
            "create_animals": True,
        },
        headers=owner,
    )
    assert purchase.status_code == 201, purchase.text
    batch_id = purchase.json()["id"]
    detail = await client.get(f"/api/purchases/{batch_id}", headers=owner)
    assert detail.status_code == 200, detail.text
    animal_ids = sorted(animal["id"] for animal in detail.json()["animals"])
    vaccine = next(task for task in detail.json()["tasks"] if task["category"] == "VACCINE")

    forged_subset = await client.post(
        "/api/health/events",
        json={
            "scope": "batch",
            "purchase_batch_id": batch_id,
            "expected_animal_ids": animal_ids[:1],
            "type": "VACCINE",
            "task_id": vaccine["id"],
        },
        headers=owner,
    )
    assert forged_subset.status_code == 409, forged_subset.text
    assert "active quarantine animals" in forged_subset.json()["detail"]

    # The exact preview becomes stale when one target leaves the active herd;
    # an old full snapshot must not complete the whole protocol duty either.
    removed = await client.post(
        f"/api/animals/{animal_ids[-1]}/status",
        json={"new_status": "DEAD"},
        headers=owner,
    )
    assert removed.status_code == 200, removed.text
    stale_full_set = await client.post(
        "/api/health/events",
        json={
            "scope": "batch",
            "purchase_batch_id": batch_id,
            "expected_animal_ids": animal_ids,
            "type": "VACCINE",
            "task_id": vaccine["id"],
        },
        headers=owner,
    )
    assert stale_full_set.status_code == 409, stale_full_set.text

    async with get_sessionmaker()() as db:
        task = await db.get(Task, vaccine["id"])
        event_count = (
            await db.execute(
                select(func.count())
                .select_from(HealthEvent)
                .where(HealthEvent.purchase_batch_id == batch_id)
            )
        ).scalar_one()
    assert task is not None and task.status == TaskStatus.PENDING.value
    assert event_count == 0


async def test_recurring_completion_vs_worker_toggle_catches_successor(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    cleaner = await role_id(client, owner, "CLEANER")
    membership_id, worker_id = await create_worker(
        client,
        owner,
        cleaner,
        "toggle-race@farm.in",
    )
    created = await client.post(
        "/api/tasks",
        json={
            "title": "Recurring personal clean",
            "due_date": today().isoformat(),
            "category": "OTHER",
            "assigned_role_id": cleaner,
            "assigned_user_id": worker_id,
            "recur_days": 1,
        },
        headers=owner,
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]

    # Queue the toggle first on a strong holder lock. Completion then owns the
    # Task and queues its successor's FK KEY SHARE behind the same membership.
    # Toggle changes only Membership with compatible NO KEY UPDATE; neither
    # side scans or rewrites Task history.
    holder = get_sessionmaker()()
    await holder.execute(
        select(FarmMembership.id).where(FarmMembership.id == membership_id).with_for_update()
    )
    async with second_client() as toggle_client, second_client() as completion_client:
        toggle_request = asyncio.create_task(
            toggle_client.put(
                f"/api/team/workers/{membership_id}/status",
                json={"is_active": False},
                headers=owner,
            )
        )
        completion_request: asyncio.Task[httpx.Response] | None = None
        try:
            await wait_for_lock_waiters(1)
            completion_request = asyncio.create_task(
                completion_client.post(f"/api/tasks/{task_id}/complete", headers=owner)
            )
            await wait_for_lock_waiters(2)
            await holder.rollback()
            toggle_response, completion_response = await finish_pair(
                toggle_request,
                completion_request,
            )
        finally:
            await holder.rollback()
            await holder.close()
            if not toggle_request.done():
                toggle_request.cancel()
            if completion_request is not None and not completion_request.done():
                completion_request.cancel()

    assert toggle_response.status_code == 200, toggle_response.text
    assert completion_response.status_code == 200, completion_response.text
    async with get_sessionmaker()() as db:
        membership = await db.get(FarmMembership, membership_id)
        rows = list(
            (
                await db.execute(
                    select(Task)
                    .where(Task.recurring_series_id == created.json()["recurring_series_id"])
                    .order_by(Task.due_date, Task.id)
                )
            ).scalars()
        )
    assert membership is not None and membership.is_active is False
    assert len(rows) == 2
    original, successor = rows
    assert original.status == TaskStatus.DONE.value
    assert successor.status == TaskStatus.PENDING.value
    assert successor.assigned_user_id == worker_id
    assert successor.assigned_role_id == cleaner


async def test_recompleted_overdue_occurrences_atomically_share_one_successor(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    cleaner = await role_id(client, owner, "CLEANER")
    series_id = "rejected-overdue-successor-race"
    async with get_sessionmaker()() as db:
        occurrences = [
            Task(
                farm_id=farm_id,
                title="Rejected overdue clean",
                due_date=today() - timedelta(days=days_ago),
                status=TaskStatus.DONE.value,
                category="CLEANING",
                auto_generated=False,
                assigned_role_id=cleaner,
                recur_days=1,
                recurring_series_id=series_id,
                completed_at=utcnow(),
            )
            for days_ago in (2, 1)
        ]
        db.add_all(occurrences)
        await db.commit()
        occurrence_ids = [task.id for task in occurrences]

    for task_id in occurrence_ids:
        rejected = await client.post(
            f"/api/tasks/{task_id}/reject",
            json={"note": "Repeat this missed occurrence"},
            headers=owner,
        )
        assert rejected.status_code == 200, rejected.text

    # Both requests start concurrently, but recurring transitions serialize on
    # Farm before Task. They therefore cannot deadlock with review rejection or
    # manufacture two successors at the same anchored date.
    async with second_client() as first_client, second_client() as second_client_instance:
        first = asyncio.create_task(
            first_client.post(f"/api/tasks/{occurrence_ids[0]}/complete", headers=owner)
        )
        second = asyncio.create_task(
            second_client_instance.post(
                f"/api/tasks/{occurrence_ids[1]}/complete",
                headers=owner,
            )
        )
        first_response, second_response = await finish_pair(first, second)

    assert first_response.status_code == 200, first_response.text
    assert second_response.status_code == 200, second_response.text
    async with get_sessionmaker()() as db:
        rows = list(
            (
                await db.execute(
                    select(Task)
                    .where(Task.recurring_series_id == series_id)
                    .order_by(Task.due_date, Task.id)
                )
            ).scalars()
        )
    assert len(rows) == 3
    assert [task.status for task in rows] == [
        TaskStatus.DONE.value,
        TaskStatus.DONE.value,
        TaskStatus.PENDING.value,
    ]
    assert rows[-1].due_date == today() + timedelta(days=1)
    assert rows[-1].assigned_role_id == cleaner


async def test_manual_role_task_share_lock_serializes_role_delete(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)
    role = await custom_role(client, owner, "Manual Assignment Race")

    entered_create = asyncio.Event()
    release_create = asyncio.Event()
    original_create = tasks_api.create_manual_task

    async def paused_create(*args: object, **kwargs: object) -> Task:
        # The route has validated and FOR SHARE-locked Role before entering
        # the service, but has not inserted the Task yet.
        entered_create.set()
        await release_create.wait()
        return await original_create(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(tasks_api, "create_manual_task", paused_create)
    async with second_client() as create_client, second_client() as delete_client:
        create_request = asyncio.create_task(
            create_client.post(
                "/api/tasks",
                json={
                    "title": "Role-locked manual task",
                    "due_date": today().isoformat(),
                    "assigned_role_id": role,
                },
                headers=owner,
            )
        )
        delete_request: asyncio.Task[httpx.Response] | None = None
        try:
            await asyncio.wait_for(entered_create.wait(), timeout=5)
            delete_request = asyncio.create_task(
                delete_client.delete(f"/api/team/roles/{role}", headers=owner)
            )
            # Deletion's FOR UPDATE conflicts with manual validation's
            # explicit FOR SHARE and cannot make the validated parent vanish.
            await wait_for_lock_waiters(1)
            release_create.set()
            create_response, delete_response = await finish_pair(
                create_request,
                delete_request,
            )
        finally:
            release_create.set()
            if not create_request.done():
                create_request.cancel()
            if delete_request is not None and not delete_request.done():
                delete_request.cancel()

    assert create_response.status_code == 201, create_response.text
    assert delete_response.status_code == 409, delete_response.text
    async with get_sessionmaker()() as db:
        deleted_role = await db.get(Role, role)
        task = await db.get(Task, create_response.json()["id"])
    assert deleted_role is not None and deleted_role.deleted_at is None
    assert task is not None and task.assigned_role_id == role


async def test_manual_animal_task_share_lock_serializes_status_removal(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)
    animal_id = await make_animal(client, owner, "MANUAL-ANIMAL-RACE")

    entered_create = asyncio.Event()
    release_create = asyncio.Event()
    original_create = tasks_api.create_manual_task

    async def paused_create(*args: object, **kwargs: object) -> Task:
        # The route has locked and re-checked ACTIVE Animal, but has not
        # inserted its task. Status removal must wait for this transaction.
        entered_create.set()
        await release_create.wait()
        return await original_create(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(tasks_api, "create_manual_task", paused_create)
    async with second_client() as create_client, second_client() as status_client:
        create_request = asyncio.create_task(
            create_client.post(
                "/api/tasks",
                json={
                    "title": "Linked manual task",
                    "due_date": today().isoformat(),
                    "animal_id": animal_id,
                },
                headers=owner,
            )
        )
        status_request: asyncio.Task[httpx.Response] | None = None
        try:
            await asyncio.wait_for(entered_create.wait(), timeout=5)
            status_request = asyncio.create_task(
                status_client.post(
                    f"/api/animals/{animal_id}/status",
                    json={"new_status": "SOLD"},
                    headers=owner,
                )
            )
            await wait_for_lock_waiters(1)
            release_create.set()
            create_response, status_response = await finish_pair(
                create_request,
                status_request,
            )
        finally:
            release_create.set()
            if not create_request.done():
                create_request.cancel()
            if status_request is not None and not status_request.done():
                status_request.cancel()

    assert create_response.status_code == 201, create_response.text
    assert status_response.status_code == 200, status_response.text
    async with get_sessionmaker()() as db:
        task = await db.get(Task, create_response.json()["id"])
        animal = await db.get(Animal, animal_id)
    assert task is not None and task.status == TaskStatus.SKIPPED.value
    assert animal is not None and animal.status == "SOLD"


async def test_generic_weaning_prelocks_doe_and_every_affected_kid(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        doe = Animal(
            farm_id=farm_id,
            tag_number="WEAN-DOE",
            sex="F",
            source="PURCHASED",
            current_bucket="RECOVERY",
        )
        buck = Animal(
            farm_id=farm_id,
            tag_number="WEAN-SIRE",
            sex="M",
            source="PURCHASED",
            current_bucket="BREEDING",
        )
        db.add_all([doe, buck])
        await db.flush()
        kids = [
            Animal(
                farm_id=farm_id,
                tag_number=f"WEAN-KID-{sex}",
                sex=sex,
                source="BORN",
                birth_type="TWIN",
                dam_id=doe.id,
                current_bucket="RECOVERY",
            )
            for sex in ("M", "F")
        ]
        db.add_all(kids)
        await db.flush()
        kidding_date = today() - timedelta(days=WEANING_DAYS)
        breeding = BreedingRecord(
            farm_id=farm_id,
            doe_id=doe.id,
            buck_id=buck.id,
            breeding_date=kidding_date - timedelta(days=150),
            ultrasound_date=kidding_date - timedelta(days=118),
            ultrasound_result_date=kidding_date - timedelta(days=118),
            ultrasound_done=True,
            pregnant=True,
            kid_count_detected=2,
            expected_kidding_date=kidding_date,
            outcome="CONFIRMED_PREGNANT",
        )
        db.add(breeding)
        await db.flush()
        kidding = KiddingRecord(
            farm_id=farm_id,
            doe_id=doe.id,
            date=kidding_date,
            breeding_record_id=breeding.id,
            ease="NORMAL",
        )
        db.add(kidding)
        await db.flush()
        db.add_all(
            [
                KidEntry(
                    farm_id=farm_id,
                    kidding_record_id=kidding.id,
                    tag=kid.tag_number,
                    sex=kid.sex,
                    status="ALIVE",
                    animal_id=kid.id,
                )
                for kid in kids
            ]
        )
        task = Task(
            farm_id=farm_id,
            title="Wean every kid",
            due_date=today(),
            category="WEANING",
            animal_id=doe.id,
            auto_generated=True,
        )
        db.add(task)
        await db.commit()
        task_id = task.id
        affected_ids = sorted([doe.id, *(kid.id for kid in kids)])

    holder = get_sessionmaker()()
    await holder.execute(select(Task.id).where(Task.id == task_id).with_for_update())
    async with second_client() as completion_client:
        request = asyncio.create_task(
            completion_client.post(f"/api/tasks/{task_id}/complete", headers=owner)
        )
        try:
            await wait_for_lock_waiters(1)
            for animal_id in affected_ids:
                await assert_animal_row_locked(animal_id)
            await holder.rollback()
            async with asyncio.timeout(10):
                response = await request
        finally:
            await holder.rollback()
            await holder.close()
            if not request.done():
                request.cancel()

    assert response.status_code == 200, response.text
    async with get_sessionmaker()() as db:
        rows = list(
            (
                await db.execute(
                    select(Animal).where(Animal.id.in_(affected_ids)).order_by(Animal.id)
                )
            ).scalars()
        )
    buckets = {row.tag_number: row.current_bucket for row in rows}
    assert buckets == {
        "WEAN-DOE": "RESTING",
        "WEAN-KID-M": "MALE_KIDS",
        "WEAN-KID-F": "FEMALE_KIDS",
    }


async def test_generic_quarantine_release_prelocks_entire_active_batch(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    purchase = await client.post(
        "/api/purchases/new",
        json={
            "date": today().isoformat(),
            "count": 2,
            "create_animals": True,
            "total_price": 200,
        },
        headers=owner,
    )
    assert purchase.status_code == 201, purchase.text
    batch_id = purchase.json()["id"]
    detail = await client.get(f"/api/purchases/{batch_id}", headers=owner)
    assert detail.status_code == 200, detail.text
    animal_ids = sorted(animal["id"] for animal in detail.json()["animals"])
    release_task = next(
        task for task in detail.json()["tasks"] if task["category"] == "BUCKET_MOVE"
    )

    holder = get_sessionmaker()()
    await holder.execute(select(Task.id).where(Task.id == release_task["id"]).with_for_update())
    async with second_client() as completion_client:
        request = asyncio.create_task(
            completion_client.post(
                f"/api/tasks/{release_task['id']}/complete",
                headers=owner,
            )
        )
        try:
            await wait_for_lock_waiters(1)
            for animal_id in animal_ids:
                await assert_animal_row_locked(animal_id)
            await holder.rollback()
            async with asyncio.timeout(10):
                response = await request
        finally:
            await holder.rollback()
            await holder.close()
            if not request.done():
                request.cancel()

    # Day-45 release is not due and its prerequisites are incomplete, but all
    # animals were demonstrably locked before the Task state check.
    assert response.status_code == 409, response.text


async def test_recurring_role_completion_vs_role_delete_has_no_cycle(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)
    role = await custom_role(client, owner, "Recurring Assignment Race")
    created = await client.post(
        "/api/tasks",
        json={
            "title": "Recurring role task",
            "due_date": today().isoformat(),
            "category": "OTHER",
            "assigned_role_id": role,
            "recur_days": 1,
        },
        headers=owner,
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]
    series_id = created.json()["recurring_series_id"]

    entered_spawn = asyncio.Event()
    release_spawn = asyncio.Event()
    original_spawn = task_service.spawn_next_occurrence

    async def paused_spawn(*args: object, **kwargs: object) -> Task:
        # complete_task owns Task here. Role deletion sees the still-pending
        # occurrence and must refuse to tombstone its only assignment target.
        entered_spawn.set()
        await release_spawn.wait()
        return await original_spawn(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(task_service, "spawn_next_occurrence", paused_spawn)
    async with second_client() as completion_client, second_client() as delete_client:
        completion_request = asyncio.create_task(
            completion_client.post(f"/api/tasks/{task_id}/complete", headers=owner)
        )
        delete_request: asyncio.Task[httpx.Response] | None = None
        try:
            await asyncio.wait_for(entered_spawn.wait(), timeout=5)
            delete_request = asyncio.create_task(
                delete_client.delete(f"/api/team/roles/{role}", headers=owner)
            )
            delete_response = await asyncio.wait_for(delete_request, timeout=5)
            release_spawn.set()
            completion_response = await asyncio.wait_for(completion_request, timeout=5)
        finally:
            release_spawn.set()
            if not completion_request.done():
                completion_request.cancel()
            if delete_request is not None and not delete_request.done():
                delete_request.cancel()

    assert completion_response.status_code == 200, completion_response.text
    assert delete_response.status_code == 409, delete_response.text
    async with get_sessionmaker()() as db:
        deleted_role = await db.get(Role, role)
        rows = list(
            (
                await db.execute(
                    select(Task)
                    .where(Task.recurring_series_id == series_id)
                    .order_by(Task.due_date, Task.id)
                )
            ).scalars()
        )
    assert len(rows) == 2
    assert deleted_role is not None and deleted_role.deleted_at is None
    assert [row.status for row in rows] == [TaskStatus.DONE.value, TaskStatus.PENDING.value]
    assert all(row.assigned_role_id == role for row in rows)


async def test_worker_deactivation_wins_before_personal_task_assignment(
    client: httpx.AsyncClient,
) -> None:
    """Assignment locks Membership then User and rechecks both after waiting."""
    owner = await owner_with_farm(client)
    cleaner = await role_id(client, owner, "CLEANER")
    membership_id, worker_id = await create_worker(
        client,
        owner,
        cleaner,
        "assignment-deactivate-race@farm.in",
    )

    holder = get_sessionmaker()()
    await holder.execute(
        select(FarmMembership.id).where(FarmMembership.id == membership_id).with_for_update()
    )
    async with second_client() as admin_client, second_client() as task_client:
        deactivate = asyncio.create_task(
            admin_client.put(
                f"/api/team/workers/{membership_id}/status",
                json={"is_active": False},
                headers=owner,
            )
        )
        assignment: asyncio.Task[httpx.Response] | None = None
        try:
            await wait_for_lock_waiters(1)
            assignment = asyncio.create_task(
                task_client.post(
                    "/api/tasks",
                    json={
                        "title": "Must not reach inactive worker",
                        "due_date": today().isoformat(),
                        "assigned_user_id": worker_id,
                    },
                    headers=owner,
                )
            )
            await wait_for_lock_waiters(2)
            await holder.rollback()
            deactivate_response, assignment_response = await finish_pair(
                deactivate,
                assignment,
            )
        finally:
            await holder.rollback()
            await holder.close()
            if not deactivate.done():
                deactivate.cancel()
            if assignment is not None and not assignment.done():
                assignment.cancel()

    assert deactivate_response.status_code == 200, deactivate_response.text
    assert assignment_response.status_code == 400, assignment_response.text
    assert "not an active member" in assignment_response.json()["detail"]
    async with get_sessionmaker()() as db:
        count = (
            await db.execute(
                select(func.count()).select_from(Task).where(Task.assigned_user_id == worker_id)
            )
        ).scalar_one()
    assert count == 0


async def test_password_reset_and_personal_task_assignment_share_canonical_order(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    cleaner = await role_id(client, owner, "CLEANER")
    membership_id, worker_id = await create_worker(
        client,
        owner,
        cleaner,
        "assignment-reset-race@farm.in",
    )

    holder = get_sessionmaker()()
    await holder.execute(
        select(FarmMembership.id).where(FarmMembership.id == membership_id).with_for_update()
    )
    async with second_client() as reset_client, second_client() as task_client:
        reset = asyncio.create_task(
            reset_client.post(
                f"/api/team/workers/{membership_id}/reset-password",
                json={"password": "replacementpass1"},
                headers=owner,
            )
        )
        assignment: asyncio.Task[httpx.Response] | None = None
        try:
            await wait_for_lock_waiters(1)
            assignment = asyncio.create_task(
                task_client.post(
                    "/api/tasks",
                    json={
                        "title": "Assignment after password reset",
                        "due_date": today().isoformat(),
                        "assigned_user_id": worker_id,
                    },
                    headers=owner,
                )
            )
            await wait_for_lock_waiters(2)
            await holder.rollback()
            reset_response, assignment_response = await finish_pair(reset, assignment)
        finally:
            await holder.rollback()
            await holder.close()
            if not reset.done():
                reset.cancel()
            if assignment is not None and not assignment.done():
                assignment.cancel()

    assert reset_response.status_code == 200, reset_response.text
    assert assignment_response.status_code == 201, assignment_response.text
    assert assignment_response.json()["assigned_user_id"] == worker_id


async def test_account_tombstone_wins_before_personal_task_assignment(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    cleaner = await role_id(client, owner, "CLEANER")
    _membership_id, worker_id = await create_worker(
        client,
        owner,
        cleaner,
        "assignment-delete-race@farm.in",
    )
    worker = await login_worker(client, owner, "assignment-delete-race@farm.in")

    holder = get_sessionmaker()()
    await holder.execute(select(User.id).where(User.id == worker_id).with_for_update())
    async with second_client() as delete_client, second_client() as task_client:
        deletion = asyncio.create_task(
            delete_client.request(
                "DELETE",
                "/api/auth/account",
                json={"current_password": WORKER_PASSWORD},
                headers=worker,
            )
        )
        assignment: asyncio.Task[httpx.Response] | None = None
        try:
            await wait_for_lock_waiters(1)
            assignment = asyncio.create_task(
                task_client.post(
                    "/api/tasks",
                    json={
                        "title": "Must not reach deleted worker",
                        "due_date": today().isoformat(),
                        "assigned_user_id": worker_id,
                    },
                    headers=owner,
                )
            )
            await wait_for_lock_waiters(2)
            await holder.rollback()
            deletion_response, assignment_response = await finish_pair(deletion, assignment)
        finally:
            await holder.rollback()
            await holder.close()
            if not deletion.done():
                deletion.cancel()
            if assignment is not None and not assignment.done():
                assignment.cancel()

    assert deletion_response.status_code == 204, deletion_response.text
    assert assignment_response.status_code == 400, assignment_response.text
    assert "not an active member" in assignment_response.json()["detail"]


@pytest.mark.parametrize("action", ["complete", "skip"])
async def test_reactivation_wins_before_personal_task_fallback_action(
    client: httpx.AsyncClient,
    action: str,
) -> None:
    """Fallback actions serialize Membership -> User with reactivation."""
    owner = await owner_with_farm(client)
    cleaner = await role_id(client, owner, "CLEANER")
    membership_id, worker_id = await create_worker(
        client,
        owner,
        cleaner,
        f"fallback-{action}-assignee@farm.in",
    )
    _peer_membership_id, _peer_user_id = await create_worker(
        client,
        owner,
        cleaner,
        f"fallback-{action}-peer@farm.in",
    )
    peer = await login_worker(client, owner, f"fallback-{action}-peer@farm.in")
    created = await client.post(
        "/api/tasks",
        json={
            "title": f"Personal fallback {action}",
            "due_date": today().isoformat(),
            "assigned_user_id": worker_id,
        },
        headers=owner,
    )
    assert created.status_code == 201, created.text
    inactive = await client.put(
        f"/api/team/workers/{membership_id}/status",
        json={"is_active": False},
        headers=owner,
    )
    assert inactive.status_code == 200, inactive.text

    holder = get_sessionmaker()()
    await holder.execute(
        select(FarmMembership.id).where(FarmMembership.id == membership_id).with_for_update()
    )
    async with second_client() as admin_client, second_client() as action_client:
        reactivate = asyncio.create_task(
            admin_client.put(
                f"/api/team/workers/{membership_id}/status",
                json={"is_active": True},
                headers=owner,
            )
        )
        duty_action: asyncio.Task[httpx.Response] | None = None
        try:
            await wait_for_lock_waiters(1)
            duty_action = asyncio.create_task(
                action_client.post(
                    f"/api/tasks/{created.json()['id']}/{action}",
                    headers=peer,
                )
            )
            await wait_for_lock_waiters(2)
            await holder.rollback()
            reactivate_response, action_response = await finish_pair(reactivate, duty_action)
        finally:
            await holder.rollback()
            await holder.close()
            if not reactivate.done():
                reactivate.cancel()
            if duty_action is not None and not duty_action.done():
                duty_action.cancel()

    assert reactivate_response.status_code == 200, reactivate_response.text
    assert action_response.status_code == 403, action_response.text
    async with get_sessionmaker()() as db:
        task = await db.get(Task, created.json()["id"])
    assert task is not None and task.status == TaskStatus.PENDING.value


@pytest.mark.parametrize("action", ["complete", "skip"])
@pytest.mark.parametrize("lifecycle", ["deactivate", "reset"])
async def test_personal_fallback_action_and_lifecycle_use_membership_then_user_order(
    client: httpx.AsyncClient,
    action: str,
    lifecycle: str,
) -> None:
    owner = await owner_with_farm(client)
    cleaner = await role_id(client, owner, "CLEANER")
    membership_id, worker_id = await create_worker(
        client,
        owner,
        cleaner,
        f"{lifecycle}-{action}-assignee@farm.in",
    )
    await create_worker(
        client,
        owner,
        cleaner,
        f"{lifecycle}-{action}-peer@farm.in",
    )
    peer = await login_worker(client, owner, f"{lifecycle}-{action}-peer@farm.in")
    created = await client.post(
        "/api/tasks",
        json={
            "title": f"{lifecycle} versus fallback {action}",
            "due_date": today().isoformat(),
            "assigned_user_id": worker_id,
        },
        headers=owner,
    )
    assert created.status_code == 201, created.text

    holder = get_sessionmaker()()
    await holder.execute(
        select(FarmMembership.id).where(FarmMembership.id == membership_id).with_for_update()
    )
    async with second_client() as admin_client, second_client() as action_client:
        if lifecycle == "deactivate":
            lifecycle_request = asyncio.create_task(
                admin_client.put(
                    f"/api/team/workers/{membership_id}/status",
                    json={"is_active": False},
                    headers=owner,
                )
            )
        else:
            lifecycle_request = asyncio.create_task(
                admin_client.post(
                    f"/api/team/workers/{membership_id}/reset-password",
                    json={"password": "replacementpass1"},
                    headers=owner,
                )
            )
        duty_action: asyncio.Task[httpx.Response] | None = None
        try:
            await wait_for_lock_waiters(1)
            duty_action = asyncio.create_task(
                action_client.post(
                    f"/api/tasks/{created.json()['id']}/{action}",
                    headers=peer,
                )
            )
            await wait_for_lock_waiters(2)
            await holder.rollback()
            lifecycle_response, action_response = await finish_pair(
                lifecycle_request,
                duty_action,
            )
        finally:
            await holder.rollback()
            await holder.close()
            if not lifecycle_request.done():
                lifecycle_request.cancel()
            if duty_action is not None and not duty_action.done():
                duty_action.cancel()

    assert lifecycle_response.status_code == 200, lifecycle_response.text
    if lifecycle == "deactivate":
        assert action_response.status_code == 200, action_response.text
        expected_status = (
            TaskStatus.DONE.value if action == "complete" else TaskStatus.SKIPPED.value
        )
    else:
        assert action_response.status_code == 403, action_response.text
        expected_status = TaskStatus.PENDING.value
    async with get_sessionmaker()() as db:
        task = await db.get(Task, created.json()["id"])
    assert task is not None and task.status == expected_status


async def test_deactivation_wins_before_blocked_mutation_authorization(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    vet = await role_id(client, owner, "VET")
    membership_id, _worker_id = await create_worker(
        client,
        owner,
        vet,
        "deactivate-auth-race@farm.in",
    )
    worker = await login_worker(client, owner, "deactivate-auth-race@farm.in")
    animal_id = await make_animal(client, owner, "AUTH-DEACTIVATE")

    holder = get_sessionmaker()()
    await holder.execute(
        select(FarmMembership.id).where(FarmMembership.id == membership_id).with_for_update()
    )
    async with second_client() as admin_client, second_client() as worker_client:
        deactivate = asyncio.create_task(
            admin_client.put(
                f"/api/team/workers/{membership_id}/status",
                json={"is_active": False},
                headers=owner,
            )
        )
        mutation: asyncio.Task[httpx.Response] | None = None
        try:
            await wait_for_lock_waiters(1)
            mutation = asyncio.create_task(
                worker_client.post(
                    f"/api/animals/{animal_id}/weight",
                    json={"weight_kg": 20},
                    headers=worker,
                )
            )
            await wait_for_lock_waiters(2)
            await holder.rollback()
            deactivate_response, mutation_response = await finish_pair(deactivate, mutation)
        finally:
            await holder.rollback()
            await holder.close()
            if not deactivate.done():
                deactivate.cancel()
            if mutation is not None and not mutation.done():
                mutation.cancel()

    assert deactivate_response.status_code == 200, deactivate_response.text
    assert mutation_response.status_code == 404, mutation_response.text
    assert mutation_response.json()["detail"] == "Farm not found"
    async with get_sessionmaker()() as db:
        weights = (
            await db.execute(
                select(func.count())
                .select_from(WeightRecord)
                .where(WeightRecord.animal_id == animal_id)
            )
        ).scalar_one()
    assert weights == 0


async def test_role_reassignment_wins_before_blocked_mutation_authorization(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    vet = await role_id(client, owner, "VET")
    cleaner = await role_id(client, owner, "CLEANER")
    membership_id, _worker_id = await create_worker(
        client,
        owner,
        vet,
        "role-change-auth-race@farm.in",
    )
    worker = await login_worker(client, owner, "role-change-auth-race@farm.in")
    animal_id = await make_animal(client, owner, "AUTH-ROLE-CHANGE")

    holder = get_sessionmaker()()
    await holder.execute(
        select(FarmMembership.id).where(FarmMembership.id == membership_id).with_for_update()
    )
    async with second_client() as admin_client, second_client() as worker_client:
        change = asyncio.create_task(
            admin_client.post(
                f"/api/team/workers/{membership_id}/role",
                json={"role_id": cleaner},
                headers=owner,
            )
        )
        mutation: asyncio.Task[httpx.Response] | None = None
        try:
            await wait_for_lock_waiters(1)
            mutation = asyncio.create_task(
                worker_client.post(
                    f"/api/animals/{animal_id}/weight",
                    json={"weight_kg": 20},
                    headers=worker,
                )
            )
            await wait_for_lock_waiters(2)
            await holder.rollback()
            change_response, mutation_response = await finish_pair(change, mutation)
        finally:
            await holder.rollback()
            await holder.close()
            if not change.done():
                change.cancel()
            if mutation is not None and not mutation.done():
                mutation.cancel()

    assert change_response.status_code == 200, change_response.text
    assert mutation_response.status_code == 403, mutation_response.text
    assert mutation_response.json()["detail"] == "Missing permission: animals.weight"


async def test_role_permission_revocation_wins_before_blocked_mutation(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    role = await custom_role(
        client,
        owner,
        "Revocable Vet",
        ["animals.view", "animals.weight"],
    )
    _membership_id, _worker_id = await create_worker(
        client,
        owner,
        role,
        "permission-auth-race@farm.in",
    )
    worker = await login_worker(client, owner, "permission-auth-race@farm.in")
    animal_id = await make_animal(client, owner, "AUTH-PERMISSION")

    holder = get_sessionmaker()()
    await holder.execute(select(Role.id).where(Role.id == role).with_for_update())
    async with second_client() as admin_client, second_client() as worker_client:
        revoke = asyncio.create_task(
            admin_client.put(
                f"/api/team/roles/{role}",
                json={"name": "Revocable Vet", "permissions": ["animals.view"]},
                headers=owner,
            )
        )
        mutation: asyncio.Task[httpx.Response] | None = None
        try:
            await wait_for_lock_waiters(1)
            mutation = asyncio.create_task(
                worker_client.post(
                    f"/api/animals/{animal_id}/weight",
                    json={"weight_kg": 20},
                    headers=worker,
                )
            )
            await wait_for_lock_waiters(2)
            await holder.rollback()
            revoke_response, mutation_response = await finish_pair(revoke, mutation)
        finally:
            await holder.rollback()
            await holder.close()
            if not revoke.done():
                revoke.cancel()
            if mutation is not None and not mutation.done():
                mutation.cancel()

    assert revoke_response.status_code == 200, revoke_response.text
    assert mutation_response.status_code == 403, mutation_response.text
    assert mutation_response.json()["detail"] == "Missing permission: animals.weight"


async def test_account_tombstone_wins_before_blocked_mutation_authorization(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    vet = await role_id(client, owner, "VET")
    _membership_id, worker_id = await create_worker(
        client,
        owner,
        vet,
        "delete-auth-race@farm.in",
    )
    worker = await login_worker(client, owner, "delete-auth-race@farm.in")
    animal_id = await make_animal(client, owner, "AUTH-TOMBSTONE")

    holder = get_sessionmaker()()
    await holder.execute(select(User.id).where(User.id == worker_id).with_for_update())
    async with second_client() as delete_client, second_client() as worker_client:
        deletion = asyncio.create_task(
            delete_client.request(
                "DELETE",
                "/api/auth/account",
                json={"current_password": WORKER_PASSWORD},
                headers=worker,
            )
        )
        mutation: asyncio.Task[httpx.Response] | None = None
        try:
            await wait_for_lock_waiters(1)
            mutation = asyncio.create_task(
                worker_client.post(
                    f"/api/animals/{animal_id}/weight",
                    json={"weight_kg": 20},
                    headers=worker,
                )
            )
            await wait_for_lock_waiters(2)
            await holder.rollback()
            deletion_response, mutation_response = await finish_pair(deletion, mutation)
        finally:
            await holder.rollback()
            await holder.close()
            if not deletion.done():
                deletion.cancel()
            if mutation is not None and not mutation.done():
                mutation.cancel()

    assert deletion_response.status_code == 204, deletion_response.text
    assert mutation_response.status_code == 401, mutation_response.text
    assert mutation_response.json()["detail"] == "Account no longer exists"
    async with get_sessionmaker()() as db:
        deleted_user = await db.get(User, worker_id)
        weights = (
            await db.execute(
                select(func.count())
                .select_from(WeightRecord)
                .where(WeightRecord.animal_id == animal_id)
            )
        ).scalar_one()
    assert deleted_user is not None and deleted_user.deleted_at is not None
    assert weights == 0


async def test_target_role_promotion_is_revalidated_under_share_lock(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)
    manager_role = await custom_role(
        client,
        owner,
        "Delegated Manager",
        ["team.manage", "animals.view"],
    )
    target_role = await custom_role(
        client,
        owner,
        "Promotable Worker",
        ["animals.view"],
    )
    await create_worker(
        client,
        owner,
        manager_role,
        "target-role-manager@farm.in",
    )
    target_membership_id, _target_user_id = await create_worker(
        client,
        owner,
        target_role,
        "target-role-worker@farm.in",
    )
    manager = await login_worker(client, owner, "target-role-manager@farm.in")

    entered_revalidation = asyncio.Event()
    release_revalidation = asyncio.Event()
    original_pin = team_api._pin_membership_role

    async def paused_pin(*args: object, **kwargs: object) -> Role:
        # The delegated manager already owns the target Membership lock here.
        # An owner can still edit its Role, so the peer-manager guard must use
        # a fresh Role row and hold SHARE through the lifecycle commit.
        entered_revalidation.set()
        await release_revalidation.wait()
        return await original_pin(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(team_api, "_pin_membership_role", paused_pin)
    async with second_client() as manager_client, second_client() as owner_client:
        toggle = asyncio.create_task(
            manager_client.put(
                f"/api/team/workers/{target_membership_id}/status",
                json={"is_active": False},
                headers=manager,
            )
        )
        promote: asyncio.Task[httpx.Response] | None = None
        try:
            await asyncio.wait_for(entered_revalidation.wait(), timeout=5)
            promote = asyncio.create_task(
                owner_client.put(
                    f"/api/team/roles/{target_role}",
                    json={
                        "name": "Promoted Manager",
                        "permissions": ["team.manage", "animals.view"],
                    },
                    headers=owner,
                )
            )
            async with asyncio.timeout(10):
                promote_response = await promote
            release_revalidation.set()
            async with asyncio.timeout(10):
                toggle_response = await toggle
        finally:
            release_revalidation.set()
            if not toggle.done():
                toggle.cancel()
            if promote is not None and not promote.done():
                promote.cancel()

    assert promote_response.status_code == 200, promote_response.text
    assert toggle_response.status_code == 403, toggle_response.text
    assert toggle_response.json()["detail"] == (
        "Only the farm owner can manage other team managers."
    )
    async with get_sessionmaker()() as db:
        membership = await db.get(FarmMembership, target_membership_id)
    assert membership is not None and membership.is_active is True
