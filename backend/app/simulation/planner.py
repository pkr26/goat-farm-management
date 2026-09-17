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

from ..characters import sanitize_single_line
from .assumptions import (
    MAX_HEAD,
    MAX_PLAN_EVENTS,
    HerdEventAssumptions,
    MortalityAssumptions,
    SimulationAssumptions,
)
from .engine import (
    NLM_CAPITAL_CEILING_PER_HEAD,
    NLM_SUBSIDY_FRACTION,
    _run_core,
    monthly_mortality_rate,
)
from .montecarlo import _apply_draws, _correlated_draws, _event_shock_path
from .results import EventFill, SimulationResult
from .vocabulary import GOAT_NOUNS, SpeciesNouns

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
# function) span 6..ceiling-1 — the ceiling is the sex's graduation age:
# sale_age for males, age_at_first_breeding for females (a female grower
# stays in the pool until she joins the doe herd, past the male sale age).
_CLASS_MAX_AGE = {"kid": 2, "weaner": 5}
# Age (months) at which an animal enters each class: kid 0, weaner 3, grower 6.
_CLASS_ENTRY_AGE = {"kid": 0, "weaner": 3, "grower": 6}

# The schema caps a whole document's event list at MAX_PLAN_EVENTS entries
# (``SimulationAssumptions.events`` max_length; the constant lives in
# assumptions.py so the schema bound and this arithmetic cannot drift).
# That budget is shared by the caller's own purchase events, the planner's
# generated purchases and one sale event per target — so the planner must
# count its chunks BEFORE building them: a tiny ``conception_rate`` scales
# ``needed`` head up trillions-fold, and materializing even a fraction of
# those chunks OOMs the single worker long before the post-hoc validation
# guard can fire (red-team RT-L8-1).
# Backstop inside the chunking loop itself. The arithmetic pre-checks above
# always fire first; this cap exists so an arithmetic regression (e.g. float
# absorption making ``remaining -= chunk`` a no-op at huge counts) can never
# degrade into an unbounded while-loop.
_MAX_MATERIALIZED_PURCHASE_EVENTS = 1000


def _class_max_age(animal_class: str, sale_age_months: int, first_breeding_months: int) -> int:
    base = animal_class.rsplit("_", maxsplit=1)[-1]
    if base == "grower":
        ceiling = (
            first_breeding_months if animal_class.startswith("female") else max(sale_age_months, 6)
        )
        return max(ceiling - 1, 6)
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


def _survival_to_event_age(mort: MortalityAssumptions, completed_age_months: int) -> float:
    """Birth-to-event survival for a kid aged ``completed_age_months`` months
    at the moment of a start-of-month sale event (one monthly mortality
    exposure per month lived, split by the class each month belongs to:
    kid phase months 0-2, weaner 3-5, grower 6+)."""
    kid_months = min(completed_age_months + 1, 3)
    weaner_months = min(max(completed_age_months - 2, 0), 3)
    grower_months = max(completed_age_months - 5, 0)
    return (
        math.pow(1.0 - mort.kid_pre_weaning, kid_months / 3.0)
        * math.pow(1.0 - mort.kid_post_weaning, weaner_months / 3.0)
        * math.pow(1.0 - mort.grower, grower_months / 12.0)
    )


