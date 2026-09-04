"""Backward planning: sale targets in real calendar months → what to do now.

The sale planner (``planner.py``) answers "will these targets fill, and what
doe purchases close the gaps" in engine-relative months. A farmer plans in
calendar months — "200 goats in Jan 2027" — and wants the plan worked *back*
through the biology: how many breedable does that implies months earlier, how
many young stock must stand in each stage along the way, and what to buy, breed
and retain starting now. This module adds that layer on top of the existing
machinery instead of duplicating it:

1. ``month_offset`` / ``month_label`` translate between the plan's calendar
   anchor (``meta.start_year_month``; simulation month 1 falls in that month)
   and the engine's 1-based months, and the horizon is extended to cover the
   last target so a distant sale is never silently unreachable.
2. ``build_plan_report`` runs unchanged underneath — forward feasibility,
   gap-closing purchases, risk probabilities.
3. The closed plan is re-run once and read into ``PlannerMonthRow``s: the
   month-by-month *stage plan* (head in every stage plus births, deaths,
   culls, sales and purchases) that delivering the targets requires.
4. ``RequirementChain`` works each young-stock target backward analytically —
   sale ← survivors ← kids born ← does kidded ← does bred — using the same
   survival/litter/conception primitives the engine applies forward, and
   ``PlannerAction`` renders the resulting to-do list with dates.

Goat/buffalo wording flows through ``SpeciesNouns`` like every other piece of
user-facing simulation text.
"""

import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .assumptions import MAX_HEAD, HerdEventAssumptions, SimulationAssumptions
from .engine import _CoreResult, _run_core
from .planner import (
    _CLASS_ENTRY_AGE,
    PlanAnimalClass,
    PlanReport,
    SaleTarget,
    _class_max_age,
    _plan_assumptions,
    _survival_to_event_age,
    build_plan_report,
)
from .vocabulary import GOAT_NOUNS, SpeciesNouns

_YEAR_MONTH_PATTERN = re.compile(r"(\d{4})-(\d{2})")

_YOUNG_STOCK_CLASSES = {
    "female_kid",
    "male_kid",
    "female_weaner",
    "male_weaner",
    "female_grower",
    "male_grower",
}


def parse_year_month(year_month: str) -> tuple[int, int]:
    """``"2027-01"`` → ``(2027, 1)``; raises on anything malformed."""
    match = _YEAR_MONTH_PATTERN.fullmatch(year_month)
    if match is None or not 1 <= int(match.group(2)) <= 12:
        raise ValueError(f"{year_month!r} is not a real YYYY-MM (e.g. '2027-01')")
    year = int(match.group(1))
    if not 1900 <= year <= 2200:
        raise ValueError(f"{year_month!r} is not a real YYYY-MM (e.g. '2027-01')")
    return year, int(match.group(2))


def month_offset(start_year_month: str, year_month: str) -> int:
    """1-based engine month a calendar month maps to (month 1 = start month).

    Raises ``ValueError`` for a target at or before the plan starts — a sale
    needs lead time, so the first month you can plan a sale into is the one
    *after* the plan starts.
    """
    start_y, start_m = parse_year_month(start_year_month)
    y, m = parse_year_month(year_month)
    offset = (y - start_y) * 12 + (m - start_m) + 1
    if offset < 2:
        raise ValueError(
            f"Target month {year_month} is at or before the plan start "
            f"{start_year_month}; the first month a sale can be planned into is "
            f"the month after the plan starts."
        )
    return offset


def month_label(start_year_month: str, month: int) -> str:
    """Engine month → calendar ``"YYYY-MM"`` (inverse of ``month_offset``)."""
    y, m = parse_year_month(start_year_month)
    total = y * 12 + (m - 1) + (month - 1)
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


class PlannerTarget(BaseModel):
    """One planned sale: ``count`` head of ``animal_class`` in a calendar month."""

    model_config = ConfigDict(extra="forbid", strict=True)

    year_month: str
    animal_class: PlanAnimalClass
    count: float = Field(gt=0.0, le=MAX_HEAD)

    @field_validator("year_month")
    @classmethod
    def _valid_year_month(cls, value: str) -> str:
        parse_year_month(value)
        return value


