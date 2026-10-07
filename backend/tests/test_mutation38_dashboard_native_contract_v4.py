"""Actual dated herd records preserve operational previews and physical reports."""

import calendar
import json
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.owner import _rate
from app.db import get_sessionmaker
from app.models import (
    Animal,
    BreedingRecord,
    BucketMove,
    Farm,
    HealthEvent,
    InsurancePolicy,
    KiddingRecord,
    KidEntry,
    Task,
    WeightRecord,
)
from app.schemas.owner import OwnerFarmBenchmarksOut, OwnerFarmOverviewOut
from app.schemas.summaries import (
    DashboardWeightOut,
    PurchaseQuarantineAnimalOut,
    QuarantineScheduleTaskOut,
)
from app.simulation.market import bakrid_occurrences

from .conftest import owner_with_farm
from .test_finance_extended import make_animal


async def farm_day(db: AsyncSession, farm_id: int) -> date:
    return (
        await db.execute(
            select(cast(func.timezone(Farm.timezone, func.now()), Date)).where(Farm.id == farm_id)
        )
    ).scalar_one()


def shifted_month(day: date, months: int) -> date:
    month_index = day.year * 12 + day.month - 1 + months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


async def test_recent_native_weights_keep_ten_observations_and_their_own_identities(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="mutation-dashboard-weights@farm.in")
    animal = await make_animal(client, owner, tag="DASH-WEIGHT")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        day = await farm_day(db, farm_id)
        weights = [
            WeightRecord(
                farm_id=farm_id,
                animal_id=animal["id"],
                date=day - timedelta(days=index),
                weight_kg=float(index + 1),
                notes="Retained scale observation",
            )
            for index in range(11)
        ]
        db.add_all(weights)
        await db.flush()
        expected_ids = [row.id for row in weights[:10]]
        await db.commit()
    response = await client.get("/api/dashboard", headers=owner)
    assert response.status_code == 200, response.text
    document = response.json()
    assert document["recent_weights_total"] == 11
    assert document["recent_weights_limit"] == 10
    assert [row["id"] for row in document["recent_weights"]] == expected_ids
    assert all(row["animal"]["id"] == animal["id"] for row in document["recent_weights"])


