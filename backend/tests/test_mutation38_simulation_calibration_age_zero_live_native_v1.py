"""Actual coherent age-zero observations complete the current native calibration API."""

from datetime import timedelta

import httpx
import pytest

from app.db import get_sessionmaker
from app.models import Animal, Farm
from app.schemas.animals import WeightIn
from app.simulation.assumptions import SimulationAssumptions
from app.utils import add_months, today

from .conftest import owner_with_farm
from .type_helpers import json_object


@pytest.mark.parametrize("age_zero_days", [0, 28])
async def test_real_current_birthdate_and_first_month_weights_are_admitted_and_calibrate(
    client: httpx.AsyncClient,
    age_zero_days: int,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    observations = [(0, 9.0), (3, 12.0), (6, 20.0), (0, 9.0), (6, 20.0)]
    stored: list[tuple[int, float]] = []
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        reference = today(farm.timezone)
        for index, (age, weight) in enumerate(observations):
            dob = (
                reference - timedelta(days=age_zero_days)
                if age == 0
                else add_months(reference, -age)
            )
            assert dob <= reference
            animal = Animal(
                farm_id=farm_id,
                tag_number=f"ACTUAL-BIRTHDATE-{index}",
                sex="F",
                source="BORN",
                current_bucket="FOUNDATION",
                date_of_birth=dob,
                status="ACTIVE",
            )
            db.add(animal)
            await db.flush()
            stored.append((animal.id, weight))
        await db.commit()
    for animal_id, weight in stored:
        admitted = WeightIn.model_validate({"date": reference.isoformat(), "weight_kg": weight})
        assert admitted.date == reference and admitted.weight_kg == weight
        response = await client.post(
            f"/api/animals/{animal_id}/weight",
            headers=owner,
            json={"date": reference.isoformat(), "weight_kg": weight},
        )
        assert response.status_code == 201, response.text
        persisted = json_object(response.json())
        assert persisted["date"] == reference.isoformat() and persisted["weight_kg"] == weight
    response = await client.get("/api/simulation/calibration", headers=owner)
    assert response.status_code == 200, response.text
    body = json_object(response.json())
    assert body["reference_date"] == reference.isoformat()
    assumptions = SimulationAssumptions.model_validate(body["assumptions"])
    evidence = {row["path"]: row for row in body["evidence"]}
    assert "growth.weight_by_age_months" in evidence
    assert evidence["growth.weight_by_age_months"]["sample_size"] == 5
    assert [assumptions.growth.weight_by_age_months[age] for age in (0, 3, 6)] == [9.0, 12.0, 20.0]
    assert assumptions.growth.birth_weight_kg == 9.0
    assert assumptions.herd.female_kids == 2
    assert assumptions.herd.female_weaners == 1
    assert assumptions.herd.female_growers == 2
    assert assumptions.growth.adult_weight_doe_kg >= max(assumptions.growth.weight_by_age_months)
    assert assumptions.growth.adult_weight_buck_kg >= max(assumptions.growth.weight_by_age_months)
