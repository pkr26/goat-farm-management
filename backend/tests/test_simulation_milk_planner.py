"""The milk planner: a daily litres target turned into a herd, a breeding
calendar and a procurement plan."""

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.lactation import monthly_milk_curve
from app.simulation.milk_planner import build_milk_plan


def _murrah() -> SimulationAssumptions:
    from app.simulation.defaults import get_preset

    return get_preset("murrah_dairy")


def test_thousand_litres_plans_a_herd_that_ships_it() -> None:
    report = build_milk_plan(_murrah(), 1000.0)
    assert report.achievable is True
    assert report.steady_average_daily_litres == pytest.approx(1000.0, rel=0.01)
    herd = report.herd
    # Analytic seed: f = target x days / lactation litres = 12.7 freshenings a
    # month; the converged herd sits near that times the calving interval
    # (14.2 months) — a wide but sane band. The calf-milk allowance (heifer
    # calves drink what the tank never ships) pushes the needed freshenings
    # above the bare seed, hence the wider band.
    assert 130.0 <= herd.breeding_does <= 300.0
    assert herd.milking_does > 0.0
    assert herd.milking_does < herd.breeding_does
    assert herd.calvings_per_month == pytest.approx(12.7, abs=4.5)
    # The existing 60-doe foundation is credited; procurement is the ramp gap
    # plus the replacement bridge, never less than the ramp tranches alone.
    assert herd.starting_does_credited == 60.0
    assert herd.purchases_total >= sum(p.count for p in report.purchases)
    assert herd.purchases_total == pytest.approx(
        sum(p.count for p in report.purchases) + herd.replacement_purchases_total, rel=1e-9
    )
    assert herd.purchases_total > 0.0
    # Projection rows exist, are ordered and carry the calendar.
    assert [m.month for m in report.projection] == list(range(1, 121))
    assert {m.calendar_month for m in report.projection} == set(range(1, 13))
    # Steady semantics: from steady_from_month on, EVERY 12-month window
    # clears 98% of target (a held state, not a first lucky window), and the
    # plan finishes on target.
    if report.steady_from_month is not None:
        daily = [m.projected_daily_litres for m in report.projection]
        start = report.steady_from_month - 1
        for end in range(start + 12, len(daily) + 1):
            assert sum(daily[end - 12 : end]) / 12.0 >= 0.98 * 1000.0
    # The plan finishes on target on the 12-month average (individual months
    # still swing with the season — the final month sits in the July trough).
    final_twelve = [m.projected_daily_litres for m in report.projection[-12:]]
    assert sum(final_twelve) / 12.0 == pytest.approx(1000.0, rel=0.02)
    # Fat-based procurement price: the verified 850 Rs/kg fat x 6.8% = 57.8 Rs/L.
    month = report.projection[-1]
    assert month.projected_monthly_revenue == pytest.approx(
        month.projected_monthly_litres * 850.0 * 6.8 / 100.0, rel=1e-9
    )


