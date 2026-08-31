"""Planner tests: feasibility, gap closing, risk scoring, and the API surface."""

import pytest

from app.simulation.assumptions import HerdEventAssumptions, SimulationAssumptions
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
def test_planner_api_contract_exists() -> None:
    from app.api.simulation import router

    paths = {route.path for route in router.routes}
    assert "/api/simulation/planner/plan" in paths


async def test_planner_api_end_to_end(client) -> None:
    from .conftest import owner_with_farm

    headers = await owner_with_farm(client)
    document = {
        "assumptions": {
            "meta": {"horizon_months": 36},
            "herd": {"does": 50, "bucks": 2, "max_breeding_does": 150},
            "sales": {"meat_price_per_kg": 400.0},
        },
        "targets": [{"month": 24, "animal_class": "male_grower", "count": 25}],
        "close_gaps": True,
        "risk_runs": 5,
    }
    resp = await client.post("/api/simulation/planner/plan", json=document, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["before"]["targets"][0]["requested"] == 25.0
    assert body["gaps_closed"] is True
    assert body["after"]["all_met"] is True
    assert body["recommended_purchases"]
    assert len(body["probabilities"]) == 1
    assert 0.0 <= body["probabilities"][0]["p_full"] <= 1.0


async def test_planner_api_rejects_targets_beyond_horizon(client) -> None:
    from .conftest import owner_with_farm

    headers = await owner_with_farm(client)
    document = {
        "assumptions": {"meta": {"horizon_months": 12}},
        "targets": [{"month": 40, "animal_class": "male_grower", "count": 5}],
    }
    resp = await client.post("/api/simulation/planner/plan", json=document, headers=headers)
    assert resp.status_code == 422
    assert "beyond the simulation horizon" in resp.text


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
async def test_planner_api_rejects_event_cap_overflow_with_422(client) -> None:
    """460 existing purchases + 50 sale targets exceeds the 500-event cap.

    The planner builds the combined event document inside the offloaded
    worker; that ValidationError must surface as a clean 422, never an
    unhandled 500.
    """
    from .conftest import owner_with_farm

    headers = await owner_with_farm(client)
    document = {
        "assumptions": {
            "meta": {"horizon_months": 60},
            "herd": {"does": 50, "bucks": 2},
            "events": [
                {"month": m, "kind": "purchase", "animal_class": "doe", "count": 1}
                for m in range(1, 61)
                for _ in range(8)  # 480 purchases across the horizon
            ][:460],
        },
        "targets": [{"month": 48, "animal_class": "male_grower", "count": 5} for _ in range(50)],
    }
    resp = await client.post("/api/simulation/planner/plan", json=document, headers=headers)
    assert resp.status_code == 422, resp.text
    assert "cannot be represented" in resp.text


async def test_planner_api_survives_schema_max_target_count(client) -> None:
    """count=100_000 validates as input; the gap-closer must split its
    purchase into schema-legal chunks instead of crashing mid-iteration."""
    from .conftest import owner_with_farm

    headers = await owner_with_farm(client)
    document = {
        "assumptions": {
            "meta": {"horizon_months": 36},
            "herd": {"does": 50, "bucks": 2, "max_breeding_does": 0},
        },
        "targets": [{"month": 30, "animal_class": "male_grower", "count": 100_000}],
        "close_gaps": True,
        "risk_runs": 0,
    }
    resp = await client.post("/api/simulation/planner/plan", json=document, headers=headers)
    assert resp.status_code in (200, 422), resp.text  # never a 500
    if resp.status_code == 200:
        for purchase in resp.json()["recommended_purchases"]:
            assert purchase["count"] <= 100_000


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
