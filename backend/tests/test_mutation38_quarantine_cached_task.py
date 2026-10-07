"""The pristine-protocol helper must respect a committed protocol fact.

This exercises the helper's documented Animal-lock precondition with a
legitimate reused AsyncSession. Its current HTTP caller normally creates a
fresh session; the test does not claim that an HTTP request preloads Tasks.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.animals import _lock_pristine_batch_protocol_for_quarantine_reentry
from app.db import get_sessionmaker
from app.models import Animal, Task

from .conftest import owner_with_farm
from .test_health_extended import make_batch


async def test_cached_pristine_task_cannot_hide_a_committed_protocol_completion(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    batch = await make_batch(client, owner, count=1, create_animals=True)
    detail = await client.get(f"/api/purchases/{batch['id']}", headers=owner)
    assert detail.status_code == 200, detail.text
    batch_id = int(batch["id"])
    animal_id = int(detail.json()["animals"][0]["id"])
    task_id = int(detail.json()["tasks"][0]["id"])

    async with get_sessionmaker()() as reused:
        cached = await reused.get(Task, task_id)
        assert cached is not None
        assert cached.status == "PENDING"
        assert cached.completed_at is None

        # This completion uses the actual authenticated domain endpoint and
        # its normal animal/batch/task lock order. It commits before the
        # prospective correction takes its Animal lock.
        completed = await client.post(f"/api/tasks/{task_id}/complete", headers=owner)
        assert completed.status_code == 200, completed.text
        async with get_sessionmaker()() as verification:
            committed_status, committed_at = (
                await verification.execute(
                    select(Task.status, Task.completed_at).where(Task.id == task_id)
                )
            ).one()
        assert committed_status == "DONE"
        assert committed_at is not None
        assert cached.status == "PENDING"
        assert cached.completed_at is None

        # Obey the helper's explicit precondition: caller holds its Animal
        # row, then helper takes Batch -> Task and rereads the winning fact.
        animal = (
            await reused.execute(
                select(Animal)
                .where(Animal.id == animal_id, Animal.farm_id == farm_id)
                .with_for_update()
            )
        ).scalar_one()
        assert animal.purchase_batch_id == batch_id
        with pytest.raises(HTTPException) as refusal:
            await _lock_pristine_batch_protocol_for_quarantine_reentry(reused, farm_id, batch_id)
        assert refusal.value.status_code == 409
        assert "protocol has started, ended, or is incomplete" in str(refusal.value.detail)
        await reused.rollback()