def test_single_doe_stops_milking_at_dry_off_in_late_pregnancy() -> None:
    """The biology the whole dairy plan rests on, verified on one animal.

    One open doe, deterministic biology (every rate 0/1), a 3-month lactation
    against a Wood curve, and a 5-month gestation with a 1-month voluntary
    waiting period: she must calve, milk exactly her lactation following the
    curve, then go DRY — no milk at all while heavily pregnant — until the
    next calving. If the engine ever "fades" milk through late pregnancy
    instead of stopping it, every milk plan built on it over-promises the
    tank.
    """
    from app.simulation.engine import _run_core

    a = SimulationAssumptions(
        meta={"horizon_months": 20, "start_year_month": "2026-01"},
        herd={"does": 1, "bucks": 0, "max_breeding_does": 0, "foundation_flock_state": "open"},
        reproduction={
            "conception_rate": 1.0,
            "gestation_months": 5,
            "lactation_months": 3,
            "months_open_before_breeding": 1,
            "litter_size": 1.0,
            "stillbirth_rate": 0.0,
            "sex_ratio_female": 0.0,
            "max_services_before_cull": 0,
        },
        mortality={"kid_pre_weaning": 0.0, "kid_post_weaning": 0.0, "grower": 0.0, "adult": 0.0},
        culling={"doe_cull_rate_annual": 0.0, "max_doe_age_months": 120},
        sales={
            "lactation_milk_litres": 300.0,
            "milk_curve_shape": "wood",
            "milk_peak_day": 65.0,
            "milk_price_per_litre": 1.0,
            "annual_milk_price_growth_rate": 0.0,
            "manure_income_per_adult_per_year": 0.0,
            "monthly_milk_yield_multipliers": [1.0] * 12,
            "meat_price_per_kg": 0.0,
        },
    )
    core = _run_core(a)
    by_month = {m.month: m for m in core.months}

    # Calvings: conceived month 1, gestation 5 → first calving month 6; with
    # perfect conception the cycle is VWP + gestation = 6 months.
    calvings = [m.month for m in core.months if m.births > 0.99]
    assert calvings == [6, 12, 18]

    # Milking months: the 3 lactation months after each calving, and only
    # those. Yield follows the curve (rising to the peak bucket), and one
    # full cycle sums to exactly the lactation litres.
    milking = [m.month for m in core.months if m.lactating_does > 0.99]
    assert milking == [6, 7, 8, 12, 13, 14, 18, 19, 20]
    first_cycle = sum(by_month[m].milk_revenue for m in (6, 7, 8))
    assert first_cycle == pytest.approx(300.0, rel=1e-6)

    # The dry gap: months 9-11 (and 15-17) carry a pregnant, NON-milking doe
    # with exactly zero milk — late pregnancy dries her off completely.
    for dry_month in (9, 10, 11, 15, 16, 17):
        row = by_month[dry_month]
        assert row.lactating_does == pytest.approx(0.0, abs=1e-9)
        assert row.pregnant_does == pytest.approx(1.0, abs=1e-9)
        assert row.milk_revenue == pytest.approx(0.0, abs=1e-9)


def test_dry_period_arithmetic_is_explicit_in_the_herd_design() -> None:
    """The plan must state the dry months every cycle implies — the number
    that answers "why do I need dry animals at all" — and the milking share
    of the herd, consistent with the simulated averages."""
    report = build_milk_plan(_murrah(), 1000.0)
    herd = report.herd
    assert herd.dry_months_per_cycle == pytest.approx(
        herd.calving_interval_months - report.curve.lactation_months, abs=1e-9
    )
    # Murrah: 10 months in milk out of a ~14-month cycle → ~4 months dry.
    assert 3.0 <= herd.dry_months_per_cycle <= 5.0
    assert herd.milking_share_of_herd == pytest.approx(
        herd.milking_does / herd.breeding_does, rel=1e-9
    )
    # The dry share of the breeding herd is the cycle arithmetic, not a
    # rounding artefact: milking share ≈ lactation / calving interval.
    assert herd.milking_share_of_herd == pytest.approx(
        report.curve.lactation_months / herd.calving_interval_months, rel=0.05
    )
    assert 0.0 < herd.milking_share_of_herd < 1.0


def test_explanations_walk_the_backward_math_with_the_plans_own_numbers() -> None:
    """The recommendation narrative quotes numbers a farmer can check against
    the report's own fields: the curve's peak, the dry months, the monthly
    calvings, the breeding herd, and the mixed-stage purchase rationale."""
    report = build_milk_plan(_murrah(), 1000.0)
    text = " ".join(report.explanations)
    assert len(report.explanations) >= 6

    curve = report.curve
    assert f"~{curve.peak_daily_litres:.1f} L/day" in text
    assert f"month {curve.peak_month_of_lactation}" in text
    assert "declining" in text
    # Dry-off stated in the curve's own terms.
    assert "DRY" in text or "dry" in text
    assert f"~{report.herd.dry_months_per_cycle:.0f} month(s)" in text
    # Target → calvings → herd chain with the plan's own figures: the seed
    # division AND the plan's measured calvings, with the gap named (calf
    # milk / attrition) — the arithmetic must close on a calculator.
    monthly_target = 1000.0 * 30.44
    seed = monthly_target / report.curve.lactation_litres
    assert f"~{seed:,.1f} fresh calvings" in text
    assert f"actually runs ~{report.herd.calvings_per_month:,.1f} calvings" in text
    assert "calf" in text  # the seed gap is disclosed, not silently absorbed
    assert f"~{report.herd.breeding_does:,.0f}" in text
    assert f"({report.herd.milking_share_of_herd:.0%})" in text
    # The purchase rationale: mixed lactation stages, staged over the ramp.
    assert "MIXED lactation stages" in text


