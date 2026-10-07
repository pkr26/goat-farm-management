"""Independent oracles for resource accounting, market growth, and evidence boundaries."""

import math
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest

import app.services.dashboard as dashboard_service
from app.db import get_sessionmaker
from app.models import Animal, BucketMove, WeightRecord
from app.services.simulation_calibration import _annual_fraction_from_exposure, _confidence
from app.simulation.assumptions import (
    FeedAssumptions,
    HerdAssumptions,
    MetaAssumptions,
    SalesAssumptions,
    SimulationAssumptions,
)
from app.simulation.engine import run_simulation
from app.simulation.market import meat_price_for_month, other_revenue_growth
from tests.conftest import owner_with_farm
from tests.test_finance_extended import get_dashboard


@pytest.mark.parametrize(
    "sample_size, expected", [(0, "low"), (9, "low"), (10, "medium"), (29, "medium"), (30, "high")]
)
def test_calibration_confidence_includes_the_documented_sample_thresholds(
    sample_size: int,
    expected: str,
) -> None:
    assert _confidence(sample_size) == expected


@pytest.mark.parametrize(
    "deaths, months, period, expected",
    [
        (0, 12.0, 12.0, 0.0),
        (1, 0.0, 12.0, 0.0),
        (1, 12.0, 12.0, 1.0 - math.exp(-1.0)),
        (1, 12.0, 3.0, 1.0 - math.exp(-0.25)),
        (3, 24.0, 12.0, 1.0 - math.exp(-1.5)),
    ],
)
def test_calibration_exposure_probability_respects_phase_length_and_zero_exposure(
    deaths: int,
    months: float,
    period: float,
    expected: float,
) -> None:
    try:
        actual = _annual_fraction_from_exposure(deaths, months, period_months=period)
    except ZeroDivisionError:
        pytest.fail("Zero exposure must return zero mortality without division.")
    assert actual == pytest.approx(expected)


async def test_dashboard_readiness_includes_exact_age_weight_and_rest_boundaries(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    reference = date(2026, 9, 30)
    monkeypatch.setattr(dashboard_service, "today", lambda timezone: reference)
    cases = [
        ("AGE-OLDER", "F", "FOUNDATION", date(2025, 9, 29), "22", None, "BREEDING"),
        ("AGE-EXACT", "F", "FOUNDATION", date(2025, 9, 30), "22", None, "BREEDING"),
        ("AGE-YOUNGER", "F", "FOUNDATION", date(2025, 10, 1), "22", None, None),
        ("WEIGHT-LOW", "F", "FOUNDATION", date(2025, 1, 1), "21.99", None, None),
        ("WEIGHT-EXACT", "F", "FOUNDATION", date(2025, 1, 1), "22", None, "BREEDING"),
        ("REST-EARLY", "F", "RESTING", date(2025, 1, 1), "22", 29, None),
        ("REST-EXACT", "F", "RESTING", date(2025, 1, 1), "22", 30, "BREEDING"),
        ("REST-LATER", "F", "RESTING", date(2025, 1, 1), "22", 31, "BREEDING"),
        ("MARKET-OLDER", "M", "MALE_KIDS", date(2026, 1, 29), "24", None, "SELL"),
        ("MARKET-EXACT", "M", "MALE_KIDS", date(2026, 1, 30), "24", None, "SELL"),
        ("MARKET-YOUNGER", "M", "MALE_KIDS", date(2026, 1, 31), "24", None, None),
    ]
    async with get_sessionmaker()() as db:
        for tag, sex, bucket, born, weight, resting_days, _target in cases:
            animal = Animal(
                farm_id=farm_id,
                tag_number=tag,
                sex=sex,
                source="BORN",
                birth_type="SINGLE",
                birth_weight=Decimal("3"),
                date_of_birth=born,
                current_bucket=bucket,
            )
            db.add(animal)
            await db.flush()
            db.add(
                WeightRecord(
                    farm_id=farm_id,
                    animal_id=animal.id,
                    date=reference,
                    weight_kg=Decimal(weight),
                )
            )
            if resting_days is not None:
                db.add(
                    BucketMove(
                        farm_id=farm_id,
                        animal_id=animal.id,
                        from_bucket="RECOVERY",
                        to_bucket="RESTING",
                        effective_date=reference - timedelta(days=resting_days),
                    )
                )
        await db.commit()
    dashboard = await get_dashboard(client, owner)
    expected = {tag: target for tag, *_, target in cases if target is not None}
    actual = {row["animal"]["tag_number"]: row["to"] for row in dashboard["suggestions"]}
    assert actual == expected
    assert dashboard["suggestions_total"] == len(expected)


def test_forecast_lactating_does_receive_their_distinct_water_allowance() -> None:
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(does=10, bucks=1, auto_purchase_bucks=False),
        feed=FeedAssumptions(
            water_litres_kid_per_day=1.0,
            water_litres_weaner_per_day=2.0,
            water_litres_grower_per_day=3.0,
            water_litres_doe_per_day=4.0,
            water_litres_lactating_doe_per_day=13.0,
            water_litres_buck_per_day=6.0,
        ),
    )
    row = run_simulation(assumptions, with_break_even=False).months[0]
    assert row.lactating_does > 0.0
    expected = 30.44 * (
        row.f_kids
        + row.m_kids
        + 2.0 * (row.f_weaners + row.m_weaners)
        + 3.0 * (row.f_growers + row.m_growers)
        + 4.0 * (row.open_does + row.pregnant_does)
        + 13.0 * row.lactating_does
        + 6.0 * row.bucks
    )
    assert row.water_litres == pytest.approx(expected, rel=1e-9)


@pytest.mark.parametrize("month, growth", [(7, 1.1), (25, 1.4641)])
def test_market_growth_compounds_for_partial_and_multiple_years(
    month: int,
    growth: float,
) -> None:
    sales = SalesAssumptions(
        meat_price_per_kg=100.0,
        annual_livestock_price_growth_rate=0.21,
        monthly_meat_price_multipliers=[1.0] * 12,
        festival_sale_months=[],
    )
    assert meat_price_for_month(
        sales,
        simulation_month=month,
        calendar_month=1,
    ) == pytest.approx(100.0 * growth)
    assert other_revenue_growth(sales, month) == pytest.approx(growth)
