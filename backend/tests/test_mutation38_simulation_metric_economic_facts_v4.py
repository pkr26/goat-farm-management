"""Actual debt, liquidation, grant and price-search facts retain meaningful metric disclosures."""

import json
from typing import Any

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import run_simulation
from app.simulation.explain import build_metric_explanations
from app.simulation.results import SimulationResult


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


@pytest.mark.parametrize(
    "horizon,term,moratorium",
    [(12, 13, 12), (12, 24, 12), (13, 14, 13)],
    ids=[
        "one-payment-after-horizon",
        "whole-second-year-after-horizon",
        "partial-final-interest-only-year",
    ],
)
def test_interest_only_horizon_reports_no_operating_principal_repayment_year(
    horizon: int, term: int, moratorium: int
) -> None:
    payload = _payload()
    payload["meta"]["horizon_months"] = horizon
    payload["costs"].update(
        {
            "capacity_basis": "planned",
            "planned_capacity_head": 1,
            "shed_cost_per_animal_place": 1200.0,
        }
    )
    payload["finance"].update(
        {
            "loan_fraction_of_project_cost": 0.5,
            "loan_term_months": term,
            "moratorium_months": moratorium,
            "include_terminal_value": False,
            "working_capital_months": 0,
        }
    )
    result = _forecast(payload)
    assert result.amortization[horizon - 1].closing_balance == 600.0
    assert sum(row.principal for row in result.annual_pl) == 600.0
    assert result.metrics.avg_dscr is None and result.metrics.min_dscr is None
    avg = next(item for item in result.metric_explanations if item.key == "avg_dscr")
    assert avg.figures["debt_years"] == 0
    assert "No principal-repaying year" in avg.explanation
    assert "no weak year" in _metric(result, "min_dscr")
    assert "balloon repayment in the final month" in _metric(result, "loan_amount")


@pytest.mark.parametrize(
    "liquidates", [False, True], ids=["actual-final-sale", "actual-closing-livestock"]
)
def test_final_month_payback_attributes_the_real_sale_or_liquidation(liquidates: bool) -> None:
    payload = _payload()
    payload["herd"]["bucks"] = 1
    payload["finance"].update(
        {
            "initial_stock_cost": 1200.0,
            "working_capital_months": 0,
            "include_terminal_value": liquidates,
            "discount_rate_annual": 0.0,
        }
    )
    if not liquidates:
        payload["events"] = [
            {
                "month": 12,
                "kind": "sale",
                "animal_class": "buck",
                "count": 1.0,
                "price_per_head": 1200.0,
            }
        ]
    result = _forecast(payload)
    assert result.metrics.payback_month == 12
    assert all(row.cumulative_cash_flow == -1200.0 for row in result.months[:11])
    payback = next(item for item in result.metric_explanations if item.key == "payback_month")
    assert payback.figures["terminal_driven"] == 1.0
    if liquidates:
        assert result.metrics.terminal_value > 1200.0
        assert "payback by liquidation" in payback.explanation
    else:
        assert result.metrics.terminal_value == 0.0
        assert "single final-injection payback" in payback.explanation
        assert "payback by liquidation" not in payback.explanation


def test_native_default_explanation_knows_an_actual_completed_unsuccessful_price_search() -> None:
    payload = _payload()
    payload["costs"].update(
        {
            "capacity_basis": "planned",
            "planned_capacity_head": 1,
            "shed_cost_per_animal_place": 1200.0,
        }
    )
    payload["finance"].update({"include_terminal_value": False, "working_capital_months": 0})
    result = _forecast(payload, with_break_even=True)
    assert result.metrics.break_even_meat_price_per_kg is None
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(payload))
    try:
        explanations = build_metric_explanations(assumptions, result)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(
            f"A completed supported price search must retain its actual explanation: {error}"
        )
    text = next(
        item for item in explanations if item.key == "break_even_meat_price_per_kg"
    ).explanation
    assert "Even at the break-even search ceiling" in text
    assert "not computed" not in text


