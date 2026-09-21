"""Planner tests: feasibility, gap closing, risk scoring, and the API surface."""

import pytest

from app.simulation.assumptions import (
    HerdEventAssumptions,
    ParityMultipliers,
    SimulationAssumptions,
)
from app.simulation.engine import _run_core
from app.simulation.market import bakrid_festival_months
from app.simulation.planner import (
    SaleTarget,
    build_plan_report,
    close_gaps,
    evaluate_plan,
    plan_probabilities,
)


def base_assumptions(**overrides: object) -> SimulationAssumptions:
    a = SimulationAssumptions()
    a.herd.max_breeding_does = 150
    for key, value in overrides.items():
        setattr(a, key, value)
    return a


def test_bakrid_months_from_default_start() -> None:
    # Default start 2026-08: Bakrid 2027-05-17 is simulation month 10, and the
    # table's dates drift ~10.5 days earlier per year through the horizon.
    months = bakrid_festival_months("2026-08", 120)
    assert months[0] == 10
    assert months == sorted(set(months))
    assert all(1 <= m <= 120 for m in months)


def test_bakrid_months_respect_horizon_and_unknown_years() -> None:
    assert bakrid_festival_months("2026-08", 9) == []  # first Bakrid is month 10
    assert bakrid_festival_months("2026-08", 10) == [10]
    # Far-future starts have no embedded calendar: no festival, no guess.
    assert bakrid_festival_months("2200-01", 120) == []


def test_seasonality_defaults_average_exactly_one() -> None:
    a = SimulationAssumptions()
    mults = a.sales.monthly_meat_price_multipliers
    assert sum(mults) / 12.0 == pytest.approx(1.0, abs=1e-12)
    # Monsoon trough, winter peak: the documented Indian market shape.
    assert min(mults[5:9]) < 1.0 < max(mults[10:12]) + max(mults[0:2])


def test_festival_months_beyond_horizon_are_pruned_not_rejected() -> None:
    # Loading defaults (festivals pre-filled for 120 months) and shrinking the
    # horizon must stay a valid document — a festival past the horizon simply
    # is not part of that plan. Validation runs at parse time, so this checks
    # the model_validate path the API actually uses.
    document = SimulationAssumptions().model_dump(mode="json")
    document["sales"]["festival_sale_months"] = [10, 22, 33]
    document["meta"]["horizon_months"] = 24
    a = SimulationAssumptions.model_validate(document)
    assert a.sales.festival_sale_months == [10, 22]
    document["sales"]["festival_sale_months"] = [10, 10]
    with pytest.raises(Exception, match="duplicates"):
        SimulationAssumptions.model_validate(document)


# ---------------------------------------------------------------------------
# Festival-month re-anchoring (P2-8, 2026-09-20 audit): auto-fill only ran
# when festival_sale_months was None, so a horizon shrink materialized a
# pruned (possibly EMPTY) list that extending the horizon never restored.
# ---------------------------------------------------------------------------


def test_festival_months_round_trip_120_24_120_restores_the_full_list() -> None:
    """Shrinking the decade plan to 24 months prunes the Bakrid list to its
    first two months; extending back to 120 must re-derive the FULL calendar,
    not carry the 2-month prefix forever."""
    full = SimulationAssumptions().sales.festival_sale_months
    assert len(full) == 10  # the default decade start covers ten Bakrids

    document = SimulationAssumptions().model_dump(mode="json")
    document["meta"]["horizon_months"] = 24
    shrunk = SimulationAssumptions.model_validate(document)
    assert shrunk.sales.festival_sale_months == full[:2]

    regrown = shrunk.model_dump(mode="json")
    regrown["meta"]["horizon_months"] = 120
    extended = SimulationAssumptions.model_validate(regrown)
    assert extended.sales.festival_sale_months == full


