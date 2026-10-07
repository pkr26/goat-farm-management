"""Completion contracts for the bounded forecast/planning operations.

Each child runs the real operation. A stalled operation fails an explicit
business test assertion after 30 seconds, independently of pytest timeouts.
"""

import json
import subprocess
import sys
import textwrap
from typing import Any

import pytest


def _complete(code: str) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(code)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("The bounded farm operation must complete within 30 seconds.")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert isinstance(result, dict)
    return result


def test_optimizer_grid_completes_with_budget_and_all_dimensions() -> None:
    result = _complete(
        """
        import json
        from app.simulation.optimization import _sample_grid
        grid = _sample_grid([[0, 1, 2], [10, 20, 30], [100, 200]], 6)
        print(json.dumps({"grid": grid}))
        """
    )
    assert result["grid"] == [[1, 10, 100], [1, 10, 200], [1, 30, 100], [1, 30, 200]]


def test_purchase_chunking_completes_and_conserves_requested_head() -> None:
    result = _complete(
        """
        import json
        from app.simulation.planner import _purchases_from
        events = _purchases_from({2: 200007.0, 1: 3.0}, reserved=3)
        print(json.dumps({"events": [event.model_dump(mode="json") for event in events]}))
        """
    )
    events = result["events"]
    assert [(event["month"], event["count"]) for event in events] == [
        (1, 3.0),
        (2, 100_000.0),
        (2, 100_000.0),
        (2, 7.0),
    ]
    assert all(event["kind"] == "purchase" and event["animal_class"] == "doe" for event in events)
    assert sum(event["count"] for event in events) == 200_010.0


def test_colliding_newborn_tags_complete_without_losing_existing_animals() -> None:
    result = _complete(
        """
        import json
        from datetime import date
        from app.simulation.daily_ops import (
            AnimalStartSpec, DailyOpsInput, DailyOpsParams, run_daily_ops,
        )
        result = run_daily_ops(DailyOpsInput(
            start_date=date(2026, 1, 1), horizon_days=7, seed=1,
            animals=[
                AnimalStartSpec(
                    tag="D1", sex="F", bucket="DELIVERY", age_months=24, bred_days_ago=149,
                ),
                AnimalStartSpec(tag="D1-1", sex="F", bucket="FEMALE_KIDS", age_months=6),
                AnimalStartSpec(tag="D1-2", sex="F", bucket="FEMALE_KIDS", age_months=6),
                AnimalStartSpec(tag="B1", sex="M", bucket="BREEDING", age_months=24),
            ],
            params=DailyOpsParams(
                conception_rate=1.0, litter_size_mean=1.0, female_fraction_at_birth=1.0,
                stillbirth_rate=0.0, abortion_rate=0.0, kid_pre_weaning_mortality=0.0,
                kid_post_weaning_mortality=0.0, grower_annual_mortality=0.0,
                adult_annual_mortality=0.0,
            ),
        ))
        print(json.dumps({
            "head_start": result.head_start,
            "totals": result.totals.model_dump(mode="json"),
            "final_head": sum(row.heads for row in result.days[-1].occupancy),
            "journeys": [journey.model_dump(mode="json") for journey in result.journeys],
        }))
        """
    )
    journeys = {journey["tag"]: journey for journey in result["journeys"]}
    assert set(journeys) == {"D1", "D1-1", "D1-2", "D1-3", "B1"}
    for tag in ("D1-1", "D1-2"):
        assert journeys[tag]["born_day"] is None
        assert journeys[tag]["dam_tag"] is None
        assert journeys[tag]["start_bucket"] == "FEMALE_KIDS"
    assert (journeys["D1-3"]["born_day"], journeys["D1-3"]["dam_tag"]) == (2, "D1")
    totals = result["totals"]
    assert (result["head_start"], totals["kids_born_alive"], result["final_head"]) == (4, 1, 5)
    assert (
        result["head_start"]
        + totals["kids_born_alive"]
        - (totals["deaths"] + totals["culls"] + totals["sales"])
        == result["final_head"]
    )


def test_monthly_forecast_completes_and_conserves_every_month() -> None:
    result = _complete(
        """
        import json
        from app.simulation.assumptions import (
            HerdAssumptions, MetaAssumptions, SimulationAssumptions,
        )
        from app.simulation.engine import run_simulation
        assumptions = SimulationAssumptions(
            meta=MetaAssumptions(horizon_months=12),
            herd=HerdAssumptions(
                does=10, bucks=1, auto_purchase_bucks=False, foundation_flock_state="open",
            ),
        )
        forecast = run_simulation(assumptions, with_break_even=False)
        print(json.dumps({"months": [month.model_dump(mode="json") for month in forecast.months]}))
        """
    )
    assert len(result["months"]) == 12
    previous = 11.0
    for month in result["months"]:
        expected = (
            previous
            + month["births"]
            + month["purchases_head"]
            - (month["deaths"] + month["sales_head"] + month["culls_head"])
        )
        assert month["total_herd"] == pytest.approx(expected)
        assert month["total_herd"] >= 0.0
        previous = month["total_herd"]


def test_irr_isolation_completes_and_reports_all_sparse_dated_returns() -> None:
    result = _complete(
        """
        import json
        from app.simulation.finance import assess_irr
        assessment = assess_irr([-100.0, 350.0, -350.0, 100.0], [0.0, 2.0, 4.0, 6.0])
        print(json.dumps({"status": assessment.status, "roots": assessment.roots}))
        """
    )
    # In z = (1 + rate)^-2, NPV = 100(z-.5)(z-1)(z-2).
    # Every zero is a valid return, including the zero-rate root.
    assert result["status"] == "multiple_roots"
    assert result["roots"] == pytest.approx([2.0**-0.5 - 1.0, 0.0, 2.0**0.5 - 1.0])


def test_measured_growth_smoothing_completes_and_preserves_pooled_observations() -> None:
    result = _complete(
        """
        import json
        from app.services.simulation_calibration import _curve_from_observations, _isotonic_fit
        observations = {0: 3.0, 6: 25.0, 12: 15.0}
        anchors = _isotonic_fit(sorted(observations.items()))
        curve = _curve_from_observations(observations, [3.0 + 2.0*m for m in range(13)])
        print(json.dumps({"anchors": anchors, "curve": curve}))
        """
    )
    assert result["anchors"] == [[0, 3.0], [6, 20.0], [12, 20.0]]
    assert sum(weight for _, weight in result["anchors"]) == 43.0
    assert result["curve"] == pytest.approx(
        [3.0 + 17.0 * month / 6.0 for month in range(7)] + [20.0] * 6
    )
