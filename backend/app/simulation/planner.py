"""Demand-driven sale planning on top of the deterministic engine.

A farm plan states *what must be sold, when*: "30 male growers at Bakrid
2028". Biology decides whether that is possible — a goat sold in month T was
born around T-10, conceived around T-15 and needed her mother settled and
served before that. This module closes that loop in the direction a farmer
actually plans:

1. ``evaluate_plan`` runs the forward engine with the targets attached and
   reports, per target, how many animals the projected herd can actually
   supply (a sale can only take what exists — the engine books the shortfall
   honestly rather than creating animals).
2. ``close_gaps`` adds doe purchases early enough for their offspring to
   backfill each shortfall, iterating the forward run until every target
   fills (or reporting that it cannot).
3. ``plan_probabilities`` replays the closed plan under the Monte Carlo's
   correlated risk draws and reports the probability of fully meeting each
   target — a plan that only just closes on expected values fails about half
   the time, and that number belongs in the decision, not in a footnote.

The planner owns the sale half of ``assumptions.events``: existing sale
events are replaced by the targets (keeping them would double-sell the same
animals). Existing purchase events are kept and honoured.
"""

import math
import random
from collections import defaultdict, deque
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .assumptions import MAX_HEAD, HerdEventAssumptions, SimulationAssumptions
from .engine import _run_core
from .montecarlo import _apply_draws, _correlated_draws, _event_shock_path
from .results import EventFill

# Same vocabulary as HerdEventAssumptions.animal_class.
PlanAnimalClass = Literal[
    "doe",
    "buck",
    "female_kid",
    "male_kid",
    "female_weaner",
    "male_weaner",
    "female_grower",
    "male_grower",
]

# Classes whose availability a doe purchase can grow: everything raised on
# the farm. Doe/buck sale targets draw on the breeding pool itself, and
# buying breeding stock in order to cull-sell it is a guaranteed loss, so
# those targets are reported, never auto-backed by purchases.
_PURCHASE_BACKED_CLASSES = {
    "female_kid",
    "male_kid",
    "female_weaner",
    "male_weaner",
    "female_grower",
    "male_grower",
}

# Latest age (months) an animal of each class can be at sale time and still
# belong to that class. Kids span 0-2, weaners 3-5; growers (handled in the
# function, as their ceiling is sale_age - 1) span 6..sale_age-1.
_CLASS_MAX_AGE = {"kid": 2, "weaner": 5}
# Age (months) at which an animal enters each class: kid 0, weaner 3, grower 6.
_CLASS_ENTRY_AGE = {"kid": 0, "weaner": 3, "grower": 6}


def _class_max_age(animal_class: str, sale_age_months: int) -> int:
    base = animal_class.rsplit("_", maxsplit=1)[-1]
    if base == "grower":
        return max(sale_age_months - 1, 6)
    return _CLASS_MAX_AGE[base]


class SaleTarget(BaseModel):
    """One planned sale: ``count`` head of ``animal_class`` in ``month``."""

    model_config = ConfigDict(extra="forbid", strict=True)

    month: int = Field(ge=1)
    animal_class: PlanAnimalClass
    count: float = Field(gt=0.0, le=MAX_HEAD)


class TargetFill(BaseModel):
    """What the projected herd can actually supply for one target."""

    month: int
    animal_class: str
    requested: float
    filled: float
    shortfall: float
    price_per_head: float
    revenue: float
    met: bool  # filled >= requested (a half-head tolerance absorbs float drift)


class PlanEvaluation(BaseModel):
    """Forward-run feasibility of a whole target list, plus the plan's money."""

    targets: list[TargetFill]
    npv: float
    minimum_cash_balance: float
    minimum_cash_month: int
    additional_working_capital_required: float
    total_shortfall: float
    all_met: bool


class TargetRisk(BaseModel):
    month: int
    animal_class: str
    requested: float
    p_full: float  # P(filled >= requested - 0.5)
    p_eighty: float  # P(filled >= 0.8 x requested)


class PlanReport(BaseModel):
    """Planner output: feasibility, the gap-closing purchases, and risk."""

    before: PlanEvaluation
    after: PlanEvaluation | None  # None when the plan already closed (or nothing helped)
    recommended_purchases: list[HerdEventAssumptions]
    gaps_closed: bool
    # Probability (over the Monte Carlo's correlated draws) that each target
    # still fills once biology and prices vary. None when no risk runs were
    # requested.
    probabilities: list[TargetRisk] | None = None
    notes: list[str] = Field(default_factory=list)


