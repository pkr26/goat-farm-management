"""Validated native forecasts and biological/pool conversion contracts."""

import json

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import (
    _draw,
    _run_core,
    _scale,
    monthly_mortality_rate,
    phase_monthly_mortality_rate,
    run_simulation,
    weight_at_age,
)


def test_native_mortality_conversions_preserve_the_stated_exposure_period() -> None:
    try:
        annual = monthly_mortality_rate(0.2)
        phase = phase_monthly_mortality_rate(0.3, 3)
        empty = phase_monthly_mortality_rate(0.3, 0)
    except (ArithmeticError, ValueError) as exc:
        pytest.fail(f"Valid mortality exposure cannot be converted: {exc}")
    assert (1.0 - annual) ** 12 == pytest.approx(0.8)
    assert (1.0 - phase) ** 3 == pytest.approx(0.7)
    assert empty == 0.0


def test_native_growth_reaches_each_configured_live_weight_anchor() -> None:
    assumptions = SimulationAssumptions.model_validate_json(
        '{"growth":{"adult_weight_doe_kg":98.76}}'
    )
    growth = assumptions.growth
    adult = growth.adult_weight_doe_kg
    try:
        birth = weight_at_age(0, growth, adult)
        yearling = weight_at_age(12, growth, adult)
        first_adult_month = weight_at_age(growth.adult_weight_age_months, growth, adult)
        later_adult = weight_at_age(growth.adult_weight_age_months + 1, growth, adult)
        interpolated = weight_at_age(13, growth, adult)
    except (ArithmeticError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"A valid configured live-weight anchor is unavailable: {exc}")
    assert birth == growth.birth_weight_kg
    assert yearling == growth.weight_by_age_months[12]
    # The configured mature-weight fact is the endpoint, including exactly
    # the first mature month. It must not be re-rounded by interpolation.
    assert first_adult_month == adult == later_adult
    assert yearling < interpolated < adult


def test_native_pool_full_removal_conserves_head_and_can_empty_the_pool() -> None:
    pool = [1.0, 3.0]
    try:
        scaled = _scale(pool, 0.0)
        taken = _draw(pool, 4.0)
    except (ArithmeticError, TypeError, ValueError) as exc:
        pytest.fail(f"A legitimate full-cohort disposal cannot complete: {exc}")
    assert scaled == [0.0, 0.0]
    assert taken == 4.0
    assert pool == [0.0, 0.0]
    assert taken + sum(pool) == 4.0


@pytest.mark.parametrize("scenario", ["unlimited", "single-service", "mixed", "sell-foundation"])
def test_valid_json_forecast_completes_and_conserves_every_starting_cohort(scenario: str) -> None:
    payload: dict[str, object] = {
        "meta": {"horizon_months": 12},
        "herd": {
            "does": 6,
            "bucks": 1,
            "female_growers": 4,
            "male_growers": 5,
            "female_weaners": 2,
            "male_weaners": 3,
            "female_kids": 1,
            "male_kids": 4,
            "female_retention_fraction": 1.0,
            "auto_purchase_bucks": False,
            "foundation_flock_state": "mixed" if scenario == "mixed" else "open",
        },
        "reproduction": {
            "conception_rate": 0.0,
            "max_services_before_cull": 1 if scenario == "single-service" else 0,
        },
        "mortality": {"kid_pre_weaning": 0.0, "kid_post_weaning": 0.0, "grower": 0.0, "adult": 0.0},
        "culling": {"doe_cull_rate_annual": 0.0, "buck_rotation_years": 10},
    }
    if scenario == "sell-foundation":
        payload["events"] = [{"month": 1, "kind": "sale", "animal_class": "doe", "count": 6.0}]
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(payload))
    try:
        result = run_simulation(assumptions, with_break_even=False)
        json.dumps(result.model_dump(mode="json"), allow_nan=False)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"A complete validated cohort forecast must return serializable results: {exc}")
    assert len(result.months) == 12
    previous = 26.0
    for row in result.months:
        assert row.deaths == pytest.approx(0.0, abs=1e-8)
        assert row.purchases_head == 0.0
        assert row.total_herd == pytest.approx(
            previous + row.births - row.sales_head - row.culls_head, abs=1e-8
        )
        previous = row.total_herd
    if scenario != "mixed":
        assert sum(row.births for row in result.months) == 0.0
        assert sum(row.sales_head for row in result.months) == pytest.approx(12.0)
        assert result.months[-1].m_growers == 0.0
        assert result.months[-1].f_growers == 0.0
        assert result.months[-1].total_herd == pytest.approx(
            8.0 if scenario == "sell-foundation" else 1.0 if scenario == "single-service" else 14.0
        )


def test_native_core_default_vocabulary_completes_a_real_scheduled_disposal() -> None:
    assumptions = SimulationAssumptions.model_validate_json(
        '{"meta":{"horizon_months":12},"events":[{"month":1,"kind":"sale","animal_class":"doe","count":1.0}]}'
    )
    try:
        core = _run_core(assumptions)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"Native core defaults must support a valid scheduled disposal: {exc}")
    assert len(core.months) == 12
    event = core.months[0].event_fills[0]
    assert event.animal_class == "doe" and event.filled == 1.0
    assert event.revenue > 0.0
    assert any("doe" in entry.casefold() for entry in core.months[0].events)
