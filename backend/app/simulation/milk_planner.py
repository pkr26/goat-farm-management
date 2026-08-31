"""Milk-target-driven dairy planning: the inverse of the forward simulation.

The forward engine answers "given this herd, how much milk?". A dairy farm is
planned the other way round: a procurement contract or bulk buyer needs
``X`` litres per *day*, and the farm must decide the herd that delivers it —
how many animals, at which lactation/pregnancy stages, bought when, and bred
when. That inverse problem is what this module solves.

The key biological facts the plan is built on (Murrah buffalo defaults):

- Yield follows a lactation curve (Wood's shape for the Murrah preset): low in
  the days after calving, peaking around day 65 of lactation, then declining
  until dry-off (see ``simulation/lactation.py``).
- A buffalo is served (AI) after a voluntary waiting period, needs on average
  ``1 / conception_rate`` monthly services to conceive, and carries a
  ``gestation_months`` pregnancy — so a calving planned for month T needs AI
  to start around ``T - gestation - (1/conception - 1)``.
- Milk therefore comes from a *stage ladder*: the herd must hold animals in
  every month-of-lactation slot, and calvings must arrive every month, or the
  daily tank swings with the curve.

Design: a steady calving flow ``f`` (freshenings per month) fills the
lactation slots with ``f`` head each, so daily output is flat at
``f x lactation_litres / calving_interval`` regardless of the curve's shape.
The planner seeds ``f`` from that identity, then runs its own expected-value
monthly simulator of the doe subsystem (same pool conventions, curve function
and compounding rates as the engine) and rescales the herd until the measured
output meets the target. What the engine cannot express — buying *in-milk*
animals at mixed stages, which is how dairy herds are actually assembled — is
this planner's core move, so the planner's projection, not an engine event
list, is the dairy truth for milk.

The engine remains the money model: for full financials, set the starting
herd to the planned size with the mixed foundation state (the preset's
convention) and run the simulation.
"""

import math
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from .assumptions import MAX_HEAD, ReproductionAssumptions, SimulationAssumptions
from .engine import monthly_mortality_rate
from .feed import DAYS_PER_MONTH
from .lactation import curve_from_assumptions


class MilkCurveSummary(BaseModel):
    """The lactation curve the plan is built on."""

    model_config = ConfigDict(extra="forbid", strict=True)

    shape: str  # "geometric" | "wood"
    lactation_litres: float
    lactation_months: int
    peak_day: float | None  # Wood shape only; None for geometric
    peak_month_of_lactation: int  # 1-based month of milk with the highest yield
    peak_daily_litres: float  # avg daily litres in that peak month (not the instantaneous peak)
    avg_daily_litres_per_milking_doe: float
    monthly_litres: list[float]  # litres per head by month of lactation


class MilkHerdDesign(BaseModel):
    """The herd that delivers the target, at steady state."""

    model_config = ConfigDict(extra="forbid", strict=True)

    daily_target_litres: float
    calving_interval_months: float  # VWP + expected services + gestation
    expected_services_per_conception: float
    breeding_does: float  # adult breeding herd (milking + dry + pregnant)
    milking_does: float  # average head in milk
    dry_does: float  # average head dry/pregnant
    calvings_per_month: float  # freshenings needed per month
    ai_services_per_month: float  # AI/natural services needed per month
    replacement_does_per_month: float  # cull + mortality outflow to replace
    heifer_calves_available_per_month: float
    heifer_surplus_per_month: float  # available - needed; negative = shortfall
    starting_does_credited: float
    purchases_total: float  # ramp tranches + replacement bridge, whole plan
    replacement_purchases_total: float  # mortality/cull bridge, whole plan
    seasonal_low_daily_litres: float  # plan output in the worst calendar month
    seasonal_high_daily_litres: float
    herd_for_year_round_target: float  # breeding herd that holds target in the trough


class MilkPurchasePlan(BaseModel):
    """One procurement tranche."""

    model_config = ConfigDict(extra="forbid", strict=True)

    month: int
    count: float
    profile: str  # how the animals enter the herd


