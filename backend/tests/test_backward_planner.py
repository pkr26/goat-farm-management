"""Engine tests for backward planning (calendar-dated targets → to-do list).

Covers the calendar math, the stage plan's consistency with the underlying
engine run, action/chain derivation, horizon extension, and the deliberate
rejections (targets at/before the plan start, past the 20-year ceiling).
"""

import pytest

from app.simulation import SimulationAssumptions
from app.simulation.backward_planner import (
    PlannerTarget,
    build_backward_plan,
    month_label,
    month_offset,
    parse_year_month,
)
from app.simulation.vocabulary import BUFFALO_NOUNS


def _anchored(**overrides: object) -> SimulationAssumptions:
    assumptions = SimulationAssumptions()
    assumptions.meta.start_year_month = "2026-01"
    for key, value in overrides.items():
        path, _, leaf = key.rpartition(".")
        obj: object = assumptions
        for part in path.split("."):
            if part:
                obj = getattr(obj, part)
        setattr(obj, leaf, value)
    return assumptions


# ---------------------------------------------------------------------------
# Calendar math
# ---------------------------------------------------------------------------
def test_parse_year_month_rejects_malformed_dates() -> None:
    assert parse_year_month("2027-01") == (2027, 1)
    assert parse_year_month("2026-12") == (2026, 12)
    for bad in ("2027-13", "2027-00", "27-01", "2027-1", "202701", "1899-12", "2201-01"):
        with pytest.raises(ValueError, match="not a real YYYY-MM"):
            parse_year_month(bad)


def test_month_offset_and_label_round_trip_across_year_boundaries() -> None:
    assert month_offset("2026-09", "2026-10") == 2
    assert month_offset("2026-09", "2027-01") == 5
    assert month_offset("2026-12", "2027-01") == 2
    assert month_offset("2026-01", "2046-01") == 241  # past the ceiling
    for month in range(2, 60):
        assert month_offset("2026-09", month_label("2026-09", month)) == month


def test_month_offset_rejects_targets_at_or_before_plan_start() -> None:
    with pytest.raises(ValueError, match="at or before the plan start"):
        month_offset("2026-09", "2026-08")
    with pytest.raises(ValueError, match="at or before the plan start"):
        month_offset("2026-09", "2026-09")


# ---------------------------------------------------------------------------
# The backward plan itself
# ---------------------------------------------------------------------------
def test_stage_plan_matches_the_engine_and_ends_at_the_last_target() -> None:
    report = build_backward_plan(
        _anchored(**{"herd.max_breeding_does": 150}),
        [PlannerTarget(year_month="2028-01", animal_class="male_grower", count=120.0)],
    )
    assert report.start_year_month == "2026-01"
    # The horizon the caller sent (120) covers the target at month 25 already.
    assert report.horizon_months == 120
    # Stage rows run month 1 (the anchor month itself) through the sale month.
    assert len(report.stage_plan) == 25
    assert report.stage_plan[0].year_month == "2026-01"
    assert report.stage_plan[-1].year_month == "2028-01"
    # Row 1 is end-of-month state (the engine's reporting convention): the
    # foundation's 50 does redistribute across open/pregnant/lactating during
    # the month and mortality takes a fraction, so allow tight tolerance.
    first = report.stage_plan[0]
    doe_mass = first.open_does + first.pregnant_does + first.lactating_does
    assert 45.0 <= doe_mass <= 50.5
    assert first.total_head > 52.0  # month-1 births with no sales yet
    # Purchases flow through the stage plan.
    assert any(row.purchases_head > 0.0 for row in report.stage_plan)


def test_horizon_is_extended_to_cover_a_distant_target() -> None:
    assumptions = _anchored(**{"meta.horizon_months": 12})
    report = build_backward_plan(
        assumptions,
        [PlannerTarget(year_month="2028-01", animal_class="male_grower", count=20.0)],
    )
    assert assumptions.meta.horizon_months == 12  # caller's object untouched
    assert report.horizon_months == 25  # extended, not capped


def test_target_beyond_twenty_years_is_rejected() -> None:
    with pytest.raises(ValueError, match="beyond the simulation's 20-year horizon"):
        build_backward_plan(
            _anchored(),
            [PlannerTarget(year_month="2047-01", animal_class="male_grower", count=5.0)],
        )


