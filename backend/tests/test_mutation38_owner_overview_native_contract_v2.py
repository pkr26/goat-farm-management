"""Actual retained farm facts roll up without crossing farms or ledger windows."""

from datetime import date, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import Date, cast, func, select

from app.db import get_sessionmaker
from app.models import (
    Animal,
    Farm,
    ScreeningFinding,
    ScreeningImage,
    ScreeningRun,
    Task,
    Transaction,
)
from app.utils import utcnow

from .conftest import create_farm, owner_with_farm
from .test_finance_extended import make_animal


async def test_real_distinct_owner_rollups_reconcile_all_three_owned_farms(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="mutation-owner-rollup@farm.in")
    second = await create_farm(client, owner, name="Distinct ledger ranch")
    empty = await create_farm(client, owner, name="Empty retained ranch")
    outsiders = await owner_with_farm(client, email="mutation-owner-rollup-other@farm.in")
    ids = [int(headers["X-Farm-Id"]) for headers in (owner, second, empty)]
    animals = [await make_animal(client, owner, tag=f"ROLLUP-A-{index}") for index in range(4)]
    await make_animal(client, second, tag="ROLLUP-B-0")
    await make_animal(client, outsiders, tag="ROLLUP-OTHER-0")
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, ids[0])
        assert farm is not None
        actual_day = (
            await db.execute(
                select(cast(func.timezone(Farm.timezone, func.now()), Date)).where(
                    Farm.id == ids[0]
                )
            )
        ).scalar_one()
        assert isinstance(actual_day, date)
        first_day = actual_day.replace(day=1)
        for index, bucket in ((0, "PREGNANCY_LATE"), (1, "DELIVERY")):
            retained = await db.get(Animal, animals[index]["id"])
            assert retained is not None
            retained.current_bucket = bucket
        held = await db.get(Animal, animals[2]["id"])
        assert held is not None
        held.movement_restricted = True
        held.restriction_reason = "Retained veterinary movement hold"
        for delta, status, count in ((-1, "PENDING", 3), (0, "PENDING", 2), (0, "DONE", 1)):
            for index in range(count):
                db.add(
                    Task(
                        farm_id=ids[0],
                        title=f"Retained duty {delta}/{status}/{index}",
                        due_date=actual_day + timedelta(days=delta),
                        category="OTHER",
                        status=status,
                        completed_at=utcnow() if status == "DONE" else None,
                    )
                )
        db.add(
            Task(
                farm_id=ids[0],
                title="Future pending duty is not today",
                due_date=actual_day + timedelta(days=1),
                category="OTHER",
                status="PENDING",
            )
        )
        for farm_id, kind, amount in (
            (ids[0], "INCOME", "7.12"),
            (ids[0], "INCOME", "4.11"),
            (ids[0], "EXPENSE", "1.12"),
            (ids[0], "EXPENSE", "1.22"),
            (ids[1], "INCOME", "33.33"),
            (ids[1], "EXPENSE", "4.44"),
            (ids[2], "INCOME", "0.00"),
        ):
            db.add(
                Transaction(
                    farm_id=farm_id,
                    date=first_day,
                    type=kind,
                    category="FEED",
                    amount=Decimal(amount),
                )
            )
        db.add(
            Transaction(
                farm_id=ids[0],
                date=first_day - timedelta(days=1),
                type="INCOME",
                category="ANIMAL_SALE",
                amount=Decimal("999.99"),
            )
        )
        db.add(
            Transaction(
                farm_id=ids[0],
                date=actual_day,
                type="EXPENSE",
                category="FEED",
                amount=Decimal("888.88"),
                voided_at=utcnow(),
                void_reason="Retained audited cancellation",
            )
        )
        # These are constraint-valid retained screening observations, not new provider invocations.
        # Two live pending observations, a reviewed one and a tombstoned one distinguish all joins.
        for index, (farm_id, status, tombstone) in enumerate(
            (
                (ids[0], "PENDING_REVIEW", False),
                (ids[0], "PENDING_REVIEW", False),
                (ids[0], "CONFIRMED", False),
                (ids[0], "PENDING_REVIEW", True),
                (ids[1], "PENDING_REVIEW", False),
                (int(outsiders["X-Farm-Id"]), "PENDING_REVIEW", False),
            )
        ):
            image = ScreeningImage(
                farm_id=farm_id,
                s3_bucket="retained-screening-archive",
                s3_key=f"raw/{farm_id}/2026-10-01/BREEDING/owner-{index}.jpg",
                status="FLAGGED",
                retention_tombstoned_at=utcnow() if tombstone else None,
            )
            db.add(image)
            await db.flush()
            run = ScreeningRun(
                farm_id=farm_id,
                image_id=image.id,
                stage="GATE",
                run_status="OK",
                verdict="flagged",
                provider="retained-observation",
                model="gate-observation",
                prompt_version="v1",
                latency_ms=7,
            )
            db.add(run)
            await db.flush()
            db.add(
                ScreeningFinding(
                    farm_id=farm_id,
                    run_id=run.id,
                    label=f"Retained visible finding {index}",
                    status=status,
                    reviewed_at=utcnow() if status == "CONFIRMED" else None,
                    reviewed_by_id=farm.owner_id if status == "CONFIRMED" else None,
                )
            )
        await db.commit()
    response = await client.get("/api/owner/overview", headers=owner)
    assert response.status_code == 200, response.text
    farms = {row["farm_id"]: row for row in response.json()["farms"]}
    assert set(farms) == set(ids)
    first = farms[ids[0]]
    assert (
        first["active_animals"],
        first["kidding_watch"],
        first["movement_restricted"],
        first["overdue_duties"],
        first["todays_duties_pending"],
        first["todays_duties_done"],
        first["open_screening_flags"],
    ) == (4, 2, 1, 3, 2, 1, 2)
    assert [Decimal(str(first[key])) for key in ("month_income", "month_expense", "month_net")] == [
        Decimal("11.23"),
        Decimal("2.34"),
        Decimal("8.89"),
    ]
    second_row = farms[ids[1]]
    assert second_row["active_animals"] == 1 and second_row["open_screening_flags"] == 1
    assert [
        Decimal(str(second_row[key])) for key in ("month_income", "month_expense", "month_net")
    ] == [Decimal("33.33"), Decimal("4.44"), Decimal("28.89")]
    empty_row = farms[ids[2]]
    assert all(
        empty_row[key] == 0
        for key in (
            "active_animals",
            "kidding_watch",
            "movement_restricted",
            "overdue_duties",
            "todays_duties_pending",
            "todays_duties_done",
            "open_screening_flags",
        )
    )
    assert all(
        Decimal(str(empty_row[key])) == Decimal("0")
        for key in ("month_income", "month_expense", "month_net")
    )