class MilkPlanMonth(BaseModel):
    """One projected month of the plan."""

    model_config = ConfigDict(extra="forbid", strict=True)

    month: int
    calendar_month: int
    breeding_does: float
    milking_does: float
    dry_does: float
    freshenings: float  # calvings this month
    ai_services: float  # services performed this month
    heifer_graduates: float
    projected_daily_litres: float
    target_daily_litres: float
    gap_daily_litres: float  # target - projected; positive = short
    projected_monthly_litres: float
    projected_monthly_revenue: float  # litres x effective milk price
    meets_target: bool  # within 5% of target


class MilkPlanReport(BaseModel):
    """Full milk plan: herd design, procurement and the monthly projection."""

    model_config = ConfigDict(extra="forbid", strict=True)

    target_daily_litres: float
    ramp_months: int
    hold_year_round: bool
    curve: MilkCurveSummary
    herd: MilkHerdDesign
    purchases: list[MilkPurchasePlan]
    projection: list[MilkPlanMonth]
    steady_from_month: int | None  # first month at/after which the plan holds
    steady_average_daily_litres: float | None
    achievable: bool
    notes: list[str] = Field(default_factory=list)


@dataclass(slots=True)
class _SimMonth:
    """One simulated month of the doe subsystem (expected-value head counts)."""

    month: int
    calendar_month: int
    breeding_does: float
    milking_does: float
    freshenings: float
    ai_services: float
    heifer_graduates: float
    daily_litres: float
    monthly_litres: float
    replacement_bought: float = 0.0


def _place_across_cycle(
    count: float,
    waiting: list[float],
    preg: list[float],
    lact: list[float],
    conception_rate: float,
) -> float:
    """Spread bought-in animals uniformly over the reproductive cycle.

    Mirrors the engine's dairy foundation placement, including the milking
    overlay anchored to month-since-calving: a waiting doe at slot i is i
    months fresh, a ready doe ~VWP months fresh, and a pregnant doe at
    gestation slot j is VWP + months-to-conceive + j months fresh (dry once
    that index passes the lactation length). A batch bought this way is
    producing the day it lands, with the stage spread that flattens the tank.

    Returns the head that join the open/ready pool.
    """
    n_slots = 1 + len(waiting) + len(preg)
    if n_slots <= 0 or count <= 0.0:
        return 0.0
    per_slot = count / n_slots
    for i in range(len(waiting)):
        waiting[i] += per_slot
    for j in range(len(preg)):
        preg[j] += per_slot
    k_months = max(1, round(1.0 / max(conception_rate, 1e-9)))
    overlay_indices = (
        list(range(len(waiting)))
        + [min(len(waiting), len(lact) - 1)]
        + [len(waiting) + k_months + j for j in range(len(preg))]
    )
    for overlay_index in overlay_indices:
        if 0 <= overlay_index < len(lact):
            lact[overlay_index] += per_slot
    return per_slot