def _marginal_kids_per_doe(
    assumptions: SimulationAssumptions,
    target: SaleTarget,
    purchase_month: int,
) -> float:
    """Expected head of ``target.animal_class`` one bought doe adds by month T.

    A compact monthly mirror of the engine's dam pipeline for a purchased doe:
    settle → monthly service with conception retries → kidding at
    ``purchase + settling + gestation`` → lactation + open period → next
    service, with the engine's adult mortality and (post-grace) rate cull
    applied month by month — and her retained daughters join the service pool
    at first-breeding age, their kids counting too (by a late target month
    the granddaughter generation dominates the young-stock pool). A birth in
    month B stands at completed age T-B-1 at a month-T sale event (events run
    before the month's aging step), so the class window is
    [T - class_max_age - 1, T - entry_age - 1] and each in-window birth
    contributes litter × sex share × birth-to-event survival. Accuracy:
    within ~2% on near targets, conservative (0.76-0.90x) on far windows
    where the daughter generations' exact attrition dominates; sire service
    capacity is also not modelled. ``close_gaps`` re-runs the engine forward,
    which applies all of this exactly and converges regardless of the seed.
    """
    sale_age = assumptions.growth.sale_age_months
    class_max_age = _class_max_age(
        target.animal_class, sale_age, assumptions.reproduction.age_at_first_breeding_months
    )
    entry_age = _CLASS_ENTRY_AGE[target.animal_class.rsplit("_", maxsplit=1)[-1]]
    earliest_birth = target.month - class_max_age - 1
    latest_birth = target.month - entry_age - 1
    if latest_birth < earliest_birth:
        return 0.0
    r = assumptions.reproduction
    mort = assumptions.mortality
    cull = assumptions.culling
    herd = assumptions.herd
    s_adult = 1.0 - monthly_mortality_rate(mort.adult)
    monthly_cull = 1.0 - monthly_mortality_rate(cull.doe_cull_rate_annual)

    first_service = purchase_month + herd.purchased_doe_settling_months
    # Dams ready for service, by month (expected mass of one bought doe).
    ready: dict[int, float] = {first_service: 1.0}
    # Dams due to kid, by month.
    kidding: dict[int, float] = {}
    # Kidding mass by month (the offspring supply this function returns from).
    births_by_month: dict[int, float] = {}
    # Home-bred daughters reaching breeding age, by month: a purchased doe's
    # contribution includes her daughters' kids (the engine's grower pool at
    # a late target month is largely the granddaughter generation — the
    # first home-bred daughters kid as early as purchase + gestation + afb).
    daughters: dict[int, float] = {}

    def _attrition(month_index: int) -> float:
        # Adult mortality always; the rate cull only from the engine's
        # foundation-year grace month 13 onward. The dam's own max-age cull
        # is deliberately NOT modelled: the engine's later-wave kidding
        # calendar is not a fixed interval (lactation/waiting slotting
        # shifts it by a month between waves), and any fixed cutoff here
        # mis-anchor whole waves. ``close_gaps`` re-runs the engine forward,
        # which applies every cull exactly.
        factor = s_adult
        if month_index >= 13:
            factor *= monthly_cull
        return factor

    afb = r.age_at_first_breeding_months
    female_per_birth = r.litter_size * (1.0 - r.stillbirth_rate) * r.sex_ratio_female
    # Survival of a female kid from birth to first-breeding age (kid and
    # weaner phases in full, grower months pro-rated).
    survival_to_afb = _survival_to_event_age(mort, afb - 1)

    last_month = latest_birth + 1  # one past: births land before the window closes
    for month_index in range(first_service, max(last_month, first_service) + 1):
        daughter_mass = daughters.pop(month_index, 0.0)
        if daughter_mass > 0.0:
            ready[month_index] = ready.get(month_index, 0.0) + daughter_mass
        if month_index in ready:
            dam_mass = ready.pop(month_index)
            conceived = dam_mass * r.conception_rate
            kidding[month_index + r.gestation_months] = (
                kidding.get(month_index + r.gestation_months, 0.0) + conceived
            )
            remaining = dam_mass - conceived
            if remaining > 1e-12:
                ready[month_index + 1] = ready.get(month_index + 1, 0.0) + remaining
        if month_index in kidding:
            dam_mass = kidding.pop(month_index)
            births_by_month[month_index] = dam_mass
            # Meat-mode cycle: kidding → lactation → open period → service.
            next_service = month_index + r.lactation_months + r.months_open_before_breeding
            ready[next_service] = ready.get(next_service, 0.0) + dam_mass
            # Her retained daughters join the service pool at breeding age.
            graduation = month_index + afb
            if graduation <= last_month:
                daughters[graduation] = (
                    daughters.get(graduation, 0.0)
                    + dam_mass * female_per_birth * survival_to_afb * herd.female_retention_fraction
                )
        for pool in (ready, kidding, daughters):
            for key in pool:
                pool[key] *= _attrition(month_index)

    female_share = (
        r.sex_ratio_female if target.animal_class.startswith("female") else 1.0 - r.sex_ratio_female
    )
    per_birth = r.litter_size * (1.0 - r.stillbirth_rate) * female_share
    head = 0.0
    for birth_month, dam_mass in births_by_month.items():
        if earliest_birth <= birth_month <= latest_birth:
            completed_age = target.month - birth_month - 1
            head += dam_mass * per_birth * _survival_to_event_age(mort, completed_age)
    return head


