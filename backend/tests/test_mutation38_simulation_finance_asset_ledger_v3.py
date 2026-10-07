"""Actual acquisition, disposal, depreciation, grant receipts and debt conserve cash.

NLM examples test the model's versioned numerical bands and funding disclosure;
they do not establish applicant eligibility or an actual government approval.
"""

import json
from typing import Any

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import _BreedingVintage, _CoreResult, _run_core


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


def _forecast(payload: dict[str, Any]) -> _CoreResult:
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(payload))
    try:
        core = _run_core(assumptions)
        json.dumps([row.model_dump(mode="json") for row in core.months], allow_nan=False)
        json.dumps(core.terminal_value_breakdown.model_dump(mode="json"), allow_nan=False)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(
            f"A valid financial forecast must complete with conserved cash and assets: {error}"
        )
    assert len(core.months) == payload["meta"]["horizon_months"]
    assert all(row.births == row.deaths == 0.0 for row in core.months)
    return core


def test_native_fractional_disposal_conserves_remaining_acquisition_cost_and_book() -> None:
    vintage = _BreedingVintage(month=1, kind="doe", cost_by_age=[600.0, 300.0, 100.0])
    try:
        disposed = vintage.remove_fraction(0.25, month=4, life=10)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(
            f"A real quarter-cohort disposition must preserve its acquisition ledger: {error}"
        )
    assert disposed == 175.0
    assert vintage.cost_by_age == [450.0, 225.0, 75.0]
    assert sum(vintage.cost_by_age) + 250.0 == 1000.0
    assert disposed + 0.7 * sum(vintage.cost_by_age) == 700.0
    assert vintage.remove_fraction(1.0, month=11, life=10) == 0.0
    assert vintage.cost_by_age == [0.0, 0.0, 0.0]


@pytest.mark.parametrize("animal_class", ["doe", "buck"])
def test_actual_adult_sale_derecognizes_the_remaining_acquisition_basis(animal_class: str) -> None:
    payload = _payload()
    payload["meta"]["horizon_months"] = 24
    payload["costs"]["breeding_stock_useful_life_months"] = 12
    payload["events"] = [
        {
            "month": 1,
            "kind": "purchase",
            "animal_class": animal_class,
            "count": 1.0,
            "price_per_head": 1200.0,
        },
        {
            "month": 4,
            "kind": "sale",
            "animal_class": animal_class,
            "count": 1.0,
            "price_per_head": 1500.0,
        },
    ]
    core = _forecast(payload)
    assert all(row.total_herd == 1.0 for row in core.months[:3])
    assert all(row.total_herd == 0.0 for row in core.months[3:])
    assert all(row.depreciation == 100.0 for row in core.months[:3])
    assert core.months[3].culls_head == 1.0
    assert core.months[3].cull_revenue == 1500.0
    assert core.months[3].breeding_stock_disposal_cost == 900.0
    assert all(
        row.depreciation == row.breeding_stock_disposal_cost == 0.0 for row in core.months[4:]
    )
    assert core.terminal_value_breakdown.breeding_stock == 0.0
    assert sum(row.breeding_stock_capex for row in core.months) == sum(
        row.depreciation + row.breeding_stock_disposal_cost for row in core.months
    )


def test_zero_physical_does_have_no_remaining_acquisition_account_after_their_age_exit() -> None:
    payload = _payload()
    payload["meta"]["horizon_months"] = 24
    payload["herd"].update(
        {"foundation_doe_age_min_months": 35, "foundation_doe_age_max_months": 35}
    )
    payload["culling"]["max_doe_age_months"] = 36
    payload["costs"]["breeding_stock_useful_life_months"] = 24
    payload["events"] = [
        {
            "month": 1,
            "kind": "purchase",
            "animal_class": "doe",
            "count": 1.0,
            "price_per_head": 2400.0,
        }
    ]
    core = _forecast(payload)
    assert sum(row.culls_head for row in core.months) == 1.0
    assert core.months[-1].total_herd == 0.0
    assert core.terminal_value_breakdown.breeding_stock == 0.0
    for row in core.months:
        if row.breeding_stock_disposal_cost:
            assert row.culls_head > 0.0, "Acquisition basis must leave with actual disposed head"
        if row.total_herd == 0.0:
            assert row.depreciation == 0.0, "No acquired breeding animals remain to depreciate"
    assert sum(
        row.depreciation + row.breeding_stock_disposal_cost for row in core.months
    ) == pytest.approx(2400.0)


