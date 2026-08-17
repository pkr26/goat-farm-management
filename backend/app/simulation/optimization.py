"""Deterministic bounded search for farm-size and operating decisions."""

import math
from collections.abc import Sequence
from itertools import product
from typing import Any

from .assumptions import MAX_HEAD, MAX_MONEY, SimulationAssumptions
from .engine import _ceil_head_ratio, _CoreResult, _run_core
from .results import OptimizationCandidate, OptimizationResult


def _linspace(low: float, high: float, steps: int) -> list[float]:
    if steps <= 1 or low == high:
        return [low]
    return [low + (high - low) * index / (steps - 1) for index in range(steps)]


def _unique_bounded(values: list[float], low: float, high: float) -> list[float]:
    bounded = sorted(min(high, max(low, value)) for value in values)
    unique: list[float] = []
    for value in bounded:
        # Subtraction-derived bounds such as ``1.0 - 0.7`` can differ from a
        # submitted ``0.3`` by one binary ULP. Treating those as two decisions
        # wastes a bounded optimizer slot on the same financing policy.
        tolerance = 8 * max(math.ulp(value), math.ulp(unique[-1])) if unique else 0.0
        if not unique or abs(value - unique[-1]) > tolerance:
            unique.append(value)
    return unique


def _candidate_from_core(
    assumptions: SimulationAssumptions,
    core: _CoreResult,
    *,
    rank: int = 0,
) -> OptimizationCandidate:
    policy = assumptions.optimization
    violations: list[str] = []
    if policy.maximum_project_cost is not None and core.project_cost > policy.maximum_project_cost:
        violations.append("project cost exceeds the configured maximum")
    if (
        policy.maximum_funding_gap is not None
        and core.additional_working_capital_required > policy.maximum_funding_gap
    ):
        violations.append("liquidity funding gap exceeds the configured maximum")
    if core.min_dscr is not None and core.min_dscr < policy.minimum_dscr:
        violations.append("minimum DSCR is below the configured floor")
    if core.projected_peak_head > core.capacity_places + 1e-9:
        violations.append("projected herd exceeds funded housing/equipment capacity")
    return OptimizationCandidate(
        rank=rank,
        starting_does=assumptions.herd.does,
        starting_bucks=assumptions.herd.bucks,
        max_breeding_does=assumptions.herd.max_breeding_does,
        sale_age_months=assumptions.growth.sale_age_months,
        female_retention_fraction=assumptions.herd.female_retention_fraction,
        loan_fraction=assumptions.finance.loan_fraction_of_project_cost,
        project_cost=core.project_cost,
        capacity_places=core.capacity_places,
        projected_peak_head=core.projected_peak_head,
        npv=core.npv,
        irr=core.irr,
        min_dscr=core.min_dscr,
        minimum_cash_balance=core.minimum_cash_balance,
        funding_gap=core.additional_working_capital_required,
        feasible=not violations,
        constraint_violations=violations,
    )


def _rank_key(candidate: OptimizationCandidate, objective: str) -> tuple[float, ...]:
    feasible = 1.0 if candidate.feasible else 0.0
    if objective == "liquidity":
        return feasible, candidate.minimum_cash_balance, candidate.npv
    if objective == "npv":
        return feasible, candidate.npv, candidate.minimum_cash_balance
    # Balanced rewards value creation but directly charges any unfunded cash
    # trough; DSCR and feasibility then distinguish similarly valued plans.
    dscr = candidate.min_dscr if candidate.min_dscr is not None else 1_000_000.0
    return feasible, candidate.npv - candidate.funding_gap, dscr, candidate.minimum_cash_balance