def _simulate(
    assumptions: SimulationAssumptions,
    herd_target: float,
    ramp_months: int,
    months: int,
    *,
    replacement_bridge: bool = True,
) -> list[_SimMonth]:
    """Expected-value monthly simulation of the dairy doe subsystem.

    Pool progression, the milking overlay, the service-count breeding policy
    (sexed semen / repeat-breeder culls), per-phase juvenile mortality,
    max-age culling, the finishing pen and the monthly compounding of
    mortality/culling follow the engine's order and conventions exactly; the
    deliberate differences are documented in the module docstring (in-milk
    purchases, heifer top-up toward the planned herd size).
    """
    r = assumptions.reproduction
    herd = assumptions.herd
    mort = assumptions.mortality
    cull = assumptions.culling
    sales = assumptions.sales
    curve = curve_from_assumptions(sales, r.lactation_months)
    start_calendar = int(assumptions.meta.start_year_month.split("-")[1])

    n_service_buckets = max(r.sexed_semen_services, r.max_services_before_cull, 1)
    waiting = [0.0] * r.months_open_before_breeding
    svc = [0.0] * n_service_buckets
    preg = [0.0] * r.gestation_months
    preg_female_fraction = [r.sex_ratio_female] * r.gestation_months
    lact = [0.0] * r.lactation_months
    finishing = [0.0] * r.lactation_months
    heifers = [0.0] * max(1, r.age_at_first_breeding_months)
    doe_ages = [0.0] * (cull.max_doe_age_months + 1)

    def _spread_doe_ages(count: float) -> None:
        if count <= 0.0:
            return
        age_ceiling = max(cull.max_doe_age_months - 12, 0)
        span_lo = min(herd.foundation_doe_age_min_months, age_ceiling)
        span_hi = max(span_lo, min(herd.foundation_doe_age_max_months, age_ceiling))
        slots = list(range(span_lo, span_hi + 1)) or [span_lo]
        for age in slots:
            doe_ages[age] += count / len(slots)

    def _cull_does(count: float) -> float:
        """Engine semantics: culls leave the breeding pools now, keep milking
        in the finishing pen, and are gone when their overlay dries off.
        Returns the head that left entirely this month (no milk to finish)."""
        nonlocal svc, waiting, preg, finishing, lact
        if count <= 0.0:
            return 0.0
        pool_total = sum(svc) + sum(waiting) + sum(preg)
        if pool_total <= 0.0:
            return 0.0
        factor = max(0.0, 1.0 - count / pool_total)
        svc = [v * factor for v in svc]
        waiting = [w * factor for w in waiting]
        preg = [p * factor for p in preg]
        lact_total = sum(lact)
        if lact_total > 0.0:
            for stage in range(len(finishing)):
                finishing[stage] += count * lact[stage] / lact_total
            return 0.0
        return count

    # Existing starting herd, placed exactly as the engine would place it.
    if herd.does > 0:
        _spread_doe_ages(float(herd.does))
        if herd.foundation_flock_state == "mixed":
            svc[0] += _place_across_cycle(
                float(herd.does), waiting, preg, lact, r.conception_rate
            )
        else:  # "open": clean start, bred from month 1
            svc[0] += float(herd.does)

    # Procurement: in-milk animals at mixed stages, staged over the ramp.
    purchases_total = max(0.0, herd_target - float(herd.does))
    per_ramp_month = purchases_total / ramp_months if ramp_months > 0 else 0.0

    from .engine import phase_monthly_mortality_rate

    s_kid = 1.0 - phase_monthly_mortality_rate(mort.kid_pre_weaning, 3)
    s_weaner = 1.0 - phase_monthly_mortality_rate(mort.kid_post_weaning, 3)
    s_grower = 1.0 - monthly_mortality_rate(mort.grower)
    s_adult = 1.0 - monthly_mortality_rate(mort.adult)
    monthly_cull_rate = monthly_mortality_rate(cull.doe_cull_rate_annual)

    records: list[_SimMonth] = []
    for month in range(1, months + 1):
        calendar_month = (start_calendar - 1 + (month - 1)) % 12 + 1

        # Purchases (start of month, like engine events).
        if month <= ramp_months and per_ramp_month > 0.0:
            svc[0] += _place_across_cycle(
                per_ramp_month, waiting, preg, lact, r.conception_rate
            )
            _spread_doe_ages(per_ramp_month)

        # Lactation progression; waiting does graduate to ready; finished
        # culls leave when their overlay dries off (engine step 2).
        lact = [0.0, *lact[:-1]]
        finishing = [0.0, *finishing[:-1]]
        if waiting:
            svc[0] += waiting[-1]
            waiting = [0.0, *waiting[:-1]]

        # Pregnancy progression and calving; fresh dams start their VWP and
        # their lactation in the calving month (engine step 3).
        fresh = preg[-1]
        fresh_female_fraction = preg_female_fraction[-1]
        preg = [0.0, *preg[:-1]]
        preg_female_fraction = [r.sex_ratio_female, *preg_female_fraction[:-1]]
        if fresh > 0.0:
            if waiting:
                waiting[0] += fresh
            else:
                svc[0] += fresh
            lact[0] += fresh

        # Heifer pipeline aging: this month's female births enter at age 0 and
        # every cohort ages one month under its class survival. The graduation
        # decision is deferred to after the cull block below: crediting
        # graduates against the herd BEFORE this month's attrition is known
        # (rate cull + mortality + repeat-breeder culls + max-age overflow)
        # systematically under-credited home-bred heifers, and the replacement
        # bridge then bought animals the farm's own pipeline was already
        # producing. Graduates are first served the following month — a
        # one-month AI delay against the engine's same-month service, a far
        # smaller divergence than wrong procurement advice.
        pending_graduates = 0.0
        if heifers:
            pending_graduates = heifers[-1]
            heifers = [0.0, *heifers[:-1]]
        female_born = fresh * r.litter_size * (1.0 - r.stillbirth_rate) * fresh_female_fraction
        if heifers:
            heifers[0] += female_born
        heifers = [
            count * (s_kid if age < 3 else s_weaner if age < 6 else s_grower)
            for age, count in enumerate(heifers)
        ]

        # Breeding: engine step 4 — service-count buckets, sexed-semen rates,
        # repeat-breeder culls (finishing pen).
        ai_services = sum(svc)
        breeding_pool_before = ai_services + sum(waiting) + sum(preg)
        new_svc = [0.0] * n_service_buckets
        conceived_sexed = 0.0
        conceived_conventional = 0.0
        repeat_culls = 0.0
        for bucket, count in enumerate(svc):
            if count <= 0.0:
                continue
            sexed = bucket < r.sexed_semen_services
            rate = r.conception_rate * (r.sexed_conception_multiplier if sexed else 1.0)
            conceived = count * rate
            failed = count - conceived
            if sexed:
                conceived_sexed += conceived
            else:
                conceived_conventional += conceived
            if bucket + 1 < n_service_buckets:
                new_svc[bucket + 1] += failed
            elif r.max_services_before_cull > 0:
                repeat_culls += failed
            else:
                new_svc[bucket] += failed
        svc = new_svc
        conceived = conceived_sexed + conceived_conventional
        if conceived > 0.0:
            preg[0] += conceived
            preg_female_fraction[0] = (
                conceived_sexed * r.sexed_female_fraction
                + conceived_conventional * r.sex_ratio_female
            ) / conceived
        # Repeat breeders already left the service buckets above; only route
        # them into the finishing pen (no second removal from the pools —
        # that would double-count the cull and hollow out gestation) and scale
        # the parallel doe-age ledger so later max-age overflows stay honest.
        if repeat_culls > 0.0:
            if breeding_pool_before > 0.0:
                doe_ages = [
                    age * max(0.0, 1.0 - repeat_culls / breeding_pool_before) for age in doe_ages
                ]
            lact_total = sum(lact)
            if lact_total > 0.0:
                for stage in range(len(finishing)):
                    finishing[stage] += repeat_culls * lact[stage] / lact_total

        # Mortality, then max-age and rate-based culling with the engine's
        # foundation-year grace (month >= 13).
        waiting = [w * s_adult for w in waiting]
        svc = [v * s_adult for v in svc]
        preg = [p * s_adult for p in preg]
        lact = [l_ * s_adult for l_ in lact]
        finishing = [f_ * s_adult for f_ in finishing]
        doe_ages = [age * s_adult for age in doe_ages]

        overflow = doe_ages[-1]
        doe_ages = [0.0, *doe_ages[:-1]]
        _cull_does(overflow)
        if month >= 13:
            breeding_now = sum(svc) + sum(waiting) + sum(preg)
            _cull_does(breeding_now * monthly_cull_rate)
            doe_ages = [age * (1.0 - monthly_cull_rate) for age in doe_ages]

        # Graduation: with this month's attrition fully applied, home-bred
        # heifers get first claim on the gap to the planned herd — retention
        # fraction first, then headroom; the surplus is sold as young stock.
        heifer_graduates = 0.0
        breeding_now = sum(svc) + sum(waiting) + sum(preg)
        room = max(0.0, herd_target - breeding_now)
        if pending_graduates > 0.0:
            heifer_graduates = min(pending_graduates * herd.female_retention_fraction, room)
            svc[0] += heifer_graduates
            doe_ages[r.age_at_first_breeding_months] += heifer_graduates

        # Replacement bridge: after the ramp, buy back what mortality, culling,
        # repeat-breeder removal and max-age attrition drain PAST what heifer
        # graduates covered. The first home-bred heifers arrive only
        # ~age-at-first-breeding months after the first calvings, and without
        # this bridge the herd visibly melts through that gap (culling starts
        # month 13, graduates around month afb+1). A real dairy buys springers
        # through exactly this window.
        replacement_bought = 0.0
        if replacement_bridge and month > ramp_months:
            shortfall = herd_target - (sum(svc) + sum(waiting) + sum(preg))
            if shortfall > 0.5:  # below half a head is float dust, not a plan
                replacement_bought = shortfall
                svc[0] += _place_across_cycle(
                    replacement_bought, waiting, preg, lact, r.conception_rate
                )
                _spread_doe_ages(replacement_bought)

        monthly_litres = (
            sum(count * yield_month for count, yield_month in zip(lact, curve, strict=True))
            * sales.monthly_milk_yield_multipliers[calendar_month - 1]
        )
        record = _SimMonth(
            month=month,
            calendar_month=calendar_month,
            breeding_does=sum(svc) + sum(waiting) + sum(preg),
            milking_does=sum(lact),
            freshenings=fresh,
            ai_services=ai_services,
            heifer_graduates=heifer_graduates,
            daily_litres=monthly_litres / DAYS_PER_MONTH,
            monthly_litres=monthly_litres,
            replacement_bought=replacement_bought,
        )
        records.append(record)

    return records


