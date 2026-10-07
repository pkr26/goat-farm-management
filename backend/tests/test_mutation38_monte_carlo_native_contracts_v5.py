"""Native uncertainty controls retain editor limits and coherent sampled results."""

import json
import math
import random
from collections.abc import Callable
from itertools import pairwise

import pytest
from pydantic import ValidationError

from app.simulation.assumptions import RiskAssumptions, RiskVariable, SimulationAssumptions
from app.simulation.montecarlo import (
    _annual_ar1_factors,
    _apply_annual_price_variation,
    _bootstrap_percentile_ci,
    _correlated_draws,
    _event_shock_path,
    _histogram,
    percentile,
    run_monte_carlo,
)
from app.simulation.shocks import MonthlyShockPath


def _complete[Result](operation: Callable[[], Result]) -> Result:
    try:
        return operation()
    except (ArithmeticError, IndexError, KeyError, TypeError, ValueError) as error:
        pytest.fail(f"Supported native uncertainty input did not complete: {error!r}")


@pytest.mark.parametrize(
    "field,lower,upper",
    [
        ("monte_carlo_runs", 1, 2000),
        ("seed", 0, 2147483647),
        ("disease_outbreak_duration_months", 1, 24),
        ("drought_duration_months", 1, 24),
        ("market_crash_duration_months", 1, 24),
    ],
)
def test_integral_risk_admission_matches_published_editor_limits(
    field: str, lower: int, upper: int
) -> None:
    # Published limits are preserved in the independent reference binding.
    # Runtime tests do not depend on untracked frontend source files.
    for admitted in (lower, upper):
        try:
            risk = RiskAssumptions.model_validate_json(json.dumps({field: admitted}))
        except ValidationError as error:
            pytest.fail(f"Published editor endpoint did not validate: {error!r}")
        assert getattr(risk, field) == admitted
    for rejected in (lower - 1, upper + 1):
        with pytest.raises(ValidationError) as failure:
            RiskAssumptions.model_validate_json(json.dumps({field: rejected}))
        assert any(field in row["loc"] for row in failure.value.errors())


@pytest.mark.parametrize("field", ["monte_carlo_runs", "seed"])
def test_omitted_run_controls_match_the_existing_editor_run_fixture(field: str) -> None:
    existing_consumer_request = {"monte_carlo_runs": 500, "seed": 42}
    assert (
        getattr(RiskAssumptions.model_validate_json("{}"), field)
        == existing_consumer_request[field]
    )


