"""Goat-meat domain calibration tests (the model 3.3.0 wave).

Pins the dairy/buffalo purge and the Osmanabadi recalibration:
- retired dairy assumption keys are stripped on validation (API tolerance
  for stored scenarios and older frontends) while genuinely unknown keys
  still fail loudly;
- every breed preset is meat-mode (surplus-milk side-line at its 0 default);
- doe first service at 12 months (field puberty ~11.5 months), single-sourced
  with the operational profile;
- the stall_fed / semi_intensive growth regimes and the 60/90-day weaning
  policy;
- the NLM 50% capital-subsidy toggle and the DPR markdown export;
- parity-keyed litter expectations and the water-demand resource lines.

Pure unit tests: no DB, no conftest fixtures.
"""

import pytest
from pydantic import ValidationError

from app.models.species import GOAT_PROFILE
from app.simulation import (
    BREED_PRESETS,
    GrowthAssumptions,
    HerdAssumptions,
    MetaAssumptions,
    ReproductionAssumptions,
    SimulationAssumptions,
    run_simulation,
)
from app.simulation.assumptions import (
    SEMI_INTENSIVE_WEIGHT_CURVE,
    STALL_FED_WEIGHT_CURVE,
    HerdEventAssumptions,
)
from app.simulation.feed import DAYS_PER_MONTH
from app.simulation.planner import build_dpr_markdown


def _toy() -> SimulationAssumptions:
    """10 open does + 1 buck, 12-month horizon, flat prices."""
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(does=10, bucks=1, foundation_flock_state="open"),
    )
    a.sales.annual_livestock_price_growth_rate = 0.0
    return a


# --- dairy purge --------------------------------------------------------------


def test_retired_dairy_fields_are_stripped_on_validation() -> None:
    """Stored scenarios/older frontends carrying the retired dairy keys still
    validate: the purge must not brick saved documents."""
    payload = SimulationAssumptions().model_dump()
    payload["reproduction"]["sexed_semen_services"] = 2
    payload["reproduction"]["sexed_female_fraction"] = 0.9
    payload["reproduction"]["sexed_conception_multiplier"] = 0.85
    payload["sales"]["lactation_milk_litres"] = 110.0
    payload["sales"]["calf_milk_litres_per_day_per_calf"] = 2.5
    payload["sales"]["milk_price_per_kg_fat"] = 850.0
    payload["sales"]["milk_fat_pct"] = 6.8
    payload["sales"]["milk_persistency_monthly"] = 0.93
    payload["sales"]["milk_curve_shape"] = "wood"
    payload["sales"]["milk_peak_day"] = 65.0
    payload["sales"]["monthly_milk_yield_multipliers"] = [1.0] * 12
    payload["sales"]["monthly_milk_price_multipliers"] = [1.0] * 12
    payload["sales"]["annual_milk_price_growth_rate"] = 0.05
    payload["sales"]["male_calf_sell_at_birth_fraction"] = 0.9
    payload["sales"]["male_calf_price_per_head"] = 2500.0
    validated = SimulationAssumptions.model_validate(payload)
    # A retired dairy payload must not leak milk into a meat run.
    assert all(row.milk_revenue == 0.0 for row in run_simulation(validated).months)


def test_genuinely_unknown_keys_still_fail() -> None:
    payload = SimulationAssumptions().model_dump()
    payload["sales"]["typo_field"] = 1.0
    with pytest.raises(ValidationError):
        SimulationAssumptions.model_validate(payload)


def test_every_preset_is_meat_mode() -> None:
    for name, preset in BREED_PRESETS.items():
        assert preset.sales.milk_sale_litres_per_doe_day == 0.0, name
        # No preset stretches the lactation (weaning-to-rebreed) pool into a
        # dairy-length lactation anymore.
        assert preset.reproduction.lactation_months == 2, name


