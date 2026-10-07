"""A genuine completed native price search prices the exact actual work reserved for admission."""

import json
from typing import Any

import pytest

from app.simulation import engine
from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import _CoreResult, run_simulation
from app.simulation.results import SimulationResult
from app.simulation.shocks import MonthlyShockPath
from app.simulation.vocabulary import SpeciesNouns


def _payload() -> dict[str, Any]:
    return {
        "meta": {"horizon_months": 12},
        "herd": {
            "does": 0,
            "bucks": 0,
            "auto_purchase_bucks": False,
            "foundation_flock_state": "open",
        },
        "reproduction": {"conception_rate": 0.0},
        "mortality": {"kid_pre_weaning": 0.0, "kid_post_weaning": 0.0, "grower": 0.0, "adult": 0.0},
        "culling": {"doe_cull_rate_annual": 0.0, "buck_rotation_years": 10},
        "sales": {
            "festival_sale_months": [],
            "annual_livestock_price_growth_rate": 0.0,
            "manure_income_per_adult_per_year": 0.0,
            "selling_cost_fraction": 0.0,
            "transport_cost_per_head": 0.0,
        },
        "feed": {
            "green_price_per_kg": 0.0,
            "purchased_green_price_per_kg": 0.0,
            "dry_price_per_kg": 0.0,
            "concentrate_price_per_kg": 0.0,
            "cultivated_fodder_acres": 0.0,
        },
        "costs": {
            "vet_per_animal_per_year": 0.0,
            "labour_per_month": 0.0,
            "insurance_pct_stock_value_annual": 0.0,
            "misc_overhead_per_month": 0.0,
            "operating_cost_growth_rate_annual": 0.0,
            "shed_cost_per_animal_place": 0.0,
            "equipment_cost_per_animal": 0.0,
        },
        "finance": {
            "loan_fraction_of_project_cost": 0.0,
            "terminal_livestock_realization_fraction": 1.0,
        },
    }


def _forecast(payload: dict[str, Any], *, with_break_even: bool = False) -> SimulationResult:
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(payload))
    try:
        result = run_simulation(assumptions, with_break_even=with_break_even)
        json.dumps(result.model_dump(mode="json"), allow_nan=False)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(
            f"An admitted financial forecast must return its complete truthful report: {error}"
        )
    assert len(result.months) == payload["meta"]["horizon_months"]
    return result


def _metric(result: SimulationResult, key: str) -> str:
    return next(item for item in result.metric_explanations if item.key == key).explanation


def test_price_search_work_allowance_matches_observed_full_native_engine_evaluations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload()
    payload["herd"]["male_growers"] = 1
    payload["sales"]["meat_price_per_kg"] = 0.0
    payload["finance"].update(
        {
            "initial_stock_cost": 1200.0,
            "include_terminal_value": False,
            "working_capital_months": 0,
            "discount_rate_annual": 0.0,
        }
    )
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(payload))
    original = engine._run_core
    evaluations = 0

    def observe(
        document: SimulationAssumptions,
        shock_path: MonthlyShockPath | None = None,
        nouns: SpeciesNouns | None = None,
    ) -> _CoreResult:
        nonlocal evaluations
        result = original(document, shock_path=shock_path, nouns=nouns)
        evaluations += 1
        return result

    monkeypatch.setattr(engine, "_run_core", observe)
    price = engine.break_even_meat_price(assumptions)
    assert price is not None and price > 0.0
    assert evaluations > 2
    assert evaluations == engine.BREAK_EVEN_PASSES, (
        "The configured admission allowance must price every actual full search evaluation, "
        "without charging fictitious extra work or omitting performed native work"
    )
