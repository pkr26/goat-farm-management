"""Real farm calendars, small gains and absent ledgers keep owner totals meaningful."""

from datetime import date, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import Date, cast, func, select

from app.db import get_sessionmaker
from app.models import (
    BreedingRecord,
    Farm,
    KiddingRecord,
    KidEntry,
    Transaction,
    WeightRecord,
)

from .conftest import create_farm, owner_with_farm
from .test_finance_extended import make_animal


async def test_owned_birth_and_service_windows_use_their_own_real_calendar(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="mutation-owner-calendar@farm.in")
    later_a = await create_farm(client, owner, name="Later calendar A")
    later_b = await create_farm(client, owner, name="Later calendar B")
    farm_ids = [int(headers["X-Farm-Id"]) for headers in (owner, later_a, later_b)]
    does = [await make_animal(client, owner, tag=f"CALENDAR-DOE-{index}") for index in range(2)]
    async with get_sessionmaker()() as db:
        for index, farm_id in enumerate(farm_ids):
            farm = await db.get(Farm, farm_id)
            assert farm is not None
            farm.timezone = "Etc/GMT+12" if index == 0 else "Pacific/Kiritimati"
        await db.flush()
        calendar_rows = (
            await db.execute(
                select(Farm.id, cast(func.timezone(Farm.timezone, func.now()), Date)).where(
                    Farm.id.in_(farm_ids)
                )
            )
        ).all()
        calendars: dict[int, date] = {}
        for farm_id, day in calendar_rows:
            calendars[farm_id] = day
        actual_day = calendars[farm_ids[0]]
        assert isinstance(actual_day, date)
        assert all(calendars[farm_id] > actual_day for farm_id in farm_ids[1:]), (
            "The actual configured farm calendars straddle the international date line"
        )
        edge = actual_day - timedelta(days=90)
        for index, doe in enumerate(does):
            result_day = edge if index == 0 else actual_day - timedelta(days=5)
            service_day = result_day - timedelta(days=32)
            db.add(
                BreedingRecord(
                    farm_id=farm_ids[0],
                    doe_id=doe["id"],
                    method="AI",
                    breeding_date=service_day,
                    ultrasound_result_date=result_day,
                    ultrasound_done=True,
                    pregnant=index == 0,
                    outcome="CONFIRMED_PREGNANT" if index == 0 else "FAILED",
                    expected_kidding_date=service_day + timedelta(days=150) if index == 0 else None,
                )
            )
            litter = KiddingRecord(
                farm_id=farm_ids[0], doe_id=doe["id"], date=result_day, ease="NORMAL"
            )
            db.add(litter)
            await db.flush()
            for kid in range(3 if index == 0 else 1):
                db.add(
                    KidEntry(
                        farm_id=farm_ids[0],
                        kidding_record_id=litter.id,
                        sex="M",
                        status="STILLBORN" if index == kid == 0 else "ALIVE",
                        birth_weight=2.5,
                    )
                )
        await db.commit()
    response = await client.get("/api/owner/benchmarks", params={"days": 90}, headers=owner)
    assert response.status_code == 200, response.text
    farms = {row["farm_id"]: row for row in response.json()["farms"]}
    assert set(farms) == set(farm_ids)
    assert farms[farm_ids[0]]["conception_rate"] == 50.0, (
        "Both services lie in their farm's window, and one conceived"
    )
    assert farms[farm_ids[0]]["kid_mortality_rate"] == 25.0, (
        "Both retained litters lie in their farm's window: one of four kids was stillborn"
    )


async def test_one_day_half_kilogram_gain_keeps_zero_and_absent_feed_ledgers(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="mutation-owner-small-gain@farm.in")
    no_feed = await create_farm(client, owner, name="No feed expense ranch")
    farm_ids = [int(headers["X-Farm-Id"]) for headers in (owner, no_feed)]
    animals = [
        await make_animal(client, headers, tag=f"SMALL-GAIN-{index}")
        for index, headers in enumerate((owner, no_feed))
    ]
    async with get_sessionmaker()() as db:
        calendar_rows = (
            await db.execute(
                select(Farm.id, cast(func.timezone(Farm.timezone, func.now()), Date)).where(
                    Farm.id.in_(farm_ids)
                )
            )
        ).all()
        calendars: dict[int, date] = {}
        for farm_id, day in calendar_rows:
            calendars[farm_id] = day
        for farm_id, animal in zip(farm_ids, animals, strict=True):
            for offset, mass in ((1, 10.0), (0, 10.5)):
                db.add(
                    WeightRecord(
                        farm_id=farm_id,
                        animal_id=animal["id"],
                        date=calendars[farm_id] - timedelta(days=offset),
                        weight_kg=mass,
                    )
                )
        db.add(
            Transaction(
                farm_id=farm_ids[0],
                date=calendars[farm_ids[0]],
                type="EXPENSE",
                category="FEED",
                amount=Decimal("0.00"),
            )
        )
        await db.commit()
    response = await client.get("/api/owner/benchmarks", params={"days": 90}, headers=owner)
    assert response.status_code == 200, response.text
    farms = {row["farm_id"]: row for row in response.json()["farms"]}
    for farm_id in farm_ids:
        assert farms[farm_id]["avg_daily_gain_kg"] == 0.5, "A real one-day interval gained 0.5 kg"
        assert farms[farm_id]["feed_cost_per_kg_gain"] == 0.0, (
            "Zero actual expense and no expense both reconcile to zero currency per kg gained"
        )


async def test_owner_overview_no_transactions_has_zero_real_net(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="mutation-owner-no-ledger@farm.in")
    response = await client.get("/api/owner/overview", headers=owner)
    assert response.status_code == 200, response.text
    row = response.json()["farms"][0]
    assert row["farm_id"] == int(owner["X-Farm-Id"])
    assert Decimal(row["month_income"]) == Decimal("0.00")
    assert Decimal(row["month_expense"]) == Decimal("0.00")
    assert Decimal(row["month_net"]) == Decimal("0.00"), (
        "An actual empty farm has no invented current-month income or net earnings"
    )
