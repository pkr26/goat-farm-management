"""Public pregnancy routes preserve legal restored keys and reserve zero."""

import asyncio
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import select, text

from app.db import get_sessionmaker
from app.models import Animal, BreedingRecord, Task, WeightRecord
from app.models.enums import TaskCategory
from app.services._common import _add_task
from app.utils import today

from .conftest import owner_with_farm


async def _restored_pending_record(
    client: httpx.AsyncClient, record_id: int
) -> tuple[dict[str, str], int, int, date]:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    observation_date = today()
    service_date = observation_date - timedelta(days=35)
    # Restore the parent and its real pending duty together, using the
    # original key on their first INSERT. No FK is removed or reassigned,
    # and every PostgreSQL CHECK/trigger remains enabled. Both zero and the
    # int4 ceiling are admitted by the stored/output record schema.
    async with get_sessionmaker()() as db:
        doe = Animal(
            farm_id=farm_id,
            tag_number="RESTORED-RECORD-DOE",
            sex="F",
            source="PURCHASED",
            current_bucket="BREEDING",
            date_of_birth=observation_date - timedelta(days=800),
        )
        buck = Animal(
            farm_id=farm_id,
            tag_number="RESTORED-RECORD-BUCK",
            sex="M",
            source="PURCHASED",
            current_bucket="BREEDING",
            date_of_birth=observation_date - timedelta(days=800),
        )
        db.add_all([doe, buck])
        await db.flush()
        db.add_all(
            [
                WeightRecord(
                    animal_id=doe.id, date=service_date - timedelta(days=1), weight_kg=26.0
                ),
                WeightRecord(
                    animal_id=buck.id, date=service_date - timedelta(days=1), weight_kg=30.0
                ),
            ]
        )
        record = BreedingRecord(
            id=record_id,
            farm_id=farm_id,
            doe_id=doe.id,
            buck_id=buck.id,
            breeding_date=service_date,
            ultrasound_date=service_date + timedelta(days=32),
            method="NATURAL",
            heat_cycle_number=1,
            outcome="PENDING",
        )
        db.add(record)
        await db.flush()
        duty = await _add_task(
            db,
            farm_id,
            "Ultrasound: RESTORED-RECORD-DOE",
            service_date + timedelta(days=32),
            TaskCategory.ULTRASOUND,
            animal_id=doe.id,
            breeding_record_id=record.id,
            title_key="ultrasound",
            title_args={
                "tag": doe.tag_number,
                "due_date": (service_date + timedelta(days=32)).isoformat(),
            },
        )
        await db.flush()
        doe_id, duty_id = doe.id, duty.id
        await db.commit()
    return owner, doe_id, duty_id, observation_date


@pytest.mark.parametrize("record_id", [0, 2_147_483_647], ids=["reserved-zero", "int4-ceiling"])
@pytest.mark.parametrize("operation", ["read", "ultrasound"])
async def test_restored_record_identity_boundaries_preserve_public_read_and_scan_contracts(
    client: httpx.AsyncClient, record_id: int, operation: str
) -> None:
    owner, doe_id, duty_id, observation_date = await _restored_pending_record(client, record_id)
    if operation == "read":
        response = await client.get(f"/api/breeding/{record_id}", headers=owner)
    else:
        response = await client.post(
            f"/api/breeding/{record_id}/ultrasound",
            headers=owner,
            json={"pregnant": True, "kid_count": 1, "date": observation_date.isoformat()},
        )
    assert response.status_code == (404 if record_id == 0 else 200), response.text
    if record_id != 0:
        assert response.json()["id"] == record_id
        assert response.json()["doe_id"] == doe_id
    async with get_sessionmaker()() as db:
        record = await db.get(BreedingRecord, record_id)
        doe = await db.get(Animal, doe_id)
        duty = await db.get(Task, duty_id)
        assert record is not None and doe is not None and duty is not None
        assert duty.breeding_record_id == record_id
        scanned = record_id != 0 and operation == "ultrasound"
        assert record.outcome == ("CONFIRMED_PREGNANT" if scanned else "PENDING")
        assert doe.current_bucket == ("PREGNANCY_EARLY" if scanned else "BREEDING")
        assert duty.status == ("DONE" if scanned else "PENDING")
        assert duty.completed_by_id is not None if scanned else duty.completed_by_id is None


async def test_reserved_record_identity_rejects_without_waiting_for_a_live_doe_transaction(
    client: httpx.AsyncClient,
) -> None:
    owner, doe_id, _duty_id, observation_date = await _restored_pending_record(client, 0)
    request: asyncio.Task[httpx.Response] | None = None
    waited_for_live_doe = False
    async with get_sessionmaker()() as holder:
        holder_pid = (await holder.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        await holder.execute(select(Animal.id).where(Animal.id == doe_id).with_for_update())
        try:
            request = asyncio.create_task(
                client.post(
                    "/api/breeding/0/ultrasound",
                    headers=owner,
                    json={"pregnant": True, "kid_count": 1, "date": observation_date.isoformat()},
                )
            )
            # Observe a real PostgreSQL dependency or the completed rejection.
            # A held herd transaction must not delay a reserved-ID request;
            # the eventual404 alone cannot expose that availability regression.
            for _ in range(2000):
                if request.done():
                    break
                async with get_sessionmaker()() as observer:
                    waited_for_live_doe = bool(
                        await observer.scalar(
                            text(
                                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                "WHERE datname = current_database() "
                                "AND wait_event_type = 'Lock' "
                                "AND :holder_pid = ANY(pg_blocking_pids(pid)))"
                            ),
                            {"holder_pid": holder_pid},
                        )
                    )
                if waited_for_live_doe:
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("Reserved-record request neither rejected nor reached the owned lock")
            await holder.rollback()
            try:
                response = await asyncio.wait_for(request, timeout=30)
            except TimeoutError:
                pytest.fail("Reserved-record request did not finish after the owned lock released")
        finally:
            await holder.rollback()
            if request is not None and not request.done():
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)
    assert response.status_code == 404, response.text
    assert not waited_for_live_doe, "Reserved record0 rejection waited for a real live-doe lock"