def test_festival_months_empty_list_re_anchors_when_the_horizon_grows() -> None:
    """The residual P2-8 bug: starting 2039-02, the first Bakrid inside a
    12-month horizon does not exist (Bakrid 2039 fell in January, 2040 in
    December — simulation month 23). Materializing [] froze that "no Bakrid
    this year" into the scenario, and extending the horizon carried it into
    the decade as "no Bakrid, ever". The empty calendar now stays None — the
    auto-fill stays live — so extending the horizon re-derives the decade's
    festival months."""
    short = SimulationAssumptions.model_validate(
        {"meta": {"start_year_month": "2039-02", "horizon_months": 12}}
    )
    # NOT []: an empty calendar is never materialized, because a stored []
    # is the user's explicit "no festival months" and must be respected.
    assert short.sales.festival_sale_months is None

    decade_document = short.model_dump(mode="json")
    decade_document["meta"]["horizon_months"] = 120
    decade = SimulationAssumptions.model_validate(decade_document)
    assert decade.sales.festival_sale_months == bakrid_festival_months("2039-02", 120)
    assert decade.sales.festival_sale_months  # non-empty: the auto-fill fired


def test_genuinely_festival_free_horizon_and_explicit_empty_list() -> None:
    """A horizon whose FULL Bakrid calendar is empty keeps the auto-fill
    live (None, not []) — 2039-02 + 22 months ends before the month-23
    Bakrid of 2040. An explicitly empty list, by contrast, is the user's
    "no festival months" and is respected verbatim on ANY horizon."""
    assert bakrid_festival_months("2039-02", 22) == []
    validated = SimulationAssumptions.model_validate(
        {"meta": {"start_year_month": "2039-02", "horizon_months": 22}}
    )
    assert validated.sales.festival_sale_months is None
    # Round-tripping the None keeps the auto-fill live for a later extension.
    again = SimulationAssumptions.model_validate(validated.model_dump(mode="json"))
    assert again.sales.festival_sale_months is None

    # The explicit disable is honored — including on horizons that DO contain
    # festivals; it must never be re-expanded by the re-anchor rule.
    document = SimulationAssumptions().model_dump(mode="json")
    document["sales"]["festival_sale_months"] = []
    disabled = SimulationAssumptions.model_validate(document)
    assert disabled.sales.festival_sale_months == []


def test_customized_non_prefix_festival_list_is_untouched() -> None:
    """A hand-set list that is NOT a prefix of the Bakrid chain is a
    customization, not an auto-derived calendar: re-anchoring must leave it
    exactly as the planner set it ([3] vs the chain's first month 10)."""
    document = SimulationAssumptions().model_dump(mode="json")
    document["sales"]["festival_sale_months"] = [3]
    validated = SimulationAssumptions.model_validate(document)
    assert validated.sales.festival_sale_months == [3]


def test_evaluate_plan_reports_honest_shortfalls() -> None:
    a = base_assumptions()
    targets = [SaleTarget(month=22, animal_class="male_grower", count=30.0)]
    evaluation = evaluate_plan(a, targets)
    fill = evaluation.targets[0]
    assert 0.0 < fill.filled < 30.0
    assert fill.shortfall == pytest.approx(30.0 - fill.filled)
    assert not fill.met and not evaluation.all_met
    assert fill.price_per_head > 0.0


def test_planner_owns_sale_events_no_double_selling() -> None:
    # A stale sale event in the assumptions must not double-sell the pool the
    # planner targets: the planner replaces sales, keeps purchases.
    a = base_assumptions()
    a.events = [
        HerdEventAssumptions(month=22, kind="sale", animal_class="male_grower", count=5),
        HerdEventAssumptions(month=3, kind="purchase", animal_class="doe", count=20),
    ]
    targets = [SaleTarget(month=22, animal_class="male_grower", count=10.0)]
    from app.simulation.planner import _plan_assumptions

    planned = _plan_assumptions(a, targets)
    sales = [e for e in planned.events if e.kind == "sale"]
    purchases = [e for e in planned.events if e.kind == "purchase"]
    assert len(sales) == 1 and sales[0].count == 10.0
    assert len(purchases) == 1 and purchases[0].month == 3


def test_close_gaps_backfills_with_purchases_until_met() -> None:
    a = base_assumptions()
    targets = [SaleTarget(month=24, animal_class="male_grower", count=25.0)]
    purchases, evaluation, closed, notes = close_gaps(a, targets)
    assert closed, notes
    assert evaluation.targets[0].filled >= 25.0 - 0.5
    assert purchases, "a shortfall of this size needs does"
    assert all(p.kind == "purchase" and p.animal_class == "doe" for p in purchases)
    # The purchase lands early enough for the offspring to reach the class.
    sale_age = a.growth.sale_age_months
    for target in targets:
        for purchase in purchases:
            assert purchase.month <= target.month - sale_age - a.reproduction.gestation_months