class PlannerMonthRow(BaseModel):
    """One month of the stage plan: head that must stand in each stage
    (end-of-month, matching the engine's reporting convention) plus the
    month's flows."""

    model_config = ConfigDict(extra="forbid")

    month: int
    year_month: str
    female_kids: float
    male_kids: float
    female_weaners: float
    male_weaners: float
    female_growers: float
    male_growers: float
    open_does: float
    pregnant_does: float
    lactating_does: float
    bucks: float
    total_head: float
    births: float
    deaths: float
    culls_head: float
    sales_head: float
    purchases_head: float


PlannerActionKind = Literal[
    "purchase",
    "breed",
    "expect_births",
    "retain",
    "sell",
]


class PlannerAction(BaseModel):
    """One dated instruction in the plan's to-do list."""

    model_config = ConfigDict(extra="forbid")

    month: int
    year_month: str
    kind: PlannerActionKind
    headline: str
    detail: str


class RequirementStep(BaseModel):
    """One link of a target's backward requirement chain."""

    model_config = ConfigDict(extra="forbid")

    year_month: str
    # Whole-head requirement (rounded up: you cannot breed 0.4 of a doe).
    quantity: int
    label: str


class RequirementChain(BaseModel):
    """The backward math for one target, earliest link first."""

    model_config = ConfigDict(extra="forbid")

    year_month: str
    animal_class: str
    count: float
    # Whether the closed plan fills this target (mirrors the feasibility
    # table; a chain can describe an unfillable target — that is the point).
    achievable: bool
    steps: list[RequirementStep]
    explanation: str


class BackwardPlanReport(BaseModel):
    """Everything the Planner page renders: feasibility (``plan``, in engine
    months — pair its ``targets``/``probabilities`` with ``targets_echo`` by
    index for calendar labels), the dated stage plan, the action list and the
    per-target requirement chains."""

    model_config = ConfigDict(extra="forbid")

    start_year_month: str
    horizon_months: int
    targets_echo: list[PlannerTarget]
    plan: PlanReport
    stage_plan: list[PlannerMonthRow]
    actions: list[PlannerAction]
    chains: list[RequirementChain]
    notes: list[str] = Field(default_factory=list)


class _ClassWindow:
    """Birth window of one young-stock class, in planner.py's conventions:
    a sale event at the start of month T sees pools as of the end of T-1, so
    an animal born in month B stands at completed age T-B-1 and the window is
    [T - class_max_age - 1, T - entry_age - 1]."""

    def __init__(self, assumptions: SimulationAssumptions, animal_class: str) -> None:
        self.entry_age = _CLASS_ENTRY_AGE[animal_class.rsplit("_", maxsplit=1)[-1]]
        self.max_age = _class_max_age(
            animal_class,
            assumptions.growth.sale_age_months,
            assumptions.reproduction.age_at_first_breeding_months,
        )
        # The age the animals are typically sold at: window midpoint.
        self.typical_age = (self.entry_age + self.max_age) // 2

    def birth_month(self, sale_month: int) -> int:
        return sale_month - self.typical_age - 1

    def bred_month(self, sale_month: int, gestation_months: int) -> int:
        return self.birth_month(sale_month) - gestation_months


def _effective_conception(assumptions: SimulationAssumptions) -> tuple[float, str]:
    """Conception probability per service-ready doe within the retry policy,
    plus a qualifier for the chain's explanation.

    ``max_services_before_cull == 0`` means unlimited monthly retries in the
    engine (failures stay in the pool and are re-served, never culled), so
    every bred doe eventually conceives — some on a later service."""
    rate = assumptions.reproduction.conception_rate
    cap = assumptions.reproduction.max_services_before_cull
    if cap == 0:
        return 1.0, "retries until pregnant"
    if cap == 1:
        return rate, "one service"
    # P(conceive within the cull cap of services), geometric sum.
    return 1.0 - math.pow(1.0 - rate, cap), f"up to {cap} services"