def test_rotation_disposes_old_sire_basis_and_preserves_the_same_month_replacement() -> None:
    payload = _payload()
    payload["herd"]["auto_purchase_bucks"] = True
    payload["culling"]["buck_rotation_years"] = 1
    payload["costs"]["breeding_stock_useful_life_months"] = 12
    payload["events"] = [
        {
            "month": 1,
            "kind": "purchase",
            "animal_class": "buck",
            "count": 1.0,
            "price_per_head": 1200.0,
        },
        {
            "month": 12,
            "kind": "purchase",
            "animal_class": "buck",
            "count": 1.0,
            "price_per_head": 600.0,
        },
    ]
    core = _forecast(payload)
    assert core.months[-1].bucks == core.months[-1].total_herd == 1.0
    assert sum(row.culls_head for row in core.months) == 1.0
    assert core.months[-1].breeding_stock_disposal_cost == pytest.approx(100.0)
    assert core.months[-1].depreciation == 50.0
    assert core.terminal_value_breakdown.breeding_stock == 550.0
    assert (
        sum(row.breeding_stock_capex for row in core.months)
        == sum(row.depreciation + row.breeding_stock_disposal_cost for row in core.months)
        + core.terminal_value_breakdown.breeding_stock
    )


def test_written_off_facilities_stop_depreciating_after_their_supported_lifetime() -> None:
    payload = _payload()
    payload["meta"]["horizon_months"] = 24
    payload["costs"].update(
        {
            "capacity_basis": "planned",
            "planned_capacity_head": 1,
            "shed_cost_per_animal_place": 1200.0,
            "equipment_cost_per_animal": 1200.0,
            "shed_residual_fraction": 0.0,
            "equipment_residual_fraction": 0.0,
            "shed_useful_life_years": 1,
            "equipment_useful_life_years": 1,
        }
    )
    core = _forecast(payload)
    assert all(row.depreciation == 200.0 for row in core.months[:12])
    assert all(row.depreciation == 0.0 for row in core.months[12:])
    assert (
        sum(row.depreciation for row in core.months)
        == core.shed_cost + core.equipment_cost
        == 2400.0
    )
    assert core.terminal_value_breakdown.shed == core.terminal_value_breakdown.equipment == 0.0


@pytest.mark.parametrize(
    "horizon,term",
    [(12, 24), (12, 12), (24, 12)],
    ids=["terminal-balloon", "settles-at-horizon", "settles-before-horizon"],
)
def test_zero_rate_loan_repayment_and_terminal_balloon_conserve_the_real_principal(
    horizon: int, term: int
) -> None:
    payload = _payload()
    payload["meta"]["horizon_months"] = horizon
    payload["costs"]["misc_overhead_per_month"] = 100.0
    payload["finance"].update(
        {
            "loan_fraction_of_project_cost": 0.5,
            "interest_rate_annual": 0.0,
            "loan_term_months": term,
            "moratorium_months": 0,
        }
    )
    core = _forecast(payload)
    assert core.loan_amount == 600.0
    assert sum(row.debt_service for row in core.months) == 600.0
    assert all(row.interest == 0.0 for row in core.amortization)
    assert sum(row.principal for row in core.amortization) == 600.0
    assert core.amortization[-1].closing_balance == 0.0
    if term > horizon:
        assert core.months[-1].debt_service == 325.0
    else:
        assert all(row.debt_service == 0.0 for row in core.months[term:])