def test_zero_assumed_meat_price_reports_the_real_required_price_without_a_percentage() -> None:
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
    result = _forecast(payload, with_break_even=True)
    assert result.metrics.break_even_meat_price_per_kg is not None
    assert result.metrics.break_even_meat_price_per_kg > 0.0
    explanation = next(
        item for item in result.metric_explanations if item.key == "break_even_meat_price_per_kg"
    )
    assert explanation.figures["safety_margin"] is None
    assert "current assumption is ₹0/kg" in explanation.explanation
    assert "percentage safety margin is not defined" in explanation.explanation


def test_approved_grant_metric_retains_actual_estimate_status_and_dated_cash() -> None:
    payload = _payload()
    payload["finance"].update(
        {
            "nlm_subsidy": True,
            "nlm_unit_females": 100,
            "nlm_unit_males": 5,
            "nlm_eligible_capital_cost": 2.0,
            "nlm_approved_subsidy_amount": 1.0,
            "nlm_subsidy_receipts": [{"month": 1, "amount": 0.5}, {"month": 12, "amount": 0.5}],
        }
    )
    result = _forecast(payload)
    assert result.metrics.subsidy_amount == result.metrics.subsidy_estimate_amount == 1.0
    text = _metric(result, "subsidy_amount")
    assert "Conditional estimate: ₹1" in text
    assert "Status: approved_scheduled" in text
    assert "No future grant reduces the opening equity" in text
    assert "NLM installments arrive later" in _metric(result, "equity")


def test_no_operating_revenue_has_no_fabricated_margin_or_unique_return() -> None:
    payload = _payload()
    payload["costs"].update(
        {
            "capacity_basis": "planned",
            "planned_capacity_head": 1,
            "shed_cost_per_animal_place": 1200.0,
        }
    )
    payload["finance"].update({"include_terminal_value": False, "working_capital_months": 0})
    result = _forecast(payload)
    assert result.metrics.operating_margin is None and result.metrics.irr is None
    assert "Operating margin is undefined" in _metric(result, "operating_margin")
    assert "IRR is undefined or unproven" in _metric(result, "irr")
    assert "Cumulative cash flow never covers" in _metric(result, "payback_month")


def test_the_first_real_emi_in_a_partial_year_remains_one_operating_repayment_year() -> None:
    payload = _payload()
    payload["meta"]["horizon_months"] = 13
    payload["herd"].update({"bucks": 1, "buck_purchase_price": 0.0})
    payload["sales"]["manure_income_per_adult_per_year"] = 1200.0
    payload["costs"].update(
        {
            "capacity_basis": "planned",
            "planned_capacity_head": 1,
            "shed_cost_per_animal_place": 1200.0,
        }
    )
    payload["finance"].update(
        {
            "loan_fraction_of_project_cost": 0.5,
            "loan_term_months": 24,
            "moratorium_months": 12,
            "include_terminal_value": False,
            "working_capital_months": 0,
        }
    )
    result = _forecast(payload)
    assert result.amortization[12].principal > 0.0
    assert result.amortization[11].principal == 0.0
    assert result.metrics.avg_dscr is not None
    avg = next(item for item in result.metric_explanations if item.key == "avg_dscr")
    assert avg.figures["debt_years"] == 1
    assert "1 principal-repaying year(s)" in avg.explanation


def test_positive_actual_returns_have_a_defined_margin_and_required_return_interpretation() -> None:
    payload = _payload()
    payload["herd"]["bucks"] = 1
    payload["sales"]["manure_income_per_adult_per_year"] = 2400.0
    payload["finance"].update(
        {
            "initial_stock_cost": 1200.0,
            "include_terminal_value": False,
            "working_capital_months": 0,
            "discount_rate_annual": 0.0,
        }
    )
    result = _forecast(payload)
    assert result.metrics.npv == 1200.0 and result.metrics.bcr == 2.0
    assert result.metrics.irr is not None and result.metrics.irr > 0.0
    assert result.metrics.operating_margin == 1.0
    assert "A positive NPV" in _metric(result, "npv")
    assert "exceeds your" in _metric(result, "irr")
    assert "100.0% of operating revenue" in _metric(result, "operating_margin")
    assert "2.00" in _metric(result, "bcr")
