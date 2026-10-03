"""Independent decision-model cases from the October audit; no external calls."""

import math
from datetime import date

import pytest
from pydantic import ValidationError

from app.services.dashboard import next_bakrid_date
from app.services.simulation_calibration import _annual_fraction_from_exposure, _is_bakrid_month
from app.simulation import SimulationAssumptions, run_simulation
from app.simulation.assumptions import SubsidyReceipt
from app.simulation.backward_planner import (
    PlannerTarget,
    _ClassWindow,
    _requirement_chain,
)
from app.simulation.daily_ops import AnimalStartSpec, DailyOpsInput, DailyOpsParams, run_daily_ops
from app.simulation.engine import _run_core, male_weight_at_age
from app.simulation.finance import IRRIsolationUnsupported, assess_irr, irr, irr_roots, npv
from app.simulation.market import bakrid_festival_months, bakrid_occurrences
from app.simulation.subsidy import NLM_POLICY_VERSION
from app.simulation.vocabulary import GOAT_NOUNS


def test_half_year_cashflows_have_three_distinct_irrs_not_a_unique_loss() -> None:
    # In z=(1+r)^(-1/2), this cubic has roots 1.5, .9 and .8.
    # Annual rates are 1/z^2-1, independently calculable.
    flows = [-1.08, 3.27, -3.2, 1.0]
    times = [0.0, 0.5, 1.0, 1.5]
    expected = sorted(1.0 / z**2 - 1.0 for z in (1.5, 0.9, 0.8))
    roots = irr_roots(flows, times)
    assert roots == pytest.approx(expected, abs=1e-10)
    assert all(abs(npv(root, flows, times)) < 1e-12 for root in roots)
    assert irr(flows, times) is None
    assert assess_irr(flows, times).status == "multiple_roots"


def test_long_nonconventional_irr_is_indeterminate_and_conventional_case_is_solved() -> None:
    flows = [-100.0, *[(-1.0) ** n * 20.0 for n in range(30)]]
    times = [n / 12.0 for n in range(len(flows))]
    assert assess_irr(flows, times).status == "indeterminate"
    assert irr(flows, times) is None
    with pytest.raises(IRRIsolationUnsupported):
        irr_roots(flows, times)
    conventional = [-100.0, *[0.0] * 29, 150.0]
    assert irr(conventional, times) == pytest.approx(1.5 ** (12 / 30) - 1.0)
    assert assess_irr([1.0, 2.0], [0.0, 1.0]).status == "no_root"
    assert assess_irr([0.0, 0.0], [0.0, 1.0]).status == "indeterminate"
    assert assess_irr([-100.0, 100.0], [0.0, 0.0]).status == "indeterminate"


@pytest.mark.parametrize(
    "litter,share,stillbirth", [(1.0, 0.5, 0.0), (2.0, 0.5, 0.0), (1.6, 0.3, 0.1)]
)
def test_backward_live_births_kiddings_and_does_have_separate_units(
    litter: float, share: float, stillbirth: float
) -> None:
    a = SimulationAssumptions()
    a.reproduction.litter_size = litter
    a.reproduction.sex_ratio_female = 1.0 - share
    a.reproduction.stillbirth_rate = stillbirth
    a.reproduction.conception_rate = 1.0
    a.reproduction.max_services_before_cull = 1
    for field in ("kid_pre_weaning", "kid_post_weaning", "grower", "adult"):
        setattr(a.mortality, field, 0.0)
    target = PlannerTarget(year_month="2028-01", animal_class="male_grower", count=100.0)
    chain = _requirement_chain(a, target, 25, True, _ClassWindow(a, "male_grower"), GOAT_NOUNS)
    bred, kidded, live_born, sold = [step.quantity for step in chain.steps]
    assert live_born == math.ceil(100 / share)
    assert kidded == math.ceil(live_born / (litter * (1.0 - stillbirth)))
    assert bred == kidded
    assert live_born * share >= sold
    assert kidded * litter * (1.0 - stillbirth) >= live_born
    if litter == 2.0:
        assert (bred, kidded, live_born, sold) == (100, 100, 200, 100)


