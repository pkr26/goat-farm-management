"""Legacy protocol overflow must fail closed with a bounded lock footprint."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db, get_sessionmaker
from app.main import create_app
from app.models import Animal, BucketMove, Task
from app.utils import today

from .conftest import owner_with_farm
from .test_health_extended import make_batch


async def _extra_legacy_task(farm_id: int, batch_id: int, label: str) -> int:
    async with get_sessionmaker()() as db:
        extra = Task(
            farm_id=farm_id,
            purchase_batch_id=batch_id,
            title=f"Extra legacy quarantine duty {label}",
            due_date=today(),
            category="QUARANTINE",
            auto_generated=True,
        )
        db.add(extra)
        await db.commit()
        return extra.id


@pytest.mark.parametrize("direction", ["reentry", "release"])
async def test_extra_legacy_batch_duty_refuses_quarantine_history_override(
    client: httpx.AsyncClient, direction: str
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    batch = await make_batch(client, owner, count=1, create_animals=True)
    detail = await client.get(f"/api/purchases/{batch['id']}", headers=owner)
    assert detail.status_code == 200, detail.text
    assert len(detail.json()["tasks"]) == 11
    animal_id = int(detail.json()["animals"][0]["id"])
    if direction == "reentry":
        correction = await client.post(
            f"/api/animals/{animal_id}/move",
            json={"to_bucket": "FOUNDATION", "history_override": True, "reason": "Correct history"},
            headers=owner,
        )
        assert correction.status_code == 200, correction.text
    original_bucket = "FOUNDATION" if direction == "reentry" else "QUARANTINE"
    target_bucket = "QUARANTINE" if direction == "reentry" else "FOUNDATION"
    extra_id = await _extra_legacy_task(farm_id, int(batch["id"]), "overflow witness")
    assert extra_id > max(int(task["id"]) for task in detail.json()["tasks"])
    async with get_sessionmaker()() as db:
        previous_moves = (
            await db.execute(
                select(func.count())
                .select_from(BucketMove)
                .where(BucketMove.animal_id == animal_id)
            )
        ).scalar_one()

    refusal = await client.post(
        f"/api/animals/{animal_id}/move",
        json={"to_bucket": target_bucket, "history_override": True, "reason": "Legacy correction"},
        headers=owner,
    )

    assert refusal.status_code == 409, refusal.text
    assert "protocol has started, ended, or is incomplete" in refusal.json()["detail"]
    async with get_sessionmaker()() as db:
        animal = await db.get(Animal, animal_id)
        assert animal is not None
        assert animal.current_bucket == original_bucket
        current_moves = (
            await db.execute(
                select(func.count())
                .select_from(BucketMove)
                .where(BucketMove.animal_id == animal_id)
            )
        ).scalar_one()
        assert current_moves == previous_moves


async def test_proven_protocol_overflow_does_not_lock_another_legacy_duty(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    batch = await make_batch(client, owner, count=1, create_animals=True)
    detail = await client.get(f"/api/purchases/{batch['id']}", headers=owner)
    assert detail.status_code == 200, detail.text
    assert len(detail.json()["tasks"]) == 11
    animal_id = int(detail.json()["animals"][0]["id"])
    await _extra_legacy_task(farm_id, int(batch["id"]), "first proves overflow")
    unrelated_to_proof = await _extra_legacy_task(
        farm_id, int(batch["id"]), "not needed for refusal"
    )

    app = create_app()

    async def request_db_with_lock_budget() -> AsyncGenerator[AsyncSession]:
        async with get_sessionmaker()() as db:
            # A finite PostgreSQL lock budget turns a genuine unwanted row
            # lock into an actual HTTP error response. No runner timeout or
            # blanket exception-to-assert conversion is involved.
            await db.execute(text("SET LOCAL lock_timeout = '250ms'"))
            yield db

    app.dependency_overrides[get_db] = request_db_with_lock_budget
    holder = get_sessionmaker()()
    await holder.execute(select(Task.id).where(Task.id == unrelated_to_proof).with_for_update())
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as bounded_client:
            refusal = await bounded_client.post(
                f"/api/animals/{animal_id}/move",
                json={
                    "to_bucket": "FOUNDATION",
                    "history_override": True,
                    "reason": "Legacy correction",
                },
                headers=owner,
            )
        assert refusal.status_code == 409, refusal.text
        assert "protocol has started, ended, or is incomplete" in refusal.json()["detail"]
    finally:
        await holder.rollback()
        await holder.close()