def test_close_gaps_refuses_impossible_targets_without_runaway() -> None:
    a = base_assumptions()
    # Month 6 grower sale: kids cannot exist yet from any purchase.
    targets = [SaleTarget(month=6, animal_class="male_grower", count=25.0)]
    purchases, _evaluation, closed, notes = close_gaps(a, targets)
    assert not closed
    assert purchases == []
    assert any("impossible" in note for note in notes)


def test_close_gaps_zero_progress_guard_sale_age_boundary() -> None:
    """P2-9 (2026-09-20 audit): at sale_age_months=6 the male-grower chain is
    a single month wide, so the marginal mirror promises yield the engine
    never delivers — every pass used to pile a full extra round of does onto
    the same permanent shortfall and the plan read as "buy ~300 does". The
    zero-progress guard must catch even the first phantom round: zero doe
    purchases, gaps open, and a note explaining purchases cannot back the
    targets."""
    a = base_assumptions()
    a.growth.sale_age_months = 6
    purchases, evaluation, closed, notes = close_gaps(
        a, [SaleTarget(month=24, animal_class="male_grower", count=25.0)]
    )
    assert purchases == []  # not one doe bought for a target no doe can back
    assert not closed
    assert evaluation.targets[0].shortfall > 0.0
    assert any("Purchases cannot back these targets" in note for note in notes)


def test_close_gaps_zero_progress_guard_no_sire_and_no_auto_purchase() -> None:
    """P2-9, sire form: with bucks=0 and auto_purchase_bucks=False no doe ever
    conceives, so purchased does close nothing — the same guard must stop the
    escalation instead of recommending a sterile herd of bought does."""
    a = base_assumptions()
    a.herd.bucks = 0
    a.herd.auto_purchase_bucks = False
    purchases, evaluation, closed, notes = close_gaps(
        a, [SaleTarget(month=24, animal_class="male_grower", count=25.0)]
    )
    assert purchases == []
    assert not closed
    assert evaluation.targets[0].shortfall > 0.0
    assert any("Purchases cannot back these targets" in note for note in notes)


def test_plan_probabilities_are_bounded_and_deterministic() -> None:
    a = base_assumptions()
    targets = [SaleTarget(month=24, animal_class="male_grower", count=25.0)]
    purchases, _, closed, _ = close_gaps(a, targets)
    assert closed
    first = plan_probabilities(a, targets, purchases, runs=25, seed=7)
    second = plan_probabilities(a, targets, purchases, runs=25, seed=7)
    assert [r.p_full for r in first] == [r.p_full for r in second]
    for risk in first:
        assert 0.0 <= risk.p_full <= 1.0
        assert risk.p_eighty >= risk.p_full


def test_build_plan_report_risk_pass_is_optional() -> None:
    a = base_assumptions()
    targets = [SaleTarget(month=24, animal_class="male_grower", count=25.0)]
    report = build_plan_report(a, targets, risk_runs=0)
    assert report.probabilities is None
    assert report.gaps_closed
    assert report.after is not None and report.after.all_met
    # The closed plan's NPV should be reported for both phases.
    assert report.before.npv != report.after.npv


