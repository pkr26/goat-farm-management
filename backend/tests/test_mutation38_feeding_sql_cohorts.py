"""Conserve real SQL-plan cohorts, historical ration evidence and displayed ordering."""

from datetime import datetime, time, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import BucketDefinition, Farm, WeightRecord
from app.services.feeding import feeding_plan
from app.services.retention import _delete_batch
from app.utils import today

from .conftest import owner_with_farm
from .test_feeding_extended import _orm_animal, make_animal


async def test_historical_plan_preserves_one_head_and_the_latest_as_of_scale_reading(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="feeding-historical-ref@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    reference = today() - timedelta(days=5)
    async with get_sessionmaker()() as db:
        animal = _orm_animal(farm_id, "AS-OF-RATION", sex="F", bucket="FOUNDATION", dob_days=500)
        db.add(animal)
        await db.flush()
        db.add_all(
            WeightRecord(farm_id=farm_id, animal_id=animal.id, date=observed, weight_kg=weight)
            for observed, weight in [
                (reference - timedelta(days=1), 30.0),
                (reference, 40.0),
                (today(), 80.0),
            ]
        )
        await db.commit()
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        lines = await feeding_plan(db, farm, reference)
        assert len(lines) == 1 and lines[0]["heads"] == 1
        assert lines[0]["basis"] == "weight"
        assert lines[0]["mean_weight_kg"] == 40.0
        assert lines[0]["kg_per_head"] == pytest.approx(1.3)
        assert lines[0]["daily_kg"] == pytest.approx(1.3)


@pytest.mark.parametrize(
    "days_since_arrival,recipe", [(2, "DRY_ROUGHAGE_ONLY"), (3, "MAINTENANCE_75_25")]
)
async def test_quarantine_plan_switches_on_the_literal_third_calendar_day(
    client: httpx.AsyncClient, days_since_arrival: int, recipe: str
) -> None:
    owner = await owner_with_farm(client, email="feeding-quarantine-day@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    reference = today()
    async with get_sessionmaker()() as db:
        animal = _orm_animal(
            farm_id, "QUARANTINE-CALENDAR", sex="F", bucket="QUARANTINE", dob_days=500
        )
        # Existing imported cohort with an actual known creation/arrival
        # instant, using the farm's India business calendar and no fabricated
        # reproductive history. This is the plan's documented no-move anchor.
        animal.created_at = datetime.combine(
            reference - timedelta(days=days_since_arrival), time(6)
        )
        db.add(animal)
        await db.commit()
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        lines = await feeding_plan(db, farm, reference)
        assert len(lines) == 1 and lines[0]["heads"] == 1
        assert lines[0]["recipe_code"] == recipe


async def test_creep_includes_day_sixty_and_requires_the_actual_active_recovery_dam(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="feeding-dependent-dam@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        dam = _orm_animal(farm_id, "DEPENDENT-DAM", sex="F", bucket="RECOVERY", dob_days=800)
        db.add(dam)
        await db.flush()
        dam_id = dam.id
        db.add_all(
            _orm_animal(
                farm_id, f"DEPENDENT-{age}", sex="F", bucket="RECOVERY", dob_days=age, dam_id=dam_id
            )
            for age in [40, 60]
        )
        await db.commit()
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        before = await feeding_plan(db, farm, today())
        terminal = next(line for line in before if line["creep_band"] == "46–60 d")
        assert terminal["heads"] == 1 and terminal["kg_per_head"] == 0.3
        assert terminal["segment"] == "CREEP_BAND"
    recorded = await client.post(
        f"/api/animals/{dam_id}/status",
        headers=owner,
        json={
            "new_status": "DEAD",
            "date": today().isoformat(),
            "notes": "Recorded death of the restored legacy dam",
        },
    )
    assert recorded.status_code == 200, recorded.text
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        after = await feeding_plan(db, farm, today())
        assert len(after) == 1 and after[0]["heads"] == 2
        assert after[0]["recipe_code"] == "LACTATING_60_40"
        assert after[0]["segment"] == "ALL"


async def test_an_already_weaned_day_sixty_grower_keeps_its_actual_weight_basis(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="feeding-weaned-day-sixty@farm.in")
    await make_animal(
        client, owner, "WEANED-SIXTY", bucket="FEMALE_KIDS", dob_days=60, weight_kg=10.0
    )
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        assert farm is not None
        lines = await feeding_plan(db, farm, today())
        assert len(lines) == 1 and lines[0]["heads"] == 1
        assert lines[0]["basis"] == "weight" and lines[0]["mean_weight_kg"] == 10.0
        assert lines[0]["kg_per_head"] == 0.5


async def test_breeding_plan_orders_female_then_male_and_supplements_only_bucks(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="feeding-sex-order@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        db.add_all(
            [_orm_animal(farm_id, "ORDER-MALE", sex="M", bucket="BREEDING", dob_days=900)]
            + [
                _orm_animal(farm_id, f"ORDER-FEMALE-{n}", sex="F", bucket="BREEDING", dob_days=800)
                for n in range(3)
            ]
        )
        await db.commit()
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        lines = await feeding_plan(db, farm, today())
        assert [line["segment"] for line in lines] == ["FEMALE", "MALE"]
        assert [line["heads"] for line in lines] == [3, 1]
        assert [line["kg_per_head"] for line in lines] == [1.2, 1.7]
        assert lines[0]["note"] is None
        assert lines[1]["note"] == "includes 0.5 kg breeding-season supplement"
        assert [shift["pct"] for shift in lines[0]["shifts"]] == [40, 20, 40]


async def test_plan_orders_equal_native_reference_priorities_with_a_missing_definition(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="feeding-restored-reference@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        definitions = list((await db.execute(select(BucketDefinition))).scalars())
        saved: dict[str, dict[str, Any]] = {
            definition.code: {
                "id": definition.id,
                "code": definition.code,
                "name": definition.name,
                "who": definition.who,
                "exit_rule": definition.exit_rule,
                "daily_kg_per_head": definition.daily_kg_per_head,
                "sort_order": definition.sort_order,
            }
            for definition in definitions
        }
        try:
            foundation = next(d for d in definitions if d.code == "FOUNDATION")
            removed = await _delete_batch(
                db,
                table=BucketDefinition,
                id_column=BucketDefinition.id,
                candidates=select(BucketDefinition.id).where(BucketDefinition.id == foundation.id),
                batch_size=1,
            )
            assert removed == 1
            for definition in definitions:
                if definition.code in {"QUARANTINE", "BREEDING"}:
                    definition.sort_order = 99
            # Real restored configuration compatibility: the definition row
            # has no inbound FK, sort_order allows nonnegative tied priorities,
            # and startup seeding deliberately preserves existing configuration.
            # Clinical animal buckets are intact and no constraint is disabled.
            db.add_all(
                [
                    _orm_animal(farm_id, "REFERENCE-Q", sex="F", bucket="QUARANTINE", dob_days=500),
                    _orm_animal(farm_id, "REFERENCE-F", sex="F", bucket="FOUNDATION", dob_days=500),
                    _orm_animal(farm_id, "REFERENCE-B-M", sex="M", bucket="BREEDING", dob_days=900),
                    *[
                        _orm_animal(
                            farm_id, f"REFERENCE-B-F-{n}", sex="F", bucket="BREEDING", dob_days=800
                        )
                        for n in range(3)
                    ],
                ]
            )
            await db.commit()
            farm = await db.get(Farm, farm_id)
            assert farm is not None
            lines = await feeding_plan(db, farm, today())
            assert [(line["bucket"], line["segment"]) for line in lines] == [
                ("QUARANTINE", "ALL"),
                ("FOUNDATION", "ALL"),
                ("BREEDING", "FEMALE"),
                ("BREEDING", "MALE"),
            ]
            assert [line["heads"] for line in lines] == [1, 1, 3, 1]
            assert sum(line["heads"] for line in lines) == 6
        finally:
            await db.rollback()
            current = {d.code: d for d in (await db.execute(select(BucketDefinition))).scalars()}
            for code, original in saved.items():
                if code not in current:
                    db.add(BucketDefinition(**original))
                else:
                    for key, value in original.items():
                        setattr(current[code], key, value)
            await db.commit()
            restored = list((await db.execute(select(BucketDefinition))).scalars())
            assert {
                d.code: {key: getattr(d, key) for key in saved[d.code]} for d in restored
            } == saved