def test_unlimited_retry_policy_cannot_rescue_zero_conception_or_erase_lead_time() -> None:
    a = SimulationAssumptions()
    a.reproduction.max_services_before_cull = 0
    a.reproduction.conception_rate = 0.0
    target = PlannerTarget(year_month="2028-01", animal_class="male_grower", count=100.0)
    with pytest.raises(ValueError, match="no bred doe ever conceives"):
        _requirement_chain(a, target, 25, True, _ClassWindow(a, "male_grower"), GOAT_NOUNS)
    a.reproduction.conception_rate = 0.5
    chain = _requirement_chain(a, target, 25, True, _ClassWindow(a, "male_grower"), GOAT_NOUNS)
    assert chain.steps[0].quantity == 2 * chain.steps[1].quantity
    assert "later retries move the birth date" in chain.steps[0].label


def test_post_weaning_hazard_is_converted_to_three_month_probability() -> None:
    animal_months = 29.2
    phase = _annual_fraction_from_exposure(1, animal_months, period_months=3.0)
    annual = _annual_fraction_from_exposure(1, animal_months)
    assert phase == pytest.approx(1.0 - math.exp(-3.0 / animal_months))
    assert 1.0 - annual == pytest.approx((1.0 - phase) ** 4)


@pytest.mark.parametrize("field,age", [("male_kids", 1), ("male_weaners", 4), ("male_growers", 7)])
def test_opening_male_stock_uses_the_same_sex_weight_curve_as_purchase_and_exit(
    field: str, age: int
) -> None:
    a = SimulationAssumptions()
    a.herd.does = a.herd.bucks = 0
    setattr(a.herd, field, 10)
    expected = (
        10
        * male_weight_at_age(age, a.growth, a.growth.adult_weight_buck_kg)
        * a.sales.meat_price_per_kg
    )
    assert _run_core(a).stock_cost == pytest.approx(expected)


def test_two_festival_occurrences_survive_the_same_gregorian_year_and_calendar_consumers() -> None:
    assert bakrid_festival_months("2039-01", 12) == [1, 12]
    assert _is_bakrid_month(date(2039, 1, 5))
    assert _is_bakrid_month(date(2039, 12, 26))
    assert next_bakrid_date(date(2039, 1, 6)) == date(2039, 12, 26)
    entries = [row for row in bakrid_occurrences() if row.observed_on.year == 2039]
    assert len(entries) == 2
    assert all(row.projected and row.source.startswith("https://") for row in entries)


def test_explicit_local_festival_dates_override_month_projection_and_reanchor() -> None:
    data = SimulationAssumptions().model_dump()
    data["meta"].update(start_year_month="2044-01", horizon_months=12)
    data["sales"].update(
        festival_date_overrides=["2044-10-31"], festival_date_source="Declared local observation"
    )
    a = SimulationAssumptions.model_validate(data)
    assert a.sales.festival_sale_months == [10]
    assert any(
        "Declared local observation" in note
        for note in run_simulation(a, with_break_even=False).warnings
    )


def test_shift_feeding_follows_quarantine_release_and_reconciles_preparation() -> None:
    params = DailyOpsParams(
        adult_annual_mortality=0.0,
        kid_pre_weaning_mortality=0.0,
        kid_post_weaning_mortality=0.0,
        grower_annual_mortality=0.0,
        abortion_rate=0.0,
    )
    result = run_daily_ops(
        DailyOpsInput(
            start_date=date(2026, 10, 3),
            horizon_days=7,
            animals=[
                AnimalStartSpec(
                    tag="Q1", sex="F", bucket="QUARANTINE", age_months=12, days_in_bucket=44
                )
            ],
            params=params,
        )
    )
    first = result.days[0]
    assert any(move.to_bucket == "FOUNDATION" for move in first.moves)
    deliveries = [
        task
        for task in first.tasks
        if task.category == "FEED" and task.headline.startswith("Deliver")
    ]
    assert [(task.time, task.building) for task in deliveries] == [
        ("06:30", "QUARANTINE"),
        ("13:30", "FOUNDATION"),
        ("19:30", "FOUNDATION"),
    ]
    for day in result.days:
        delivered: dict[str, float] = {}
        for line in day.feeding:
            assert line.daily_kg == pytest.approx(
                line.morning_kg + line.afternoon_kg + line.night_kg
            )
            delivered[line.recipe] = delivered.get(line.recipe, 0.0) + line.daily_kg
        for recipe, prepared in day.feed_prepared_kg_by_recipe.items():
            assert prepared == pytest.approx(
                delivered.get(recipe, 0.0) + day.feed_unused_kg_by_recipe.get(recipe, 0.0),
                abs=0.001,
            )


