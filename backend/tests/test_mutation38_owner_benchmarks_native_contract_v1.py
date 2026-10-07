"""Native retained breeding, birth, weight and ledger facts preserve the owner report."""

import json
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import Date, cast, func, select

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
from app.schemas.owner import OwnerFarmBenchmarksOut, OwnerFarmOverviewOut
from app.utils import utcnow

from .conftest import create_farm, owner_with_farm
from .test_finance_extended import make_animal


async def test_real_owner_benchmarks_keep_window_edges_and_report_physical_rates(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="mutation-owner-benchmark@farm.in")
    zero = await create_farm(client, owner, name="Zero conception ranch")
    empty = await create_farm(client, owner, name="Empty benchmark ranch")
    ids = [int(headers["X-Farm-Id"]) for headers in (owner, zero, empty)]
    growing = await make_animal(client, owner, tag="BENCH-A-GAIN")
    assessed = [await make_animal(client, owner, tag=f"BENCH-A-DOE-{index}") for index in range(7)]
    failed = [await make_animal(client, zero, tag=f"BENCH-B-DOE-{index}") for index in range(2)]
    sold = [await make_animal(client, owner, tag=f"BENCH-A-SALE-{index}") for index in range(4)]
    async with get_sessionmaker()() as db:
        actual_day = (
            await db.execute(
                select(cast(func.timezone(Farm.timezone, func.now()), Date)).where(
                    Farm.id == ids[0]
                )
            )
        ).scalar_one()
        assert isinstance(actual_day, date)
        first_day = actual_day - timedelta(days=90)
        for index, row in enumerate(assessed):
            confirmed = index < 3
            result_day = first_day if index in (0, 3) else actual_day - timedelta(days=5)
            service_day = result_day - timedelta(days=32)
            db.add(
                BreedingRecord(
                    farm_id=ids[0],
                    doe_id=row["id"],
                    method="AI",
                    breeding_date=service_day,
                    ultrasound_date=result_day,
                    ultrasound_result_date=result_day,
                    ultrasound_done=True,
                    pregnant=confirmed,
                    outcome="CONFIRMED_PREGNANT" if confirmed else "FAILED",
                    expected_kidding_date=service_day + timedelta(days=150) if confirmed else None,
                )
            )
        for row in failed:
            db.add(
                BreedingRecord(
                    farm_id=ids[1],
                    doe_id=row["id"],
                    method="AI",
                    breeding_date=actual_day - timedelta(days=40),
                    ultrasound_result_date=actual_day - timedelta(days=8),
                    ultrasound_done=True,
                    pregnant=False,
                    outcome="FAILED",
                )
            )
        litter = KiddingRecord(
            farm_id=ids[0], doe_id=assessed[0]["id"], date=first_day, ease="NORMAL"
        )
        db.add(litter)
        await db.flush()
        for index in range(3):
            db.add(
                KidEntry(
                    farm_id=ids[0],
                    kidding_record_id=litter.id,
                    sex="M",
                    status="STILLBORN" if index == 0 else "ALIVE",
                    birth_weight=2.5,
                )
            )
        for delta, mass in ((0, 15.0), (3, 40.0), (7, 18.0)):
            db.add(
                WeightRecord(
                    farm_id=ids[0],
                    animal_id=growing["id"],
                    date=first_day + timedelta(days=delta),
                    weight_kg=mass,
                )
            )
        for index, (sale, purchase) in enumerate(
            (("1000.01", None), ("333.34", "300.00"), ("900.55", "0.00"), ("77777.77", None))
        ):
            animal = await db.get(Animal, sold[index]["id"])
            assert animal is not None
            animal.status = "SOLD"
            animal.status_date = first_day - timedelta(days=1) if index == 3 else first_day
            animal.sale_price = Decimal(sale)
            animal.purchase_price = Decimal(purchase) if purchase is not None else None
        for kind, amount, day, voided in (
            ("EXPENSE", "1.11", first_day, False),
            ("EXPENSE", "0.89", actual_day, False),
            ("EXPENSE", "222.22", first_day - timedelta(days=1), False),
            ("EXPENSE", "333.33", actual_day, True),
            ("INCOME", "444.44", actual_day, False),
        ):
            db.add(
                Transaction(
                    farm_id=ids[0],
                    date=day,
                    type=kind,
                    category="FEED",
                    amount=Decimal(amount),
                    voided_at=utcnow() if voided else None,
                    void_reason="Retained ledger correction" if voided else None,
                )
            )
        await db.commit()
    response = await client.get("/api/owner/benchmarks", params={"days": 90}, headers=owner)
    assert response.status_code == 200, response.text
    farms = {row["farm_id"]: row for row in response.json()["farms"]}
    assert set(farms) == set(ids)
    row = farms[ids[0]]
    assert row["conception_rate"] == 42.9, "Three of seven assessed services conceived"
    assert row["kid_mortality_rate"] == 33.3, "One of three retained birth entries is stillborn"
    assert row["avg_daily_gain_kg"] == 0.429, "The measured endpoints gained 3 kg in seven days"
    assert row["feed_cost_per_kg_gain"] == 0.67, "The active feed ledger spent INR2 for 3 kg gained"
    assert row["animals_sold"] == 3
    assert row["profit_per_animal_sold"] == 644.63, (
        "The three actual retained sales reconcile to INR1933.90 realized margin"
    )
    assert farms[ids[1]]["conception_rate"] == 0.0, (
        "Two genuinely failed services have zero success"
    )
    assert farms[ids[1]]["kid_mortality_rate"] is None
    assert farms[ids[2]]["conception_rate"] is None
    assert farms[ids[2]]["animals_sold"] == 0
    assert farms[ids[2]]["profit_per_animal_sold"] is None


async def test_omitted_owner_window_keeps_the_published_ninety_day_response(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="mutation-owner-default-window@farm.in")
    response = await client.get("/api/owner/benchmarks", headers=owner)
    assert response.status_code == 200, response.text
    assert response.json()["days"] == 90


@pytest.mark.parametrize("view", ["overview", "benchmarks"])
async def test_public_owner_farm_dto_rejects_a_nonpositive_identity(
    client: httpx.AsyncClient, view: str
) -> None:
    owner = await owner_with_farm(client, email="mutation-owner-dto@farm.in")
    response = await client.get("/api/owner/" + view, headers=owner)
    assert response.status_code == 200, response.text
    document = response.json()["farms"][0]
    model = OwnerFarmOverviewOut if view == "overview" else OwnerFarmBenchmarksOut
    observed = model.model_validate_json(json.dumps(document))
    assert observed.farm_id == int(owner["X-Farm-Id"])
    document["farm_id"] = 0
    refused = False
    try:
        model.model_validate_json(json.dumps(document))
    except ValidationError:
        refused = True
    assert refused, "The public farm result DTO requires a positive identifier"
