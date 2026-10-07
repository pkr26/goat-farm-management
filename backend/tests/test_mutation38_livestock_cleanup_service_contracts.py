"""Native cleanup bounds and audit retention with production PostgreSQL checks.

The service helpers accept arbitrary reason strings and valid stored pending
cohorts, including restored manual duties. These cases exercise that contract;
the ordinary generated quarantine calendar contains only eleven duties.
"""

import httpx
import pytest
from sqlalchemy import func, insert, select, text

from app.db import get_sessionmaker
from app.models import Task, TaskStatus
from app.services.animals import (
    skip_pending_tasks_for_animal,
    skip_pending_tasks_for_empty_batch,
)
from app.utils import today

from .conftest import owner_with_farm


async def _animal(client: httpx.AsyncClient, owner: dict[str, str]) -> int:
    response = await client.post(
        "/api/animals",
        headers=owner,
        json={
            "tag_number": "CLEANUP-CONTRACT",
            "sex": "F",
            "source": "BORN",
            "current_bucket": "FOUNDATION",
            "historical_import_reason": "Existing herd migration",
        },
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


async def _batch(client: httpx.AsyncClient, owner: dict[str, str], *, active_count: int = 0) -> int:
    response = await client.post(
        "/api/purchases/new",
        headers=owner,
        json={
            "date": today().isoformat(),
            "count": max(active_count, 1),
            "create_animals": active_count > 0,
        },
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


async def _duties(farm_id: int, target: int, *, batch: bool, count: int = 2) -> list[int]:
    link = "purchase_batch_id" if batch else "animal_id"
    async with get_sessionmaker()() as db:
        result = await db.execute(
            insert(Task).returning(Task.id),
            [
                {
                    "farm_id": farm_id,
                    link: target,
                    "title": f"Restored manual observation {index}",
                    "due_date": today(),
                    "category": "OTHER",
                    "status": "PENDING",
                    "auto_generated": False,
                }
                for index in range(count)
            ],
        )
        ids = list(result.scalars())
        await db.commit()
    return ids


@pytest.mark.parametrize("batch", [False, True], ids=["animal", "empty-batch"])
@pytest.mark.parametrize(
    "batch_size", [1, 10000, 0, 10001], ids=["minimum", "maximum", "below", "above"]
)
async def test_cleanup_accepts_inclusive_literal_bounds_and_rejects_outside(
    client: httpx.AsyncClient, batch: bool, batch_size: int
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    target = await _batch(client, owner) if batch else await _animal(client, owner)
    ids = await _duties(farm_id, target, batch=batch)
    cleanup = skip_pending_tasks_for_empty_batch if batch else skip_pending_tasks_for_animal
    async with get_sessionmaker()() as db:
        if batch_size in (0, 10001):
            with pytest.raises(ValueError, match="between 1 and 10000"):
                await cleanup(db, farm_id, target, batch_size=batch_size)
            expected_skipped = 0
        else:
            try:
                skipped = await cleanup(db, farm_id, target, batch_size=batch_size)
            except Exception as error:
                pytest.fail(f"valid cleanup bound must complete: {error!r}")
            expected_skipped = 1 if batch_size == 1 else 2
            assert skipped == expected_skipped
        await db.commit()
    async with get_sessionmaker()() as db:
        stored = list((await db.execute(select(Task.status).where(Task.id.in_(ids)))).scalars())
    assert stored.count("SKIPPED") == expected_skipped
    assert stored.count("PENDING") == 2 - expected_skipped


async def test_cleanup_retains_exactly_255_characters_of_a_long_native_reason(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    animal_id = await _animal(client, owner)
    ids = await _duties(farm_id, animal_id, batch=False, count=1)
    reason = "Retired: " + "x" * 247
    assert len(reason) == 256
    async with get_sessionmaker()() as db:
        try:
            skipped = await skip_pending_tasks_for_animal(db, farm_id, animal_id, reason)
            await db.commit()
        except Exception as error:
            pytest.fail(f"native long reason must fit the audit column: {error!r}")
    assert skipped == 1
    async with get_sessionmaker()() as db:
        duty = await db.get(Task, ids[0])
        assert duty is not None
        assert duty.status == "SKIPPED"
        assert duty.skip_reason == reason[:255]
        assert len(duty.skip_reason) == 255
        assert duty.skipped_at is not None
        assert duty.skipped_by_id is None


async def test_empty_batch_cleanup_preserves_the_default_500_row_budget(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    batch_id = await _batch(client, owner)
    # A restored valid cohort can exceed the current generated eleven-duty
    # protocol. Keep every schema/FK/status check enabled for all 501 rows.
    ids = await _duties(farm_id, batch_id, batch=True, count=501)
    async with get_sessionmaker()() as db:
        skipped = await skip_pending_tasks_for_empty_batch(db, farm_id, batch_id)
        await db.commit()
    assert skipped == 500
    async with get_sessionmaker()() as db:
        pending = (
            await db.execute(
                select(func.count())
                .select_from(Task)
                .where(Task.id.in_(ids), Task.status == "PENDING")
            )
        ).scalar_one()
    assert pending == 1


async def test_nonempty_two_animal_batch_reports_zero_and_keeps_duties_pending(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    batch_id = await _batch(client, owner, active_count=2)
    async with get_sessionmaker()() as db:
        before = list(
            (
                await db.execute(
                    select(Task.id).where(
                        Task.purchase_batch_id == batch_id, Task.status == "PENDING"
                    )
                )
            ).scalars()
        )
        assert before
        try:
            skipped = await skip_pending_tasks_for_empty_batch(db, farm_id, batch_id)
        except Exception as error:
            pytest.fail(f"a nonempty batch is a supported no-op: {error!r}")
        assert skipped == 0
        await db.commit()
    async with get_sessionmaker()() as db:
        after = list(
            (
                await db.execute(
                    select(Task.id).where(
                        Task.purchase_batch_id == batch_id, Task.status == "PENDING"
                    )
                )
            ).scalars()
        )
    assert after == before


async def test_foreign_batch_reports_zero_without_touching_its_duties(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    other = await owner_with_farm(client, email="other-cleanup@farm.in", farm_name="Other")
    batch_id = await _batch(client, other)
    ids = await _duties(int(other["X-Farm-Id"]), batch_id, batch=True, count=1)
    async with get_sessionmaker()() as db:
        assert await skip_pending_tasks_for_empty_batch(db, int(owner["X-Farm-Id"]), batch_id) == 0
        await db.commit()
    async with get_sessionmaker()() as db:
        duty = await db.get(Task, ids[0])
        assert duty is not None
        assert duty.status == "PENDING"


async def test_animal_cleanup_skips_a_contended_duty_and_processes_unlocked_work(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    animal_id = await _animal(client, owner)
    ids = await _duties(farm_id, animal_id, batch=False)
    async with get_sessionmaker()() as holder:
        await holder.execute(select(Task.id).where(Task.id == ids[0]).with_for_update())
        async with get_sessionmaker()() as worker:
            await worker.execute(text("SET LOCAL lock_timeout = '250ms'"))
            try:
                skipped = await skip_pending_tasks_for_animal(
                    worker, farm_id, animal_id, batch_size=2
                )
                await worker.commit()
            except Exception as error:
                pytest.fail(f"bounded animal cleanup must skip contended work: {error!r}")
            assert skipped == 1
        await holder.rollback()
    async with get_sessionmaker()() as db:
        states = {
            row.id: row.status
            for row in (
                await db.execute(select(Task.id, Task.status).where(Task.id.in_(ids)))
            ).all()
        }
    assert states == {
        ids[0]: TaskStatus.PENDING.value,
        ids[1]: TaskStatus.SKIPPED.value,
    }
