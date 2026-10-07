"""Valid resource plans conserve feed mass, actual crop costs and water demand."""

import json
from typing import Any

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import run_simulation
from app.simulation.results import SimulationResult


def _forecast(
    acres: float, *, free_feed: bool, bucks: int = 1, horizon: int = 12
) -> SimulationResult:
    payload: dict[str, Any] = {
        "meta": {"horizon_months": horizon},
        "herd": {
            "does": 0,
            "bucks": bucks,
            "female_kids": 0,
            "male_kids": 0,
            "female_weaners": 0,
            "male_weaners": 0,
            "female_growers": 0,
            "male_growers": 0,
            "auto_purchase_bucks": False,
            "foundation_flock_state": "open",
        },
        "growth": {"adult_weight_buck_kg": 50.0},
        "mortality": {"adult": 0.0},
        "feed": {
            "dmi_buck": 0.02,
            "concentrate_share_buck": 0.4,
            "green_dm_pct": 0.25,
            "dry_dm_pct": 0.5,
            "concentrate_dm_pct": 0.8,
            "grazing_dm_fraction": 0.0,
            "cultivated_fodder_acres": acres,
            "fodder_yield_t_dm_per_acre_year": 6.0,
            "initial_fodder_stock_kg_dm": 0.0,
            "fodder_storage_capacity_kg_dm": 0.0,
            "annual_feed_price_growth_rate": 0.0,
            "monthly_green_price_multipliers": [1.0] * 12,
            "monthly_dry_price_multipliers": [1.0] * 12,
            "monthly_concentrate_price_multipliers": [1.0] * 12,
            "monthly_fodder_yield_multipliers": [1.0] * 12,
            "green_price_per_kg": 0.0 if free_feed else 3.0,
            "purchased_green_price_per_kg": 0.0 if free_feed else 7.0,
            "dry_price_per_kg": 0.0 if free_feed else 11.0,
            "concentrate_price_per_kg": 0.0 if free_feed else 13.0,
            "water_litres_buck_per_day": 9.0,
        },
    }
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(payload))
    try:
        result = run_simulation(assumptions, with_break_even=False)
        json.dumps(result.model_dump(mode="json"), allow_nan=False)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"A valid resource plan, including free feed, must complete: {exc}")
    assert len(result.months) == horizon
    return result


@pytest.mark.parametrize("horizon", [12, 24], ids=["one-year", "two-years"])
@pytest.mark.parametrize("acres", [0.0, 0.024], ids=["market-fodder", "grown-fodder"])
@pytest.mark.parametrize("free_feed", [False, True], ids=["priced", "free"])
def test_constant_sire_ration_prices_actual_crop_and_shortfall_and_conserves_water(
    acres: float,
    free_feed: bool,
    horizon: int,
) -> None:
    result = _forecast(acres, free_feed=free_feed, horizon=horizon)
    # One 50kg buck eating 2% BW daily consumes 30.44kg DM monthly. Forty
    # percent is concentrate; the remainder has a 2:1 green/dry DM split.
    # At 25/50/80% DM the delivered amounts are 48.704/12.176/15.22kg.
    cultivated_dm = 12.0 if acres else 0.0
    expected_home = cultivated_dm / 0.25
    expected_bought = 48.704 - expected_home
    expected_cost = (
        0.0
        if free_feed
        else expected_home * 3.0 + expected_bought * 7.0 + 12.176 * 11.0 + 15.22 * 13.0
    )
    for month in result.months:
        assert month.total_herd == month.bucks == 1.0
        assert month.feed_green_kg == pytest.approx(48.704)
        assert month.feed_homegrown_green_kg == pytest.approx(expected_home)
        assert month.feed_purchased_green_kg == pytest.approx(expected_bought)
        assert month.feed_dry_kg == pytest.approx(12.176)
        assert month.feed_concentrate_kg == pytest.approx(15.22)
        assert month.feed_cost == pytest.approx(expected_cost)
        assert month.fodder_surplus_kg == pytest.approx(cultivated_dm - 12.176)
        assert month.fodder_stock_kg_dm == month.fodder_waste_kg_dm == 0.0
        assert month.water_litres == pytest.approx(273.96)
    summary = result.feed_summary
    years = horizon // 12
    assert summary.annual_green_kg == pytest.approx([48.704 * 12] * years)
    assert summary.annual_homegrown_green_kg == pytest.approx([expected_home * 12] * years)
    assert summary.annual_purchased_green_kg == pytest.approx([expected_bought * 12] * years)
    assert summary.annual_dry_kg == pytest.approx([12.176 * 12] * years)
    assert summary.annual_concentrate_kg == pytest.approx([15.22 * 12] * years)
    assert summary.annual_fodder_waste_kg_dm == [0.0] * years
    assert summary.land_requirement_acres == pytest.approx(0.024352)
    assert summary.fodder_deficit_months == horizon
    assert summary.peak_fodder_stock_kg_dm == 0.0
    assert summary.annual_feed_cost == pytest.approx([expected_cost * 12] * (horizon // 12))
    assert summary.annual_water_litres == pytest.approx([273.96 * 12] * (horizon // 12))
    assert summary.peak_water_litres_per_day == pytest.approx(9.0)


def test_empty_resource_plan_does_not_grow_or_spoil_a_crop_for_absent_animals() -> None:
    result = _forecast(3.0, free_feed=False, bucks=0)
    for month in result.months:
        assert month.total_herd == 0.0
        assert month.feed_green_kg == month.feed_homegrown_green_kg == 0.0
        assert month.feed_purchased_green_kg == month.feed_cost == 0.0
        assert month.fodder_stock_kg_dm == month.fodder_waste_kg_dm == 0.0
        assert month.fodder_surplus_kg == month.water_litres == 0.0
