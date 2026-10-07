"""Actual complete growth, single-animal confidence and festival-sale observations."""

from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.db import get_sessionmaker
from app.models import (
    Animal,
    BreedingRecord,
    Farm,
    KiddingRecord,
    KidEntry,
    WeightRecord,
)
from app.simulation.assumptions import SimulationAssumptions
from app.simulation.defaults import get_preset
from app.utils import add_months, today

from .conftest import owner_with_farm
from .type_helpers import json_object


async def test_actual_credible_large_live_birth_weights_raise_only_decreasing_early_growth_ages(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        reference = today(farm.timezone)
        birth = reference - timedelta(days=120)
        buck = Animal(
            farm_id=farm_id,
            tag_number="OBS-SIRE",
            sex="M",
            source="BORN",
            current_bucket="BREEDING",
            date_of_birth=reference - timedelta(days=1000),
            status="ACTIVE",
        )
        db.add(buck)
        await db.flush()
        ordinal = 0
        for index, gestation in enumerate((90, 150, 150, 220, 221)):
            doe = Animal(
                farm_id=farm_id,
                tag_number=f"OBS-DAM-{index}",
                sex="F",
                source="BORN",
                current_bucket="FOUNDATION",
                date_of_birth=reference - timedelta(days=1000),
                status="ACTIVE",
            )
            db.add(doe)
            await db.flush()
            bred_on = birth - timedelta(days=gestation)
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
                expected_kidding_date=bred_on + timedelta(days=150),
            )
            db.add(breeding)
            await db.flush()
            kidding = KiddingRecord(
                farm_id=farm_id, doe_id=doe.id, date=birth, breeding_record_id=breeding.id
            )
            db.add(kidding)
            await db.flush()
            for _ in range(2):
                stillborn = ordinal >= 8
                dead = ordinal == 0
                sex = "F" if ordinal < 5 else "M"
                animal_id = None
                if not stillborn:
                    kid = Animal(
                        farm_id=farm_id,
                        tag_number=f"OBS-KID-{ordinal}",
                        sex=sex,
                        source="BORN",
                        current_bucket="FOUNDATION",
                        date_of_birth=birth,
                        status="DEAD" if dead else "ACTIVE",
                        status_date=birth + timedelta(days=30) if dead else None,
                    )
                    db.add(kid)
                    await db.flush()
                    animal_id = kid.id
                db.add(
                    KidEntry(
                        farm_id=farm_id,
                        kidding_record_id=kidding.id,
                        animal_id=animal_id,
                        sex=sex,
                        status="STILLBORN" if stillborn else "DIED" if dead else "ALIVE",
                        birth_weight=0.5
                        if stillborn
                        else 7.1 + ordinal / 10
                        if ordinal < 5
                        else None,
                        mortality_reported_at=birth + timedelta(days=30) if dead else None,
                    )
                )
                ordinal += 1
        await db.commit()
    response = await _request(client, owner)
    assert response.status_code == 200, response.text
    body = json_object(response.json())
    assumptions = SimulationAssumptions.model_validate(body["assumptions"])
    evidence = {row["path"]: row for row in body["evidence"]}
    assert {
        "reproduction.gestation_months",
        "growth.birth_weight_kg",
        "growth.weight_by_age_months",
        "mortality.kid_pre_weaning",
        "reproduction.stillbirth_rate",
    }.issubset(evidence)
    assert assumptions.reproduction.conception_rate == 1.0
    assert assumptions.reproduction.litter_size == 2.0
    assert assumptions.reproduction.parity_multipliers.conception_rate == [1.0]
    assert assumptions.reproduction.parity_multipliers.litter_size == [1.0]
    assert assumptions.reproduction.gestation_months == 5
    assert evidence["reproduction.gestation_months"]["sample_size"] == 4
    assert assumptions.reproduction.stillbirth_rate == pytest.approx(2 / 10)
    assert assumptions.reproduction.sex_ratio_female == pytest.approx(5 / 8)
    assert assumptions.mortality.kid_pre_weaning == pytest.approx(1 / 8)
    assert assumptions.growth.birth_weight_kg == pytest.approx(7.3)
    assert assumptions.growth.weight_by_age_months[0] == pytest.approx(7.3)
    assert all(weight >= 7.3 for weight in assumptions.growth.weight_by_age_months)
    assert evidence["growth.birth_weight_kg"]["sample_size"] == 5
    assert evidence["growth.weight_by_age_months"]["sample_size"] == 5
    assert evidence["mortality.kid_pre_weaning"]["sample_size"] == 8
    assert evidence["reproduction.stillbirth_rate"]["sample_size"] == 10

    assert assumptions.growth.weight_by_age_months[1] == pytest.approx(7.3)