def _measure(
    records: list[_SimMonth],
    window: int,
    hold_year_round: bool,
    ramp_months: int = 1,
) -> float:
    """Steady-state daily output.

    Average mode: mean over the tail window. Year-round mode: the minimum
    over everything after the ramp — the stage mix wobbles mid-horizon
    (heifer graduation waves), so sizing for only the final window's trough
    still leaves summer dips below target. The slice start is clamped so the
    tail is never empty: a 12-month projection with a 6-month ramp has no
    month-13+ to measure, and falling back to ALL records there would size
    the herd off the partial-herd ramp months (a several-fold over-purchase).
    """
    if hold_year_round:
        start = min(max(ramp_months, 12), max(ramp_months, len(records) - 1))
        tail = records[start:] or records[-1:]
        return min(m.daily_litres for m in tail)
    # Average mode must skip the ramp entirely: a 12-month projection with a
    # 6-month ramp otherwise sizes the herd so the *ramp-inclusive* mean hits
    # the target and every post-ramp month overshoots it by the ramp deficit.
    # When the post-ramp stretch is shorter than the window, average just
    # those months — never reach back into the ramp for filler.
    start = max(ramp_months, len(records) - window, 0)
    tail = records[start:][-window:]
    if not tail:
        return 0.0
    return sum(m.daily_litres for m in tail) / len(tail)