def _sample_evenly[T](values: list[T], limit: int) -> list[T]:
    if len(values) <= limit:
        return values
    if limit <= 0:
        return []
    if limit == 1:
        return [values[len(values) // 2]]
    indices = {round(index * (len(values) - 1) / (limit - 1)) for index in range(limit)}
    return [values[index] for index in sorted(indices)]


def _sample_grid(axes: Sequence[Sequence[Any]], limit: int) -> list[tuple[Any, ...]]:
    """Thin each decision axis until their product fits ``limit``.

    Striding across the flattened Cartesian product instead aliases against its
    period: a constant stride over ``retentions x loan_fractions`` can land on
    the same loan fraction every time, silently dropping a whole decision
    dimension from the search while still spending the full candidate budget.
    Thinning the axes keeps every dimension represented.
    """
    if limit <= 0:
        return []
    counts = [len(axis) for axis in axes]

    def product_size(sizes: list[int]) -> int:
        size = 1
        for count in sizes:
            size *= count
        return size

    # Shed one value at a time from whichever axis is currently widest, so the
    # grid lands as close under the budget as it can. Halving overshot: three
    # retentions x three loan fractions capped at six collapsed the retentions
    # to one and spent only three of the six available slots.
    while product_size(counts) > limit:
        widest = max(range(len(counts)), key=lambda index: counts[index])
        if counts[widest] <= 1:
            break
        counts[widest] -= 1
    thinned = [_sample_evenly(list(axis), count) for axis, count in zip(axes, counts, strict=True)]
    grid: list[tuple[Any, ...]] = [tuple(combination) for combination in product(*thinned)]
    return grid[:limit]


def run_optimization(a: SimulationAssumptions) -> OptimizationResult:
    """Search a reproducible, schema-bounded decision grid.

    The optimizer changes only decisions a farmer can actually act on: opening
    doe count/target breeding pool, sale age, female retention and debt share.
    Biology, prices and risk assumptions remain untouched, making each
    recommendation directly comparable with the submitted baseline.
    """
    policy = a.optimization
    base_core = _run_core(a)
    baseline = _candidate_from_core(a, base_core)
    baseline_key = (
        a.herd.does,
        a.herd.bucks,
        a.herd.max_breeding_does,
        a.growth.sale_age_months,
        a.herd.female_retention_fraction,
        a.finance.loan_fraction_of_project_cost,
    )

    doe_scales = _linspace(policy.doe_scale_low, policy.doe_scale_high, policy.doe_scale_steps)
    sale_ages = list(
        range(
            max(6, a.growth.sale_age_months - policy.sale_age_radius_months),
            min(24, a.growth.sale_age_months + policy.sale_age_radius_months) + 1,
        )
    )
    retentions = _unique_bounded(
        [
            a.herd.female_retention_fraction - policy.retention_step,
            a.herd.female_retention_fraction,
            a.herd.female_retention_fraction + policy.retention_step,
        ],
        0.0,
        1.0,
    )
    max_loan = max(0.0, 1.0 - a.finance.subsidy_fraction)
    loan_fractions = _unique_bounded(
        [
            a.finance.loan_fraction_of_project_cost - policy.loan_fraction_step,
            a.finance.loan_fraction_of_project_cost,
            a.finance.loan_fraction_of_project_cost + policy.loan_fraction_step,
        ],
        0.0,
        max_loan,
    )
    # The configured ceiling includes the submitted baseline. Reserving its
    # slot guarantees both a fair comparison and a hard bound on CPU work.
    grid: list[tuple[Any, ...]] = _sample_grid(
        (doe_scales, sale_ages, retentions, loan_fractions),
        max(0, policy.max_candidates - 1),
    )

    seen: set[tuple[int, int, int, int, float, float]] = {baseline_key}
    evaluated: list[OptimizationCandidate] = [baseline]
    for doe_scale, sale_age, retention, loan_fraction in grid:
        variant = a.model_copy(deep=True)
        variant.herd.does = min(MAX_HEAD, max(0, round(a.herd.does * doe_scale)))
        if a.herd.max_breeding_does == 0:
            # 0 is the documented "no cap" sentinel, not an unset value. Scaling
            # it turned an unconstrained-growth baseline into a hard-capped one
            # in every candidate, so the search never contained the policy the
            # user actually submitted.
            variant.herd.max_breeding_does = 0
        else:
            # ...and the same sentinel must never be produced by arithmetic:
            # round() reaches 0 for any small scale, which the engine would then
            # read as "unlimited" — the exact opposite of a zero cap.
            variant.herd.max_breeding_does = min(
                MAX_HEAD,
                max(variant.herd.does, round(a.herd.max_breeding_does * doe_scale), 1),
            )
        if variant.herd.auto_purchase_bucks:
            variant.herd.bucks = (
                _ceil_head_ratio(variant.herd.does, variant.culling.buck_doe_ratio)
                if variant.herd.does > 0
                else 0
            )
        if a.finance.initial_stock_cost > 0.0:
            stock_cost_delta = (variant.herd.does - a.herd.does) * a.herd.doe_purchase_price + (
                variant.herd.bucks - a.herd.bucks
            ) * a.herd.buck_purchase_price
            # Floor above the sentinel, not at it. 0.0 does not mean "free" for
            # this field — engine.py reads it as "auto-compute from the herd",
            # so a candidate whose delta drove the explicit cost to zero was
            # silently re-priced off the herd and charged MORE for a SMALLER
            # flock, inverting its rank.
            variant.finance.initial_stock_cost = min(
                MAX_MONEY,
                max(0.01, a.finance.initial_stock_cost + stock_cost_delta),
            )
        variant.growth.sale_age_months = sale_age
        variant.herd.female_retention_fraction = retention
        variant.finance.loan_fraction_of_project_cost = loan_fraction
        key = (
            variant.herd.does,
            variant.herd.bucks,
            variant.herd.max_breeding_does,
            sale_age,
            retention,
            loan_fraction,
        )
        if key in seen:
            continue
        seen.add(key)
        validated = SimulationAssumptions.model_validate(variant.model_dump())
        evaluated.append(_candidate_from_core(validated, _run_core(validated)))

    evaluated.sort(key=lambda item: _rank_key(item, policy.objective), reverse=True)
    for rank, candidate in enumerate(evaluated, start=1):
        candidate.rank = rank
    baseline = next(
        (
            candidate
            for candidate in evaluated
            if (
                candidate.starting_does,
                candidate.starting_bucks,
                candidate.max_breeding_does,
                candidate.sale_age_months,
                candidate.female_retention_fraction,
                candidate.loan_fraction,
            )
            == baseline_key
        ),
        baseline,
    )
    recommended = next((candidate for candidate in evaluated if candidate.feasible), None)
    alternatives = [candidate for candidate in evaluated if candidate is not recommended][:4]
    return OptimizationResult(
        objective=policy.objective,
        evaluated_candidates=len(evaluated),
        feasible_candidates=sum(1 for candidate in evaluated if candidate.feasible),
        baseline=baseline,
        recommended=recommended,
        alternatives=alternatives,
    )