def _subsidy_payload() -> dict[str, Any]:
    payload = _payload()
    payload["herd"].update(
        {"does": 100, "bucks": 5, "doe_purchase_price": 0.0, "buck_purchase_price": 0.0}
    )
    payload["costs"].update(
        {
            "capacity_basis": "planned",
            "planned_capacity_head": 105,
            "shed_cost_per_animal_place": 20000.0,
        }
    )
    payload["finance"].update({"nlm_subsidy": True, "nlm_eligible_capital_cost": 2_000_000.0})
    return payload


@pytest.mark.parametrize(
    "mode", ["opening-unit", "declared-subset", "unknown-cost", "unlisted-unit", "below-minimum"]
)
def test_policy_estimate_discloses_real_unit_and_budget_without_inventing_receipts(
    mode: str,
) -> None:
    payload = _subsidy_payload()
    expected_cap: float | None = 1_000_000.0
    expected_estimate: float | None = 1_000_000.0
    expected_status = "estimate_only"
    if mode == "declared-subset":
        payload["herd"].update({"does": 200, "bucks": 10})
        payload["finance"].update({"nlm_unit_females": 100, "nlm_unit_males": 5})
    elif mode == "unknown-cost":
        payload["finance"].pop("nlm_eligible_capital_cost")
        expected_estimate, expected_status = None, "eligible_cost_unknown"
    elif mode == "unlisted-unit":
        payload["finance"].update({"nlm_unit_females": 150, "nlm_unit_males": 8})
        expected_cap, expected_estimate, expected_status = None, None, "unsupported_unit"
    elif mode == "below-minimum":
        payload["finance"].update({"nlm_unit_females": 50, "nlm_unit_males": 2})
        expected_cap, expected_estimate, expected_status = 0.0, 0.0, "ineligible"
    core = _forecast(payload)
    assert core.subsidy_cap == expected_cap
    assert core.subsidy_estimate_amount == expected_estimate
    assert core.subsidy_status == expected_status
    assert core.subsidy_amount == 0.0
    assert all(row.subsidy_receipt == 0.0 for row in core.months)
    assert core.equity == core.project_cost


def test_approved_two_half_award_preserves_first_and_exact_horizon_cash_receipts() -> None:
    payload = _subsidy_payload()
    payload["finance"].update(
        {
            "nlm_approved_subsidy_amount": 1_000_000.0,
            "nlm_subsidy_receipts": [
                {"month": 1, "amount": 500_000.0},
                {"month": 12, "amount": 500_000.0},
            ],
        }
    )
    core = _forecast(payload)
    assert core.subsidy_status == "approved_scheduled"
    assert core.subsidy_amount == 1_000_000.0
    assert [(row.month, row.subsidy_receipt) for row in core.months if row.subsidy_receipt] == [
        (1, 500_000.0),
        (12, 500_000.0),
    ]
    assert core.equity == core.project_cost


def test_actual_manure_cash_is_positive_in_the_monthly_and_annual_revenue_accounts() -> None:
    payload = _payload()
    payload["herd"]["bucks"] = 1
    payload["sales"]["manure_income_per_adult_per_year"] = 1200.0
    payload["finance"]["include_terminal_value"] = False
    core = _forecast(payload)
    assert all(
        row.total_herd == 1.0 and row.manure_revenue == row.net_cash_flow == 100.0
        for row in core.months
    )
    assert sum(row.net_cash_flow for row in core.months) == 1200.0
    assert core.annual_pl[0].total_revenue == 1200.0


def test_an_unlisted_zero_award_has_a_controlled_native_policy_error() -> None:
    payload = _subsidy_payload()
    payload["finance"].update(
        {"nlm_unit_females": 150, "nlm_unit_males": 8, "nlm_approved_subsidy_amount": 0.0}
    )
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(payload))
    try:
        _run_core(assumptions)
    except ValueError as error:
        assert "supported eligible-cost/unit cap" in str(error)
    except (ArithmeticError, AttributeError, LookupError, TypeError) as error:
        pytest.fail(f"Unsupported zero award requires a controlled native policy error: {error}")
    else:
        pytest.fail(
            "An unsupported unit with a declared award must receive its native policy error"
        )
