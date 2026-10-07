"""Public uncertainty geometry, reporting and admissible sensitivity behavior."""

import json
import math
import random
import statistics
from collections.abc import Callable

import pytest
from pydantic import ValidationError

from app.simulation.assumptions import RiskVariable, SimulationAssumptions
from app.simulation.engine import run_simulation
from app.simulation.explain import build_narrative_report
from app.simulation.montecarlo import (
    _correlated_draws,
    _event_shock_path,
    _pct_label,
    run_monte_carlo,
    run_sensitivity,
)
from app.simulation.results import MonteCarloResult
from app.simulation.shocks import MonthlyShockPath


def _complete[Result](operation: Callable[[], Result]) -> Result:
    try:
        return operation()
    except (ArithmeticError, IndexError, KeyError, TypeError, ValueError) as error:
        pytest.fail(f"Admitted uncertainty analysis did not complete: {error!r}")


def _loss_assumptions(runs: int) -> SimulationAssumptions:
    return SimulationAssumptions.model_validate_json(
        json.dumps(
            {
                "meta": {"horizon_months": 12, "start_year_month": "2051-01"},
                "herd": {"does": 0, "bucks": 0, "auto_purchase_bucks": False},
                "finance": {
                    "loan_fraction_of_project_cost": 0.0,
                    "initial_stock_cost": 1000.0,
                },
                "risk": {"monte_carlo_runs": runs, "seed": 17},
            }
        )
    )


@pytest.mark.parametrize(
    "field,delta",
    [
        ("npv_histogram_counts", -1),
        ("npv_histogram_counts", 1),
        ("npv_histogram_edges", -1),
        ("npv_histogram_edges", 1),
    ],
)
def test_public_uncertainty_document_rejects_a_missing_or_extra_histogram_component(
    field: str, delta: int
) -> None:
    result = _complete(lambda: run_monte_carlo(_loss_assumptions(2)))
    valid = result.model_dump(mode="json")
    assert MonteCarloResult.model_validate_json(json.dumps(valid)) == result
    counts, edges = result.npv_histogram_counts, result.npv_histogram_edges
    assert len(edges) == len(counts) + 1 and sum(counts) == result.runs
    altered = json.loads(json.dumps(valid))
    if delta < 0:
        altered[field].pop()
    else:
        # An extra empty bucket even preserves total observations; shape still matters.
        altered[field].append(0 if field.endswith("counts") else edges[-1] + 1.0)
    with pytest.raises(ValidationError) as failure:
        MonteCarloResult.model_validate_json(json.dumps(altered))
    assert any(field in row["loc"] for row in failure.value.errors())


@pytest.mark.parametrize("runs", [1, 2, 8])
def test_real_risk_report_preserves_available_interval_endpoints_and_singleton_absence(
    runs: int,
) -> None:
    assumptions = _loss_assumptions(runs)
    result = _complete(
        lambda: run_simulation(assumptions, with_break_even=False, with_monte_carlo=True)
    )
    mc = result.monte_carlo
    assert mc is not None
    section = next(row for row in result.narrative_report if row.key == "risks")
    assert section.figures["mc_runs"] == runs
    assert section.figures["prob_dscr_below_one"] is None
    assert any("not measurable" in paragraph for paragraph in section.paragraphs)
    assert not any(
        "Run with Monte Carlo and sensitivity enabled" in paragraph
        for paragraph in section.paragraphs
    )
    for percentile in (5, 50, 95):
        interval = getattr(mc, f"npv_p{percentile}_ci")
        for index, side in enumerate(("low", "high")):
            assert section.figures[f"npv_p{percentile}_ci_{side}"] == (
                None if interval is None else interval[index]
            )
    noisy = [paragraph for paragraph in section.paragraphs if "sampling noise" in paragraph]
    assert bool(noisy) == (runs > 1)
    json.dumps(result.model_dump(mode="json"), allow_nan=False)


@pytest.mark.parametrize("strength", [0.6, 0.9])
def test_real_correlated_samples_retain_configured_triangular_marginal_moments(
    strength: float,
) -> None:
    names = (
        "meat_price",
        "feed_price",
        "adult_mortality",
        "kid_mortality",
        "litter_size",
        "conception_rate",
        "fodder_yield",
        "operating_cost",
        "milk_price",
    )
    variables = {name: RiskVariable(low=0.5, high=1.5) for name in names}
    rng = random.Random(7)
    draws = _complete(lambda: [_correlated_draws(rng, variables, strength) for _ in range(4096)])
    # Triangular(a=0.5, mode=1, b=1.5) has mean1 and variance1/24.
    # Wide tolerances test the configured distribution, not one exact seeded path.
    for name in names:
        values = [draw[name] for draw in draws]
        assert all(0.5 <= value <= 1.5 for value in values)
        assert statistics.mean(values) == pytest.approx(1.0, abs=0.02)
        assert statistics.pvariance(values) == pytest.approx(1.0 / 24.0, abs=0.012)