def _purchase_month_for(target: SaleTarget, assumptions: SimulationAssumptions) -> int:
    """Latest sensible buy month whose doe's kids still reach the class by T.

    Backwards from the sale: class age at T, plus gestation, plus settling,
    plus one month of slack for the breeding cycle.
    """
    sale_age = assumptions.growth.sale_age_months
    class_max_age = _class_max_age(
        target.animal_class, sale_age, assumptions.reproduction.age_at_first_breeding_months
    )
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
    nouns: SpeciesNouns = GOAT_NOUNS,
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
        label = nouns.event_label(target.animal_class)
        if target.animal_class in _PURCHASE_BACKED_CLASSES:
            notes.append(
                f"Month {target.month}: selling {target.count:g} {label} "
                f"exceeds the projected pool by {fill.shortfall:.1f} head."
            )
        else:
            # Policy exclusion, not a biology limit: say so, and do not let
            # the generic "move it later" advice fire for a cause it cannot fix.
            notes.append(
                f"Month {target.month}: selling {target.count:g} {label} exceeds the "
                f"projected pool by {fill.shortfall:.1f} head. The planner never buys "
                f"breeding stock to fuel cull sales — a {nouns.female} bought for her "
                f"lifetime of {nouns.young_plural} is worth far more than her meat. "
                "Sell fewer, or grow the pool by retaining more young stock in "
                "earlier months."
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

    # Loop-invariant slice of the event budget: the caller's kept purchase
    # events plus one sale event per target. Computed once so the in-loop
    # pre-check and the final materialization below enforce the SAME
    # reserved-inclusive budget (a final call with reserved=0 would rely on
    # the dict being untouched since the last loop check).
    reserved_events = sum(1 for event in assumptions.events if event.kind == "purchase") + len(
        targets
    )

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
                    f"the earliest month a {nouns.female} bought at month 1 puts "
                    f"{nouns.event_label(target.animal_class)} in the sale pool is month "
                    f"{earliest}. Move the sale to at least that month or source the "
                    "animals as young stock."
                )
                continue
            purchase_month = _purchase_month_for(target, assumptions)
            marginal = _marginal_kids_per_doe(assumptions, target, purchase_month)
            if marginal <= 0.0:
                continue
            # A denormal marginal can push the ratio past float range
            # (``math.ceil`` of inf raises); anything at or beyond the
            # purchasable ceiling is rejected by the event-cap check below,
            # so clamp instead of ever materializing it.
            ratio = fill.shortfall / marginal
            ceiling = MAX_HEAD * MAX_PLAN_EVENTS
            needed = min(math.ceil(min(ratio, float(ceiling))), ceiling)
            if needed <= 0:
                continue
            purchases_by_month[purchase_month] = (
                purchases_by_month.get(purchase_month, 0.0) + needed
            )
            changed = True
        if not changed:
            break
        # Bound the materialization BEFORE building anything (RT-L8-1): the
        # plan's event document = the caller's kept purchases + one sale event
        # per target + the chunks below, all countable by pure arithmetic. A
        # demand that cannot fit must fail as this cheap ValueError (mapped to
        # 422 by the API), never as an OOM after half a billion objects exist.
        _check_purchase_event_budget(purchases_by_month, reserved=reserved_events)
        purchases = _purchases_from(purchases_by_month, reserved=reserved_events)
        evaluation = _evaluate(assumptions, targets, purchases)

    return (
        _purchases_from(purchases_by_month, reserved=reserved_events),
        evaluation,
        evaluation.all_met,
        notes,
    )


def _projected_purchase_events(by_month: dict[int, float]) -> int:
    """Event count ``_purchases_from`` would build, by pure arithmetic.

    No allocation, so an absurd demand (trillions of ``MAX_HEAD`` chunks)
    is measurable and rejectable before a single event object exists.
    """
    return sum(math.ceil(count / MAX_HEAD) for count in by_month.values())