def test_surplus_milk_line_books_only_lactating_doe_days() -> None:
    a = _toy()
    a.sales.milk_sale_litres_per_doe_day = 1.0  # Osmanabadi mid (0.5-1.5 kg/d)
    result = run_simulation(a, with_break_even=False)
    month6 = result.months[5]  # first kiddings land in month 6
    assert month6.lactating_does > 0.0
    assert month6.milk_revenue == pytest.approx(
        month6.lactating_does * 1.0 * DAYS_PER_MONTH * a.sales.milk_price_per_litre,
        rel=1e-9,
    )
    # Before the first kidding there is no lactating pool and no milk sale.
    assert all(row.milk_revenue == 0.0 for row in result.months[:5])


# --- breeding age -------------------------------------------------------------


def test_first_service_age_is_twelve_months_everywhere() -> None:
    """Osmanabadi field puberty ~11.5 months: the operational floor and the
    projection default agree on 12 months (age at first kidding ~17+ months
    against the 19-20 month norm)."""
    assert GOAT_PROFILE.min_breeding_age_months == 12
    assert GOAT_PROFILE.min_breeding_weight_kg == 22.0  # weight gate unchanged
    assert SimulationAssumptions().reproduction.age_at_first_breeding_months == 12


# --- growth regime ------------------------------------------------------------


def test_growth_regime_defaults_to_the_stall_fed_curve() -> None:
    g = GrowthAssumptions()
    assert g.growth_regime == "stall_fed"
    assert list(g.weight_by_age_months) == list(STALL_FED_WEIGHT_CURVE)


def test_semi_intensive_regime_uses_the_cirg_field_curve() -> None:
    g = GrowthAssumptions(growth_regime="semi_intensive")
    assert list(g.weight_by_age_months) == list(SEMI_INTENSIVE_WEIGHT_CURVE)
    # CIRG field anchors: ~6.3 kg at 3 months, ~19.6 kg at 12.
    assert g.weight_by_age_months[3] == pytest.approx(6.3)
    assert g.weight_by_age_months[12] == pytest.approx(19.6)
    # The engine actually prices the lighter field animal: a 3-month weaner
    # is 6.3 kg, not the stall-fed 12.1 kg.
    from app.simulation.engine import weight_at_age

    assert weight_at_age(3, g, g.adult_weight_doe_kg) == pytest.approx(6.3)


def test_explicit_weight_table_wins_over_the_regime() -> None:
    table = [2.5 + 2.0 * m for m in range(13)]
    g = GrowthAssumptions(growth_regime="semi_intensive", weight_by_age_months=table)
    assert list(g.weight_by_age_months) == table


# --- weaning policy -----------------------------------------------------------


def test_weaning_policy_defaults_to_the_operational_60_days() -> None:
    r = ReproductionAssumptions()
    assert r.weaning_days == 60
    assert r.lactation_months == 2
    # The operational profile stays at 60 days (drives the ~8-month cycle).
    assert GOAT_PROFILE.weaning_days == 60


def test_weaning_90_derives_a_three_month_lactation_pool() -> None:
    r = ReproductionAssumptions(weaning_days=90)
    assert r.lactation_months == 3
    # An explicit lactation length always wins over the derivation.
    explicit = ReproductionAssumptions(weaning_days=90, lactation_months=4)
    assert explicit.lactation_months == 4
    # The longer weaning stretches the projected kidding cycle by a month, so
    # fewer second kiddings land inside the horizon (24 months: the second
    # kidding wave at month ~14 vs ~15 is the visible difference).
    toy24 = _toy()
    toy24.meta.horizon_months = 24
    base = run_simulation(toy24, with_break_even=False)
    conservative = _toy()
    conservative.meta.horizon_months = 24
    # The 90-day policy derives lactation_months=3 at construction (the
    # derivation fires at validation, not on attribute mutation).
    conservative.reproduction = ReproductionAssumptions(weaning_days=90)
    conservative_result = run_simulation(conservative, with_break_even=False)
    assert sum(row.births for row in conservative_result.months) < sum(
        row.births for row in base.months
    )


