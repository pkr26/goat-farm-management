"""Finite inactive-task cleanup accepts its bounds and skips contended rows."""

import asyncio

import httpx
import pytest
from sqlalchemy import select, text

from app.db import get_sessionmaker
from app.models import Task, TaskStatus
from app.services.animals import skip_inactive_animal_tasks_batch
from app.utils import today

from .conftest import owner_with_farm
from .test_concurrency import make_animal


async def _sold_animal(
    client: httpx.AsyncClient, email: str, tag: str
) -> tuple[dict[str, str], int]:
    owner = await owner_with_farm(client, email=email)
    animal_id = await make_animal(client, owner, tag=tag)
    sold = await client.post(
        f"/api/animals/{animal_id}/status", headers=owner, json={"new_status": "SOLD"}
    )
    assert sold.status_code == 200, sold.text
    assert sold.json()["status"] == "SOLD"
    return owner, animal_id


def _residual_task(farm_id: int, animal_id: int, title: str) -> Task:
    # Stored pending residue after retirement is the helper's documented
    # input, also used by the existing live-session cleanup regression.
    return Task(
        farm_id=farm_id,
        animal_id=animal_id,
        title=title,
        due_date=today(),
        category="OTHER",
        status=TaskStatus.PENDING.value,
        auto_generated=False,
    )


@pytest.mark.parametrize(
    "batch_size",
    [1, 10_000, 0, 10_001],
    ids=["inclusive-one", "inclusive-ten-thousand", "zero-rejected", "over-cap-rejected"],
)
async def test_inactive_cleanup_enforces_its_literal_batch_bounds_and_preserves_valid_work(
    client: httpx.AsyncClient, batch_size: int
) -> None:
    if batch_size in (0, 10_001):
        async with get_sessionmaker()() as db:
            with pytest.raises(ValueError, match="batch_size must be between 1 and 10000"):
                await skip_inactive_animal_tasks_batch(db, batch_size=batch_size)
        return

    owner, animal_id = await _sold_animal(client, "cleanup-bounds@farm.in", "CLEANUP-BOUND")
    async with get_sessionmaker()() as db:
        task = _residual_task(int(owner["X-Farm-Id"]), animal_id, "Residual after actual sale")
        db.add(task)
        await db.commit()
        task_id = task.id
    async with get_sessionmaker()() as db:
        try:
            skipped = await skip_inactive_animal_tasks_batch(db, batch_size=batch_size)
        except ValueError as exc:
            pytest.fail(f"The supported inclusive batch size {batch_size} was rejected: {exc}")
        assert skipped == 1
        await db.commit()
    async with get_sessionmaker()() as db:
        saved = await db.get(Task, task_id)
        assert saved is not None
        assert saved.status == TaskStatus.SKIPPED.value
        assert saved.skipped_at is not None
        assert saved.skip_reason == "Animal removed from active lifecycle"


async def test_inactive_cleanup_skips_a_real_locked_task_and_converges_unlocked_other_farm_work(
    client: httpx.AsyncClient,
) -> None:
    first_owner, first_animal = await _sold_animal(
        client, "cleanup-lock-first@farm.in", "CLEANUP-LOCKED"
    )
    second_owner, second_animal = await _sold_animal(
        client, "cleanup-lock-second@farm.in", "CLEANUP-FREE"
    )
    active_animal = await make_animal(client, first_owner, tag="CLEANUP-ACTIVE")
    async with get_sessionmaker()() as db:
        locked_task = _residual_task(int(first_owner["X-Farm-Id"]), first_animal, "Locked residue")
        free_task = _residual_task(int(second_owner["X-Farm-Id"]), second_animal, "Free residue")
        active_task = _residual_task(int(first_owner["X-Farm-Id"]), active_animal, "Active duty")
        db.add_all([locked_task, free_task, active_task])
        await db.commit()
        locked_id, free_id, active_id = locked_task.id, free_task.id, active_task.id
    assert locked_id < free_id < active_id

    holder = get_sessionmaker()()
    await holder.execute(select(Task.id).where(Task.id == locked_id).with_for_update())
    holder_pid = await holder.scalar(text("SELECT pg_backend_pid()"))
    assert isinstance(holder_pid, int)

    async def clean_one_batch() -> int:
        async with get_sessionmaker()() as db:
            count = await skip_inactive_animal_tasks_batch(db, batch_size=1)
            await db.commit()
            return count

    cleanup = asyncio.create_task(clean_one_batch())
    try:
        done, _ = await asyncio.wait({cleanup}, timeout=10)
        if not done:
            async with get_sessionmaker()() as observer:
                blocked = await observer.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE datname = current_database() "
                        "AND :holder_pid = ANY(pg_blocking_pids(pid)))"
                    ),
                    {"holder_pid": holder_pid},
                )
            assert blocked is True, "cleanup did not finish within its explicit completion bound"
            pytest.fail(
                "Finite cleanup waited on the held oldest Task lock instead of skipping it "
                "and processing the unlocked retired-animal duty"
            )
        assert await cleanup == 1
        async with get_sessionmaker()() as observer:
            remaining = await observer.get(Task, locked_id)
            finished = await observer.get(Task, free_id)
            active = await observer.get(Task, active_id)
            assert remaining is not None and finished is not None and active is not None
            assert remaining.status == active.status == TaskStatus.PENDING.value
            assert remaining.skipped_at is active.skipped_at is None
            assert finished.status == TaskStatus.SKIPPED.value
            assert finished.skipped_at is not None
            assert finished.skip_reason == "Animal removed from active lifecycle"
    finally:
        if not cleanup.done():
            cleanup.cancel()
        await asyncio.gather(cleanup, return_exceptions=True)
        await holder.rollback()
        await holder.close()

    async with get_sessionmaker()() as db:
        assert await skip_inactive_animal_tasks_batch(db, batch_size=1) == 1
        await db.commit()
    async with get_sessionmaker()() as observer:
        formerly_locked = await observer.get(Task, locked_id)
        active = await observer.get(Task, active_id)
        assert formerly_locked is not None and active is not None
        assert formerly_locked.status == TaskStatus.SKIPPED.value
        assert active.status == TaskStatus.PENDING.value