def _check_purchase_event_budget(by_month: dict[int, float], *, reserved: int = 0) -> None:
    """Raise before materialization when the chunked purchases cannot fit.

    ``reserved`` is the event headroom the rest of the plan document already
    consumes (the caller's kept purchase events plus one sale event per
    target). The message is actionable on purpose: the usual cause is a
    near-zero conception rate scaling the doe demand beyond every limit.
    """
    projected = _projected_purchase_events(by_month)
    if reserved + projected > MAX_PLAN_EVENTS:
        reserved_note = (
            f" ({reserved} events are already reserved by the plan's kept purchase/sale events)"
            if reserved
            else ""
        )
        raise ValueError(
            f"plan requires {projected} purchase events; cap is {MAX_PLAN_EVENTS} "
            f"— raise conception_rate or lower the shortfall{reserved_note}"
        )


def _purchases_from(by_month: dict[int, float], *, reserved: int = 0) -> list[HerdEventAssumptions]:
    """Chunked per-month purchase events, each inside the schema's head cap.

    A huge shortfall can demand more than ``MAX_HEAD`` head in one month; a
    single event would then fail validation mid-iteration (an unhandled 500
    from the API). Splitting keeps every event legal; a plan whose chunks
    would exceed the event cap is rejected here by arithmetic, before a
    single event is built — materializing the chunk list inside the priced
    budget was an OOM vector, not a validation error (RT-L8-1). ``reserved``
    is the headroom the rest of the plan document already consumes, so this
    self-contained check enforces the same budget close_gaps pre-checked.
    """
    _check_purchase_event_budget(by_month, reserved=reserved)
    events: list[HerdEventAssumptions] = []
    for month, count in sorted(by_month.items()):
        remaining = count
        while remaining > 0.0:
            if len(events) >= _MAX_MATERIALIZED_PURCHASE_EVENTS:
                # Unreachable while the pre-check above holds; kept so the
                # loop can never run away even if that arithmetic regresses.
                raise ValueError(
                    "purchase chunking exceeded the "
                    f"{_MAX_MATERIALIZED_PURCHASE_EVENTS}-event backstop; "
                    f"cap is {MAX_PLAN_EVENTS} — raise conception_rate or "
                    "lower the shortfall"
                )
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