def test_explanations_say_no_purchases_when_the_herd_already_covers_it() -> None:
    report = build_milk_plan(_murrah(), 200.0)
    assert report.herd.breeding_does < 60.0  # 200 L/day fits inside the foundation
    assert report.purchases == []
    assert any("No purchases needed" in line for line in report.explanations)


def test_milking_plus_dry_equals_breeding_every_month() -> None:
    report = build_milk_plan(_murrah(), 500.0)
    for row in report.projection:
        assert row.milking_does + row.dry_does == pytest.approx(row.breeding_does, abs=1e-6)
        assert row.gap_daily_litres == pytest.approx(
            row.target_daily_litres - row.projected_daily_litres, abs=1e-9
        )
        # The breeding calendar is populated every month once steady.
        assert row.ai_services >= 0.0
        assert row.freshenings >= 0.0


def test_ramp_stages_purchases_and_ramps_the_tank() -> None:
    report = build_milk_plan(_murrah(), 1000.0, ramp_months=6)
    assert [p.month for p in report.purchases] == [1, 2, 3, 4, 5, 6]
    counts = {p.count for p in report.purchases}
    assert len(counts) == 1  # evenly staged
    early = [m.projected_daily_litres for m in report.projection[:3]]
    assert all(litres < 1000.0 for litres in early)
    # The full tranche lands by the end of the ramp.
    assert report.projection[5].projected_daily_litres > early[0]
    assert report.achievable is True


def test_year_round_mode_sizes_for_the_seasonal_trough() -> None:
    average = build_milk_plan(_murrah(), 1000.0)
    trough = build_milk_plan(_murrah(), 1000.0, hold_year_round=True)
    # Holding the target through the ~0.86x summer trough needs a bigger herd
    # and overshoots the annual average.
    assert trough.herd.breeding_does > average.herd.breeding_does
    assert trough.steady_average_daily_litres > 1000.0
    # In hold mode the design herd already IS the year-round herd: the field
    # must not divide by the trough multiplier a second time.
    assert trough.herd.herd_for_year_round_target == pytest.approx(
        trough.herd.breeding_does, rel=0.05
    )
    # In average mode the field is the (larger) herd that would hold the
    # trough.
    assert average.herd.herd_for_year_round_target > average.herd.breeding_does
    # Every steady month in year-round mode meets the target.
    steady = trough.projection[24:]
    assert all(row.meets_target for row in steady)


def test_seasonal_band_matches_the_simulation_not_the_raw_multipliers() -> None:
    # The band scales the steady average by each multiplier's distance from
    # the multipliers' own mean (the Murrah vector averages 0.989, not 1.0),
    # so it must bracket the actual projection output, not double-shrink it.
    report = build_milk_plan(_murrah(), 1000.0)
    tail = report.projection[-24:]
    actual_low = min(m.projected_daily_litres for m in tail)
    assert report.herd.seasonal_low_daily_litres == pytest.approx(actual_low, rel=0.03)
    assert report.herd.seasonal_high_daily_litres >= report.steady_average_daily_litres