@pytest.mark.parametrize("sale_age", [9, 24])
def test_admitted_sale_age_sensitivity_retains_feasible_scheduled_arrivals_and_ceiling(
    sale_age: int,
) -> None:
    payload = {
        "meta": {"horizon_months": 24, "start_year_month": "2051-01"},
        "growth": {"sale_age_months": sale_age},
        "events": [
            {
                "month": 20,
                "kind": "purchase",
                "animal_class": "male_grower",
                "count": 5,
                "age_months": 8,
            }
        ],
    }
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(payload))
    items = _complete(lambda: run_sensitivity(assumptions))
    sale = next(item for item in items if item.parameter == "sale_age_months")
    assert all(
        math.isfinite(item.delta_npv_low) and math.isfinite(item.delta_npv_high) for item in items
    )
    if sale_age == 9:
        assert sale.label_low == "+0 month(s)" and sale.delta_npv_low == 0.0
    else:
        assert sale.label_high == "+0 month(s)" and sale.delta_npv_high == 0.0


@pytest.mark.parametrize("months", [6, 12])
def test_native_neutral_shock_calendar_completes_without_changing_any_channel(
    months: int,
) -> None:
    path = _complete(lambda: MonthlyShockPath.neutral(months))
    for values in (
        path.meat_price,
        path.feed_price,
        path.adult_mortality,
        path.kid_mortality,
        path.conception,
        path.litter_size,
        path.fodder_yield,
        path.operating_cost,
        path.milk_price,
        path.milk_yield,
    ):
        assert values == [1.0] * months
    assert path.disease_outbreaks == path.drought_events == path.market_crashes == 0


@pytest.mark.parametrize("missing", ["npv_p5_ci", "prob_npv_negative_se"])
def test_valid_native_result_with_one_unknown_sampling_field_omits_confidence_prose(
    missing: str,
) -> None:
    assumptions = _loss_assumptions(2)
    result = _complete(
        lambda: run_simulation(assumptions, with_break_even=False, with_monte_carlo=True)
    )
    assert result.monte_carlo is not None
    wire = result.monte_carlo.model_dump(mode="json")
    # Both fields explicitly permit absent/unknown observations. Retain every
    # genuinely computed outcome; remove only one optional field through the
    # native result schema rather than substituting any forecast ledger.
    wire.pop(missing)
    result.monte_carlo = MonteCarloResult.model_validate_json(json.dumps(wire))
    report = _complete(lambda: build_narrative_report(assumptions, result))
    section = next(row for row in report if row.key == "risks")
    assert not any("sampling noise" in paragraph for paragraph in section.paragraphs)
    assert section.figures["mc_runs"] == 2


def test_omitted_episode_durations_preserve_the_full_two_year_event_calendar() -> None:
    assumptions = SimulationAssumptions.model_validate_json(
        json.dumps(
            {
                "meta": {"horizon_months": 24},
                "risk": {
                    "disease_outbreak_probability_annual": 1.0,
                    "drought_probability_annual": 1.0,
                    "market_crash_probability_annual": 1.0,
                },
            }
        )
    )
    path = _complete(lambda: _event_shock_path(assumptions, random.Random(29)))
    # Existing default consumer episodes are three/four/three months.
    assert (path.disease_outbreaks, path.drought_events, path.market_crashes) == (8, 6, 8)


def test_zero_event_forecast_does_not_claim_configured_disasters() -> None:
    assumptions = _loss_assumptions(2)
    payload = assumptions.model_dump(mode="json")
    payload["risk"].update(
        disease_outbreak_probability_annual=0.0,
        drought_probability_annual=0.0,
        market_crash_probability_annual=0.0,
    )
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(payload))
    result = _complete(
        lambda: run_simulation(assumptions, with_break_even=False, with_monte_carlo=True)
    )
    assert result.monte_carlo is not None
    assert result.monte_carlo.mean_disease_outbreaks == 0.0
    assert result.monte_carlo.mean_drought_events == result.monte_carlo.mean_market_crashes == 0.0
    section = next(row for row in result.narrative_report if row.key == "risks")
    assert any(
        "No adverse-event draws are configured" in paragraph for paragraph in section.paragraphs
    )


def test_fully_admitted_conception_ceiling_retains_a_valid_low_sensitivity_variant() -> None:
    assumptions = SimulationAssumptions.model_validate_json(
        json.dumps({"meta": {"horizon_months": 12}, "reproduction": {"conception_rate": 1.0}})
    )
    items = _complete(lambda: run_sensitivity(assumptions))
    conception = next(item for item in items if item.parameter == "conception_rate")
    assert conception.label_low == "-20.0%" and conception.label_high == "+0.0%"
    assert math.isfinite(conception.delta_npv_low) and conception.delta_npv_high == 0.0


def test_zero_baseline_percentage_label_completes_without_a_division_error() -> None:
    assert _complete(lambda: _pct_label(0.0, 0.0)) == "+0%"


def test_real_mixed_profit_and_loss_runs_report_the_binomial_sampling_error() -> None:
    assumptions = SimulationAssumptions.model_validate_json(
        json.dumps(
            {
                "meta": {"horizon_months": 12, "start_year_month": "2051-01"},
                "herd": {"does": 0, "bucks": 0, "male_growers": 1, "auto_purchase_bucks": False},
                "finance": {"loan_fraction_of_project_cost": 0.0, "initial_stock_cost": 2000.0},
                "costs": {"labour_per_month": 0.0, "misc_overhead_per_month": 0.0},
                "risk": {"monte_carlo_runs": 8, "seed": 17},
            }
        )
    )
    result = _complete(lambda: run_monte_carlo(assumptions))
    probability = result.prob_npv_negative
    assert 0.0 < probability < 1.0
    assert result.prob_npv_negative_se == pytest.approx(
        math.sqrt(probability * (1.0 - probability) / result.runs), abs=1e-12
    )