def build_dpr_markdown(
    assumptions: SimulationAssumptions,
    result: SimulationResult,
    *,
    plan_name: str | None = None,
) -> str:
    """DPR-style markdown summary of a projection, for a NABARD loan file.

    A Detailed Project Report states the unit, the capital outlay and its
    funding (bank loan / subsidy / promoter equity) and the viability figures
    a lender appraises (NPV, BCR, DSCR, payback) — all read off the existing
    deterministic result, never recomputed differently here. When the NLM
    toggle is on, the scheme basis is stated explicitly.
    """
    m = result.metrics
    b = result.project_cost_breakdown
    horizon_years = assumptions.meta.horizon_months / 12.0

    def _cr(value: float) -> str:
        return f"₹{value:,.0f}"

    def _opt_pct(value: float | None) -> str:
        return "n/a" if value is None else f"{value * 100.0:.1f}%"

    def _opt_dscr(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.2f}"

    # The plan name is farm-entered free text interpolated into a loan
    # document: newlines/U+2028 could forge additional DPR sections and
    # directional overrides could visually alter the title (2026-09-16
    # audit, INJ-1). The title renders on exactly one line, period.
    title = sanitize_single_line(plan_name) if plan_name else ""
    if not title:
        title = "Goat rearing unit"
    lines = [
        f"# Detailed Project Report — {title}",
        "",
        f"Projection: {assumptions.meta.horizon_months} months "
        f"({horizon_years:g} years) from {assumptions.meta.start_year_month}.",
        "",
        "## Unit",
        "",
        f"- Breeding {GOAT_NOUNS.female_plural}: {assumptions.herd.does} "
        f"+ {assumptions.herd.bucks} {GOAT_NOUNS.male_plural}",
        f"- Shed/equipment capacity: {b.capacity_places:.0f} animal places",
        "",
        "## Capital outlay",
        "",
        "| Component | Amount |",
        "| --- | ---: |",
        f"| Shed ({b.capacity_places:.0f} places) | {_cr(b.shed_cost)} |",
        f"| Equipment | {_cr(b.equipment_cost)} |",
        f"| Foundation stock | {_cr(b.stock_cost)} |",
        f"| Working capital | {_cr(b.working_capital)} |",
        f"| **Total project cost** | **{_cr(m.project_cost)}** |",
        "",
        "## Means of finance",
        "",
        "| Source | Amount |",
        "| --- | ---: |",
        f"| Bank loan | {_cr(m.loan_amount)} |",
        f"| Capital subsidy | {_cr(m.subsidy_amount)} |",
        f"| Promoter equity | {_cr(m.equity)} |",
        "",
    ]
    if assumptions.finance.nlm_subsidy:
        lines += [
            "The subsidy line follows the National Livestock Mission (NLM) "
            f"goat-unit structure: a {NLM_SUBSIDY_FRACTION:.0%} back-ended "
            "capital subsidy on eligible capital (shed, animals, fodder, "
            "equipment and insurance are all eligible), capped per unit size "
            f"at about {_cr(NLM_CAPITAL_CEILING_PER_HEAD)} per breeding head — "
            "the scheme's published bands run from a 100F+5M unit's ~₹10 "
            "lakh up to a 500F+25M unit's ~₹50 lakh of eligible capital.",
            "",
        ]
    lines += [
        f"## Viability ({horizon_years:g}-year projection)",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| NPV @ {assumptions.finance.discount_rate_annual:.0%} | {_cr(m.npv)} |",
        f"| IRR / MIRR | {_opt_pct(m.irr)} / {_opt_pct(m.mirr)} |",
        f"| Benefit-cost ratio | {'n/a' if m.bcr is None else f'{m.bcr:.2f}'} |",
        f"| Average / weakest DSCR (repaying years) | "
        f"{_opt_dscr(m.avg_dscr)} / {_opt_dscr(m.min_dscr)} |",
        f"| Payback | "
        f"{'beyond horizon' if m.payback_month is None else f'month {m.payback_month}'} |",
        "| Break-even meat price | "
        + (
            "n/a"
            if m.break_even_meat_price_per_kg is None
            else f"₹{m.break_even_meat_price_per_kg:,.0f}/kg"
        )
        + " |",
        "",
        "## Annual operating summary",
        "",
        "| Year | Revenue | Operating cost | EBITDA | Debt service | DSCR |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row, dscr in zip(result.annual_pl, m.dscr_per_year, strict=True):
        lines.append(
            f"| {row.year} | {_cr(row.total_revenue)} | {_cr(row.total_opex)} "
            f"| {_cr(row.ebitda)} | {_cr(row.debt_service)} | {dscr:.2f} |"
        )
    lines += [
        "",
        "_Figures are model projections from the farm's own assumptions; they "
        "support, and do not replace, the bank's appraisal._",
        "",
    ]
    return "\n".join(lines)


def build_plan_report(
    assumptions: SimulationAssumptions,
    targets: list[SaleTarget],
    *,
    close_gaps_enabled: bool = True,
    risk_runs: int = 0,
    nouns: SpeciesNouns = GOAT_NOUNS,
) -> PlanReport:
    """Full planner pass: evaluate, close gaps, then risk-score the closed plan."""
    assumptions = SimulationAssumptions.model_validate(assumptions.model_dump())
    before = _evaluate(assumptions, targets)
    purchases: list[HerdEventAssumptions] = []
    after: PlanEvaluation | None = None
    gaps_closed = before.all_met
    notes: list[str] = []

    if close_gaps_enabled and not before.all_met:
        purchases, after, gaps_closed, gap_notes = close_gaps(assumptions, targets, nouns=nouns)
        notes.extend(gap_notes)
        if not gaps_closed:
            notes.append(
                "Gap closing stopped with a shortfall remaining: the biology "
                f"(litter size, mortality, {nouns.parturition} interval) cannot reliably "
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