def test_weaning_days_rejects_unsupported_values() -> None:
    with pytest.raises(ValidationError):
        ReproductionAssumptions(weaning_days=75)  # type: ignore[arg-type]


# --- NLM subsidy + DPR export --------------------------------------------------


def test_nlm_toggle_only_does_not_promise_a_grant_for_the_default_ineligible_unit() -> None:
    a = SimulationAssumptions()
    a.finance.nlm_subsidy = True
    result = run_simulation(a, with_break_even=False)
    assert result.metrics.subsidy_amount == 0.0
    assert result.metrics.subsidy_estimate_amount == 0.0
    assert result.metrics.subsidy_status == "ineligible"
    assert result.metrics.equity == pytest.approx(
        result.metrics.project_cost - result.metrics.loan_amount
    )


def test_nlm_scheduled_young_purchases_do_not_invent_a_qualifying_breeder_unit() -> None:
    a = SimulationAssumptions(
        herd=HerdAssumptions(does=0, bucks=0, auto_purchase_bucks=False),
        events=[
            HerdEventAssumptions(month=1, kind="purchase", animal_class="female_grower", count=100),
            HerdEventAssumptions(month=1, kind="purchase", animal_class="buck", count=5),
        ],
    )
    a.finance.nlm_subsidy = True
    result = run_simulation(a, with_break_even=False)
    assert result.metrics.subsidy_status == "ineligible"
    assert result.metrics.subsidy_amount == 0.0
    # A declared proposed unit and eligible-item budget can produce a
    # conditional estimate; neither automatically creates approved funding.
    a.finance.nlm_unit_females = 100
    a.finance.nlm_unit_males = 5
    a.finance.nlm_eligible_capital_cost = 3_000_000.0
    declared = run_simulation(a, with_break_even=False)
    assert declared.metrics.subsidy_estimate_amount == 1_000_000.0
    assert declared.metrics.subsidy_amount == 0.0


def test_dpr_markdown_states_the_scheme_basis_and_figures() -> None:
    a = SimulationAssumptions()
    a.finance.nlm_subsidy = True
    result = run_simulation(a, with_break_even=True)
    markdown = build_dpr_markdown(a, result, plan_name="Navipet 50+2")
    assert markdown.startswith("# Detailed Project Report — Navipet 50+2")
    assert "National Livestock Mission" in markdown
    assert "50% back-ended capital subsidy" in markdown
    assert "100F+5M" in markdown and "500F+25M" in markdown
    assert f"₹{result.metrics.project_cost:,.0f}" in markdown
    assert f"₹{result.metrics.subsidy_amount:,.0f}" in markdown
    assert "| Year | Revenue |" in markdown
    assert "DSCR" in markdown and "NPV @ 12%" in markdown


def test_dpr_markdown_title_is_single_line_and_directional_free() -> None:
    """2026-09-16 audit INJ-1: a plan name must not forge DPR sections.

    Newlines (or U+2028/U+2029, or directional overrides) in the plan name
    used to land verbatim in the loan document, letting any planner-capable
    member inject fake "Means of finance" tables or formula-looking cells
    into an artifact banks read as official.
    """
    a = SimulationAssumptions()
    result = run_simulation(a, with_break_even=False)
    evil = (
        "Unit\n\n## Means of finance\n\n| Bank loan | \u20b90 |\n"
        '| Equity | \u20b99 crore |\n| Officer | =HYPERLINK("http://evil.example","c") |'
        "\u2028PUNE"
    )
    markdown = build_dpr_markdown(a, result, plan_name=evil)
    title_line = markdown.splitlines()[0]
    assert title_line == (
        "# Detailed Project Report — Unit ## Means of finance | Bank loan | "
        '₹0 | | Equity | ₹9 crore | | Officer | =HYPERLINK("http://evil.example","c") | PUNE'
    )
    # No line separator or carriage return from the payload survived, and
    # the forged content exists only inside the single title line (the
    # document's own genuine "## Means of finance" section is untouched).
    assert "\u2028" not in markdown
    assert "\r" not in markdown
    assert title_line.count("Means of finance") == 1
    # A whitespace-only name falls back to the default title.
    blank = build_dpr_markdown(a, result, plan_name="\r\n\u2028 ")
    assert blank.splitlines()[0] == "# Detailed Project Report — Goat rearing unit"