def test_short_hold_window_does_not_explode_into_over_purchase() -> None:
    # Regression: a 12-month projection with a 6-month ramp in hold mode used
    # to fall back to measuring the partial-herd ramp months and size a herd
    # several times larger than needed (848 purchases for a 1,000 L/day
    # target instead of ~150).
    short = build_milk_plan(
        _murrah(), 1000.0, ramp_months=6, projection_months=12, hold_year_round=True
    )
    reference = build_milk_plan(_murrah(), 1000.0, ramp_months=1, hold_year_round=True)
    assert short.herd.purchases_total < 2.0 * reference.herd.purchases_total
    assert max(m.projected_daily_litres for m in short.projection) < 1.6 * 1000.0


def test_short_projection_is_achievable_through_the_heifer_gap() -> None:
    # Culling starts month 13 but the first home-bred heifers graduate around
    # month 33 (gestation + breeding age): the replacement bridge must hold
    # the herd through that gap instead of letting the plan melt.
    report = build_milk_plan(_murrah(), 1000.0, projection_months=24)
    assert report.achievable is True
    assert report.herd.replacement_purchases_total > 0.0
    assert any("Replacement bridge" in note for note in report.notes)
    # The breeding herd never melts below 85% of its month-13 level.
    month13 = report.projection[12].breeding_does
    assert all(m.breeding_does >= 0.85 * month13 for m in report.projection[12:])


def test_pipeline_shortfall_relies_on_purchases_and_says_so() -> None:
    a = _murrah()
    a.culling.doe_cull_rate_annual = 0.60  # culling outruns the calf pipeline
    a.reproduction.sex_ratio_female = 0.1
    report = build_milk_plan(a, 1000.0)
    # The plan only closes by continuously buying replacements; that must be
    # stated, not silently certified.
    assert report.herd.replacement_purchases_total > 100.0
    assert any("Replacement bridge" in note for note in report.notes)


def test_projection_window_is_clamped_to_the_horizon() -> None:
    short = _murrah()
    short.meta.horizon_months = 24
    report = build_milk_plan(short, 400.0, projection_months=60)
    assert len(report.projection) == 24


def test_non_dairy_assumptions_are_rejected() -> None:
    meat_only = SimulationAssumptions()  # Osmanabadi: lactation_milk_litres = 0
    with pytest.raises(ValueError, match="dairy scenario"):
        build_milk_plan(meat_only, 100.0)


def test_ramp_past_the_projection_window_is_rejected() -> None:
    with pytest.raises(ValueError, match="ramp"):
        build_milk_plan(_murrah(), 100.0, ramp_months=12, projection_months=12)


def test_oversized_target_is_rejected_not_run() -> None:
    with pytest.raises(ValueError, match="head cap"):
        build_milk_plan(_murrah(), 10_000_000.0)


def test_notes_carry_the_breeding_lead_time() -> None:
    report = build_milk_plan(_murrah(), 100.0)
    joined = " ".join(report.notes)
    # Gestation 10 + ~1 month to conceive at 45% per service.
    assert "11 months" in joined
    assert "AI" in joined
    # The seasonality note explains the summer trough explicitly.
    assert any("summer" in note or "trough" in note for note in report.notes)


def test_open_foundation_delays_milk_but_still_plans() -> None:
    a = _murrah()
    a.herd.foundation_flock_state = "open"
    report = build_milk_plan(a, 1000.0)
    # Open does produce nothing until first calving (~12 months at Murrah
    # biology), but the bought-in in-milk animals ship from month 1.
    assert report.projection[0].projected_daily_litres > 0.0
    assert report.achievable is True


def test_surplus_starting_herd_needs_no_purchases() -> None:
    a = _murrah()
    a.herd.does = 400  # already above the ~170 needed for 1000 L/day
    report = build_milk_plan(a, 1000.0)
    assert report.purchases == []
    assert any("surplus" in note for note in report.notes)


