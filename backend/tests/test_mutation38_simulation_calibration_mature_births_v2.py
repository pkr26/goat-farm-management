"""Real births retain mature exposure and truthful warnings at the model ceiling."""

from datetime import timedelta

import httpx
import pytest

from app.db import get_sessionmaker
from app.models import Animal, BreedingRecord, Farm, KiddingRecord, KidEntry
from app.simulation.assumptions import SimulationAssumptions
from app.utils import today

from .conftest import owner_with_farm
from .type_helpers import json_object


async def test_actual_births_at_completed_weaning_and_stillbirth_ceiling_are_reported_truthfully(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        reference = today(farm.timezone)
        # The declared three 30.44-day model months complete on day91;
        # a day61 live birth has completed only two months of exposure.
        buck = Animal(
            farm_id=farm_id,
            tag_number="MATURE-SIRE",
            sex="M",
            source="BORN",
            current_bucket="BREEDING",
            date_of_birth=reference - timedelta(days=1000),
            status="ACTIVE",
        )
        db.add(buck)
        await db.flush()
        ordinal = 0
        for litter in range(5):
            birth = reference - timedelta(days=61 if litter == 2 else 91)
            doe = Animal(
                farm_id=farm_id,
                tag_number=f"MATURE-DAM-{litter}",
                sex="F",
                source="BORN",
                current_bucket="FOUNDATION",
                date_of_birth=reference - timedelta(days=1000),
                status="ACTIVE",
            )
            db.add(doe)
            await db.flush()
            bred_on = birth - timedelta(days=150)
            breeding = BreedingRecord(
                farm_id=farm_id,
                doe_id=doe.id,
                buck_id=buck.id,
                breeding_date=bred_on,
                ultrasound_date=bred_on + timedelta(days=32),
                ultrasound_result_date=bred_on + timedelta(days=32),
                ultrasound_done=True,
                pregnant=True,
                outcome="CONFIRMED_PREGNANT",
                expected_kidding_date=birth,
            )
            db.add(breeding)
            await db.flush()
            kidding = KiddingRecord(
                farm_id=farm_id,
                doe_id=doe.id,
                date=birth,
                breeding_record_id=breeding.id,
            )
            db.add(kidding)
            await db.flush()
            for _ in range(2):
                stillborn = ordinal >= 5
                dead = ordinal == 0
                animal_id = None
                if not stillborn:
                    animal = Animal(
                        farm_id=farm_id,
                        tag_number=f"MATURE-KID-{ordinal}",
                        sex="F",
                        source="BORN",
                        current_bucket="FOUNDATION",
                        date_of_birth=birth,
                        status="DEAD" if dead else "ACTIVE",
                        status_date=birth + timedelta(days=30) if dead else None,
                    )
                    db.add(animal)
                    await db.flush()
                    animal_id = animal.id
                db.add(
                    KidEntry(
                        farm_id=farm_id,
                        kidding_record_id=kidding.id,
                        animal_id=animal_id,
                        sex="F",
                        status="STILLBORN" if stillborn else "DIED" if dead else "ALIVE",
                        mortality_reported_at=birth + timedelta(days=30) if dead else None,
                    )
                )
                ordinal += 1
        await db.commit()
    response = await client.get("/api/simulation/calibration", headers=owner)
    assert response.status_code == 200, response.text
    body = json_object(response.json())
    assumptions = SimulationAssumptions.model_validate(body["assumptions"])
    evidence = {row["path"]: row for row in body["evidence"]}
    assert {"reproduction.stillbirth_rate", "mortality.kid_pre_weaning"}.issubset(evidence)
    assert assumptions.reproduction.stillbirth_rate == pytest.approx(0.5)
    assert evidence["reproduction.stillbirth_rate"]["sample_size"] == 10
    assert assumptions.mortality.kid_pre_weaning == pytest.approx(1 / 4)
    assert evidence["mortality.kid_pre_weaning"]["sample_size"] == 4
    assert not any("exceeds the model ceiling" in warning for warning in body["warnings"])
