"""Valid breeding-pool forecasts preserve cohort head, exposure and occupied places."""

import json
from dataclasses import replace
from typing import Any

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import _run_core, run_simulation
from app.simulation.results import SimulationResult
from app.simulation.shocks import MonthlyShockPath


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
            "female_retention_fraction": 0.0,
            "foundation_flock_state": "open",
        },
        "reproduction": {
            "conception_rate": 0.0,
            "litter_size": 2.0,
            "stillbirth_rate": 0.0,
            "max_services_before_cull": 0,
            "parity_multipliers": {"litter_size": [1.0], "conception_rate": [1.0]},
        },
        "mortality": {"kid_pre_weaning": 0.0, "kid_post_weaning": 0.0, "grower": 0.0, "adult": 0.0},
        "culling": {"doe_cull_rate_annual": 0.0, "buck_rotation_years": 10},
        "sales": {"festival_sale_months": []},
    }


def _forecast(payload: dict[str, Any], opening: float) -> SimulationResult:
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(payload))
    try:
        result = run_simulation(assumptions, with_break_even=False)
        json.dumps(result.model_dump(mode="json"), allow_nan=False)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"A complete valid reproductive cohort forecast cannot finish: {exc}")
    assert len(result.months) == 12
    previous = opening
    for row in result.months:
        assert row.deaths == pytest.approx(0.0, abs=1e-8)
        assert row.total_herd == pytest.approx(
            previous + row.births + row.purchases_head - row.sales_head - row.culls_head,
            abs=1e-8,
        )
        previous = row.total_herd
    return result


@pytest.mark.parametrize("settling", [0, 1], ids=["immediate-service", "one-month-settle"])
def test_purchased_does_complete_with_one_service_bucket_and_no_sire(settling: int) -> None:
    payload = _payload()
    payload["herd"]["purchased_doe_settling_months"] = settling
    payload["events"] = [{"month": 1, "kind": "purchase", "animal_class": "doe", "count": 2.0}]
    result = _forecast(payload, 0.0)
    first = result.months[0]
    assert first.purchases_head == first.event_fills[0].filled == 2.0
    assert first.breeding_stock_capex > 0.0
    assert first.purchase_cost == 0.0
    assert sum(row.births + row.culls_head + row.sales_head for row in result.months) == 0.0
    assert all(row.total_herd == 2.0 for row in result.months)


@pytest.mark.parametrize(
    "gestation,lactation",
    [(1, 1), (1, 2), (5, 1), (5, 2)],
    ids=["short-both", "short-gestation", "short-lactation", "usual-cycle"],
)
def test_mixed_foundation_finishes_existing_pregnancies_without_rebreed_wait(
    gestation: int,
    lactation: int,
) -> None:
    payload = _payload()
    payload["herd"].update({"does": 9, "foundation_flock_state": "mixed"})
    payload["reproduction"].update(
        {
            "gestation_months": gestation,
            "lactation_months": lactation,
            "months_open_before_breeding": 0,
        }
    )
    result = _forecast(payload, 9.0)
    # Mixed foundation placement shares its nine does among one service slot
    # and every gestation/lactation month. No further service can conceive.
    expected_births = 9.0 * gestation / (1 + gestation + lactation) * 2.0
    assert sum(row.births for row in result.months) == pytest.approx(expected_births)
    assert all(row.births == 0.0 for row in result.months[gestation:])
    assert sum(row.culls_head + row.purchases_head for row in result.months) == 0.0


def test_shed_places_include_does_held_between_same_month_purchase_and_sale() -> None:
    payload = _payload()
    payload["herd"]["purchased_doe_settling_months"] = 2
    payload["costs"] = {"capacity_basis": "projected_peak", "capacity_buffer_fraction": 0.0}
    payload["events"] = [
        {"month": 1, "kind": "purchase", "animal_class": "doe", "count": 2.0},
        {"month": 1, "kind": "sale", "animal_class": "doe", "count": 2.0},
    ]
    result = _forecast(payload, 0.0)
    assert result.months[0].total_herd == 0.0
    assert result.months[0].event_fills[0].filled == result.months[0].event_fills[1].filled == 2.0
    assert result.project_cost_breakdown.projected_peak_head == 2.0
    assert result.project_cost_breakdown.capacity_places == 2.0


@pytest.mark.parametrize(
    "stock,mortality,months",
    [
        ("female_kids", "kid_pre_weaning", 3),
        ("female_weaners", "kid_post_weaning", 3),
        ("female_growers", "grower", 12),
        ("does", "adult", 12),
    ],
    ids=["pre-wean", "post-wean", "grower", "adult"],
)
def test_native_mortality_shock_doubles_loss_before_exposure_conversion(
    stock: str,
    mortality: str,
    months: int,
) -> None:
    payload = _payload()
    payload["herd"][stock] = 10
    payload["mortality"][mortality] = 0.25
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(payload))
    neutral = MonthlyShockPath.neutral(12)
    shock = replace(neutral, kid_mortality=[2.0] * 12, adult_mortality=[2.0] * 12)
    try:
        core = _run_core(assumptions, shock_path=shock)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"A positive twofold native mortality exposure must complete: {exc}")
    expected_survivors = 10.0 * 0.5 ** (1.0 / months)
    assert len(core.months) == 12
    assert core.months[0].births == core.months[0].sales_head == core.months[0].culls_head == 0.0
    assert core.months[0].total_herd == pytest.approx(expected_survivors)
    assert core.months[0].deaths == pytest.approx(10.0 - expected_survivors)
