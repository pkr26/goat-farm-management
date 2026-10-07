"""Existence reads tolerate duplicate confirmed provenance admitted by storage."""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import MultipleResultsFound

from app.db import get_sessionmaker
from app.models import BreedingRecord, KiddingRecord, Task
from app.services.breeding import doe_has_open_breeding

from .conftest import owner_with_farm
from .test_breeding_extended import pregnant_doe


async def test_restored_confirmed_provenance_still_means_an_open_pregnancy(
    client: httpx.AsyncClient,
) -> None:
    """A restored copy of one confirmed event is not a second biological event."""
    owner = await owner_with_farm(client)
    doe, _buck, breeding = await pregnant_doe(client, owner)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        original = await db.get(BreedingRecord, breeding["id"])
        assert original is not None and original.outcome == "CONFIRMED_PREGNANT"
        duties_before = list(
            (
                await db.execute(
                    select(Task.id).where(Task.breeding_record_id == original.id).order_by(Task.id)
                )
            ).scalars()
        )
        assert duties_before
        # Normal ORM insertion and commit enforce every current constraint.
        # The partial uniqueness rule covers PENDING, so backup provenance
        # can legally repeat this already-confirmed event under another key.
        restored = BreedingRecord(
            created_at=original.created_at,
            farm_id=original.farm_id,
            doe_id=original.doe_id,
            buck_id=original.buck_id,
            semen_sire_name=original.semen_sire_name,
            breeding_date=original.breeding_date,
            method=original.method,
            heat_cycle_number=original.heat_cycle_number,
            ultrasound_date=original.ultrasound_date,
            ultrasound_result_date=original.ultrasound_result_date,
            ultrasound_done=original.ultrasound_done,
            pregnant=original.pregnant,
            kid_count_detected=original.kid_count_detected,
            expected_kidding_date=original.expected_kidding_date,
            outcome=original.outcome,
            created_by_id=original.created_by_id,
        )
        db.add(restored)
        await db.commit()
        restored_id = restored.id

    async with get_sessionmaker()() as db:
        records = list(
            (
                await db.execute(
                    select(BreedingRecord).where(
                        BreedingRecord.id.in_([breeding["id"], restored_id])
                    )
                )
            ).scalars()
        )
        assert len(records) == 2
        assert all(record.outcome == "CONFIRMED_PREGNANT" for record in records)
        assert not list(
            (
                await db.execute(
                    select(KiddingRecord.id).where(
                        KiddingRecord.breeding_record_id.in_([breeding["id"], restored_id])
                    )
                )
            ).scalars()
        )
        assert (
            list(
                (
                    await db.execute(
                        select(Task.id)
                        .where(Task.breeding_record_id.in_([breeding["id"], restored_id]))
                        .order_by(Task.id)
                    )
                ).scalars()
            )
            == duties_before
        )
        try:
            has_open_pregnancy = await doe_has_open_breeding(db, farm_id, doe["id"])
        except MultipleResultsFound as error:
            pytest.fail(f"Valid restored provenance must preserve the existence contract: {error}")
        assert has_open_pregnancy is True