def _requirement_chain(
    assumptions: SimulationAssumptions,
    target: PlannerTarget,
    month: int,
    achievable: bool,
    window: _ClassWindow,
    nouns: SpeciesNouns,
) -> RequirementChain:
    """Work one young-stock target backward through the biology, evaluated at
    the class's typical sale age (the same survival/litter/conception
    primitives the engine applies forward)."""
    r = assumptions.reproduction
    mort = assumptions.mortality
    birth_month = window.birth_month(month)
    bred_month = window.bred_month(month, r.gestation_months)
    start = assumptions.meta.start_year_month

    sex_share = (
        r.sex_ratio_female if target.animal_class.startswith("female") else 1.0 - r.sex_ratio_female
    )
    survival = _survival_to_event_age(mort, window.typical_age)
    per_birth_of_sex = r.litter_size * (1.0 - r.stillbirth_rate) * sex_share
    kids_of_sex_needed = math.ceil(target.count / survival)
    total_kids_needed = math.ceil(kids_of_sex_needed / per_birth_of_sex)
    kids_per_doe = r.litter_size * (1.0 - r.stillbirth_rate)
    does_kidded = math.ceil(total_kids_needed / kids_per_doe)
    conception, service_note = _effective_conception(assumptions)
    does_bred = math.ceil(does_kidded / conception)

    steps = [
        RequirementStep(
            year_month=month_label(start, bred_month),
            quantity=does_bred,
            label=f"breedable {nouns.female}(s) bred ({service_note})",
        ),
        RequirementStep(
            year_month=month_label(start, birth_month),
            quantity=does_kidded,
            label=f"{nouns.female}(s) due to {nouns.parturition}",
        ),
        RequirementStep(
            year_month=month_label(start, birth_month),
            quantity=total_kids_needed,
            label=f"{nouns.young_plural} born (both sexes)",
        ),
        RequirementStep(
            year_month=month_label(start, month),
            quantity=math.ceil(target.count),
            label=f"{nouns.event_label(target.animal_class)} sold",
        ),
    ]
    young_loss_pct = round((1.0 - survival) * 100.0)
    sexed_caveat = (
        " (Sexed-semen services are configured on this farm; the engine's sexed "
        "conception penalty shifts these figures slightly.)"
        if r.sexed_semen_services > 0
        else ""
    )
    explanation = (
        f"Selling {target.count:g} {nouns.event_label(target.animal_class)} at "
        f"~{window.typical_age} months needs ~{kids_of_sex_needed} alive at sale; "
        f"with {young_loss_pct}% born-to-sale mortality that is ~{total_kids_needed} "
        f"{nouns.young_plural} born around {month_label(start, birth_month)} "
        f"(litter size {r.litter_size:g}, {round(sex_share * 100)}% of the sex you "
        f"sell), from ~{does_kidded} {nouns.female}(s) {nouns.parturition} and ~{does_bred} "
        f"bred about {r.gestation_months} months earlier. Your herd must hold that many "
        f"breedable {nouns.female_plural} then — buy early enough to settle, or retain "
        f"more young {nouns.female_plural}." + sexed_caveat
    )
    return RequirementChain(
        year_month=target.year_month,
        animal_class=target.animal_class,
        count=target.count,
        achievable=achievable,
        steps=steps,
        explanation=explanation,
    )


def _pool_target_chain(
    target: PlannerTarget,
    achievable: bool,
    nouns: SpeciesNouns,
) -> RequirementChain:
    """Doe/buck targets draw on the breeding pool itself; say so instead of
    inventing a purchase-backed chain (the sale planner's policy)."""
    return RequirementChain(
        year_month=target.year_month,
        animal_class=target.animal_class,
        count=target.count,
        achievable=achievable,
        steps=[
            RequirementStep(
                year_month=target.year_month,
                quantity=math.ceil(target.count),
                label=f"{nouns.event_label(target.animal_class)} sold from the breeding pool",
            )
        ],
        explanation=(
            f"Selling {target.count:g} {nouns.event_label(target.animal_class)} draws on the "
            "breeding pool itself. The planner never buys breeding stock to fuel cull "
            f"sales — a {nouns.female} bought for her lifetime of {nouns.young_plural} is "
            "worth far more than her meat. Grow the pool by retaining more young stock "
            "in earlier months, or sell fewer."
        ),
    )