def test_purchased_doe_settles_before_first_service() -> None:
    def assumptions(settling_months: int) -> SimulationAssumptions:
        a = base_assumptions()
        a.mortality.adult = 0.0
        a.herd.bucks = 3
        a.herd.auto_purchase_bucks = False  # isolate the doe purchase's effect
        a.herd.purchased_doe_settling_months = settling_months
        # Flat parity: the bought cohort must not shift the herd-wide parity
        # weighting (it would move every other doe's conception rate too).
        a.reproduction.parity_multipliers = ParityMultipliers(
            litter_size=[1.0], conception_rate=[1.0]
        )
        a.events = [HerdEventAssumptions(month=3, kind="purchase", animal_class="doe", count=10)]
        return a

    settling = _run_core(assumptions(1))
    immediate = _run_core(assumptions(0))
    without_purchase = assumptions(1)
    without_purchase.events = []
    base = _run_core(without_purchase)

    m3, m3_now, m3_base = settling.months[2], immediate.months[2], base.months[2]
    # She is in the herd from month 3 (fed, counted, mortal)...
    assert m3.purchases_head == 10.0
    assert m3.total_herd > m3_base.total_herd + 9.0
    # ...but with settling=1 she cannot conceive in her arrival month: the
    # pregnancy count matches the no-purchase run, while settling=0 lifts it
    # by roughly the bought cohort's conceptions (~10 x 0.85).
    assert m3.pregnant_does == pytest.approx(m3_base.pregnant_does, abs=1e-9)
    assert m3_now.pregnant_does > m3_base.pregnant_does + 5.0


def test_run_simulation_revalidates_mutated_assumptions() -> None:
    # Programmatic mutation bypasses field validators; the public entry must
    # refuse an incoherent document (negative equity) instead of running it.
    from pydantic import ValidationError

    bad = SimulationAssumptions()
    bad.finance.subsidy_fraction = 0.5  # 0.85 loan + 0.50 subsidy > 1
    with pytest.raises(ValidationError):
        _ = __import__("app.simulation.engine", fromlist=["run_simulation"]).run_simulation(bad)


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------
async def test_run_endpoint_returns_structured_event_fills(client) -> None:
    """The /run response carries the planner's structured fills end to end."""
    from .conftest import owner_with_farm

    headers = await owner_with_farm(client)
    document = {
        "meta": {"horizon_months": 24},
        "herd": {"does": 50, "bucks": 2, "max_breeding_does": 150},
        "events": [
            {"month": 22, "kind": "sale", "animal_class": "male_grower", "count": 30},
            {"month": 3, "kind": "purchase", "animal_class": "doe", "count": 10},
        ],
    }
    resp = await client.post("/api/simulation/run", json={"assumptions": document}, headers=headers)
    assert resp.status_code == 200, resp.text
    months = resp.json()["months"]
    sale_month = next(m for m in months if m["month"] == 22)
    fills = sale_month["event_fills"]
    assert len(fills) == 1
    fill = fills[0]
    assert fill["kind"] == "sale" and fill["animal_class"] == "male_grower"
    assert fill["requested"] == 30.0
    assert 0.0 < fill["filled"] < 30.0
    assert fill["shortfall"] == 30.0 - fill["filled"]
    purchase_month = next(m for m in months if m["month"] == 3)
    assert purchase_month["event_fills"][0]["kind"] == "purchase"
    assert purchase_month["event_fills"][0]["shortfall"] == 0.0


# ---------------------------------------------------------------------------
# Audit regression tests: plans that cannot be represented must 422, not 500
# ---------------------------------------------------------------------------
def test_oversized_purchase_is_chunked_not_rejected() -> None:
    from app.simulation.planner import _purchases_from

    events = _purchases_from({8: 165_000.0})
    assert [event.count for event in events] == [100_000.0, 65_000.0]
    assert all(event.month == 8 and event.animal_class == "doe" for event in events)


def test_earliest_supply_boundary_month_is_reported_not_skipped() -> None:
    """Growers first become purchasable at month 14 (defaults): month 13 must
    carry the impossible note; month 14 must close. The boundary used to be
    off by one, silently skipping month 13 with a misleading generic note."""
    a = SimulationAssumptions()
    impossible = build_plan_report(
        a, [SaleTarget(month=13, animal_class="male_grower", count=25.0)]
    )
    assert impossible.recommended_purchases == []
    assert any("impossible" in note for note in impossible.notes)

    possible = build_plan_report(a, [SaleTarget(month=14, animal_class="male_grower", count=25.0)])
    assert possible.gaps_closed
    assert possible.after is not None and possible.after.targets[0].met


def test_doe_sale_shortfall_gets_a_policy_note_not_silence() -> None:
    report = build_plan_report(
        SimulationAssumptions(), [SaleTarget(month=24, animal_class="doe", count=100.0)]
    )
    assert any("never buys breeding stock" in note for note in report.notes)
    assert report.recommended_purchases == []