def _service_ladder(reproduction: ReproductionAssumptions) -> tuple[float, float, float]:
    """Expected services per successful conception, the blended female
    fraction of births, and the per-attempt repeat-breeder failure fraction,
    under the engine's per-attempt service policy (sexed semen for the first
    services, conventional after, repeat-breeder cull at the configured
    limit). All three feed the plan's steady-state identities.
    """
    r = reproduction
    n_services = max(r.max_services_before_cull, r.sexed_semen_services, 1)
    rates = [
        r.conception_rate * (r.sexed_conception_multiplier if i < r.sexed_semen_services else 1.0)
        for i in range(n_services)
    ]
    female_fractions = [
        r.sexed_female_fraction if i < r.sexed_semen_services else r.sex_ratio_female
        for i in range(n_services)
    ]
    survival = 1.0
    success_mass = 0.0
    services_mass = 0.0
    female_mass = 0.0
    for index, rate in enumerate(rates):
        probability = survival * rate
        success_mass += probability
        services_mass += probability * (index + 1)
        female_mass += probability * female_fractions[index]
        survival *= 1.0 - rate
    if success_mass <= 0.0:
        return 1.0 / max(r.conception_rate, 1e-9), r.sex_ratio_female, 0.0
    return services_mass / success_mass, female_mass / success_mass, survival


def _trend_is_stable(records: list[_SimMonth], window: int, multipliers: list[float]) -> bool:
    """No meaningful decline across the steady window, season-adjusted.

    Each month is deseasonalised by its own calendar multiplier first, so a
    tail window whose second half sits in the summer trough is not mistaken
    for a melting herd; real decline (pipeline insufficiency) survives the
    adjustment.
    """
    tail = records[-window:]
    if len(tail) < 4:
        return True

    def _mean(months: list[_SimMonth]) -> float:
        adjusted = [m.daily_litres / multipliers[m.calendar_month - 1] for m in months]
        return sum(adjusted) / len(adjusted)

    half = len(tail) // 2
    first_half = _mean(tail[:half])
    second_half = _mean(tail[half:])
    return first_half <= 0.0 or second_half >= 0.98 * first_half