def _plan_assumptions(
    assumptions: SimulationAssumptions,
    targets: list[SaleTarget],
    purchases: list[HerdEventAssumptions] | None = None,
) -> SimulationAssumptions:
    """Deep copy with the planner's event set: purchases kept, sales owned.

    The 500-event schema cap still applies; a plan that would exceed it fails
    validation loudly instead of being silently truncated.
    """
    variant = assumptions.model_copy(deep=True)
    kept_purchases = [event for event in variant.events if event.kind == "purchase"]
    sale_events = [
        HerdEventAssumptions(
            month=target.month,
            kind="sale",
            animal_class=target.animal_class,
            count=target.count,
        )
        for target in targets
    ]
    variant.events = [
        *kept_purchases,
        *(purchases or []),
        *sale_events,
    ]
    return SimulationAssumptions.model_validate(variant.model_dump())


def _match_target_fills(fills: list[EventFill], targets: list[SaleTarget]) -> list[TargetFill]:
    """Pair each sale fill with its target, in order, per (month, class).

    The engine groups events by month, so fills arrive chronologically even
    when targets were given out of order; the queue preserves the within-month
    ordering both sides share.
    """
    queues: dict[tuple[int, str], deque[EventFill]] = defaultdict(deque)
    for fill in fills:
        if fill.kind == "sale":
            queues[(fill.month, fill.animal_class)].append(fill)
    results: list[TargetFill] = []
    for target in targets:
        fill = queues[(target.month, target.animal_class)].popleft()
        results.append(
            TargetFill(
                month=target.month,
                animal_class=target.animal_class,
                requested=target.count,
                filled=fill.filled,
                shortfall=max(0.0, target.count - fill.filled),
                price_per_head=fill.price_per_head,
                revenue=fill.revenue if fill.filled > 0.0 else 0.0,
                met=fill.filled >= target.count - 0.5,
            )
        )
    return results


def _evaluate(
    assumptions: SimulationAssumptions,
    targets: list[SaleTarget],
    purchases: list[HerdEventAssumptions] | None = None,
) -> PlanEvaluation:
    core = _run_core(_plan_assumptions(assumptions, targets, purchases))
    fills = [fill for month in core.months for fill in month.event_fills]
    target_fills = _match_target_fills(fills, targets)
    return PlanEvaluation(
        targets=target_fills,
        npv=core.npv,
        minimum_cash_balance=core.minimum_cash_balance,
        minimum_cash_month=core.minimum_cash_month,
        additional_working_capital_required=core.additional_working_capital_required,
        total_shortfall=sum(item.shortfall for item in target_fills),
        all_met=all(item.met for item in target_fills),
    )


def evaluate_plan(assumptions: SimulationAssumptions, targets: list[SaleTarget]) -> PlanEvaluation:
    """Feasibility of the targets against the herd the assumptions project."""
    return _evaluate(assumptions, targets)


def _kidding_interval_months(assumptions: SimulationAssumptions) -> int:
    r = assumptions.reproduction
    return r.gestation_months + r.lactation_months + r.months_open_before_breeding


def _marginal_kids_per_doe(
    assumptions: SimulationAssumptions,
    target: SaleTarget,
    purchase_month: int,
) -> float:
    """Expected head of ``target.animal_class`` one bought doe adds by month T.

    Conservative by construction: only kiddings whose offspring are still
    young enough to sit in the class at T are counted, each contributing
    ``litter x sex share x birth-to-class survival``. Conception rate is
    omitted while kid mortality is charged at a full-year fraction; at the
    default biology these nearly cancel (measured ~6% under the engine's own
    marginal supply). Any residual error is corrected by the next forward
    pass in ``close_gaps``.
    """
    sale_age = assumptions.growth.sale_age_months
    class_max_age = _class_max_age(target.animal_class, sale_age)
    # A kid born in month b is aged (T - b) at sale month T and belongs to the
    # class while 0 <= T-b <= class_max_age (kids/weaners) or 6 <= T-b <= A-1
    # (growers, who are still pre-sale-age).
    earliest_birth = target.month - class_max_age
    # The engine's event draw sees the end of month T-1, where an animal born
    # in month B stands at age T-1-B. It is still in the class while that age
    # is <= class_max_age, i.e. B >= T-1-class_max_age... but it must also
    # have ENTERED the class by then (age >= entry age): B <= T-1-entry_age.
    # Kids: born T-3..T; weaners: T-5..T-3; growers: T-A+1..T-6.
    latest_birth = target.month - _CLASS_ENTRY_AGE[target.animal_class.rsplit("_", maxsplit=1)[-1]]
    if latest_birth < earliest_birth:
        return 0.0
    first_kidding = (
        purchase_month
        + assumptions.herd.purchased_doe_settling_months
        + 1
        + assumptions.reproduction.gestation_months
    )
    kiddings = 0
    kidding_month = first_kidding
    interval = _kidding_interval_months(assumptions)
    while kidding_month <= latest_birth:
        if kidding_month >= earliest_birth:
            kiddings += 1
        kidding_month += interval
    if kiddings == 0:
        return 0.0
    mort = assumptions.mortality
    age_at_sale_years = class_max_age / 12.0
    survival = (
        (1.0 - mort.kid_pre_weaning)
        * (1.0 - mort.kid_post_weaning)
        * math.pow(1.0 - mort.grower, max(0.0, age_at_sale_years - 0.5))
    )
    r = assumptions.reproduction
    female_share = (
        r.sex_ratio_female if target.animal_class.startswith("female") else 1.0 - r.sex_ratio_female
    )
    per_kidding = r.litter_size * (1.0 - r.stillbirth_rate) * female_share
    return kiddings * per_kidding * survival


