"""Resolve unanswered restored provenance and its genuinely answered service."""

from datetime import date, timedelta

import httpx
from sqlalchemy import select, text

from app.db import get_sessionmaker
from app.models import BreedingRecord, Transaction
from app.utils import today

from .conftest import owner_with_farm
from .test_kidding_husbandry import breed, confirm_pregnancy, make_animal


async def test_retirement_resolves_an_earlier_restored_pending_snapshot_and_its_real_confirmation(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe = await make_animal(client, owner, "RESTORED-SERVICE-DOE", sex="F")
    buck = await make_animal(client, owner, "RESTORED-SERVICE-BUCK", sex="M")
    async with get_sessionmaker()() as db:
        # A restored archive may retain an earlier key for an unanswered
        # snapshot of the same physical service. Reserve that ordinary
        # positive identity before the real current writer records it; no
        # animal, task or clinical foreign key is rewritten.
        await db.execute(text("SELECT setval('breeding_records_id_seq', 2, false)"))
        await db.commit()
    pending = await breed(client, owner, doe["id"], buck["id"], today() - timedelta(days=40))
    assert pending["id"] == 2
    async with get_sessionmaker()() as db:
        actual = await db.get(BreedingRecord, pending["id"])
        assert actual is not None and actual.outcome == "PENDING"
        # Capture the real unanswered service before its real scan. The
        # duplicate below restores this exact earlier observation state;
        # it does not claim a second biological pregnancy or invent a scan.
        archived = BreedingRecord(
            id=1,
            farm_id=actual.farm_id,
            doe_id=actual.doe_id,
            buck_id=actual.buck_id,
            breeding_date=actual.breeding_date,
            method=actual.method,
            heat_cycle_number=actual.heat_cycle_number,
            ultrasound_date=actual.ultrasound_date,
            ultrasound_result_date=actual.ultrasound_result_date,
            ultrasound_done=actual.ultrasound_done,
            pregnant=actual.pregnant,
            kid_count_detected=actual.kid_count_detected,
            expected_kidding_date=actual.expected_kidding_date,
            outcome=actual.outcome,
            created_by_id=actual.created_by_id,
            created_at=actual.created_at,
        )
    confirmed = await confirm_pregnancy(client, owner, pending["id"], kid_count=1)
    assert confirmed["outcome"] == "CONFIRMED_PREGNANT"
    async with get_sessionmaker()() as db:
        db.add(archived)
        await db.commit()
        originals = list(
            (await db.execute(select(BreedingRecord).order_by(BreedingRecord.id))).scalars()
        )
        assert [record.outcome for record in originals] == ["PENDING", "CONFIRMED_PREGNANT"]
        assert originals[0].ultrasound_done is False and originals[0].pregnant is None
        assert originals[1].ultrasound_done is True and originals[1].pregnant is True
        assert originals[1].ultrasound_result_date is not None
    response = await client.post(
        f"/api/animals/{doe['id']}/status",
        headers=owner | {"Idempotency-Key": "retire-restored-service-snapshots"},
        json={"new_status": "SOLD", "date": today().isoformat(), "sale_price": 5000},
    )
    assert response.status_code == 200, response.text
    async with get_sessionmaker()() as db:
        records = list(
            (await db.execute(select(BreedingRecord).order_by(BreedingRecord.id))).scalars()
        )
        assert [record.outcome for record in records] == ["UNASSESSED", "ABORTED"]
        assert records[0].ultrasound_done is False and records[0].pregnant is None
        assert records[0].ultrasound_result_date is None and records[0].loss_date is None
        assert records[1].pregnant is False and records[1].loss_date == today()
        assert records[1].loss_cause == "ANIMAL_STATUS_CHANGE"
        assert records[1].ultrasound_result_date == date.fromisoformat(
            confirmed["ultrasound_result_date"]
        )
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
        assert len(ledger) == 1 and ledger[0].amount == 5000