@pytest.mark.parametrize(
    "females,males,expected,status",
    [
        (3, 1, 0.0, "ineligible"),
        (100, 5, 1_000_000.0, "estimate_only"),
        (500, 25, 5_000_000.0, "estimate_only"),
        (2000, 100, None, "unsupported_unit"),
    ],
)
def test_nlm_estimates_follow_published_subsidy_caps_without_booking_cash(
    females: int, males: int, expected: float | None, status: str
) -> None:
    a = SimulationAssumptions()
    a.finance.nlm_subsidy = True
    a.finance.nlm_unit_females = females
    a.finance.nlm_unit_males = males
    a.finance.nlm_eligible_capital_cost = 20_000_000.0
    result = run_simulation(a, with_break_even=False)
    assert result.metrics.subsidy_estimate_amount == expected
    assert result.metrics.subsidy_status == status
    assert result.metrics.subsidy_amount == 0.0
    assert result.metrics.subsidy_policy_version == NLM_POLICY_VERSION
    assert result.metrics.equity == pytest.approx(
        result.metrics.project_cost - result.metrics.loan_amount
    )
    assert all(row.subsidy_receipt == 0 for row in result.months)


def test_approved_nlm_installments_have_dated_cashflows_and_do_not_reduce_opening_funds() -> None:
    baseline = SimulationAssumptions()
    baseline.meta.horizon_months = 12
    before = run_simulation(baseline, with_break_even=False)
    funded = baseline.model_dump()
    funded["finance"].update(
        nlm_subsidy=True,
        nlm_unit_females=100,
        nlm_unit_males=5,
        nlm_eligible_capital_cost=2_000_000.0,
        nlm_approved_subsidy_amount=1_000_000.0,
        nlm_subsidy_receipts=[{"month": 3, "amount": 500_000.0}, {"month": 9, "amount": 500_000.0}],
    )
    after = run_simulation(SimulationAssumptions.model_validate(funded), with_break_even=False)
    assert after.metrics.equity == before.metrics.equity
    assert after.metrics.subsidy_amount == 1_000_000.0
    assert after.metrics.subsidy_status == "approved_scheduled"
    assert [row.subsidy_receipt for row in after.months] == [
        0.0,
        0.0,
        500_000.0,
        *[0.0] * 5,
        500_000.0,
        *[0.0] * 3,
    ]
    assert after.metrics.npv - before.metrics.npv == pytest.approx(
        500_000.0 / 1.12 ** (3 / 12) + 500_000.0 / 1.12 ** (9 / 12)
    )
    assert after.annual_pl[0].subsidy_receipt == 1_000_000.0
    assert after.annual_pl[0].ebitda == before.annual_pl[0].ebitda
    for old, new in zip(before.months, after.months, strict=True):
        assert new.net_cash_flow - old.net_cash_flow == pytest.approx(new.subsidy_receipt)


def test_unapproved_or_month_zero_nlm_funding_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SubsidyReceipt(month=0, amount=500_000.0)
    data = SimulationAssumptions().model_dump()
    data["finance"].update(nlm_subsidy=True, nlm_subsidy_receipts=[{"month": 1, "amount": 1.0}])
    with pytest.raises(ValidationError, match="require a positive approved"):
        SimulationAssumptions.model_validate(data)