def test_planner_matches_the_engine_on_a_shared_foundation() -> None:
    """The planner's simulator and the engine must agree on the same biology.

    Same 60-doe mixed foundation, no ramp purchases, and the replacement
    bridge off (the one deliberate divergence: the engine lets culling and
    mortality shrink the herd; the planner buys through it). With those
    aside, the two models' monthly milk must track each other through the
    foundation transient — pinning the overlay anchoring (a waiting doe at
    slot i is i months fresh) on both sides.
    """
    from app.simulation.engine import _run_core
    from app.simulation.milk_planner import _simulate

    a = _murrah()
    a.meta.horizon_months = 24
    # Neutral price (flat ₹1/L, no seasonality or growth) so the engine's
    # milk_revenue IS its litres; yield seasonality stays as configured.
    a.sales.milk_price_per_kg_fat = 0.0
    a.sales.milk_price_per_litre = 1.0
    a.sales.monthly_milk_price_multipliers = [1.0] * 12
    a.sales.annual_milk_price_growth_rate = 0.0
    engine = _run_core(a)
    planned = _simulate(a, float(a.herd.does), ramp_months=1, months=24, replacement_bridge=False)
    for plan_month, engine_month in zip(planned, engine.months, strict=True):
        assert plan_month.month == engine_month.month
        assert plan_month.breeding_does == pytest.approx(
            engine_month.open_does + engine_month.pregnant_does, abs=0.05
        )
        assert plan_month.milking_does == pytest.approx(engine_month.lactating_does, abs=0.05)
        assert plan_month.daily_litres == pytest.approx(engine_month.milk_revenue / 30.44, rel=0.02)


def test_degenerate_wood_curve_fails_loudly_not_by_zero_division() -> None:
    # Schema-valid peak days keep the curve computable; a degenerate direct
    # call must raise, never ZeroDivisionError.
    with pytest.raises(ValueError, match="degenerated"):
        monthly_milk_curve(2400.0, 10, shape="wood", peak_day=1e-5)
    with pytest.raises(ValueError):
        # Below the schema floor: pydantic rejects it at validation time.
        SimulationAssumptions(sales={"lactation_milk_litres": 2400.0, "milk_peak_day": 0.5})


def test_ai_lead_note_explains_gestational_attrition() -> None:
    report = build_milk_plan(_murrah(), 1000.0)
    joined = " ".join(report.notes)
    assert "lost to culling and mortality during gestation" in joined


# --- API ------------------------------------------------------------------------


def test_milk_planner_api_contract_exists() -> None:
    from app.api.planner import router

    paths = {route.path for route in router.routes}
    assert "/api/planner/milk-plan" in paths


async def test_milk_planner_api_end_to_end(client) -> None:
    from app.simulation.defaults import get_preset

    from .conftest import owner_with_farm

    headers = await owner_with_farm(client)
    document = {
        "assumptions": get_preset("murrah_dairy").model_dump(),
        "daily_target_litres": 1000.0,
        "ramp_months": 1,
        "projection_months": 24,
    }
    resp = await client.post("/api/planner/milk-plan", json=document, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["achievable"] is True
    assert body["target_daily_litres"] == 1000.0
    assert 130.0 <= body["herd"]["breeding_does"] <= 300.0
    assert len(body["projection"]) == 24
    assert body["projection"][0]["target_daily_litres"] == 1000.0
    assert body["purchases"], "the 60-doe foundation cannot ship 1000 L/day alone"
    assert any("AI" in note for note in body["notes"])


async def test_milk_planner_api_rejects_non_dairy(client) -> None:
    from .conftest import owner_with_farm

    headers = await owner_with_farm(client)
    document = {
        "assumptions": SimulationAssumptions().model_dump(),
        "daily_target_litres": 500.0,
    }
    resp = await client.post("/api/planner/milk-plan", json=document, headers=headers)
    assert resp.status_code == 422
    assert "dairy" in resp.text


async def test_milk_planner_api_rejects_bad_targets(client) -> None:
    from .conftest import owner_with_farm

    headers = await owner_with_farm(client)
    base = {
        "assumptions": SimulationAssumptions(sales={"lactation_milk_litres": 2000.0}).model_dump()
    }
    resp = await client.post(
        "/api/planner/milk-plan",
        json={**base, "daily_target_litres": -5.0},
        headers=headers,
    )
    assert resp.status_code == 422