def _purchase_month_for(target: SaleTarget, assumptions: SimulationAssumptions) -> int:
    """Latest sensible buy month whose doe's kids still reach the class by T.

    Backwards from the sale: class age at T, plus gestation, plus settling,
    plus one month of slack for the breeding cycle.
    """
    sale_age = assumptions.growth.sale_age_months
    class_max_age = _class_max_age(target.animal_class, sale_age)
    lead = (
        class_max_age
        + assumptions.reproduction.gestation_months
        + assumptions.herd.purchased_doe_settling_months
        + 1
    )
    return max(1, target.month - lead)


def close_gaps(
    assumptions: SimulationAssumptions,
    targets: list[SaleTarget],
    *,
    max_iterations: int = 8,
) -> tuple[list[HerdEventAssumptions], PlanEvaluation, bool, list[str]]:
    """Iteratively add doe purchases until every young-stock target fills.

    Returns ``(purchases, evaluation_with_purchases, gaps_closed, notes)``.
    Doe/buck sale targets are never auto-backed (the note says why); their
    shortfall stands.
    """
    notes: list[str] = []
    impossible_noted: set[int] = set()
    purchases_by_month: dict[int, float] = {}
    evaluation = _evaluate(assumptions, targets)

    for target, fill in zip(targets, evaluation.targets, strict=True):
        if fill.met:
            continue
        label = target.animal_class.replace("_", " ")
        if target.animal_class in _PURCHASE_BACKED_CLASSES:
            notes.append(
                f"Month {target.month}: selling {target.count:g} {label}(s) "
                f"exceeds the projected pool by {fill.shortfall:.1f} head."
            )
        else:
            # Policy exclusion, not a biology limit: say so, and do not let
            # the generic "move it later" advice fire for a cause it cannot fix.
            notes.append(
                f"Month {target.month}: selling {target.count:g} {label}(s) exceeds the "
                f"projected pool by {fill.shortfall:.1f} head. The planner never buys "
                "breeding stock to fuel cull sales — a doe bought for her lifetime of "
                "kids is worth far more than her meat. Sell fewer, or grow the pool by "
                "retaining more young stock in earlier months."
            )

    def _class_entry_age(animal_class: str) -> int:
        base = animal_class.rsplit("_", maxsplit=1)[-1]
        return {"kid": 0, "weaner": 3}.get(base, 6)

    # A target whose purchase window (class entry age + gestation + settling)
    # starts before month 1 is biologically impossible to back with purchases:
    # a doe bought at month 1 kids around month 7, and her offspring cannot
    # walk into the grower pool before month 13. Escalating purchases for such
    # a target never converges (each pass adds more does for the same
    # permanent shortfall), so it is reported with the earliest month a fresh
    # purchase could actually supply this class, and skipped.
    def _earliest_supply_month(animal_class: str) -> int:
        # +1 because a sale event fires at the START of month T, seeing pools
        # as of the END of month T-1: offspring born in month B first stand in
        # the class pool at the end of B + entry_age, so the earliest event
        # month that can draw them is B + entry_age + 1.
        return (
            1
            + assumptions.herd.purchased_doe_settling_months
            + assumptions.reproduction.gestation_months
            + _class_entry_age(animal_class)
            + 1
        )

    def _impossible(target: SaleTarget) -> bool:
        return target.month < _earliest_supply_month(target.animal_class)

    for _ in range(max_iterations):
        if evaluation.all_met:
            break
        changed = False
        for target, fill in zip(targets, evaluation.targets, strict=True):
            if fill.met or target.animal_class not in _PURCHASE_BACKED_CLASSES:
                continue
            if _impossible(target):
                if target.month in impossible_noted:
                    continue
                impossible_noted.add(target.month)
                earliest = _earliest_supply_month(target.animal_class)
                notes.append(
                    f"Month {target.month}: impossible to supply with fresh purchases — "
                    f"the earliest month a doe bought at month 1 puts "
                    f"{target.animal_class.replace('_', ' ')} in the sale pool is month "
                    f"{earliest}. Move the sale to at least that month or source the "
                    "animals as young stock."
                )
                continue
            purchase_month = _purchase_month_for(target, assumptions)
            marginal = _marginal_kids_per_doe(assumptions, target, purchase_month)
            if marginal <= 0.0:
                continue
            needed = math.ceil(fill.shortfall / marginal)
            if needed <= 0:
                continue
            purchases_by_month[purchase_month] = (
                purchases_by_month.get(purchase_month, 0.0) + needed
            )
            changed = True
        if not changed:
            break
        purchases = _purchases_from(purchases_by_month)
        evaluation = _evaluate(assumptions, targets, purchases)

    return (
        _purchases_from(purchases_by_month),
        evaluation,
        evaluation.all_met,
        notes,
    )