def build_milk_plan(
    assumptions: SimulationAssumptions,
    daily_target_litres: float,
    *,
    ramp_months: int = 1,
    projection_months: int | None = None,
    hold_year_round: bool = False,
) -> MilkPlanReport:
    """Design the herd, procurement and breeding calendar for a milk target.

    Iterates the simulator: seed the herd from the flat-supply identity, run,
    measure steady output, rescale, repeat until the target is met (or the
    head-count cap says it cannot be).
    """
    assumptions = SimulationAssumptions.model_validate(assumptions.model_dump())
    r = assumptions.reproduction
    sales = assumptions.sales
    if sales.lactation_milk_litres <= 0.0 or r.lactation_months <= 0:
        raise ValueError(
            "The milk planner needs a dairy scenario: set sales.lactation_milk_litres > 0."
        )
    horizon = assumptions.meta.horizon_months
    months = min(horizon, projection_months or horizon)
    if months < 12:
        raise ValueError("projection window must cover at least 12 months")
    if ramp_months >= months:
        raise ValueError("ramp_months must leave at least one projection month after the ramp")

    curve = curve_from_assumptions(sales, r.lactation_months)
    expected_services, blended_female_fraction, repeat_breeder_fraction = _service_ladder(r)
    calving_interval = r.months_open_before_breeding + expected_services + r.gestation_months

    # Seed: f freshenings/month fill the lactation slots, giving flat daily
    # supply f x lactation_litres / (CI x DAYS_PER_MONTH); the breeding herd
    # that sustains f freshenings is f x CI.
    freshenings_needed = daily_target_litres * DAYS_PER_MONTH / sales.lactation_milk_litres
    herd_target = freshenings_needed * calving_interval
    if herd_target > MAX_HEAD:
        raise ValueError(
            f"A {daily_target_litres:g} L/day target needs ~{herd_target:,.0f} breeding "
            f"animals, beyond the model's {MAX_HEAD:,} head cap."
        )

    # Iterate to the design herd. Milk scales ~linearly with herd size at
    # fixed biology, so a multiplicative update converges in a few passes.
    window = min(24, max(12, months // 2))
    for _ in range(8):
        records = _simulate(assumptions, herd_target, ramp_months, months)
        measured = _measure(records, window, hold_year_round, ramp_months)
        if measured <= 0.0:
            break
        error = daily_target_litres / measured
        if abs(error - 1.0) <= 0.005:
            break
        herd_target = min(float(MAX_HEAD), herd_target * error)
    else:
        records = _simulate(assumptions, herd_target, ramp_months, months)
        measured = _measure(records, window, hold_year_round, ramp_months)

    achievable = measured >= 0.95 * daily_target_litres and _trend_is_stable(
        records, window, sales.monthly_milk_yield_multipliers
    )
    notes: list[str] = _build_notes(assumptions, herd_target, records, window, hold_year_round)

    steady_tail = records[-window:]
    steady_average = sum(m.daily_litres for m in steady_tail) / len(steady_tail)
    # "Holds from month M" means every 12-month window ending at or after M
    # clears the bar — reporting the FIRST clearing window alone would claim
    # a hold through later dips (heifer waves, seasonal troughs).
    steady_from = None
    rolling: list[float] = []
    last_failing_end = 0
    for record in records:
        rolling.append(record.daily_litres)
        if len(rolling) > 12:
            rolling.pop(0)
        if len(rolling) == 12:
            mean = sum(rolling) / 12.0
            floor = min(rolling) if hold_year_round else mean
            if floor < 0.98 * daily_target_litres:
                last_failing_end = record.month
    if last_failing_end == 0:
        steady_from = 1
    elif records[-1].month - last_failing_end >= 12:
        steady_from = last_failing_end + 1

    # The seasonal band scales the steady average by each multiplier's
    # distance from the multipliers' OWN mean (which need not be 1.0), so it
    # matches what the simulation actually produces rather than assuming a
    # normalised curve.
    seasonality = sales.monthly_milk_yield_multipliers
    seasonality_mean = sum(seasonality) / len(seasonality) or 1.0
    if seasonality_mean > 0.0 and steady_average > 0.0:
        seasonal_low = steady_average * min(seasonality) / seasonality_mean
        seasonal_high = steady_average * max(seasonality) / seasonality_mean
    else:
        seasonal_low = seasonal_high = 0.0
    if hold_year_round:
        # The design herd already holds the trough; scaling again would
        # double-count the seasonality.
        herd_year_round = herd_target
    else:
        herd_year_round = herd_target * seasonality_mean / max(min(seasonality), 1e-9)

    starting_credited = float(assumptions.herd.does)
    ramp_purchases = max(0.0, herd_target - starting_credited)
    replacement_purchases = sum(m.replacement_bought for m in records)
    purchases_total = ramp_purchases + replacement_purchases
    purchases = (
        [
            MilkPurchasePlan(
                month=m,
                count=ramp_purchases / ramp_months,
                profile="in-milk buffalo at mixed lactation stages (flat from day one)",
            )
            for m in range(1, ramp_months + 1)
        ]
        if ramp_purchases > 0.0
        else []
    )

    if sales.milk_price_per_kg_fat > 0.0 and sales.milk_fat_pct > 0.0:
        effective_price = sales.milk_price_per_kg_fat * sales.milk_fat_pct / 100.0
    else:
        effective_price = sales.milk_price_per_litre

    tail = records[-window:]
    avg_milking = sum(m.milking_does for m in tail) / len(tail)
    avg_breeding = sum(m.breeding_does for m in tail) / len(tail)
    avg_fresh = sum(m.freshenings for m in tail) / len(tail)
    avg_ai = sum(m.ai_services for m in tail) / len(tail)
    female_born = avg_fresh * r.litter_size * (1.0 - r.stillbirth_rate) * blended_female_fraction
    # Rough survival of a female calf to breeding age, for pipeline sizing.
    grower_years = max(0.0, (r.age_at_first_breeding_months - 6) / 12.0)
    to_breeding_age = (
        (1.0 - assumptions.mortality.kid_pre_weaning)
        * (1.0 - assumptions.mortality.kid_post_weaning)
        * math.pow(1.0 - assumptions.mortality.grower, grower_years)
    )
    heifers_available = female_born * to_breeding_age
    # Total replacement demand: voluntary cull + adult mortality + the
    # repeat-breeder culls the service policy removes (a fraction of every
    # breeding attempt fails all permitted services; attempts recur each
    # calving interval). Counting only the voluntary rate overstated the
    # heifer surplus whenever a service-cull limit was configured.
    replacement_needed = avg_breeding * (
        1.0 - math.pow(1.0 - assumptions.culling.doe_cull_rate_annual, 1 / 12.0)
    ) + avg_breeding * (1.0 - math.pow(1.0 - assumptions.mortality.adult, 1 / 12.0))
    if repeat_breeder_fraction > 0.0:
        attempts_per_month = 1.0 / max(calving_interval, 1e-9)
        replacement_needed += avg_breeding * attempts_per_month * repeat_breeder_fraction

    peak_index = max(range(len(curve)), key=lambda i: curve[i])
    report = MilkPlanReport(
        target_daily_litres=daily_target_litres,
        ramp_months=ramp_months,
        hold_year_round=hold_year_round,
        curve=MilkCurveSummary(
            shape=sales.milk_curve_shape,
            lactation_litres=sales.lactation_milk_litres,
            lactation_months=r.lactation_months,
            peak_day=sales.milk_peak_day if sales.milk_curve_shape == "wood" else None,
            peak_month_of_lactation=peak_index + 1,
            peak_daily_litres=curve[peak_index] / DAYS_PER_MONTH,
            avg_daily_litres_per_milking_doe=(
                sales.lactation_milk_litres / r.lactation_months / DAYS_PER_MONTH
            ),
            monthly_litres=curve,
        ),
        herd=MilkHerdDesign(
            daily_target_litres=daily_target_litres,
            calving_interval_months=calving_interval,
            expected_services_per_conception=expected_services,
            breeding_does=avg_breeding,
            milking_does=avg_milking,
            dry_does=avg_breeding - avg_milking,
            calvings_per_month=avg_fresh,
            ai_services_per_month=avg_ai,
            replacement_does_per_month=replacement_needed,
            heifer_calves_available_per_month=heifers_available,
            heifer_surplus_per_month=heifers_available - replacement_needed,
            starting_does_credited=starting_credited,
            purchases_total=purchases_total,
            replacement_purchases_total=replacement_purchases,
            seasonal_low_daily_litres=seasonal_low,
            seasonal_high_daily_litres=seasonal_high,
            herd_for_year_round_target=herd_year_round,
        ),
        purchases=purchases,
        projection=[
            MilkPlanMonth(
                month=m.month,
                calendar_month=m.calendar_month,
                breeding_does=m.breeding_does,
                milking_does=m.milking_does,
                dry_does=m.breeding_does - m.milking_does,
                freshenings=m.freshenings,
                ai_services=m.ai_services,
                heifer_graduates=m.heifer_graduates,
                projected_daily_litres=m.daily_litres,
                target_daily_litres=daily_target_litres,
                gap_daily_litres=daily_target_litres - m.daily_litres,
                projected_monthly_litres=m.monthly_litres,
                projected_monthly_revenue=m.monthly_litres * effective_price,
                meets_target=m.daily_litres >= 0.95 * daily_target_litres,
            )
            for m in records
        ],
        steady_from_month=steady_from,
        steady_average_daily_litres=steady_average,
        achievable=achievable,
        notes=notes,
    )
    return report


def _build_notes(
    assumptions: SimulationAssumptions,
    herd_target: float,
    records: list[_SimMonth],
    window: int,
    hold_year_round: bool,
) -> list[str]:
    r = assumptions.reproduction
    sales = assumptions.sales
    notes: list[str] = []

    replacement_purchases = sum(m.replacement_bought for m in records)
    if replacement_purchases > 0.5:
        per_month = replacement_purchases / max(1, len(records))
        notes.append(
            f"Replacement bridge: ~{replacement_purchases:,.0f} head (~{per_month:.1f}/month) "
            "bought across the plan to cover culling and mortality that the heifer pipeline "
            "cannot — heifers from this herd only start graduating around month "
            f"{1 + r.gestation_months + r.age_at_first_breeding_months}. If this line is "
            "large, raise heifer retention or lower the cull rate instead of buying."
        )

    # Breeding lead time: to land calvings in month T, first service around
    # T - gestation - (expected failed services before the one that sticks).
    lead = max(0, round(1.0 / r.conception_rate) - 1)
    notes.append(
        f"To land calvings in month T, start AI about {r.gestation_months + lead} months "
        f"earlier (gestation {r.gestation_months} + ~{lead} month(s) to conceive at "
        f"{r.conception_rate:.0%} per service); with a {r.months_open_before_breeding}-month "
        "voluntary waiting period after each calving. Monthly AI sessions run above "
        "calvings / conception rate because some conceptions are lost to culling and "
        "mortality during gestation."
    )
    seasonality = sales.monthly_milk_yield_multipliers
    if max(seasonality) - min(seasonality) > 1e-6:
        notes.append(
            "Yield seasonality moves the tank between "
            f"{min(seasonality):.2f}x and {max(seasonality):.2f}x the annual mean. This plan "
            + (
                "holds the target in the trough month."
                if hold_year_round
                else "meets the target on the 12-month average: expect summer shortfalls and "
                "winter surplus. Ask for the year-round variant to size for the trough."
            )
        )
    tail = records[-window:]
    if tail:
        half = len(tail) // 2 or 1
        first_half = sum(m.daily_litres for m in tail[:half]) / half
        second_half = sum(m.daily_litres for m in tail[half:]) / (len(tail) - half)
        if first_half > 0 and second_half < 0.98 * first_half:
            notes.append(
                "Projected output declines across the horizon: culling and mortality are "
                "outrunning the heifer pipeline, so this target is NOT achievable as "
                "configured. Retain more heifers (raise herd.female_retention_fraction or "
                "reproduction.sex_ratio_female) or plan replacement purchases."
            )
    if herd_target > float(assumptions.herd.does):
        notes.append(
            f"Procurement: buy ~{herd_target - assumptions.herd.does:,.0f} in-milk animals at "
            "mixed lactation stages (staged over the ramp); a batch bought this way is "
            "producing the day it lands."
        )
    elif herd_target < float(assumptions.herd.does):
        notes.append(
            f"The starting herd ({assumptions.herd.does}) already exceeds the ~{herd_target:,.0f} "
            "needed for this target; the surplus is available for sale or a bigger contract."
        )
    notes.append(
        "Full financials (feed, labour, loan, cash flow) for this herd: set the starting herd "
        "to the planned size with the mixed foundation state and run the simulation. Scheduled "
        "event purchases in the engine are modelled as dry animals that settle before first "
        "service — this planner's projection is the dairy truth for milk. Milk revenue here "
        "uses today's effective price; the engine also applies price seasonality and growth."
    )
    return notes