async def _request(client: httpx.AsyncClient, owner: dict[str, str]) -> httpx.Response:
    try:
        response = await client.get("/api/simulation/calibration", headers=owner)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(
            f"Complete supported real native observations must produce a calibration: {exc}"
        )
    assert response.status_code == 200, response.text
    return response


async def test_actual_one_animal_snapshot_is_complete_and_reports_high_cohort_confidence(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        reference = today(farm.timezone)
        db.add(
            Animal(
                farm_id=farm_id,
                tag_number="ONLY-ACTUAL-DOE",
                sex="F",
                source="BORN",
                current_bucket="FOUNDATION",
                date_of_birth=reference - timedelta(days=800),
                status="ACTIVE",
            )
        )
        await db.commit()
    body = json_object((await _request(client, owner)).json())
    evidence = {row["path"]: row for row in body["evidence"]}
    assert body["assumptions"]["herd"]["does"] == 1
    assert evidence["herd.does"]["sample_size"] == 1
    assert evidence["herd.does"]["confidence"] == "high"


async def test_actual_five_weights_at_three_ages_include_the_lookback_boundary_and_known_births(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        reference = today(farm.timezone)
        boundary = add_months(reference, -24)
        observations = [
            (0, 5.0, boundary),
            (3, 10.0, reference),
            (3, 14.0, reference),
            (6, 18.0, reference),
            (6, 22.0, reference),
        ]
        for index, (age, weight, observed_on) in enumerate(observations):
            dob = add_months(observed_on, -age)
            animal = Animal(
                farm_id=farm_id,
                tag_number=f"REAL-GROWTH-{index}",
                sex="F",
                source="PURCHASED",
                current_bucket="FOUNDATION",
                date_of_birth=dob if index != 2 else None,
                estimated_dob=dob if index == 2 else None,
                purchase_date=dob,
                status="ACTIVE",
            )
            db.add(animal)
            await db.flush()
            db.add(
                WeightRecord(
                    farm_id=farm_id, animal_id=animal.id, date=observed_on, weight_kg=weight
                )
            )
        await db.commit()
    body = json_object((await _request(client, owner)).json())
    assumptions = SimulationAssumptions.model_validate(body["assumptions"])
    evidence: dict[str, Any] = {row["path"]: row for row in body["evidence"]}
    assert "growth.weight_by_age_months" in evidence, (
        "Five complete actual weights at three ages must retain the growth evidence"
    )
    curve = assumptions.growth.weight_by_age_months
    assert curve[0] == assumptions.growth.birth_weight_kg == pytest.approx(5.0)
    assert curve[3] == pytest.approx(12.0)
    assert curve[6] == pytest.approx(20.0)
    assert evidence["growth.weight_by_age_months"]["sample_size"] == 5


async def test_actual_festival_sales_remove_the_premium_before_calibrating_the_plain_base_price(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        reference = today(farm.timezone)
        # Historical native sale date from the current-release public calendar.
        sold_on = reference.replace(month=5, day=28)
        assert sold_on <= reference
        for index in range(5):
            db.add(
                Animal(
                    farm_id=farm_id,
                    tag_number=f"REAL-FESTIVAL-SALE-{index}",
                    sex="M",
                    source="BORN",
                    current_bucket="FOUNDATION",
                    date_of_birth=sold_on - timedelta(days=900),
                    status="SOLD",
                    status_date=sold_on,
                    sale_price=Decimal("9000"),
                    sale_weight_kg=30.0,
                )
            )
        await db.commit()
    body = json_object((await _request(client, owner)).json())
    evidence = {row["path"]: row for row in body["evidence"]}
    assert "sales.meat_price_per_kg" in evidence
    expected = 300.0 / (1.0 + get_preset("osmanabadi").sales.eid_price_uplift)
    assert body["assumptions"]["sales"]["meat_price_per_kg"] == pytest.approx(expected)
    assert evidence["sales.meat_price_per_kg"]["sample_size"] == 5
