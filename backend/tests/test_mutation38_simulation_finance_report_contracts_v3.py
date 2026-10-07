"""Financial narratives expose the actual forecast, receipt timing and forgone wages."""

import json
from typing import Any

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import run_simulation
from app.simulation.explain import _ranked, _share
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


def _forecast(payload: dict[str, Any]) -> SimulationResult:
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(payload))
    try:
        result = run_simulation(assumptions, with_break_even=False)
        json.dumps(result.model_dump(mode="json"), allow_nan=False)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(
            f"An admitted financial forecast must return its complete truthful report: {error}"
        )
    assert len(result.months) == payload["meta"]["horizon_months"]
    return result


def _text(result: SimulationResult, key: str) -> str:
    return " ".join(
        next(section for section in result.narrative_report if section.key == key).paragraphs
    )


def _metric(result: SimulationResult, key: str) -> str:
    return next(item for item in result.metric_explanations if item.key == key).explanation


def test_profitable_cash_report_discloses_established_returns_and_actual_payback() -> None:
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
    assert result.metrics.npv == 1200.0
    assert result.metrics.bcr == 2.0
    assert result.metrics.irr is not None and result.metrics.irr > 0.0
    assert result.metrics.payback_month == 6
    verdict = _text(result, "viability_verdict")
    assert "All standard checks pass" in verdict
    assert "BCR is 2.00" in verdict
    assert f"IRR is {result.metrics.irr * 100:.1f}%" in verdict
    assert "month 6 (year 1)" in verdict
    assert "VIABLE" in verdict and "NOT VIABLE" not in verdict


def test_no_return_project_reports_the_absent_root_and_actual_negative_npv() -> None:
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
    assert result.metrics.npv == -1200.0 and result.metrics.irr is None
    assert "NOT VIABLE" in _text(result, "viability_verdict")
    assert "a unique IRR is not established" in _text(result, "viability_verdict")
    assert "undefined or unproven" in _metric(result, "irr")
    assert "negative" in _metric(result, "npv")


@pytest.mark.parametrize(
    "does,mixed,wage",
    [(1, False, 1200.0), (9, True, 4800.0)],
    ids=["single-doe-half-attendant", "all-mixed-adult-phases"],
)
def test_unpaid_family_work_discloses_the_same_actual_adult_attendance_cost(
    does: int, mixed: bool, wage: float
) -> None:
    payload = _payload()
    payload["herd"].update({"does": does, "foundation_flock_state": "mixed" if mixed else "open"})
    payload["costs"].update(
        {
            "family_labour": True,
            "labour_per_month": 200.0,
            "labour_per_head_threshold": 5 if mixed else 60,
        }
    )
    result = _forecast(payload)
    assert all(row.labour_cost == 0.0 for row in result.months)
    assert all(
        row.open_does + row.pregnant_does + row.lactating_does == does for row in result.months
    )
    cost = _text(result, "cost_mix")
    assert "profit is earned on unpaid family work" in cost
    assert f"₹{wage:,.0f} over the projection" in cost


def test_zero_doe_and_zero_breeder_acquisition_reports_no_fabricated_labour_or_purchase() -> None:
    payload = _payload()
    payload["costs"]["family_labour"] = True
    result = _forecast(payload)
    cost = _text(result, "cost_mix")
    assert "profit is earned on unpaid family work" not in cost
    assert "Buying breeding stock" not in cost
    assert all(row.breeding_stock_capex == 0.0 for row in result.months)


def test_trading_stock_cash_is_a_positive_operating_cost_in_the_report() -> None:
    payload = _payload()
    payload["events"] = [
        {
            "month": 1,
            "kind": "purchase",
            "animal_class": "male_kid",
            "count": 1.0,
            "price_per_head": 333.0,
        }
    ]
    result = _forecast(payload)
    costs = next(s for s in result.narrative_report if s.key == "cost_mix")
    assert sum(row.purchase_cost for row in result.months) == 333.0
    assert costs.figures["stock_purchases"] == costs.figures["total_opex"] == 333.0
    assert "Operating costs total ₹333" in _text(result, "cost_mix")
    assert "stock purchases ₹333" in _text(result, "cost_mix")


@pytest.mark.parametrize("nlm", [False, True], ids=["assumed-up-front", "dated-approved-grant"])
def test_reports_keep_the_actual_custom_and_declared_nlm_receipt_timing_distinct(nlm: bool) -> None:
    payload = _payload()
    payload["costs"].update(
        {
            "capacity_basis": "planned",
            "planned_capacity_head": 1,
            "shed_cost_per_animal_place": 1200.0,
        }
    )
    if nlm:
        payload["finance"].update(
            {
                "nlm_subsidy": True,
                "nlm_unit_females": 100,
                "nlm_unit_males": 5,
                "nlm_eligible_capital_cost": 2.0,
                "nlm_approved_subsidy_amount": 1.0,
                "nlm_subsidy_receipts": [
                    {"month": 1, "amount": 0.5},
                    {"month": 12, "amount": 0.5},
                ],
            }
        )
    else:
        payload["finance"]["subsidy_fraction"] = 0.2
    result = _forecast(payload)
    overview = _text(result, "overview")
    subsidy = _metric(result, "subsidy_amount")
    if nlm:
        assert "declared approved NLM receipts" in overview and "arrive later" in overview
        assert "Conditional estimate" in subsidy and "two equal installments" in subsidy
        assert "No future grant reduces" in subsidy and "Status: approved_scheduled" in subsidy
        assert result.metrics.subsidy_amount == 1.0
    else:
        assert "assumed up-front subsidy" in overview
        assert "User-assumed up-front capital subsidy" in subsidy
        assert "20.0%" in subsidy and result.metrics.subsidy_amount == 240.0


@pytest.mark.parametrize(
    "items,total,expected",
    [
        ([("vet", 5.0)], 5.0, "vet ₹5 (100.0%)"),
        (
            [("vet", 5.0), ("feed", 15.0), ("labour", 10.0)],
            30.0,
            "feed ₹15 (50.0%), labour ₹10 (33.3%) and vet ₹5 (16.7%)",
        ),
    ],
    ids=["one-paid-line", "three-ranked-cost-lines"],
)
def test_native_cost_ranking_preserves_every_actual_paid_line(
    items: list[tuple[str, float]], total: float, expected: str
) -> None:
    try:
        actual = _ranked(items, total)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(f"Valid paid cost lines must retain their complete ranked disclosure: {error}")
    assert actual == expected


def test_a_zero_total_cost_has_no_defined_percentage_share() -> None:
    try:
        actual = _share(0.0, 0.0)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(
            f"A genuinely empty cost total must return the declared undefined share: {error}"
        )
    assert actual == "—"
