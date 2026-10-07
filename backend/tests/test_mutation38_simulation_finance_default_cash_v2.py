"""Omitted published financial defaults retain real wages, assets, reserves and tax.

The examples isolate cash and carrying-value consequences rather than inspecting
literal model defaults. Each forecast originates from an ordinary JSON document.
"""

import json
from typing import Any

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import _CoreResult, _run_core


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


@pytest.mark.parametrize(
    "does,expected_wage", [(30, 100.0), (61, 300.0)], ids=["half-attendant", "third-half-step"]
)
def test_omitted_worker_capacity_books_the_documented_half_attendant_steps(
    does: int, expected_wage: float
) -> None:
    payload = _payload()
    payload["herd"]["does"] = does
    payload["costs"]["labour_per_month"] = 200.0
    core = _forecast(payload)
    assert all(row.total_herd == does and row.culls_head == 0.0 for row in core.months)
    assert all(row.labour_cost == expected_wage for row in core.months)
    assert sum(row.labour_cost for row in core.months) == 12 * expected_wage


def test_omitted_breeder_life_depreciates_bought_sire_over_five_asset_years() -> None:
    payload = _payload()
    payload["events"] = [
        {
            "month": 1,
            "kind": "purchase",
            "animal_class": "buck",
            "count": 1.0,
            "price_per_head": 1200.0,
        }
    ]
    core = _forecast(payload)
    assert core.months[0].breeding_stock_capex == 1200.0
    assert all(row.total_herd == row.bucks == 1.0 for row in core.months)
    assert all(
        row.depreciation == 20.0 and row.breeding_stock_disposal_cost == 0.0 for row in core.months
    )
    assert core.terminal_value_breakdown.breeding_stock == 960.0
    assert (
        sum(row.depreciation for row in core.months) + core.terminal_value_breakdown.breeding_stock
        == 1200.0
    )


def test_omitted_facility_lives_preserve_twenty_and_seven_year_asset_cash() -> None:
    payload = _payload()
    payload["costs"].update(
        {
            "capacity_basis": "planned",
            "planned_capacity_head": 1,
            "shed_cost_per_animal_place": 1200.0,
            "equipment_cost_per_animal": 840.0,
            "shed_residual_fraction": 0.0,
            "equipment_residual_fraction": 0.0,
        }
    )
    core = _forecast(payload)
    assert core.project_cost == core.shed_cost + core.equipment_cost == 2040.0
    assert all(row.total_herd == 0.0 and row.depreciation == 15.0 for row in core.months)
    assert core.terminal_value_breakdown.shed == 1140.0
    assert core.terminal_value_breakdown.equipment == 720.0
    assert (
        core.project_cost
        == sum(row.depreciation for row in core.months)
        + core.terminal_value_breakdown.shed
        + core.terminal_value_breakdown.equipment
    )


def test_omitted_working_reserve_funds_and_recovers_a_full_operating_year() -> None:
    payload = _payload()
    payload["costs"]["misc_overhead_per_month"] = 100.0
    core = _forecast(payload)
    assert all(row.misc_cost == 100.0 and row.total_herd == 0.0 for row in core.months)
    assert core.project_cost == core.working_capital == 1200.0
    assert core.terminal_value_breakdown.working_capital == 1200.0
    assert core.months[-1].terminal_value == 1200.0


def test_omitted_loss_carryforward_offsets_real_next_year_sale_profit() -> None:
    payload = _payload()
    payload["meta"]["horizon_months"] = 24
    payload["costs"]["misc_overhead_per_month"] = 100.0
    payload["finance"]["income_tax_rate"] = 0.5
    payload["events"] = [
        {
            "month": 1,
            "kind": "purchase",
            "animal_class": "buck",
            "count": 1.0,
            "price_per_head": 0.0,
        },
        {
            "month": 13,
            "kind": "sale",
            "animal_class": "buck",
            "count": 1.0,
            "price_per_head": 5000.0,
        },
    ]
    core = _forecast(payload)
    assert all(row.total_herd == 1.0 for row in core.months[:12])
    assert all(row.total_herd == 0.0 for row in core.months[12:])
    assert core.months[12].cull_revenue == 5000.0
    assert all(row.depreciation == 0.0 and row.misc_cost == 100.0 for row in core.months)
    assert sum(row.tax for row in core.months[:12]) == 0.0
    assert sum(row.tax for row in core.months[12:]) == 1300.0
    assert core.months[-1].tax == 1300.0


def test_omitted_moratorium_starts_real_principal_repayment_after_twelve_months() -> None:
    payload = _payload()
    payload["meta"]["horizon_months"] = 24
    payload["costs"]["misc_overhead_per_month"] = 100.0
    payload["finance"].update({"loan_fraction_of_project_cost": 1.0, "interest_rate_annual": 0.12})
    core = _forecast(payload)
    assert core.loan_amount == 1200.0
    assert all(row.interest == 12.0 and row.principal == 0.0 for row in core.amortization[:12])
    assert core.amortization[12].principal > 0.0
    assert core.amortization[12].payment > 12.0
    assert core.amortization[12].closing_balance < 1200.0