def test_dpr_markdown_without_nlm_omits_the_scheme_note() -> None:
    a = SimulationAssumptions()
    markdown = build_dpr_markdown(a, run_simulation(a, with_break_even=False))
    assert "National Livestock Mission" not in markdown
    assert "Promoter equity" in markdown


def test_nlm_subsidy_explanation_states_the_scheme() -> None:
    a = SimulationAssumptions()
    a.finance.nlm_subsidy = True
    result = run_simulation(a, with_break_even=False)
    subsidy = next(e for e in result.metric_explanations if e.key == "subsidy_amount")
    assert "National Livestock Mission" in subsidy.explanation
    assert subsidy.figures["nlm_subsidy"] == "on"


# --- litter expectations (item 6) ---------------------------------------------


def test_litter_expectation_is_parity_keyed_in_the_narrative() -> None:
    result = run_simulation(SimulationAssumptions(), with_break_even=False)
    trajectory = next(s for s in result.narrative_report if s.key == "herd_trajectory")
    text = "\n".join(trajectory.paragraphs)
    assert "35-40%" in text  # Osmanabadi twinning
    assert "5-13%" in text  # triplets
    # Mature litter ~1.6; maiden does run at the 0.85 parity multiplier.
    assert "1.60" in text
    assert f"{1.6 * 0.85:.2f}" in text  # ~1.36 maiden average
    assert "parity 1" in text


def test_litter_expectation_absent_for_single_kidding_breeds() -> None:
    a = SimulationAssumptions()
    a.reproduction.litter_size = 1.0
    result = run_simulation(a, with_break_even=False)
    trajectory = next(s for s in result.narrative_report if s.key == "herd_trajectory")
    assert "35-40%" not in "\n".join(trajectory.paragraphs)


# --- water demand (item 8) -----------------------------------------------------


def test_water_demand_sums_the_per_class_daily_rates() -> None:
    a = _toy()
    result = run_simulation(a, with_break_even=False)
    month = result.months[0]
    feed = a.feed
    expected = (
        (month.f_kids + month.m_kids) * feed.water_litres_kid_per_day
        + (month.f_weaners + month.m_weaners) * feed.water_litres_weaner_per_day
        + (month.f_growers + month.m_growers) * feed.water_litres_grower_per_day
        + (month.open_does + month.pregnant_does) * feed.water_litres_doe_per_day
        + month.lactating_does * feed.water_litres_lactating_doe_per_day
        + month.bucks * feed.water_litres_buck_per_day
    ) * DAYS_PER_MONTH
    assert month.water_litres == pytest.approx(expected, rel=1e-9)
    # The lactating-doe rate carries the 10-15 L/d Deccan-summer need.
    assert 10.0 <= feed.water_litres_lactating_doe_per_day <= 15.0


def test_water_appears_in_the_resource_plan_and_narrative() -> None:
    result = run_simulation(SimulationAssumptions(), with_break_even=False)
    summary = result.feed_summary
    assert len(summary.annual_water_litres) == 10
    assert summary.annual_water_litres[0] == pytest.approx(
        sum(row.water_litres for row in result.months[:12])
    )
    assert summary.peak_water_litres_per_day > 0.0
    costs = next(s for s in result.narrative_report if s.key == "cost_mix")
    text = "\n".join(costs.paragraphs)
    assert "Water demand" in text
    assert "10-15 L/day" in text
    assert costs.figures["peak_water_litres_per_day"] == pytest.approx(
        summary.peak_water_litres_per_day
    )
