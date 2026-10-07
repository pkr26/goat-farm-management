"""Purchase detail keeps profile permission, task scope and positive-ID contracts."""

from typing import Literal

import httpx
import pytest
from sqlalchemy import select, update

from app.db import get_sessionmaker
from app.models import PurchaseBatch, Task
from app.utils import today

from .conftest import owner_with_farm, provisioned_worker_login
from .test_health_extended import make_batch
from .test_tasks_extended import add_worker, make_custom_role

ANIMAL_SUMMARY_FIELDS = {"id", "tag_number", "sex", "current_bucket", "status"}
TASK_SUMMARY_FIELDS = {
    "id",
    "title",
    "title_key",
    "title_args",
    "due_date",
    "status",
    "category",
}


@pytest.mark.parametrize("access", ["owner", "purchases_only", "operator"])
async def test_purchase_detail_succeeds_with_the_authorized_profile_and_task_scope(
    client: httpx.AsyncClient, access: Literal["owner", "purchases_only", "operator"]
) -> None:
    owner = await owner_with_farm(client)
    batch = await make_batch(
        client, owner, count=1, supplier="Purchase detail source", total_price=9000
    )
    headers = owner
    visible_task_id: int | None = None
    assigned_role_id: int | None = None
    if access != "owner":
        permissions = ["purchases.view"]
        if access == "operator":
            permissions.extend(["animals.view", "tasks.view"])
        assigned_role_id = await make_custom_role(client, owner, "Detail reader", permissions)
        email = "detail-reader@farm.in"
        await add_worker(client, owner, assigned_role_id, email)
        headers, _ = await provisioned_worker_login(client, email, "workerpass123")
        headers |= {"X-Farm-Id": owner["X-Farm-Id"]}
        if access == "operator":
            # Persist one real role-scoped quarantine duty; other roles' rows
            # remain purchase schedule summaries for this reader.
            async with get_sessionmaker()() as db:
                visible_task_id = (
                    await db.execute(
                        select(Task.id)
                        .where(Task.purchase_batch_id == batch["id"])
                        .order_by(Task.due_date, Task.id)
                        .limit(1)
                    )
                ).scalar_one()
                await db.execute(
                    update(Task)
                    .where(Task.id == visible_task_id)
                    .values(assigned_role_id=assigned_role_id, assigned_user_id=None)
                )
                await db.commit()

    response = await client.get(f"/api/purchases/{batch['id']}", headers=headers)
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["batch"]["id"] == batch["id"]
    assert detail["batch"]["animals_created"] == detail["animals_total"] == 1
    assert len(detail["animals"]) == 1
    assert len(detail["tasks"]) == 11
    animal = detail["animals"][0]
    if access == "purchases_only":
        assert set(animal) == ANIMAL_SUMMARY_FIELDS
        assert all(set(task) == TASK_SUMMARY_FIELDS for task in detail["tasks"])
    else:
        assert "created_at" in animal, "Animal viewers receive the full profile contract"
        assert animal["purchase_price"] == 9000.0
        assert animal["seller_name"] == "Purchase detail source"
        assert animal["purchase_date"] == today().isoformat()
        for task in detail["tasks"]:
            if access == "owner" or task["id"] == visible_task_id:
                assert "assigned_role_id" in task, "Visible duties retain task attribution"
                assert "created_at" in task
                assert "completed_by_id" in task
                if access == "operator":
                    assert task["assigned_role_id"] == assigned_role_id
                    assert task["assigned_role_name"] == "Detail reader"
            else:
                assert set(task) == TASK_SUMMARY_FIELDS


async def test_purchase_detail_rejects_zero_even_when_a_legacy_batch_uses_that_id(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    # Restored legacy batches may have no generated animals, protocol or sex.
    # The database permits their historical key, but HTTP resource IDs remain
    # positive even if an old row happens to use zero.
    async with get_sessionmaker()() as db:
        db.add(
            PurchaseBatch(
                id=0,
                farm_id=int(owner["X-Farm-Id"]),
                date=today(),
                count=1,
                supplier="Restored legacy purchase",
                sex=None,
                notes="Legacy ledger-only batch",
            )
        )
        await db.commit()
    async with get_sessionmaker()() as db:
        restored = await db.get(PurchaseBatch, 0)
        assert restored is not None
        assert restored.supplier == "Restored legacy purchase"
    response = await client.get("/api/purchases/0", headers=owner)
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Purchase batch not found"
