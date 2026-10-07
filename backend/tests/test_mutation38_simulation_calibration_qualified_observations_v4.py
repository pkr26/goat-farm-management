"""Actual native farm observations retain class, evidence and exposure boundaries."""

from collections import Counter
from datetime import timedelta
from decimal import Decimal
from math import exp

import httpx
import pytest

from app.db import get_sessionmaker
from app.models import (
    Animal,
    BreedingRecord,
    Farm,
    FeedInventory,
    KiddingRecord,
    KidEntry,
    Transaction,
)
from app.simulation.assumptions import SimulationAssumptions
from app.simulation.defaults import get_preset
from app.simulation.feed import DAYS_PER_MONTH
from app.utils import add_months, today

from .conftest import owner_with_farm
from .type_helpers import json_object


async def test_current_native_herd_cohorts_use_calendar_birthdays_and_estimated_dob(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    expected: Counter[str] = Counter()
    threshold = get_preset("osmanabadi").reproduction.age_at_first_breeding_months
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        reference = today(farm.timezone)
        for sex in ("F", "M"):
            for calendar_age in range(14):
                for upcoming in range(2 if calendar_age else 1):
                    dob = add_months(reference, -calendar_age) + timedelta(days=upcoming)
                    age = calendar_age - upcoming
                    field = (
                        ("does" if sex == "F" else "bucks")
                        if age >= (threshold if sex == "F" else 12)
                        else (
                            ("female_" if sex == "F" else "male_")
                            + ("kids" if age < 3 else "weaners" if age < 6 else "growers")
                        )
                    )
                    expected[field] += 1
                    db.add(
                        Animal(
                            farm_id=farm_id,
                            tag_number=f"MONTH-{sex}-{calendar_age}-{upcoming}",
                            sex=sex,
                            source="PURCHASED",
                            current_bucket="FOUNDATION",
                            date_of_birth=dob,
                            purchase_date=max(dob, reference - timedelta(days=60)),
                            status="ACTIVE",
                        )
                    )
            for calendar_age in (2, 5):
                expected[
                    ("female_" if sex == "F" else "male_")
                    + ("kids" if calendar_age < 3 else "weaners")
                ] += 1
                db.add(
                    Animal(
                        farm_id=farm_id,
                        tag_number=f"ESTIMATED-{sex}-{calendar_age}",
                        sex=sex,
                        source="PURCHASED",
                        current_bucket="FOUNDATION",
                        estimated_dob=add_months(reference, -calendar_age),
                        purchase_date=reference - timedelta(days=1),
                        status="ACTIVE",
                    )
                )
        await db.commit()
    response = await client.get("/api/simulation/calibration", headers=owner)
    assert response.status_code == 200, response.text
    body = json_object(response.json())
    assumptions = SimulationAssumptions.model_validate(body["assumptions"])
    evidence = {row["path"]: row for row in body["evidence"]}
    assert {"herd." + field for field in expected}.issubset(evidence)
    for field, count in expected.items():
        assert getattr(assumptions.herd, field) == count
        assert evidence["herd." + field]["calibrated_value"] == count
    assert sum(getattr(assumptions.herd, field) for field in expected) == sum(expected.values())


@pytest.mark.parametrize(
    "sample_size,confidence", [(9, "low"), (10, "medium"), (29, "medium"), (30, "high")]
)
async def test_native_structured_feed_evidence_confidence_follows_supported_observation_bands(
    client: httpx.AsyncClient, sample_size: int, confidence: str
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        reference = today(farm.timezone)
        inventory = FeedInventory(
            farm_id=farm_id,
            ingredient="Observed dry fodder",
            category="ROUGHAGE_DRY",
            unit="kg",
            qty_on_hand=float(sample_size),
        )
        db.add(inventory)
        await db.flush()
        for index in range(sample_size):
            db.add(
                Transaction(
                    farm_id=farm_id,
                    date=reference - timedelta(days=index),
                    type="EXPENSE",
                    category="FEED",
                    amount=Decimal("2"),
                    feed_inventory_id=inventory.id,
                    feed_quantity_kg=Decimal("1"),
                    feed_unit_price_per_kg=Decimal("2"),
                )
            )
        await db.commit()
    response = await client.get("/api/simulation/calibration", headers=owner)
    assert response.status_code == 200, response.text
    body = json_object(response.json())
    evidence = {row["path"]: row for row in body["evidence"]}
    assert "feed.dry_price_per_kg" in evidence
    observed = evidence["feed.dry_price_per_kg"]
    assert observed["calibrated_value"] == pytest.approx(2.0)
    assert observed["sample_size"] == sample_size
    assert observed["confidence"] == confidence


async def test_five_actual_kiddings_calibrate_ten_kids_with_live_birth_and_phase_evidence(
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
                        else 5.0 + ordinal / 10
                        if ordinal < 5
                        else None,
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
    assert assumptions.growth.birth_weight_kg == pytest.approx(5.2)
    assert assumptions.growth.weight_by_age_months[0] == pytest.approx(5.2)
    assert all(weight >= 5.2 for weight in assumptions.growth.weight_by_age_months)
    assert evidence["growth.birth_weight_kg"]["sample_size"] == 5
    assert evidence["growth.weight_by_age_months"]["sample_size"] == 5
    assert evidence["mortality.kid_pre_weaning"]["sample_size"] == 8
    assert evidence["reproduction.stillbirth_rate"]["sample_size"] == 10


@pytest.mark.parametrize("mixed", [False, True])
async def test_actual_dated_deaths_use_complete_owned_exposure_in_each_native_age_class(
    client: httpx.AsyncClient,
    mixed: bool,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        reference = today(farm.timezone)
        cohorts: list[tuple[str, int | None]] = [("weaner", 160)]
        if mixed:
            cohorts.extend([("grower", 300), ("adult", 800), ("unknown", None)])
        for label, age_days in cohorts:
            for index in range(10):
                dob = reference - timedelta(days=age_days) if age_days is not None else None
                acquired = dob if dob is not None else reference - timedelta(days=40)
                left = reference - timedelta(days=10) if index == 0 else reference
                animal = Animal(
                    farm_id=farm_id,
                    tag_number=f"EXPOSURE-{label}-{index}",
                    sex="M",
                    source="PURCHASED",
                    current_bucket="FOUNDATION",
                    date_of_birth=dob,
                    purchase_date=acquired,
                    status="DEAD" if index == 0 else "ACTIVE",
                    status_date=left if index == 0 else None,
                )
                db.add(animal)
        await db.commit()
    response = await client.get("/api/simulation/calibration", headers=owner)
    assert response.status_code == 200, response.text
    body = json_object(response.json())
    assumptions = SimulationAssumptions.model_validate(body["assumptions"])
    evidence = {row["path"]: row for row in body["evidence"]}
    # These intervals are facts of the four concrete dated cohorts above,
    # rather than another implementation of the query's per-animal algorithm.
    # All earlier class intervals fit inside the actual 24-month lookback.
    young_days = 10 * (160 - round(3 * DAYS_PER_MONTH)) - 10
    table: dict[str, tuple[int, int, int, float]] = {
        "kid_post_weaning": (young_days, 1, 10, 3.0),
    }
    if mixed:
        table["kid_post_weaning"] = (
            young_days + 20 * (round(6 * DAYS_PER_MONTH) - round(3 * DAYS_PER_MONTH)),
            1,
            30,
            3.0,
        )
        table["grower"] = (
            10 * (300 - round(6 * DAYS_PER_MONTH))
            - 10
            + 10 * (round(12 * DAYS_PER_MONTH) - round(6 * DAYS_PER_MONTH)),
            1,
            20,
            12.0,
        )
        table["adult"] = (
            10 * (800 - round(12 * DAYS_PER_MONTH)) - 10 + 10 * 40 - 10,
            2,
            20,
            12.0,
        )
    assert {"mortality." + field for field in table}.issubset(evidence)
    for field, (exposure_days, deaths, observed, period) in table.items():
        expected = 1.0 - exp(-deaths * period * DAYS_PER_MONTH / exposure_days)
        assert getattr(assumptions.mortality, field) == pytest.approx(expected)
        assert evidence["mortality." + field]["sample_size"] == observed
        assert evidence["mortality." + field]["calibrated_value"] == pytest.approx(expected)
