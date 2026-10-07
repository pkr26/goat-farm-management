"""Finance correction tolerates duplicate retained references to the same known lifecycle fact."""

from datetime import timedelta

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import BreedingRecord, BucketMove, Transaction
from app.utils import today

from .conftest import owner_with_farm
from .test_finance_extended import (
    change_status,
    make_animal,
    make_breeding,
    make_buck,
    make_doe,
    ultrasound,
)


async def test_public_purchase_correction_keeps_same_fact_date_with_duplicate_retained_initial_move(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(
        client,
        owner,
        "RETAINED-PURCHASE-MOVE",
        purchase_price=10,
        purchase_date=today().isoformat(),
        historical_import_reason="Existing audited purchased animal",
    )
    async with get_sessionmaker()() as db:
        moves = list(
            (
                await db.execute(
                    select(BucketMove).where(
                        BucketMove.animal_id == animal["id"], BucketMove.from_bucket.is_(None)
                    )
                )
            ).scalars()
        )
        assert len(moves) == 1
        original = moves[0]
        # Restore a duplicate reference to this exact known initial placement,
        # preserving its farm, effective date, actor, reason and audit instant.
        # The enabled head schema admits it; no new lifecycle event is invented.
        duplicate = BucketMove(
            **{
                column.key: getattr(original, column.key)
                for column in BucketMove.__table__.columns
                if column.key != "id"
            }
        )
        db.add(duplicate)
        await db.commit()
        ledger = list(
            (
                await db.execute(
                    select(Transaction).where(
                        Transaction.source_type == "ANIMAL_PURCHASE",
                        Transaction.source_id == animal["id"],
                        Transaction.voided_at.is_(None),
                    )
                )
            ).scalars()
        )
        assert len(ledger) == 1
        transaction_id = ledger[0].id
        effective_date = original.effective_date
    corrected = await client.post(
        f"/api/finance/transactions/{transaction_id}/correct",
        headers=owner,
        json={
            "date": effective_date.isoformat(),
            "type": "EXPENSE",
            "category": "ANIMAL_PURCHASE",
            "amount": 12,
            "reason": "Correct recorded purchase amount",
        },
    )
    assert corrected.status_code == 201, corrected.text
    assert corrected.json()["amount"] == 12
    async with get_sessionmaker()() as db:
        retained = list(
            (
                await db.execute(
                    select(BucketMove).where(
                        BucketMove.animal_id == animal["id"], BucketMove.from_bucket.is_(None)
                    )
                )
            ).scalars()
        )
        assert len(retained) == 2 and all(row.effective_date == effective_date for row in retained)
        active = list(
            (
                await db.execute(
                    select(Transaction).where(
                        Transaction.source_type == "ANIMAL_PURCHASE",
                        Transaction.source_id == animal["id"],
                        Transaction.voided_at.is_(None),
                    )
                )
            ).scalars()
        )
        assert len(active) == 1 and active[0].correction_of_id == transaction_id


async def test_public_same_day_sale_correction_preserves_duplicate_retained_auto_abort_facts(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, "RETAINED-ABORT-DOE")
    buck = await make_buck(client, owner, "RETAINED-ABORT-BUCK")
    breeding = await make_breeding(
        client, owner, doe["id"], buck["id"], breeding_date=today() - timedelta(days=60)
    )
    await ultrasound(client, owner, breeding["id"], pregnant=True)
    await change_status(client, owner, doe["id"], "SOLD", date=today().isoformat(), sale_price=10)
    async with get_sessionmaker()() as db:
        original = await db.get(BreedingRecord, breeding["id"])
        assert original is not None and original.outcome == "ABORTED"
        assert original.loss_cause == "ANIMAL_STATUS_CHANGE" and original.loss_date == today()
        duplicate = BreedingRecord(
            **{
                column.key: getattr(original, column.key)
                for column in BreedingRecord.__table__.columns
                if column.key != "id"
            }
        )
        db.add(duplicate)
        await db.commit()
        sale = list(
            (
                await db.execute(
                    select(Transaction).where(
                        Transaction.source_type == "ANIMAL_SALE",
                        Transaction.source_id == doe["id"],
                        Transaction.voided_at.is_(None),
                    )
                )
            ).scalars()
        )
        assert len(sale) == 1
        transaction_id = sale[0].id
        loss_facts = [
            (row.id, row.loss_date, row.loss_cause, row.loss_recorded_by_id, row.loss_recorded_at)
            for row in (original, duplicate)
        ]
    corrected = await client.post(
        f"/api/finance/transactions/{transaction_id}/correct",
        headers=owner,
        json={
            "date": today().isoformat(),
            "type": "INCOME",
            "category": "ANIMAL_SALE",
            "amount": 12,
            "reason": "Correct recorded sale amount",
        },
    )
    assert corrected.status_code == 201, corrected.text
    assert corrected.json()["amount"] == 12
    async with get_sessionmaker()() as db:
        retained = list(
            (
                await db.execute(
                    select(BreedingRecord)
                    .where(BreedingRecord.doe_id == doe["id"], BreedingRecord.outcome == "ABORTED")
                    .order_by(BreedingRecord.id)
                )
            ).scalars()
        )
        assert [
            (row.id, row.loss_date, row.loss_cause, row.loss_recorded_by_id, row.loss_recorded_at)
            for row in retained
        ] == loss_facts
