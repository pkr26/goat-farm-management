"""Refresh a permitted batch correction before inspecting restored legacy rows.

Legacy rows are restored through constrained ORM writes. The invoice date is
corrected through the real API while the batch still has no linked animals.
The guard is then called with its documented Animal-lock precondition.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.api.animals import _lock_pristine_batch_protocol_for_quarantine_reentry
from app.db import get_sessionmaker
from app.models import (
    Animal,
    BucketMove,
    Farm,
    PurchaseBatch,
    Task,
    Transaction,
    quarantine_schedule,
)
from app.services.purchases import schedule_quarantine_tasks
from app.utils import today

from .conftest import owner_with_farm
from .test_health_extended import make_batch


@pytest.mark.parametrize("restored_protocol", ["current", "archived"])
async def test_cached_batch_date_cannot_override_corrected_restored_protocol(
    client: httpx.AsyncClient, restored_protocol: str
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    original_date = today() - timedelta(days=2)
    corrected_date = today() - timedelta(days=1)
    created = await make_batch(
        client,
        owner,
        date=original_date.isoformat(),
        count=1,
        sex="F",
        total_price=100,
        create_animals=False,
    )
    batch_id = int(created["id"])

    async with get_sessionmaker()() as reused:
        cached = await reused.get(PurchaseBatch, batch_id)
        assert cached is not None
        assert cached.date == original_date
        archived_schedule = quarantine_schedule(cached)
        transaction_id = (
            await reused.execute(
                select(Transaction.id).where(
                    Transaction.farm_id == farm_id,
                    Transaction.source_type == "PURCHASE_BATCH",
                    Transaction.source_id == batch_id,
                    Transaction.voided_at.is_(None),
                )
            )
        ).scalar_one()

        # No Animal has yet been associated: this correction is expressly
        # allowed by the source reconciler, rather than evading its fence.
        corrected = await client.post(
            f"/api/finance/transactions/{transaction_id}/correct",
            headers=owner,
            json={
                "date": corrected_date.isoformat(),
                "type": "EXPENSE",
                "category": "ANIMAL_PURCHASE",
                "amount": 100,
                "reason": "Invoice arrival date corrected before legacy herd restoration",
            },
        )
        assert corrected.status_code == 201, corrected.text
        assert corrected.json()["date"] == corrected_date.isoformat()
        assert cached.date == original_date

        async with get_sessionmaker()() as restoration:
            committed = await restoration.get(PurchaseBatch, batch_id)
            farm = await restoration.get(Farm, farm_id)
            assert committed is not None
            assert farm is not None
            assert committed.date == corrected_date
            assert committed.total_price == Decimal("100.00")
            linked_count = (
                await restoration.execute(
                    select(func.count(Animal.id)).where(
                        Animal.farm_id == farm_id, Animal.purchase_batch_id == batch_id
                    )
                )
            ).scalar_one()
            protocol_count = (
                await restoration.execute(
                    select(func.count(Task.id)).where(
                        Task.farm_id == farm_id, Task.purchase_batch_id == batch_id
                    )
                )
            ).scalar_one()
            assert linked_count == 0
            assert protocol_count == 0

            # All normal constraints and triggers remain active. A restored
            # purchased animal agrees with the corrected source date/price.
            restored = Animal(
                farm_id=farm_id,
                tag_number="LEGACY-RESTORED-ARRIVAL",
                sex="F",
                source="PURCHASED",
                current_bucket="QUARANTINE",
                status="ACTIVE",
                purchase_batch_id=batch_id,
                purchase_date=corrected_date,
                purchase_price=Decimal("100.00"),
            )
            restoration.add(restored)
            await restoration.flush()
            animal_id = restored.id
            restoration.add(
                BucketMove(
                    farm_id=farm_id,
                    animal_id=animal_id,
                    from_bucket=None,
                    to_bucket="QUARANTINE",
                    effective_date=corrected_date,
                    reason="Legacy arrival restored against corrected purchase invoice",
                )
            )
            if restored_protocol == "current":
                await schedule_quarantine_tasks(restoration, farm, committed)
            else:
                # An archived pending series is legal stored data but must
                # not count as the corrected batch's authoritative protocol.
                restoration.add_all(
                    Task(
                        farm_id=farm_id,
                        purchase_batch_id=batch_id,
                        title=spec["title"],
                        title_key=spec["title_key"],
                        title_args=spec["title_args"],
                        due_date=spec["due_date"],
                        category=spec["category"],
                        status="PENDING",
                        auto_generated=True,
                    )
                    for spec in archived_schedule
                )
            await restoration.commit()

        # Batch was cached before its permissible correction. Its later
        # linkage does not retroactively make that earlier correction illegal.
        animal = (
            await reused.execute(
                select(Animal)
                .where(Animal.id == animal_id, Animal.farm_id == farm_id)
                .with_for_update()
            )
        ).scalar_one()
        assert animal.purchase_batch_id == batch_id
        assert animal.purchase_date == corrected_date
        if restored_protocol == "current":
            refusal_error: HTTPException | None = None
            try:
                await _lock_pristine_batch_protocol_for_quarantine_reentry(
                    reused, farm_id, batch_id
                )
            except HTTPException as exc:
                refusal_error = exc
            assert refusal_error is None, (
                "A pristine restored protocol agrees with the corrected batch date and "
                f"must permit the guarded move; got {refusal_error}"
            )
        else:
            with pytest.raises(HTTPException) as refusal:
                await _lock_pristine_batch_protocol_for_quarantine_reentry(
                    reused, farm_id, batch_id
                )
            assert refusal.value.status_code == 409
            assert "protocol has started, ended, or is incomplete" in str(refusal.value.detail)
        await reused.rollback()