def _stage_plan_rows(
    core: _CoreResult,
    start_year_month: str,
    through_month: int,
) -> list[PlannerMonthRow]:
    return [
        PlannerMonthRow(
            month=record.month,
            year_month=month_label(start_year_month, record.month),
            female_kids=record.f_kids,
            male_kids=record.m_kids,
            female_weaners=record.f_weaners,
            male_weaners=record.m_weaners,
            female_growers=record.f_growers,
            male_growers=record.m_growers,
            open_does=record.open_does,
            pregnant_does=record.pregnant_does,
            lactating_does=record.lactating_does,
            bucks=record.bucks,
            total_head=record.total_herd,
            births=record.births,
            deaths=record.deaths,
            culls_head=record.culls_head,
            sales_head=record.sales_head,
            purchases_head=record.purchases_head,
        )
        for record in core.months[:through_month]
    ]


def _purchase_actions(
    assumptions: SimulationAssumptions,
    purchases: list[HerdEventAssumptions],
    start_year_month: str,
    target_classes: list[str],
    nouns: SpeciesNouns,
) -> list[PlannerAction]:
    """Why each purchase month is what it is: the lead-time chain worked
    backward from the sales it fuels. The lead is the TIGHTEST of the target
    classes' windows (``planner._purchase_month_for``'s formula: class max
    age + gestation + settling + 1) — the deadline that sets the buy date."""
    r = assumptions.reproduction
    herd = assumptions.herd
    sale_age = assumptions.growth.sale_age_months
    afb = r.age_at_first_breeding_months
    class_leads = [
        _class_max_age(animal_class, sale_age, afb)
        + r.gestation_months
        + herd.purchased_doe_settling_months
        + 1
        for animal_class in target_classes or ["male_grower"]
    ]
    lead = min(class_leads)
    grow_months = lead - r.gestation_months - herd.purchased_doe_settling_months - 1
    by_month: dict[int, float] = {}
    for event in purchases:
        by_month[event.month] = by_month.get(event.month, 0.0) + event.count
    actions: list[PlannerAction] = []
    for month, count in sorted(by_month.items()):
        label = month_label(start_year_month, month)
        actions.append(
            PlannerAction(
                month=month,
                year_month=label,
                kind="purchase",
                headline=f"Buy ~{math.ceil(count)} {nouns.female_plural}",
                detail=(
                    f"Why this month: a {nouns.female} bought in {label} needs "
                    f"{herd.purchased_doe_settling_months} month(s) to settle, is bred, kids "
                    f"~{r.gestation_months} months later, and her {nouns.young_plural} then "
                    f"need ~{grow_months} months to grow into the sale class — a "
                    f"~{lead}-month chain from cheque to sale (the tightest of your targets' "
                    "windows). Buying later than this month makes the target unfillable; "
                    "buying earlier spreads the cost and risk."
                ),
            )
        )
    return actions


