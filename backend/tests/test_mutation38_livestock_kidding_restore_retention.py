"""Kidding fails closed when native bounded retention removes restored provenance."""

import asyncio
from datetime import date
from typing import Any

import httpx
import pytest
from sqlalchemy import Result, Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Executable

from app.db import get_sessionmaker
from app.models import Animal, BreedingRecord, KiddingRecord, KidEntry, Task
from app.services.retention import _delete_batch

from .conftest import owner_with_farm
from .test_breeding_extended import pregnant_doe


@pytest.mark.parametrize("removed_scope", ["breeding_record", "restored_parent"])
async def test_kidding_returns404_when_native_retention_removes_unlinked_restored_provenance(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    removed_scope: str,
) -> None:
    owner = await owner_with_farm(client, email="kidding-restore-retention@farm.in")
    doe_data, buck_data, donor_data = await pregnant_doe(client, owner, gestation_days=160)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        donor = await db.get(BreedingRecord, donor_data["id"])
        original_doe = await db.get(Animal, doe_data["id"])
        assert donor is not None and original_doe is not None
        donor_duties = list(
            (
                await db.execute(
                    select(Task.id).where(Task.breeding_record_id == donor.id).order_by(Task.id)
                )
            ).scalars()
        )
        assert donor_duties and donor.outcome == "CONFIRMED_PREGNANT"
        restored_doe_id = original_doe.id
        if removed_scope == "restored_parent":
            # Restore an additional identity for the retained historical
            # entity at initial insertion. The real donor animal and its
            # entire clinical graph remain untouched and referenced.
            copied_doe = Animal(
                farm_id=original_doe.farm_id,
                tag_number="RESTORED-RETENTION-DAM",
                breed=original_doe.breed,
                sex=original_doe.sex,
                date_of_birth=original_doe.date_of_birth,
                estimated_dob=original_doe.estimated_dob,
                source=original_doe.source,
                purchase_date=original_doe.purchase_date,
                purchase_price=original_doe.purchase_price,
                seller_name=original_doe.seller_name,
                current_bucket=original_doe.current_bucket,
                status=original_doe.status,
                created_at=original_doe.created_at,
                coat_color=original_doe.coat_color,
                horned=original_doe.horned,
            )
            db.add(copied_doe)
            await db.flush()
            restored_doe_id = copied_doe.id
        # This is a provenance copy of the actual confirmed event, not an
        # invented scan or a second biological event. Every CHECK, FK and
        # uniqueness constraint remains enabled. The restored record has
        # no inbound Task/Kidding links; all original duties stay on donor.
        restored = BreedingRecord(
            created_at=donor.created_at,
            farm_id=donor.farm_id,
            doe_id=restored_doe_id,
            buck_id=donor.buck_id,
            breeding_date=donor.breeding_date,
            method=donor.method,
            heat_cycle_number=donor.heat_cycle_number,
            ultrasound_date=donor.ultrasound_date,
            ultrasound_result_date=donor.ultrasound_result_date,
            ultrasound_done=donor.ultrasound_done,
            pregnant=donor.pregnant,
            kid_count_detected=donor.kid_count_detected,
            expected_kidding_date=donor.expected_kidding_date,
            outcome=donor.outcome,
            created_by_id=donor.created_by_id,
        )
        db.add(restored)
        await db.commit()
        restored_id = restored.id
        assert restored_id != donor.id
        expected_date = donor.expected_kidding_date
        assert isinstance(expected_date, date)

    read_finished = asyncio.Event()
    permit_request = asyncio.Event()
    original_execute = AsyncSession.execute

    async def pause_after_real_precheck(
        db: AsyncSession,
        statement: Executable,
        params: dict[str, Any] | list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Result[Any]:
        result = await original_execute(db, statement, params, **kwargs)
        if isinstance(statement, Select):
            names = tuple(column["name"] for column in statement.column_descriptions)
            if names == ("doe_id", "buck_id", "farm_id") and (
                restored_id in statement.compile().params.values()
            ):
                # The actual scalar SELECT has executed and buffered its
                # real same-farm row. Pause only the next SQL boundary;
                # return its original Result unchanged after retention.
                read_finished.set()
                await permit_request.wait()
        return result

    monkeypatch.setattr(AsyncSession, "execute", pause_after_real_precheck)
    request = asyncio.create_task(
        client.post(
            "/api/kidding",
            json={
                "breeding_record_id": restored_id,
                "date": expected_date.isoformat(),
                "kids": [{"sex": "F", "birth_weight": 2.5}],
            },
            headers=owner,
        )
    )
    try:
        try:
            await asyncio.wait_for(read_finished.wait(), timeout=30)
        except TimeoutError:
            pytest.fail("The real kidding request did not finish its restored-row scalar read")
        async with get_sessionmaker()() as retention:
            # Native _delete_batch documents a bounded locked table delete
            # with typed generic table/id/candidates arguments. Current
            # scheduled retention never targets these clinical tables; the
            # entire-backend native utility contract admits these arguments.
            removed = await _delete_batch(
                retention,
                table=BreedingRecord,
                id_column=BreedingRecord.id,
                candidates=select(BreedingRecord.id).where(
                    BreedingRecord.id == restored_id, BreedingRecord.farm_id == farm_id
                ),
                batch_size=1,
            )
            assert removed == 1
            if removed_scope == "restored_parent":
                removed = await _delete_batch(
                    retention,
                    table=Animal,
                    id_column=Animal.id,
                    candidates=select(Animal.id).where(
                        Animal.id == restored_doe_id, Animal.farm_id == farm_id
                    ),
                    batch_size=1,
                )
                assert removed == 1
            await retention.commit()
        async with get_sessionmaker()() as observer:
            assert await observer.get(BreedingRecord, restored_id) is None
            if removed_scope == "restored_parent":
                assert await observer.get(Animal, restored_doe_id) is None
        permit_request.set()
        try:
            response = await asyncio.wait_for(request, timeout=30)
        except TimeoutError:
            pytest.fail("The real kidding request did not finish after bounded retention committed")
    finally:
        permit_request.set()
        if not request.done():
            request.cancel()
        await asyncio.gather(request, return_exceptions=True)

    assert response.status_code == 404, response.text
    async with get_sessionmaker()() as observer:
        original = await observer.get(BreedingRecord, donor_data["id"])
        assert original is not None and original.outcome == "CONFIRMED_PREGNANT"
        assert original.doe_id == doe_data["id"] and original.buck_id == buck_data["id"]
        assert await observer.get(Animal, doe_data["id"]) is not None
        assert await observer.get(Animal, buck_data["id"]) is not None
        assert (
            list(
                (
                    await observer.execute(
                        select(Task.id)
                        .where(Task.breeding_record_id == donor_data["id"])
                        .order_by(Task.id)
                    )
                ).scalars()
            )
            == donor_duties
        )
        assert not list((await observer.execute(select(KiddingRecord.id))).scalars())
        assert not list((await observer.execute(select(KidEntry.id))).scalars())