async def test_native_kidding_preview_balances_backlog_with_today_and_fourteen_day_edge(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="mutation-dashboard-kidding@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        day = await farm_day(db, farm_id)
        dates = [day - timedelta(days=index + 1) for index in range(60)]
        dates += [day + timedelta(days=index % 15) for index in range(60)]
        dates += [day + timedelta(days=15)]
        pairs = []
        for index, due in enumerate(dates):
            doe = Animal(
                farm_id=farm_id,
                tag_number=f"DASH-DUE-{index:03d}",
                sex="F",
                source="BORN",
                date_of_birth=day - timedelta(days=800),
                birth_type="SINGLE",
                birth_weight=2.5,
                current_bucket="PREGNANCY_LATE",
            )
            db.add(doe)
            await db.flush()
            service = due - timedelta(days=150)
            record = BreedingRecord(
                farm_id=farm_id,
                doe_id=doe.id,
                method="AI",
                breeding_date=service,
                ultrasound_done=True,
                pregnant=True,
                ultrasound_result_date=service + timedelta(days=32),
                outcome="CONFIRMED_PREGNANT",
                expected_kidding_date=due,
            )
            db.add(record)
            pairs.append((record, due))
        await db.flush()
        past = sorted(
            ((r.id, due) for r, due in pairs if due < day),
            key=lambda item: (item[1], item[0]),
            reverse=True,
        )[:50]
        future = sorted(
            ((r.id, due) for r, due in pairs if day <= due <= day + timedelta(days=14)),
            key=lambda item: (item[1], item[0]),
        )[:50]
        expected_ids = [identity for identity, _ in [*reversed(past), *future]]
        await db.commit()
    response = await client.get("/api/dashboard", headers=owner)
    assert response.status_code == 200, response.text
    document = response.json()
    assert document["kiddings_due_total"] == 120, (
        "Sixty actual overdue pregnancies and sixty due within fourteen days remain visible"
    )
    assert [row["id"] for row in document["kiddings_due"]] == expected_ids


async def test_native_pending_ultrasound_and_policy_horizons_keep_exact_edges(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="mutation-dashboard-horizons@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        day = await farm_day(db, farm_id)
        tasks = [
            Task(
                farm_id=farm_id,
                title=f"Retained scan {offset}",
                due_date=day + timedelta(days=offset),
                category="ULTRASOUND",
                status="PENDING",
            )
            for offset in (0, 7, 8)
        ]
        policies = [
            InsurancePolicy(
                farm_id=farm_id,
                policy_number=f"DASH-POL-{offset}",
                insurer="Retained native cover",
                sum_insured=Decimal("15000"),
                premium=Decimal("450"),
                start_date=day - timedelta(days=10),
                renewal_date=day + timedelta(days=offset),
                status="active",
            )
            for offset in (59, 60, 61)
        ]
        db.add_all([*tasks, *policies])
        await db.flush()
        expected_scans = [task.id for task in tasks[:2]]
        expected_policies = [policy.id for policy in policies[:2]]
        await db.commit()
    response = await client.get("/api/dashboard", headers=owner)
    assert response.status_code == 200, response.text
    document = response.json()
    assert document["todays_tasks_total"] == 1
    assert [row["id"] for row in document["todays_tasks"]] == expected_scans[:1]
    assert document["ultrasounds_due_total"] == 2
    assert [row["id"] for row in document["ultrasounds_due"]] == expected_scans
    assert document["insurance_expiring_total"] == 2
    assert [row["id"] for row in document["insurance_expiring"]] == expected_policies


async def test_native_empty_dashboard_and_a_retained_hold_preserve_zero_and_reason(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="mutation-dashboard-empty-hold@farm.in")
    empty = await client.get("/api/dashboard", headers=owner)
    assert empty.status_code == 200, empty.text
    assert empty.json()["insurance_expiring_total"] == 0
    assert empty.json()["restricted_animals_total"] == 0
    held = await make_animal(client, owner, tag="DASH-HELD")
    async with get_sessionmaker()() as db:
        animal = await db.get(Animal, held["id"])
        assert animal is not None
        animal.movement_restricted = True
        animal.restriction_reason = "Retained regulatory hold awaiting clearance"
        await db.commit()
    response = await client.get("/api/dashboard", headers=owner)
    assert response.status_code == 200, response.text
    document = response.json()
    assert document["total_active"] == 1
    assert document["sex_counts"] == {"M": 0, "F": 1}
    assert document["restricted_animals_total"] == 1
    assert document["restricted_animals"][0]["animal"]["id"] == held["id"]
    assert (
        document["restricted_animals"][0]["reason"] == "Retained regulatory hold awaiting clearance"
    )


@pytest.mark.parametrize("live_counts,expected", [([2, 2, 1], 1.67), ([0], 0.0)])
async def test_native_reports_reconcile_live_litter_average_and_one_kilogram_weight(
    client: httpx.AsyncClient,
    live_counts: list[int],
    expected: float,
) -> None:
    owner = await owner_with_farm(client, email="mutation-dashboard-report@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    animals = [await make_animal(client, owner, tag=f"DASH-REPORT-{index}") for index in range(2)]
    async with get_sessionmaker()() as db:
        day = await farm_day(db, farm_id)
        for animal, mass in zip(animals, (1.0, 3.0), strict=True):
            db.add(WeightRecord(farm_id=farm_id, animal_id=animal["id"], date=day, weight_kg=mass))
        for index, live in enumerate(live_counts):
            litter = KiddingRecord(
                farm_id=farm_id,
                doe_id=animals[0]["id"],
                date=day - timedelta(days=20 + index),
                ease="NORMAL",
            )
            db.add(litter)
            await db.flush()
            for _kid in range(max(live, 1)):
                db.add(
                    KidEntry(
                        farm_id=farm_id,
                        kidding_record_id=litter.id,
                        sex="M",
                        status="ALIVE" if live else "STILLBORN",
                        birth_weight=2.5,
                    )
                )
        await db.commit()
    response = await client.get("/api/dashboard/reports", headers=owner)
    assert response.status_code == 200, response.text
    document = response.json()
    foundation = next(row for row in document["bucket_rows"] if row["code"] == "FOUNDATION")
    assert foundation["count"] == 2
    assert foundation["avg_weight"] == 2.0, "The actual live masses are 1 kg and 3 kg"
    assert document["breeding"]["kiddings"] == len(live_counts)
    assert document["breeding"]["kids_per_kidding"] == expected


async def test_native_readiness_uses_dated_weight_rest_history_and_current_safety_facts(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="mutation-dashboard-readiness@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        day = await farm_day(db, farm_id)
        animals = []
        for tag, sex, bucket, age_months, mass in (
            ("NATIVE-REST", "F", "RESTING", 18, 22.0),
            ("NATIVE-EARLY", "F", "PREGNANCY_EARLY", 18, 22.0),
            ("NATIVE-LATE", "F", "PREGNANCY_LATE", 18, 22.0),
            ("NATIVE-MARKET", "M", "MALE_KIDS", 8, 24.0),
            ("NATIVE-WITHDRAWAL", "M", "MALE_KIDS", 8, 24.0),
            ("NATIVE-HELD", "M", "MALE_KIDS", 8, 24.0),
        ):
            animal = Animal(
                farm_id=farm_id,
                tag_number=tag,
                sex=sex,
                source="BORN",
                date_of_birth=shifted_month(day, -age_months),
                birth_type="SINGLE",
                birth_weight=2.5,
                current_bucket=bucket,
            )
            if tag == "NATIVE-HELD":
                animal.movement_restricted = True
                animal.restriction_reason = "Retained clinical hold"
            db.add(animal)
            await db.flush()
            for offset, observed in ((1, mass - 1), (0, mass)):
                db.add(
                    WeightRecord(
                        farm_id=farm_id,
                        animal_id=animal.id,
                        date=day - timedelta(days=offset),
                        weight_kg=observed,
                    )
                )
            db.add(
                WeightRecord(
                    farm_id=farm_id,
                    animal_id=animal.id,
                    date=day + timedelta(days=1),
                    weight_kg=99.0,
                )
            )
            animals.append(animal)
        db.add(
            BucketMove(
                farm_id=farm_id,
                animal_id=animals[0].id,
                from_bucket="RECOVERY",
                to_bucket="RESTING",
                effective_date=day - timedelta(days=45),
                reason="Retained postpartum lifecycle fact",
            )
        )
        for animal, elapsed in ((animals[1], 100), (animals[2], 135)):
            service = day - timedelta(days=elapsed)
            db.add(
                BreedingRecord(
                    farm_id=farm_id,
                    doe_id=animal.id,
                    method="AI",
                    breeding_date=service,
                    ultrasound_done=True,
                    pregnant=True,
                    ultrasound_result_date=service + timedelta(days=32),
                    outcome="CONFIRMED_PREGNANT",
                    expected_kidding_date=service + timedelta(days=150),
                )
            )
        db.add(
            HealthEvent(
                farm_id=farm_id,
                animal_id=animals[4].id,
                date=day,
                type="TREATMENT",
                withdrawal_until=day,
            )
        )
        await db.commit()
    response = await client.get("/api/dashboard", headers=owner)
    assert response.status_code == 200, response.text
    document = response.json()
    suggestions = {row["animal"]["tag_number"]: row for row in document["suggestions"]}
    assert set(suggestions) == {"NATIVE-REST", "NATIVE-EARLY", "NATIVE-LATE", "NATIVE-MARKET"}
    assert document["suggestions_total"] == 4
    assert suggestions["NATIVE-REST"]["reason"] == "45 days resting (flush done)"
    assert suggestions["NATIVE-EARLY"]["to"] == "PREGNANCY_LATE"
    assert "Gestation day 100" in suggestions["NATIVE-EARLY"]["reason"]
    assert suggestions["NATIVE-LATE"]["to"] == "DELIVERY"
    assert "Gestation day 135" in suggestions["NATIVE-LATE"]["reason"]
    assert suggestions["NATIVE-MARKET"]["to"] == "SELL"
    assert suggestions["NATIVE-MARKET"]["reason"] == (
        "8 mo, 24.0 kg — market ready (window 8–9 mo, 24–28 kg)"
    )


async def test_native_month_end_births_do_not_break_the_actual_calendar_advisory(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="mutation-dashboard-calendar@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        day = await farm_day(db, farm_id)
        festival = next(
            entry.observed_on for entry in bakrid_occurrences() if entry.observed_on > day
        )
        start = shifted_month(festival, -2)
        births = {
            shifted_month(edge, -9)
            for edge in (
                start - timedelta(days=1),
                start,
                festival,
                festival + timedelta(days=1),
                shifted_month(festival, -3),
            )
        }
        for index in range(9):
            month = shifted_month(day, -index)
            month_end = date(
                month.year, month.month, calendar.monthrange(month.year, month.month)[1]
            )
            if month_end <= day:
                births.add(month_end)
        admitted = sorted(birth for birth in births if birth <= day)
        expected_count = sum(
            birth > shifted_month(day, -9) and start <= shifted_month(birth, 9) <= festival
            for birth in admitted
        )
        for index, birth in enumerate(admitted):
            db.add(
                Animal(
                    farm_id=farm_id,
                    tag_number=f"NATIVE-CALENDAR-{index}",
                    sex="M",
                    source="BORN",
                    date_of_birth=birth,
                    birth_type="SINGLE",
                    birth_weight=2.5,
                    current_bucket="MALE_KIDS",
                )
            )
        await db.commit()
    response = await client.get("/api/dashboard", headers=owner)
    assert response.status_code == 200, response.text
    advisory = response.json()["advisory"]
    if expected_count:
        assert advisory == {
            "key": "bakrid_hold",
            "args": {
                "count": expected_count,
                "festival_date": festival.isoformat(),
            },
        }
    else:
        assert advisory is None


@pytest.mark.parametrize("component", ["weight", "quarantine_animal", "quarantine_task"])
async def test_declared_summary_dtos_adapt_actual_native_records_without_extra_fields(
    client: httpx.AsyncClient,
    component: str,
) -> None:
    owner = await owner_with_farm(client, email="mutation-dashboard-summary@farm.in")
    animal_doc = await make_animal(client, owner, tag="NATIVE-SUMMARY")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        day = await farm_day(db, farm_id)
        animal = await db.get(Animal, animal_doc["id"])
        assert animal is not None
        weight = WeightRecord(farm_id=farm_id, animal_id=animal.id, date=day, weight_kg=23.5)
        task = Task(
            farm_id=farm_id,
            animal_id=animal.id,
            title="Retained quarantine check",
            due_date=day,
            status="PENDING",
            category="QUARANTINE",
        )
        db.add_all([weight, task])
        await db.commit()
        await db.refresh(weight, attribute_names=["animal"])
        admitted = False
        document: dict[str, object] = {}
        try:
            if component == "weight":
                document = DashboardWeightOut.model_validate(weight).model_dump(mode="json")
            elif component == "quarantine_animal":
                document = PurchaseQuarantineAnimalOut.model_validate(animal).model_dump(
                    mode="json"
                )
            else:
                document = QuarantineScheduleTaskOut.model_validate(task).model_dump(mode="json")
            admitted = True
        except ValidationError:
            pass
        assert admitted, "The declared aggregate DTO accepts an actual persisted ORM record"
        assert document["id"] == (
            weight.id
            if component == "weight"
            else animal.id
            if component == "quarantine_animal"
            else task.id
        )
        assert not {"purchase_price", "seller_name", "restriction_reason", "created_by_id"} & set(
            document
        )


@pytest.mark.parametrize(
    "numerator,denominator,expected",
    [
        (3, 7, 42.9),
        (0, 7, 0.0),
        (None, 7, 0.0),
        (5, None, None),
        (5, 0, None),
    ],
)
def test_owner_percentages_keep_physical_units_and_absent_observations(
    numerator: int | None,
    denominator: int | None,
    expected: float | None,
) -> None:
    assert _rate(numerator, denominator) == expected


@pytest.mark.parametrize("view", ["overview", "benchmarks"])
async def test_actual_public_owner_dto_requires_a_positive_farm_identity(
    client: httpx.AsyncClient,
    view: str,
) -> None:
    owner = await owner_with_farm(client, email="mutation-dashboard-owner-dto@farm.in")
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
    assert refused, "The declared public farm identity boundary rejects zero"


async def test_owner_default_window_is_ninety_actual_calendar_days(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="mutation-dashboard-owner-window@farm.in")
    response = await client.get("/api/owner/benchmarks", headers=owner)
    assert response.status_code == 200, response.text
    assert response.json()["days"] == 90
