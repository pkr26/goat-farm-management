"""Real admitted subnormal investment reaches the DPR finite-result defense."""

import httpx

from .conftest import owner_with_farm


async def test_dpr_rejects_actual_unrepresentable_return_with_its_validation_status(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    # All declared inputs are finite and in the public JSON envelope. One
    # healthy sire earns ordinary manure cash; its tiny supplied opening
    # investment makes the actual finite cash-flow ratio overflow MIRR.
    # No calculation, limiter, formatter or finite-result probe is replaced.
    payload = {
        "meta": {"horizon_months": 12, "start_year_month": "2026-01"},
        "herd": {"does": 0, "bucks": 1, "auto_purchase_bucks": False},
        "mortality": {"adult": 0.0},
        "culling": {"buck_rotation_years": 10},
        "sales": {"manure_income_per_adult_per_year": 1200.0, "festival_sale_months": []},
        "feed": {
            "green_price_per_kg": 0.0,
            "purchased_green_price_per_kg": 0.0,
            "dry_price_per_kg": 0.0,
            "concentrate_price_per_kg": 0.0,
            "cultivated_fodder_acres": 0.0,
        },
        "costs": {
            "shed_cost_per_animal_place": 0.0,
            "equipment_cost_per_animal": 0.0,
            "labour_per_month": 0.0,
            "vet_per_animal_per_year": 0.0,
            "misc_overhead_per_month": 0.0,
            "insurance_pct_stock_value_annual": 0.0,
            "operating_cost_growth_rate_annual": 0.0,
        },
        "finance": {
            "initial_stock_cost": 5e-324,
            "working_capital_months": 0,
            "loan_fraction_of_project_cost": 0.0,
            "include_terminal_value": False,
        },
    }
    created = await client.post(
        "/api/planner/plans",
        headers=owner,
        json={
            "name": "Finite lender response",
            "start_year_month": "2026-01",
            "targets": [{"year_month": "2026-12", "animal_class": "male_grower", "count": 1.0}],
            "assumptions": payload,
        },
    )
    assert created.status_code == 201, created.text
    response = await client.get(f"/api/planner/plans/{created.json()['id']}/dpr", headers=owner)
    assert response.status_code == 422, response.text
    assert response.json() == {
        "detail": "These inputs produce non-finite results.",
        "code": "VALIDATION_ERROR",
    }
