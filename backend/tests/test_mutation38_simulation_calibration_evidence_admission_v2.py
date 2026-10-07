"""Real complete native observations retain evidence admission, boundaries and prices."""

from datetime import date, timedelta
from decimal import Decimal
from math import exp

import httpx
import pytest

from app.db import get_sessionmaker
from app.models import (
    Animal,
    BreedingRecord,
    Farm,
    KiddingRecord,
    KidEntry,
    Transaction,
    WeightRecord,
)
from app.simulation.assumptions import SimulationAssumptions
from app.simulation.defaults import get_preset
from app.utils import add_months, today

from .conftest import owner_with_farm
from .type_helpers import json_object


async def _calibration(client: httpx.AsyncClient, owner: dict[str, str]) -> dict[str, object]:
    response = await client.get("/api/simulation/calibration", headers=owner)
    assert response.status_code == 200, response.text
    return json_object(response.json())


@pytest.mark.parametrize("samples,ages", [(4, 3), (5, 2), (5, 3)])
async def test_real_growth_evidence_requires_five_observations_at_three_ages(
    client: httpx.AsyncClient,
    samples: int,
    ages: int,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    points = [(0, 3.0), (6, 12.0), (12, 18.0)] if ages == 3 else [(0, 3.0), (12, 18.0)]
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        reference = today(farm.timezone)
        for index in range(samples):
            age, weight = points[index % ages]
            dob = add_months(reference, -age)
            animal = Animal(
                farm_id=farm_id,
                tag_number=f"ANCHOR-{index}",
                sex="F",
                source="PURCHASED",
                current_bucket="FOUNDATION",
                date_of_birth=dob,
                purchase_date=dob,
                status="ACTIVE",
            )
            db.add(animal)
            await db.flush()
            db.add(
                WeightRecord(farm_id=farm_id, animal_id=animal.id, date=reference, weight_kg=weight)
            )
        await db.commit()
    body = json_object(await _calibration(client, owner))
    assumptions = SimulationAssumptions.model_validate(body["assumptions"])
    evidence = {row["path"]: row for row in body["evidence"]}
    admitted = samples == 5 and ages == 3
    assert ("growth.weight_by_age_months" in evidence) is admitted
    if admitted:
        assert evidence["growth.weight_by_age_months"]["sample_size"] == 5
        assert [assumptions.growth.weight_by_age_months[a] for a in (0, 6, 12)] == [3.0, 12.0, 18.0]
    else:
        assert (
            assumptions.growth.weight_by_age_months
            == get_preset("osmanabadi").growth.weight_by_age_months
        )


@pytest.mark.parametrize("samples", [2, 3])
async def test_real_latest_adult_weights_require_three_distinct_animals_per_sex(
    client: httpx.AsyncClient,
    samples: int,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        reference = today(farm.timezone)
        for sex, weight in [("F", 70.0), ("M", 80.0)]:
            for index in range(samples):
                dob = add_months(reference, -24) if sex == "F" else None
                animal = Animal(
                    farm_id=farm_id,
                    tag_number=f"ADULT-{sex}-{index}",
                    sex=sex,
                    source="PURCHASED",
                    current_bucket="FOUNDATION",
                    date_of_birth=dob,
                    purchase_date=reference - timedelta(days=50),
                    status="ACTIVE",
                )
                db.add(animal)
                await db.flush()
                db.add(
                    WeightRecord(
                        farm_id=farm_id,
                        animal_id=animal.id,
                        date=reference - timedelta(days=1),
                        weight_kg=10.0,
                    )
                )
                db.add(
                    WeightRecord(
                        farm_id=farm_id, animal_id=animal.id, date=reference, weight_kg=weight
                    )
                )
        await db.commit()
    body = json_object(await _calibration(client, owner))
    assumptions = SimulationAssumptions.model_validate(body["assumptions"])
    evidence = {row["path"]: row for row in body["evidence"]}
    for field, weight in [("adult_weight_doe_kg", 70.0), ("adult_weight_buck_kg", 80.0)]:
        path = "growth." + field
        assert (path in evidence) is (samples == 3)
        if samples == 3:
            assert getattr(assumptions.growth, field) == weight
            assert evidence[path]["sample_size"] == 3


@pytest.mark.parametrize("gestation", [90, 220])
async def test_five_complete_native_kiddings_retain_both_plausible_gestation_endpoints(
    client: httpx.AsyncClient,
    gestation: int,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        reference = today(farm.timezone)
        birth = reference
        buck = Animal(
            farm_id=farm_id,
            tag_number="SIRE",
            sex="M",
            source="BORN",
            current_bucket="BREEDING",
            date_of_birth=reference - timedelta(days=1000),
            status="ACTIVE",
        )
        db.add(buck)
        await db.flush()
        for index in range(5):
            dam = Animal(
                farm_id=farm_id,
                tag_number=f"DAM-{index}",
                sex="F",
                source="BORN",
                current_bucket="FOUNDATION",
                date_of_birth=reference - timedelta(days=1000),
                status="ACTIVE",
            )
            db.add(dam)
            await db.flush()
            bred_on = birth - timedelta(days=gestation)
            breeding = BreedingRecord(
                farm_id=farm_id,
                doe_id=dam.id,
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
                farm_id=farm_id, doe_id=dam.id, date=birth, breeding_record_id=breeding.id
            )
            db.add(kidding)
            await db.flush()
            db.add(
                KidEntry(
                    farm_id=farm_id,
                    kidding_record_id=kidding.id,
                    sex="F",
                    status="ALIVE",
                    birth_weight=3.0,
                )
            )
        await db.commit()
    body = json_object(await _calibration(client, owner))
    assumptions = SimulationAssumptions.model_validate(body["assumptions"])
    evidence = {row["path"]: row for row in body["evidence"]}
    assert {"reproduction.gestation_months", "growth.birth_weight_kg"}.issubset(evidence)
    assert assumptions.reproduction.gestation_months == (3 if gestation == 90 else 7)
    assert evidence["reproduction.gestation_months"]["sample_size"] == 5
    assert assumptions.growth.birth_weight_kg == 3.0
    assert evidence["growth.birth_weight_kg"]["sample_size"] == 5


@pytest.mark.parametrize("samples", [2, 3])
async def test_native_foundation_purchases_and_cull_dispositions_retain_sex_specific_prices(
    client: httpx.AsyncClient,
    samples: int,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        reference = today(farm.timezone)
        for sex, price, unit in [("F", 20000, 100), ("M", 30000, 200)]:
            for index in range(samples):
                db.add(
                    Animal(
                        farm_id=farm_id,
                        tag_number=f"FOUNDATION-{sex}-{index}",
                        sex=sex,
                        source="PURCHASED",
                        current_bucket="FOUNDATION",
                        date_of_birth=None,
                        purchase_date=reference,
                        purchase_price=price,
                        status="ACTIVE",
                    )
                )
                db.add(
                    Animal(
                        farm_id=farm_id,
                        tag_number=f"CULL-{sex}-{index}",
                        sex=sex,
                        source="PURCHASED",
                        current_bucket="FOUNDATION",
                        date_of_birth=reference - timedelta(days=1000),
                        purchase_date=reference - timedelta(days=500),
                        status="CULLED",
                        status_date=reference,
                        sale_price=unit * 30,
                        sale_weight_kg=30.0,
                    )
                )
            for index in range(3):
                db.add(
                    Animal(
                        farm_id=farm_id,
                        tag_number=f"YOUNG-{sex}-{index}",
                        sex=sex,
                        source="PURCHASED",
                        current_bucket="FOUNDATION",
                        date_of_birth=reference - timedelta(days=31),
                        purchase_date=reference,
                        purchase_price=1,
                        status="ACTIVE",
                    )
                )
        for index in range(5):
            db.add(
                Animal(
                    farm_id=farm_id,
                    tag_number=f"SALE-{index}",
                    sex="M",
                    source="PURCHASED",
                    current_bucket="FOUNDATION",
                    date_of_birth=reference - timedelta(days=300),
                    purchase_date=reference - timedelta(days=200),
                    status="SOLD",
                    status_date=reference,
                    sale_price=9000,
                    sale_weight_kg=30.0,
                )
            )
        await db.commit()
    body = json_object(await _calibration(client, owner))
    assumptions = SimulationAssumptions.model_validate(body["assumptions"])
    evidence = {row["path"]: row for row in body["evidence"]}
    for group, field, value in [
        ("herd", "doe_purchase_price", 20000),
        ("herd", "buck_purchase_price", 30000),
        ("sales", "cull_doe_price_per_kg", 100),
        ("sales", "cull_buck_price_per_kg", 200),
    ]:
        path = group + "." + field
        assert (path in evidence) is (samples == 3)
        if samples == 3:
            assert getattr(getattr(assumptions, group), field) == value
            assert evidence[path]["sample_size"] == 3
    assert "sales.meat_price_per_kg" in evidence
    assert assumptions.sales.meat_price_per_kg == 300.0
    assert evidence["sales.meat_price_per_kg"]["sample_size"] == 5


@pytest.mark.parametrize("samples,months", [(11, 4), (12, 3), (12, 4)])
async def test_actual_seasonal_sale_evidence_retains_observation_bands_and_annual_mean(
    client: httpx.AsyncClient,
    samples: int,
    months: int,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    table = [(2, 200), (6, 300), (7, 500), (8, 600)][:months]
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        reference = today(farm.timezone)
        for index in range(samples):
            month, unit = table[index % months]
            sold_on = date(reference.year, month, 1)
            assert sold_on <= reference
            db.add(
                Animal(
                    farm_id=farm_id,
                    tag_number=f"SEASONAL-{index}",
                    sex="M",
                    source="PURCHASED",
                    current_bucket="FOUNDATION",
                    date_of_birth=sold_on - timedelta(days=300),
                    purchase_date=sold_on - timedelta(days=200),
                    status="SOLD",
                    status_date=sold_on,
                    sale_price=unit * 30,
                    sale_weight_kg=30.0,
                )
            )
        await db.commit()
    body = json_object(await _calibration(client, owner))
    assumptions = SimulationAssumptions.model_validate(body["assumptions"])
    evidence = {row["path"]: row for row in body["evidence"]}
    assert "sales.meat_price_per_kg" in evidence
    assert evidence["sales.meat_price_per_kg"]["sample_size"] == samples
    admitted = samples == 12 and months == 4
    assert ("sales.monthly_meat_price_multipliers" in evidence) is admitted
    if admitted:
        assert evidence["sales.monthly_meat_price_multipliers"]["sample_size"] == 12
        assert assumptions.sales.meat_price_per_kg == pytest.approx(400)
        curve = assumptions.sales.monthly_meat_price_multipliers
        assert len(curve) == 12 and sum(curve) == pytest.approx(12)
        assert [curve[month - 1] for month, _ in table] == pytest.approx([0.5, 0.75, 1.25, 1.5])


async def test_native_death_on_the_reference_date_is_inside_the_complete_owned_exposure(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        reference = today(farm.timezone)
        for index in range(10):
            db.add(
                Animal(
                    farm_id=farm_id,
                    tag_number=f"EXPOSURE-END-{index}",
                    sex="M",
                    source="PURCHASED",
                    current_bucket="FOUNDATION",
                    date_of_birth=None,
                    purchase_date=reference - timedelta(days=42),
                    status="DEAD" if index == 0 else "ACTIVE",
                    status_date=reference if index == 0 else None,
                )
            )
        await db.commit()
    body = json_object(await _calibration(client, owner))
    assumptions = SimulationAssumptions.model_validate(body["assumptions"])
    evidence = {row["path"]: row for row in body["evidence"]}
    assert "mortality.adult" in evidence
    assert evidence["mortality.adult"]["sample_size"] == 10
    assert assumptions.mortality.adult == pytest.approx(1 - exp(-365.28 / 420))


async def test_real_first_expense_and_current_head_preserve_recurring_cost_evidence(
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
                tag_number="ONLY-DOE",
                sex="F",
                source="PURCHASED",
                current_bucket="FOUNDATION",
                date_of_birth=reference - timedelta(days=1000),
                purchase_date=reference - timedelta(days=100),
                status="ACTIVE",
            )
        )
        for category, amount in [("LABOUR", 1000), ("VET", 100), ("MEDICINE", 200), ("OTHER", 300)]:
            db.add(
                Transaction(
                    farm_id=farm_id,
                    date=reference,
                    type="EXPENSE",
                    category=category,
                    amount=Decimal(amount),
                )
            )
        db.add(
            Transaction(
                farm_id=farm_id,
                date=reference - timedelta(days=31),
                type="INCOME",
                category="OTHER",
                amount=Decimal(999999),
            )
        )
        await db.commit()
    body = json_object(await _calibration(client, owner))
    assumptions = SimulationAssumptions.model_validate(body["assumptions"])
    evidence = {row["path"]: row for row in body["evidence"]}
    assert {
        "costs.labour_per_month",
        "costs.vet_per_animal_per_year",
        "costs.misc_overhead_per_month",
    }.issubset(evidence)
    assert assumptions.costs.labour_per_month == 2000
    assert assumptions.costs.vet_per_animal_per_year == 3600
    assert assumptions.costs.misc_overhead_per_month == 300
    assert evidence["costs.vet_per_animal_per_year"]["sample_size"] == 2
    assert any("1 month(s) of ledger history" in row["method"] for row in evidence.values())


async def test_actual_noisy_weights_pool_all_four_observed_ages_before_extending_the_curve(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        reference = today(farm.timezone)
        for index, (age, weight) in enumerate([(0, 5.0), (3, 6.0), (3, 6.0), (6, 2.0), (12, 1.0)]):
            dob = add_months(reference, -age)
            animal = Animal(
                farm_id=farm_id,
                tag_number=f"NOISY-{index}",
                sex="F",
                source="PURCHASED",
                current_bucket="FOUNDATION",
                date_of_birth=dob,
                purchase_date=dob,
                status="ACTIVE",
            )
            db.add(animal)
            await db.flush()
            db.add(
                WeightRecord(farm_id=farm_id, animal_id=animal.id, date=reference, weight_kg=weight)
            )
        await db.commit()
    body = json_object(await _calibration(client, owner))
    assumptions = SimulationAssumptions.model_validate(body["assumptions"])
    evidence = {row["path"]: row for row in body["evidence"]}
    assert "growth.weight_by_age_months" in evidence
    assert evidence["growth.weight_by_age_months"]["sample_size"] == 5
    # These four distinct age medians require a single pooled block: their
    # equal-weight least-squares nondecreasing fit is (5+6+2+1)/4 =3.5.
    assert assumptions.growth.weight_by_age_months == pytest.approx([3.5] * 13)
    assert assumptions.growth.birth_weight_kg == pytest.approx(3.5)
