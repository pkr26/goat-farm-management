"""Real saved-plan DPRs share actual native simulation CPU admission limits."""

import httpx
import pytest

from app.api import _run_limits
from app.simulation.engine import BREAK_EVEN_PASSES

from .conftest import owner_with_farm


@pytest.mark.parametrize(
    "between_runs", [False, True], ids=["one-reserved-report", "below-two-report-reservations"]
)
async def test_saved_plan_dpr_reserves_the_full_documented_projection_work(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, between_runs: bool
) -> None:
    owner = await owner_with_farm(client)
    horizon = 12
    required_reservation = (1 + BREAK_EVEN_PASSES) * horizon
    budget = 2 * required_reservation - horizon if between_runs else required_reservation
    # Configure the genuine native limiter; its production probes, charges,
    # offloading and HTTP error paths all remain unchanged.
    window = _run_limits._RunCostWindow(window_seconds=300, budget=budget, max_keys=100)
    monkeypatch.setattr(_run_limits, "_run_budget", window)
    created = await client.post(
        "/api/planner/plans",
        headers=owner,
        json={
            "name": "Budgeted lender report",
            "start_year_month": "2026-01",
            "targets": [{"year_month": "2026-12", "animal_class": "male_grower", "count": 1.0}],
            "assumptions": {
                "meta": {"horizon_months": horizon, "start_year_month": "2026-01"},
                "herd": {"does": 0, "bucks": 0, "auto_purchase_bucks": False},
                "costs": {"misc_overhead_per_month": 0.0},
            },
        },
    )
    assert created.status_code == 201, created.text
    path = f"/api/planner/plans/{created.json()['id']}/dpr"
    first = await client.get(path, headers=owner)
    assert first.status_code == 200, first.text
    assert "Budgeted lender report" in first.text
    assert first.headers["content-type"].startswith("text/markdown")
    second = await client.get(path, headers=owner)
    assert second.status_code == 429, "A second full DPR exceeds this actual tenant work allowance"
    assert "CPU budget exhausted" in second.text
    assert second.headers["retry-after"] == "300"