def test_actions_and_chains_cover_the_full_backward_story() -> None:
    report = build_backward_plan(
        _anchored(**{"herd.max_breeding_does": 150}),
        [
            # Month 13 is one month before the earliest a fresh purchase can
            # put a grower in the sale pool (month 14) — biologically
            # impossible, and its breeding window predates the plan.
            PlannerTarget(year_month="2027-01", animal_class="male_grower", count=30.0),
            PlannerTarget(year_month="2027-06", animal_class="male_grower", count=30.0),
            PlannerTarget(year_month="2028-01", animal_class="male_grower", count=120.0),
        ],
    )
    kinds = {action.kind for action in report.actions}
    # Purchases exist because the targets exceed the 50-doe foundation.
    assert {"purchase", "breed", "expect_births", "sell"} <= kinds
    # Actions are dated and sorted by month.
    months = [action.month for action in report.actions]
    assert months == sorted(months)
    sell = [a for a in report.actions if a.kind == "sell"]
    assert [a.year_month for a in sell] == ["2027-01", "2027-06", "2028-01"]

    # One chain per target, earliest link first: bred → kidded → born → sold.
    assert [chain.year_month for chain in report.chains] == [
        "2027-01",
        "2027-06",
        "2028-01",
    ]
    chain = report.chains[2]
    labels = [step.label for step in chain.steps]
    assert "breedable doe(s) bred (retries until pregnant)" in labels[0]
    assert chain.steps[-1].year_month == "2028-01"
    assert chain.steps[-1].quantity == 120
    # The biology only ever adds head as you walk backward within a link:
    # sold <= born (mortality + the other sex's share), kidded <= bred (some
    # services miss), and born >= kidded for a >1 litter.
    bred, kidded, born, sold = (step.quantity for step in chain.steps)
    assert sold <= born and kidded <= bred and born >= kidded

    # The infeasible month-13 target keeps its chain, flagged not achievable,
    # and its breeding window is called out as already missed; the later two
    # targets close with purchases.
    assert report.chains[0].achievable is False
    assert report.chains[1].achievable is True
    assert report.chains[2].achievable is True
    assert any("already behind you" in action.detail for action in report.actions)


def test_requirement_chain_math_is_consistent_with_biology() -> None:
    """120 male growers at ~7 months, Osmanabadi defaults: the chain must
    reproduce litter size, sex share, survival and conception exactly."""
    report = build_backward_plan(
        _anchored(),
        [PlannerTarget(year_month="2028-01", animal_class="male_grower", count=120.0)],
    )
    chain = report.chains[0]
    r = report.plan.before.targets[0]  # type: ignore[attr-defined]
    assert r.requested == 120.0
    bred, kidded, born, sold = (step.quantity for step in chain.steps)
    # kids_per_doe = litter × (1 − stillbirth) = 1.6 × 0.98
    assert kidded == -(-born // 1.568)  # ceil division against the per-doe yield
    # Defaults allow unlimited service retries, so every bred doe eventually
    # kids (some on a later service): bred == kidded.
    assert bred == kidded
    assert sold == 120
    # Born head ≥ sold head (mortality and the other sex's share add animals).
    assert born >= 120


def test_doe_targets_get_the_pool_policy_not_a_purchase_chain() -> None:
    report = build_backward_plan(
        _anchored(),
        [PlannerTarget(year_month="2027-06", animal_class="doe", count=10.0)],
    )
    chain = report.chains[0]
    assert len(chain.steps) == 1
    assert "breeding pool" in chain.steps[0].label
    assert "never buys breeding stock" in chain.explanation
    assert report.plan.recommended_purchases == []


def test_purchase_recommendation_explains_its_lead_time() -> None:
    """The buy action must justify its month with the biology: settling,
    gestation and growth — the numbers the requirement chains show."""
    report = build_backward_plan(
        _anchored(**{"herd.max_breeding_does": 150}),
        [PlannerTarget(year_month="2028-01", animal_class="male_grower", count=120.0)],
    )
    purchase = next(a for a in report.actions if a.kind == "purchase")
    assert "1 month(s) to settle" in purchase.detail
    assert "kids ~5 months later" in purchase.detail
    assert "chain from cheque to sale" in purchase.detail
    # And the one-line recommendation ties the purchases to the targets.
    assert any(note.startswith("Recommendation in one line") for note in report.notes)


def test_no_recommendation_note_when_the_plan_needs_no_purchases() -> None:
    report = build_backward_plan(
        _anchored(),
        [PlannerTarget(year_month="2028-01", animal_class="male_grower", count=5.0)],
    )
    assert report.plan.recommended_purchases == []
    assert not any("Recommendation in one line" in note for note in report.notes)


def test_buffalo_nouns_reach_chains_and_actions() -> None:
    report = build_backward_plan(
        _anchored(),
        [PlannerTarget(year_month="2028-01", animal_class="male_grower", count=20.0)],
        nouns=BUFFALO_NOUNS,
    )
    assert "milking buffalo" in report.chains[0].explanation
    assert any("milking buffalo" in action.headline for action in report.actions)


def test_notes_lead_with_the_plan_window() -> None:
    report = build_backward_plan(
        _anchored(),
        [PlannerTarget(year_month="2027-06", animal_class="male_grower", count=5.0)],
    )
    assert report.notes[0].startswith("Plan runs 2026-01 → 2027-06 (18 months).")