@pytest.mark.parametrize("horizon", [6, 13, 24, 25])
def test_annual_factors_use_one_real_independent_innovation_per_projection_year(
    horizon: int,
) -> None:
    rng = random.Random(73)
    factors = _complete(lambda: _annual_ar1_factors(horizon, rng, 0.0))
    reference = random.Random(73)
    innovations = [reference.normalvariate(0.0, 1.0) for _ in range(math.ceil(horizon / 12))]
    assert len(factors) == horizon
    for month, factor in enumerate(factors):
        assert factor == innovations[month // 12]
    # Fixed draw order preserves common random numbers for the next channel.
    assert rng.getstate() == reference.getstate()


def test_two_genuine_outcomes_have_a_bootstrap_sampling_interval() -> None:
    interval = _complete(lambda: _bootstrap_percentile_ci([0.0, 8.0], 0.5, random.Random(19)))
    assert interval is not None
    assert interval == (0.0, 8.0)


@pytest.mark.parametrize("fraction,expected", [(0.0, 2.0), (0.25, 4.0), (0.5, 6.0), (1.0, 10.0)])
def test_linear_percentiles_preserve_actual_two_outcome_cash_endpoints(
    fraction: float, expected: float
) -> None:
    value = _complete(lambda: percentile([10.0, 2.0], fraction))
    assert value == expected


def test_subnormal_cash_histogram_retains_all_observations_and_bin_geometry() -> None:
    values = [0.0, math.ulp(0.0)]
    counts, edges = _complete(lambda: _histogram(values, bins=4))
    assert len(counts) == 4 and len(edges) == len(counts) + 1
    assert sum(counts) == len(values) and all(count >= 0 for count in counts)
    assert edges[0] <= min(values) and edges[-1] >= max(values)
    assert all(left < right for left, right in pairwise(edges))


@pytest.mark.parametrize("disable_first", [False, True])
def test_zero_correlation_recovers_the_real_gaussian_triangular_marginals(
    disable_first: bool,
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
    risks = {name: RiskVariable(low=0.8, high=1.2) for name in names}
    if disable_first:
        risks["meat_price"].enabled = False
    observed = _complete(lambda: _correlated_draws(random.Random(41), risks, 0.0))
    assert set(observed) == set(risks)
    reference = random.Random(41)
    # The published factor model has market, climate and disease innovations.
    for _ in range(3):
        reference.normalvariate(0.0, 1.0)
    for name in names:
        innovation = reference.normalvariate(0.0, 1.0)
        if not risks[name].enabled:
            assert observed[name] == 1.0
            continue
        value = observed[name]
        assert 0.8 <= value <= 1.2
        # Independent triangular CDF, evaluated at the actual sampled value.
        cdf = (
            (value - 0.8) ** 2 / (0.4 * 0.2)
            if value <= 1.0
            else 1.0 - (1.2 - value) ** 2 / (0.4 * 0.2)
        )
        assert cdf == pytest.approx(0.5 * (1.0 + math.erf(innovation / math.sqrt(2.0))), abs=1e-12)


def test_disabled_meat_risk_retains_the_later_enabled_annual_feed_process() -> None:
    assumptions = SimulationAssumptions.model_validate_json(
        json.dumps(
            {
                "meta": {"horizon_months": 13},
                "risk": {"meat_price": {"enabled": False}, "feed_price": {"enabled": True}},
            }
        )
    )
    path = MonthlyShockPath.neutral(13)
    effective = _complete(
        lambda: _apply_annual_price_variation(
            path, {"meat_price": 1.0, "feed_price": 1.15}, assumptions, random.Random(61)
        )
    )
    assert effective["meat_price"] == 1.0 and path.meat_price == [1.0] * 13
    assert effective["feed_price"] != 1.15
    assert path.feed_price[0] != 1.0 and path.feed_price[12] != path.feed_price[0]
    reference = random.Random(61)
    # The disabled meat channel still consumes its two annual innovations.
    for _ in range(2):
        reference.normalvariate(0.0, 1.0)
    feed_innovation = reference.normalvariate(0.0, 1.0)
    low, high = assumptions.risk.feed_price.low, assumptions.risk.feed_price.high
    triangular_variance = (low * low + 1.0 + high * high - low - high - low * high) / 18.0
    annual_variance = triangular_variance / 2.0
    first_factor = math.sqrt(1.0 - assumptions.risk.price_process_rho**2) * feed_innovation
    expected = math.exp(math.sqrt(annual_variance) * first_factor - annual_variance / 2.0)
    assert path.feed_price[0] == pytest.approx(expected, abs=1e-12)


def test_omitted_event_durations_retain_the_existing_twelve_month_episode_calendar() -> None:
    assumptions = SimulationAssumptions.model_validate_json(
        json.dumps(
            {
                "meta": {"horizon_months": 12},
                "risk": {
                    "disease_outbreak_probability_annual": 1.0,
                    "drought_probability_annual": 1.0,
                    "market_crash_probability_annual": 1.0,
                },
            }
        )
    )
    path = _complete(lambda: _event_shock_path(assumptions, random.Random(29)))
    assert (path.disease_outbreaks, path.drought_events, path.market_crashes) == (4, 3, 4)
    assert len(path.meat_price) == assumptions.meta.horizon_months


@pytest.mark.parametrize("runs", [1, 2])
def test_real_debt_free_loss_forecast_reports_measurable_risk_and_optional_sampling(
    runs: int,
) -> None:
    assumptions = SimulationAssumptions.model_validate_json(
        json.dumps(
            {
                "meta": {"horizon_months": 12, "start_year_month": "2051-01"},
                "herd": {"does": 0, "bucks": 0, "auto_purchase_bucks": False},
                "finance": {"loan_fraction_of_project_cost": 0.0, "initial_stock_cost": 1000.0},
                "risk": {"monte_carlo_runs": runs, "seed": 17},
            }
        )
    )
    result = _complete(lambda: run_monte_carlo(assumptions))
    json.dumps(result.model_dump(mode="json"), allow_nan=False)
    assert result.runs == runs and result.seed == assumptions.risk.seed
    assert result.npv_p95 < 0.0 and result.prob_npv_negative == 1.0
    assert result.prob_dscr_below_one is None
    assert sum(result.npv_histogram_counts) == runs
    assert len(result.npv_histogram_edges) == len(result.npv_histogram_counts) + 1
    assert len(result.cash_percentiles.p50) == assumptions.meta.horizon_months
    if runs == 1:
        assert result.npv_p5_ci is None and result.prob_npv_negative_se is None
    else:
        assert result.npv_p5_ci is not None and result.prob_npv_negative_se == 0.0


def test_omitted_annual_price_variation_preserves_the_enabled_public_forecast() -> None:
    payload = {
        "meta": {"horizon_months": 13, "start_year_month": "2051-01"},
        "herd": {"does": 0, "bucks": 0, "male_growers": 1, "auto_purchase_bucks": False},
        "risk": {"monte_carlo_runs": 2, "seed": 37},
    }
    implicit = SimulationAssumptions.model_validate_json(json.dumps(payload))
    explicit = SimulationAssumptions.model_validate_json(
        json.dumps(
            payload
            | {"risk": {"monte_carlo_runs": 2, "seed": 37, "within_run_price_variation": True}}
        )
    )
    observed = _complete(lambda: run_monte_carlo(implicit))
    enabled = _complete(lambda: run_monte_carlo(explicit))
    assert observed.model_dump(mode="json") == enabled.model_dump(mode="json")
