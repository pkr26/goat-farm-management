"""Breeding policy dates, replacement sires and proportional book-value conservation."""

import json
from typing import Any

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import run_simulation
from app.simulation.results import SimulationResult


def _payload() -> dict[str, Any]:
    return {
        "meta": {"horizon_months": 12},
        "herd": {
            "does": 0,
            "bucks": 0,
            "female_kids": 0,
            "male_kids": 0,
            "female_weaners": 0,
            "male_weaners": 0,
            "female_growers": 0,
            "male_growers": 0,
            "auto_purchase_bucks": False,
            "foundation_flock_state": "open",
            "doe_purchase_price": 100.0,
            "buck_purchase_price": 2222.0,
        },
        "reproduction": {"conception_rate": 0.0, "max_services_before_cull": 0},
        "mortality": {"kid_pre_weaning": 0.0, "kid_post_weaning": 0.0, "grower": 0.0, "adult": 0.0},
        "culling": {"doe_cull_rate_annual": 0.0, "buck_rotation_years": 10},
        "sales": {
            "annual_livestock_price_growth_rate": 0.0,
            "monthly_meat_price_multipliers": [1.0] * 12,
            "festival_sale_months": [],
        },
    }


def _forecast(payload: dict[str, Any], opening: float) -> SimulationResult:
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(payload))
    try:
        result = run_simulation(assumptions, with_break_even=False)
        json.dumps(result.model_dump(mode="json"), allow_nan=False)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"A valid funded breeding policy cannot return its complete accounts: {exc}")
    assert len(result.months) == payload["meta"]["horizon_months"]
    previous = opening
    for row in result.months:
        assert row.births == row.deaths == row.sales_head == 0.0
        assert row.total_herd == pytest.approx(
            previous + row.purchases_head - row.culls_head, abs=1e-8
        )
        previous = row.total_herd
    return result


def test_automatic_sire_capacity_is_funded_before_service_and_replaced_at_rotation() -> None:
    payload = _payload()
    payload["herd"].update({"does": 3, "auto_purchase_bucks": True})
    payload["culling"].update({"buck_doe_ratio": 2, "buck_rotation_years": 1})
    result = _forecast(payload, 3.0)
    assert result.months[0].purchases_head == 2.0
    assert result.months[0].breeding_stock_capex == 4444.0
    assert all(row.bucks == 2.0 and row.total_herd == 5.0 for row in result.months)
    assert all(row.purchases_head == row.culls_head == 0.0 for row in result.months[1:11])
    assert result.months[11].culls_head == result.months[11].purchases_head == 2.0
    assert result.months[11].breeding_stock_capex == 4444.0


def test_rotation_retains_the_fresh_scheduled_sire_and_buys_only_the_remaining_gap() -> None:
    payload = _payload()
    payload["herd"].update({"does": 3, "bucks": 2, "auto_purchase_bucks": True})
    payload["culling"].update({"buck_doe_ratio": 2, "buck_rotation_years": 1})
    payload["events"] = [{"month": 12, "kind": "purchase", "animal_class": "buck", "count": 1.0}]
    result = _forecast(payload, 5.0)
    final = result.months[11]
    assert final.event_fills[0].filled == 1.0
    assert final.purchases_head == final.culls_head == 2.0
    assert final.bucks == 2.0
    assert final.breeding_stock_capex == 4444.0
    assert all(row.purchases_head == row.culls_head == 0.0 for row in result.months[:11])


def test_rate_cull_starts_after_foundation_year_and_compounds_over_twelve_exposures() -> None:
    payload = _payload()
    payload["meta"]["horizon_months"] = 24
    payload["herd"]["does"] = 100
    payload["culling"]["doe_cull_rate_annual"] = 0.2
    result = _forecast(payload, 100.0)
    monthly_fraction = 1.0 - 0.8 ** (1.0 / 12.0)
    assert all(row.culls_head == 0.0 for row in result.months[:12])
    assert result.months[12].culls_head == pytest.approx(100.0 * monthly_fraction)
    assert sum(row.culls_head for row in result.months[12:]) == pytest.approx(20.0)
    assert result.months[-1].total_herd == pytest.approx(80.0)


def test_repeat_cull_proportional_basis_includes_the_still_settling_breeding_pool() -> None:
    payload = _payload()
    payload["herd"].update({"does": 6, "bucks": 1, "purchased_doe_settling_months": 2})
    payload["reproduction"]["max_services_before_cull"] = 1
    payload["events"] = [{"month": 1, "kind": "purchase", "animal_class": "doe", "count": 2.0}]
    result = _forecast(payload, 7.0)
    first = result.months[0]
    assert first.purchases_head == 2.0 and first.culls_head == 6.0
    assert first.total_herd == 3.0
    assert first.breeding_stock_capex == 200.0
    # The native acquisition-vintage account documents proportional disposal
    # over every live breeding doe, including the two settling arrivals.
    assert first.breeding_stock_disposal_cost == pytest.approx(200.0 * 6.0 / 8.0)


def test_rate_cull_removes_only_its_fraction_of_the_surviving_acquisition_book_value() -> None:
    payload = _payload()
    payload["meta"]["horizon_months"] = 24
    payload["culling"]["doe_cull_rate_annual"] = 0.2
    payload["costs"] = {"breeding_stock_useful_life_months": 60}
    payload["events"] = [{"month": 1, "kind": "purchase", "animal_class": "doe", "count": 2.0}]
    result = _forecast(payload, 0.0)
    first_cull = result.months[12]
    assert result.months[0].breeding_stock_capex == 200.0
    assert all(row.breeding_stock_disposal_cost == 0.0 for row in result.months[:12])
    disposed_fraction = first_cull.culls_head / result.months[11].total_herd
    assert first_cull.breeding_stock_disposal_cost == pytest.approx(
        200.0 * disposed_fraction * (1.0 - 12.0 / 60.0)
    )


def test_automatic_restaff_funds_sires_while_purchased_does_are_still_settling() -> None:
    payload = _payload()
    payload["herd"].update({"auto_purchase_bucks": True, "purchased_doe_settling_months": 2})
    payload["culling"]["buck_doe_ratio"] = 2
    payload["events"] = [{"month": 1, "kind": "purchase", "animal_class": "doe", "count": 3.0}]
    result = _forecast(payload, 0.0)
    first = result.months[0]
    assert first.event_fills[0].filled == 3.0
    assert first.purchases_head == first.total_herd == 5.0
    assert first.bucks == 2.0
    assert first.breeding_stock_capex == 300.0 + 4444.0
    assert all(row.bucks == 2.0 for row in result.months)