def _purchases_from(by_month: dict[int, float]) -> list[HerdEventAssumptions]:
    """Chunked per-month purchase events, each inside the schema's head cap.

    A huge shortfall can demand more than ``MAX_HEAD`` head in one month; a
    single event would then fail validation mid-iteration (an unhandled 500
    from the API). Splitting keeps every event legal; a plan so large it
    exceeds the total event cap still fails validation loudly — by design.
    """
    events: list[HerdEventAssumptions] = []
    for month, count in sorted(by_month.items()):
        remaining = count
        while remaining > 0.0:
            chunk = min(remaining, float(MAX_HEAD))
            events.append(
                HerdEventAssumptions(month=month, kind="purchase", animal_class="doe", count=chunk)
            )
            remaining -= chunk
    return events


def plan_probabilities(
    assumptions: SimulationAssumptions,
    targets: list[SaleTarget],
    purchases: list[HerdEventAssumptions] | None = None,
    *,
    runs: int = 200,
    seed: int | None = None,
) -> list[TargetRisk]:
    """Probability each target still fills under correlated risk draws.

    Uses the same Gaussian-copula machinery and event shocks as the main
    Monte Carlo, so a plan's risk is measured by the model the farm already
    trusts — not a second, hidden one.
    """
    base = _plan_assumptions(assumptions, targets, purchases)
    rng = random.Random(seed if seed is not None else assumptions.risk.seed)
    risk_vars = {
        "meat_price": assumptions.risk.meat_price,
        "milk_price": assumptions.risk.milk_price,
        "feed_price": assumptions.risk.feed_price,
        "adult_mortality": assumptions.risk.adult_mortality,
        "kid_mortality": assumptions.risk.kid_mortality,
        "litter_size": assumptions.risk.litter_size,
        "conception_rate": assumptions.risk.conception_rate,
        "fodder_yield": assumptions.risk.fodder_yield,
        "operating_cost": assumptions.risk.operating_cost,
    }
    full = [0] * len(targets)
    eighty = [0] * len(targets)
    completed = 0
    for _ in range(runs):
        draws = _correlated_draws(rng, risk_vars, assumptions.risk.correlation_strength)
        path = _event_shock_path(assumptions, rng)
        core = _run_core(_apply_draws(base, draws), path)
        fills = [fill for month in core.months for fill in month.event_fills]
        matched = _match_target_fills(fills, targets)
        completed += 1
        for index, item in enumerate(matched):
            if item.filled >= item.requested - 0.5:
                full[index] += 1
            if item.filled >= 0.8 * item.requested - 0.5:
                eighty[index] += 1
    denominator = completed or 1
    return [
        TargetRisk(
            month=target.month,
            animal_class=target.animal_class,
            requested=target.count,
            p_full=full[index] / denominator,
            p_eighty=eighty[index] / denominator,
        )
        for index, target in enumerate(targets)
    ]


def build_plan_report(
    assumptions: SimulationAssumptions,
    targets: list[SaleTarget],
    *,
    close_gaps_enabled: bool = True,
    risk_runs: int = 0,
) -> PlanReport:
    """Full planner pass: evaluate, close gaps, then risk-score the closed plan."""
    assumptions = SimulationAssumptions.model_validate(assumptions.model_dump())
    before = _evaluate(assumptions, targets)
    purchases: list[HerdEventAssumptions] = []
    after: PlanEvaluation | None = None
    gaps_closed = before.all_met
    notes: list[str] = []

    if close_gaps_enabled and not before.all_met:
        purchases, after, gaps_closed, gap_notes = close_gaps(assumptions, targets)
        notes.extend(gap_notes)
        if not gaps_closed:
            notes.append(
                "Gap closing stopped with a shortfall remaining: the biology "
                "(litter size, mortality, kidding interval) cannot reliably "
                "produce these targets even with early purchases. Reduce the "
                "targets or move them later."
            )

    probabilities = None
    if risk_runs > 0:
        probabilities = plan_probabilities(assumptions, targets, purchases or None, runs=risk_runs)

    return PlanReport(
        before=before,
        after=after,
        recommended_purchases=purchases,
        gaps_closed=gaps_closed,
        probabilities=probabilities,
        notes=notes,
    )
