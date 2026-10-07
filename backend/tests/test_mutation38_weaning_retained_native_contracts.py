"""Legacy litter lookup and incomplete native prelocks fail without losing real litter facts."""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import MultipleResultsFound

from app.db import get_sessionmaker
from app.models import Animal, BreedingRecord, Farm, KiddingRecord, KidEntry, Task, User
from app.services.tasks import _guard_generated_weaning_task, complete_task
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import kid_on_ekd, pregnant_doe


async def test_legacy_weaning_lookup_retains_the_oldest_actual_litter_when_provenance_is_duplicated(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(
        client, email="legacy-weaning-duplicate-provenance@example.test"
    )
    farm_id = int(headers["X-Farm-Id"])
    doe, _, breeding = await pregnant_doe(client, headers, "LEGACY-WEANING-DOE", gestation_days=245)
    actual = await kid_on_ekd(
        client,
        headers,
        breeding,
        kids=[
            {"tag": "LEGACY-WEANING-M", "sex": "M", "birth_weight": 2.8, "status": "ALIVE"},
            {"tag": "LEGACY-WEANING-F", "sex": "F", "birth_weight": 2.5, "status": "ALIVE"},
        ],
    )
    expected_ids = {int(kid["animal_id"]) for kid in actual["kids"]}
    async with get_sessionmaker()() as db:
        donor = await db.get(KiddingRecord, actual["id"])
        assert donor is not None and donor.breeding_record_id == breeding["id"]
        # The historical nullable association intentionally remains supported.
        # This retained duplicate describes the same imported milestone, not a
        # second biological event. The original audited pregnancy/kids/duty
        # graph stays intact; no contemporary association or fact is removed.
        retained = KiddingRecord(
            farm_id=farm_id,
            doe_id=doe["id"],
            date=donor.date,
            breeding_record_id=None,
            ease=donor.ease,
            parity=donor.parity,
            notes="Retained duplicate historical milestone with unknown pregnancy association",
        )
        legacy = Task(
            farm_id=farm_id,
            title="Retained legacy weaning milestone",
            due_date=donor.date + timedelta(days=60),
            animal_id=doe["id"],
            breeding_record_id=None,
            category="WEANING",
            status="PENDING",
            auto_generated=True,
        )
        db.add_all([retained, legacy])
        await db.commit()
        retained_id, task_id = retained.id, legacy.id
        assert retained_id > donor.id
    async with get_sessionmaker()() as db:
        rows = list(
            (
                await db.execute(
                    select(KiddingRecord)
                    .where(
                        KiddingRecord.farm_id == farm_id,
                        KiddingRecord.doe_id == doe["id"],
                        KiddingRecord.date == donor.date,
                    )
                    .order_by(KiddingRecord.id)
                )
            ).scalars()
        )
        assert [row.id for row in rows] == [actual["id"], retained_id]
        assert rows[0].breeding_record_id == breeding["id"] and rows[1].breeding_record_id is None
        task = (
            await db.execute(select(Task).where(Task.id == task_id).with_for_update())
        ).scalar_one()
        try:
            linked_ids = await _guard_generated_weaning_task(db, task)
        except MultipleResultsFound as error:
            pytest.fail(
                f"Supported retained milestone must resolve its oldest actual litter: {error}"
            )
        assert linked_ids == expected_ids
        assert task.status == "PENDING" and task.completed_at is None
        assert await db.get(BreedingRecord, breeding["id"]) is not None
        actual_kids = list(
            (
                await db.execute(select(KidEntry).where(KidEntry.kidding_record_id == actual["id"]))
            ).scalars()
        )
        assert {kid.animal_id for kid in actual_kids} == expected_ids
        assert all(kid.status == "ALIVE" for kid in actual_kids)


async def test_native_weaning_without_its_prelocked_doe_is_a_guarded_conflict(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="native-weaning-missing-prelock@example.test")
    farm_id = int(headers["X-Farm-Id"])
    doe, _, breeding = await pregnant_doe(
        client, headers, "MISSING-WEANING-DOE", gestation_days=245
    )
    actual = await kid_on_ekd(
        client,
        headers,
        breeding,
        kids=[{"tag": "MISSING-WEANING-KID", "sex": "F", "birth_weight": 2.8, "status": "ALIVE"}],
    )
    linked_ids = {doe["id"], *(kid["animal_id"] for kid in actual["kids"])}
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        owner = await db.get(User, farm.owner_id)
        assert owner is not None
        task = (
            await db.execute(
                select(Task)
                .where(Task.breeding_record_id == breeding["id"], Task.category == "WEANING")
                .with_for_update()
            )
        ).scalar_one()
        assert task.status == "PENDING" and task.animal_id == doe["id"]
        task_id = task.id
        # A native caller supplied an incomplete prelock set. This accepted
        # argument shape must be refused by the documented unavailable-animal
        # gate before completion or movement, using only actual persisted rows.
        try:
            await complete_task(
                db, task, owner, locked_animals=[], reference_date=today(farm.timezone)
            )
        except ValueError as error:
            assert "animal linked to this weaning duty is unavailable" in str(error)
        except AttributeError as error:
            pytest.fail(f"Missing native weaning prelock must be a guarded conflict: {error}")
        else:
            pytest.fail("Missing native weaning prelock must not complete the actual duty")
        await db.rollback()
    async with get_sessionmaker()() as db:
        retained_task = await db.get(Task, task_id)
        assert retained_task is not None and retained_task.status == "PENDING"
        assert retained_task.completed_at is None and retained_task.completed_by_id is None
        animals = list(
            (await db.execute(select(Animal).where(Animal.id.in_(linked_ids)))).scalars()
        )
        assert {animal.id for animal in animals} == linked_ids
        assert all(
            animal.status == "ACTIVE" and animal.current_bucket == "RECOVERY" for animal in animals
        )