def build_backward_plan(
    assumptions: SimulationAssumptions,
    targets: list[PlannerTarget],
    *,
    close_gaps_enabled: bool = True,
    risk_runs: int = 0,
    nouns: SpeciesNouns = GOAT_NOUNS,
) -> BackwardPlanReport:
    """Plan backward from calendar-dated sale targets to today's to-do list."""
    if not targets:
        raise ValueError("A plan needs at least one sale target.")

    start = assumptions.meta.start_year_month
    parse_year_month(start)
    offsets = [month_offset(start, target.year_month) for target in targets]
    last_month = max(offsets)
    if last_month > 240:
        raise ValueError(
            f"Target month {targets[offsets.index(last_month)].year_month} is beyond the "
            "simulation's 20-year horizon."
        )

    variant = assumptions.model_copy(deep=True)
    # Cover the last target even when the caller's horizon was shorter, so a
    # distant sale is judged by biology rather than by an arbitrary cutoff.
    variant.meta.horizon_months = max(variant.meta.horizon_months, last_month)

    sale_targets = [
        SaleTarget(month=offset, animal_class=target.animal_class, count=target.count)
        for target, offset in zip(targets, offsets, strict=True)
    ]
    plan = build_plan_report(
        variant,
        sale_targets,
        close_gaps_enabled=close_gaps_enabled,
        risk_runs=risk_runs,
        nouns=nouns,
    )

    purchases = plan.recommended_purchases or []
    core = _run_core(_plan_assumptions(variant, sale_targets, purchases or None))
    stage_plan = _stage_plan_rows(core, start, last_month)

    evaluation = plan.after if plan.after is not None else plan.before
    young_stock_classes = [
        target.animal_class for target in targets if target.animal_class in _YOUNG_STOCK_CLASSES
    ]
    actions: list[PlannerAction] = list(
        _purchase_actions(variant, purchases, start, young_stock_classes, nouns)
    )
    chains: list[RequirementChain] = []
    for target, offset, fill in zip(targets, offsets, evaluation.targets, strict=True):
        achievable = fill.met
        label = month_label(start, offset)
        actions.append(
            PlannerAction(
                month=offset,
                year_month=label,
                kind="sell",
                headline=f"Sell {target.count:g} {nouns.event_label(target.animal_class)}",
                detail=(
                    f"Planned revenue ≈ ₹{fill.revenue:,.0f}. "
                    + (
                        "The closed plan fills this target."
                        if achievable
                        else f"Short by {fill.shortfall:.1f} head even after purchases — "
                        "reduce the target or move it later."
                    )
                ),
            )
        )
        if target.animal_class in _YOUNG_STOCK_CLASSES:
            window = _ClassWindow(variant, target.animal_class)
            chain = _requirement_chain(variant, target, offset, achievable, window, nouns)
            chains.append(chain)
            bred_month = window.bred_month(offset, variant.reproduction.gestation_months)
            # A bred month before month 1 is not a to-do — it is the reason
            # the target cannot fill: the breeding that produced these
            # animals had to happen before the plan existed. Label it as a
            # missed deadline rather than an instruction.
            missed_deadline = bred_month < 1
            actions.append(
                PlannerAction(
                    month=bred_month,
                    year_month=month_label(start, bred_month),
                    kind="breed",
                    headline=(
                        f"Breed ~{chain.steps[0].quantity} {nouns.female_plural} "
                        f"({nouns.event_label(target.animal_class)} for {label})"
                    ),
                    detail=(
                        "This breeding window is already behind you — the target cannot "
                        "be met now; move the sale later or source young stock directly."
                        if missed_deadline
                        else "Serve every open, settled doe this month; conception ≈ "
                        f"{variant.reproduction.conception_rate:.0%} per service."
                    ),
                )
            )
            birth_month = window.birth_month(offset)
            actions.append(
                PlannerAction(
                    month=birth_month,
                    year_month=month_label(start, birth_month),
                    kind="expect_births",
                    headline=(
                        f"Expect ~{chain.steps[2].quantity} {nouns.young_plural} born "
                        f"(target {label})"
                    ),
                    detail=(
                        f"{nouns.parturition.capitalize()}-season care decides the mortality "
                        "the plan assumes (young-stock loss ≈ "
                        f"{variant.mortality.kid_pre_weaning:.0%} before weaning)."
                    ),
                )
            )
        else:
            chains.append(_pool_target_chain(target, achievable, nouns))

    if purchases and variant.herd.female_retention_fraction < 1.0:
        actions.append(
            PlannerAction(
                month=1,
                year_month=start,
                kind="retain",
                headline=f"Retain more female {nouns.young_plural}",
                detail=(
                    f"Retention is {variant.herd.female_retention_fraction:.0%} today; every "
                    "retained daughter replaces a purchased doe later. Raise retention (and "
                    "the breeding-doe cap if it binds) to cut the purchase bill."
                ),
            )
        )

    actions.sort(key=lambda action: (action.month, action.kind))

    notes = list(plan.notes)
    if purchases:
        total_bought = sum(event.count for event in purchases)
        first_month = min(event.month for event in purchases)
        notes.insert(
            0,
            f"Recommendation in one line: buy ~{total_bought:,.0f} {nouns.female_plural} from "
            f"{month_label(start, first_month)} so their {nouns.young_plural} are standing in "
            "the sale classes on time; each target's requirement chain shows the breeding "
            "behind its number.",
        )
    notes.insert(
        0,
        f"Plan runs {start} → {month_label(start, last_month)} "
        f"({last_month} months). Month 1 is {start}.",
    )

    return BackwardPlanReport(
        start_year_month=start,
        horizon_months=variant.meta.horizon_months,
        targets_echo=targets,
        plan=plan,
        stage_plan=stage_plan,
        actions=actions,
        chains=chains,
        notes=notes,
    )
