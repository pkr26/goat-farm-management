"""Status-handler service rejection rolls back real closure and permits retry."""

from datetime import date

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import animals as animals_api
from app.db import get_sessionmaker
from app.models import Animal, BreedingRecord, BucketMove, Task, Transaction
from app.services.breeding import mark_aborted
from app.utils import today

from .conftest import owner_with_farm
from .test_kidding_husbandry import confirmed_pregnancy


async def test_public_terminal_status_preserves_422_service_rejection_and_atomic_same_key_retry(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client)
    doe, _buck, breeding = await confirmed_pregnancy(
        client, owner, tag="RETIREMENT-REVIEW", bred_days_ago=40, kid_count=1
    )
    original_closure = mark_aborted
    actual_closure_ran = False

    async def reject_after_real_closure(
        db: AsyncSession,
        br: BreedingRecord,
        *,
        loss_date: date,
        loss_cause: str,
        loss_notes: str | None,
        recorded_by_id: int,
        allow_late_administrative_close: bool = False,
    ) -> BreedingRecord:
        nonlocal actual_closure_ran
        resolved = await original_closure(
            db,
            br,
            loss_date=loss_date,
            loss_cause=loss_cause,
            loss_notes=loss_notes,
            recorded_by_id=recorded_by_id,
            allow_late_administrative_close=allow_late_administrative_close,
        )
        await db.flush()
        assert resolved.outcome == "ABORTED" and resolved.loss_date == loss_date
        actual_closure_ran = True
        # Deliberate service-boundary failure injection after REAL writes:
        # this handler explicitly translates ValueError into422 and promises
        # an atomic lifecycle. Ordinary current guards may reject earlier;
        # no validated request or clinical observation is modified.
        raise ValueError("Pregnancy resolution needs an administrative review")

    request_headers = owner | {"Idempotency-Key": "terminal-review-retry"}
    request_body = {"new_status": "SOLD", "date": today().isoformat(), "sale_price": 5000}
    monkeypatch.setattr(animals_api, "mark_aborted", reject_after_real_closure)
    rejected = await client.post(
        f"/api/animals/{doe['id']}/status", headers=request_headers, json=request_body
    )
    assert actual_closure_ran
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["detail"] == "Pregnancy resolution needs an administrative review"
    async with get_sessionmaker()() as db:
        retained = await db.get(Animal, doe["id"])
        record = await db.get(BreedingRecord, breeding["id"])
        assert retained is not None and record is not None
        assert retained.status == "ACTIVE" and retained.sale_price is None
        assert record.outcome == "CONFIRMED_PREGNANT" and record.pregnant is True
        assert record.loss_date is None and record.loss_recorded_at is None
        assert not (
            await db.execute(
                select(Transaction.id).where(
                    Transaction.related_animal_id == doe["id"],
                    Transaction.source_type == "ANIMAL_SALE",
                )
            )
        ).first()
        assert not (
            await db.execute(
                select(BucketMove.id).where(
                    BucketMove.animal_id == doe["id"],
                    BucketMove.reason.like("Pregnancy auto-aborted%"),
                )
            )
        ).first()
        care = list(
            (
                await db.execute(
                    select(Task).where(
                        Task.breeding_record_id == breeding["id"], Task.category == "VACCINE"
                    )
                )
            ).scalars()
        )
        assert care and all(task.status == "PENDING" for task in care)
        assert all(task.skipped_at is None and task.skip_reason is None for task in care)
    monkeypatch.setattr(animals_api, "mark_aborted", original_closure)
    permitted = await client.post(
        f"/api/animals/{doe['id']}/status", headers=request_headers, json=request_body
    )
    assert permitted.status_code == 200, permitted.text
    async with get_sessionmaker()() as db:
        retired = await db.get(Animal, doe["id"])
        closed = await db.get(BreedingRecord, breeding["id"])
        ledger = list(
            (
                await db.execute(
                    select(Transaction).where(
                        Transaction.related_animal_id == doe["id"],
                        Transaction.source_type == "ANIMAL_SALE",
                    )
                )
            ).scalars()
        )
        assert retired is not None and retired.status == "SOLD"
        assert closed is not None and closed.outcome == "ABORTED"
        assert closed.loss_cause == "ANIMAL_STATUS_CHANGE"
        assert len(ledger) == 1 and ledger[0].amount == 5000
