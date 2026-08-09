"""Deterministic monthly sex-age-structured cohort engine (GBADs/CIRAD-EcoRum style).

All animal counts are expected values (floats); nothing is rounded inside the
loop. The female pipeline is::

    f_kid -> f_weaner -> f_grower (6 m .. first-breeding age-1)
    -> open does -> pregnant (gestation months) -> lactating -> open ...

Males follow f/m kid -> weaner -> grower and are sold for meat at
``growth.sale_age_months``. Breeding-age female growers are retained into the
doe pool at ``herd.female_retention_fraction`` (subject to
``herd.max_breeding_does``); the surplus is sold as meat.

Documented v1 approximations:
- Foundation does are mixed-age adults (ages spread uniformly over 24-60
  months); with ``herd.foundation_flock_state="mixed"`` (the default) they are
  also spread uniformly across the reproductive cycle, while ``"open"`` starts
  them all open and ready to breed in month 1. Initial kids/weaners/growers
  are placed mid-class (age 1 / 4 / mid-grower).
- Scheduled herd events (``SimulationAssumptions.events``) are applied at the
  start of their month, before aging/breeding/mortality, and purchased animals
  are placed mid-class like foundation stock. Event purchases are operating
  cost in that month (they never join the project cost); adult event sales are
  booked at cull value, young-stock sales at live-weight meat value, unless an
  explicit ``price_per_head`` is given.
- Conception requires at least one buck: with ``bucks == 0`` (a zero-buck
  starting herd without ``auto_purchase_bucks``, or after the whole battery is
  sold/culled) no doe conceives until a buck is present. Any positive buck
  count serves the whole herd at the full conception rate — there is no
  buck:doe ratio scaling of fertility in v1 (the ratio drives only the
  auto-purchase headcount).
- Weaning is modelled at month 3 (the kid class spans ages 0-2) versus the
  operational system's day 60, and the default meat sale age is 12 months
  versus the farm's 8-9 month marketing — the projection weans and sells
  systematically later than live records enforce.
- Shed/equipment capacity is based on the *initial* adult + grower count, not
  the projected peak herd.
- Cull removals (rate-based and max-age) are taken proportionally from all doe
  reproductive pools; doe ages are tracked in a parallel cohort array whose
  total always equals the pooled doe count.
- Working capital is ``working_capital_months`` x the average monthly opex of
  simulation year 1 (excluding scheduled event purchases).
- No depreciation and no terminal/herd-salvage value in v1. Symmetrically,
  when the loan term outlives the horizon, the balance still owed at the end
  is charged in the final month as extra principal — it is part of that
  month's ``debt_service`` (terminal debt without a terminal asset would
  overstate every return metric), so the annual P&L debt-service columns and
  DSCR reflect the balloon.
"""

import math
from dataclasses import dataclass, field

from .assumptions import GrowthAssumptions, HerdEventAssumptions, SimulationAssumptions
from .feed import class_feed, combine_feed, cultivated_green_supply_kg
from .finance import (
    AmortizationRow,
    amortization_schedule,
    bcr,
    irr,
    npv,
    payback_month,
)
from .results import (
    AmortizationRowModel,
    AnnualPLRow,
    FeedSummary,
    MonthlyRow,
    ProjectCostBreakdown,
    SimulationResult,
    ViabilityMetrics,
)


def monthly_mortality_rate(annual_fraction: float) -> float:
    """Convert an annual mortality fraction to a monthly compounding rate."""
    # NOTE: math.pow (not ``**``) keeps the return type float under mypy 2.x,
    # which infers float ** float as Any inside function bodies.
    return 1.0 - math.pow(1.0 - annual_fraction, 1.0 / 12.0)


def weight_at_age(age_months: int, growth: GrowthAssumptions, adult_weight_kg: float) -> float:
    """Live weight (kg) at a given age: table for 0-12 m, then a linear approach
    to the adult weight, reached at 24 months."""
    table = growth.weight_by_age_months
    if age_months <= 0:
        return table[0]
    if age_months >= 24:
        return adult_weight_kg
    if age_months < len(table):
        return table[age_months]
    frac = (age_months - 12) / 12.0
    return table[12] + frac * (adult_weight_kg - table[12])


@dataclass
class _MonthRecord:
    """Raw monthly values before debt service is known (filled in pass 2)."""

    month: int
    calendar_month: int
    f_kids: float
    f_weaners: float
    f_growers: float
    open_does: float
    pregnant_does: float
    lactating_does: float
    m_kids: float
    m_weaners: float
    m_growers: float
    bucks: float
    total_herd: float
    births: float
    deaths: float
    sales_head: float
    sales_revenue: float
    culls_head: float
    cull_revenue: float
    milk_revenue: float
    manure_revenue: float
    purchases_head: float
    purchase_cost: float
    feed_green_kg: float
    feed_dry_kg: float
    feed_concentrate_kg: float
    feed_cost: float
    vet_cost: float
    labour_cost: float
    insurance_cost: float
    misc_cost: float
    fodder_surplus_kg: float
    events: list[str] = field(default_factory=list)

    @property
    def revenue(self) -> float:
        return self.sales_revenue + self.cull_revenue + self.milk_revenue + self.manure_revenue

    @property
    def opex(self) -> float:
        """Operating cost excluding debt service (includes buck purchases)."""
        return (
            self.feed_cost
            + self.vet_cost
            + self.labour_cost
            + self.insurance_cost
            + self.misc_cost
            + self.purchase_cost
        )


@dataclass
class _CoreResult:
    """Everything a deterministic run produces; consumed by the public wrapper
    and by the Monte Carlo / sensitivity drivers."""

    records: list[_MonthRecord]
    months: list[MonthlyRow]
    annual_pl: list[AnnualPLRow]
    amortization: list[AmortizationRow]
    feed_summary: FeedSummary
    project_cost: float
    shed_cost: float
    equipment_cost: float
    stock_cost: float
    working_capital: float
    loan_amount: float
    subsidy_amount: float
    equity: float
    npv: float
    irr: float | None
    bcr: float | None
    dscr_per_year: list[float]
    avg_dscr: float | None
    min_dscr: float | None
    payback_month: int | None


def _scale(values: list[float], factor: float) -> list[float]:
    return [v * factor for v in values]


_EVENT_LABELS = {
    "doe": "doe(s)",
    "buck": "buck(s)",
    "female_kid": "female kid(s)",
    "male_kid": "male kid(s)",
    "female_weaner": "female weaner(s)",
    "male_weaner": "male weaner(s)",
    "female_grower": "female grower(s)",
    "male_grower": "male grower(s)",
}

# Event-sale classes booked as adult disposals (cull pricing); the rest are
# young-stock meat sales.
_EVENT_ADULT_CLASSES = ("doe", "buck")


def _draw(pool: list[float], requested: float) -> float:
    """Remove up to ``requested`` head proportionally across an age pool
    (in place) and return how many were actually taken."""
    available = sum(pool)
    take = min(requested, available)
    if take > 0.0:
        factor = 1.0 - take / available
        pool[:] = [v * factor for v in pool]
    return take


def _run_core(a: SimulationAssumptions) -> _CoreResult:
    r = a.reproduction
    mort = a.mortality
    cull = a.culling
    g = a.growth
    sales = a.sales
    feed = a.feed
    costs = a.costs
    fin = a.finance

    afb = r.age_at_first_breeding_months
    sale_age = g.sale_age_months
    doe_w = g.adult_weight_doe_kg
    buck_w = g.adult_weight_buck_kg

    # --- initial cohorts (foundation stock placed mid-class, see module docstring)
    f_kid = [0.0, float(a.herd.female_kids), 0.0]  # ages 0..2
    f_weaner = [0.0, float(a.herd.female_weaners), 0.0]  # ages 3..5
    f_grower = [0.0] * (afb - 6)  # ages 6..afb-1
    if f_grower:
        f_grower[len(f_grower) // 2] = float(a.herd.female_growers)
    else:
        # afb == 6: the grower chain is empty, so a starting female grower is
        # already breeding-age. Park her at the end of the weaner class so she
        # graduates in month 1 through the normal retention/cap path — exactly
        # what a one-slot chain (afb == 7) does. Dropping her silently deleted
        # head the promoter is still charged for in shed and stock cost.
        f_weaner[-1] += float(a.herd.female_growers)
    m_kid = [0.0, float(a.herd.male_kids), 0.0]
    m_weaner = [0.0, float(a.herd.male_weaners), 0.0]
    m_grower = [0.0] * (sale_age - 6)  # ages 6..sale_age-1
    if m_grower:
        m_grower[len(m_grower) // 2] = float(a.herd.male_growers)
    else:
        # sale_age == 6: same empty-chain case — the male is already at sale
        # age, so he graduates out of the weaner class and is sold in month 1.
        m_weaner[-1] += float(a.herd.male_growers)

    open_waiting = [0.0] * r.months_open_before_breeding
    open_ready = float(a.herd.does)
    preg = [0.0] * r.gestation_months
    lact = [0.0] * r.lactation_months
    if a.herd.foundation_flock_state == "mixed" and a.herd.does > 0:
        # Realistic purchased flock: does spread uniformly across the whole
        # reproductive cycle (open-ready slot + waiting + gestation + lactation
        # slots), so kiddings and sales are spread from month 1 instead of
        # arriving as one synchronized wave.
        n_slots = 1 + len(open_waiting) + len(preg) + len(lact)
        per_slot = float(a.herd.does) / n_slots
        open_ready = per_slot
        open_waiting = [per_slot] * len(open_waiting)
        preg = [per_slot] * len(preg)
        lact = [per_slot] * len(lact)
    # Parallel doe age cohorts (all breeding does). Foundation does are spread
    # uniformly over ages 24..60 months (a purchased flock is mixed-age); this
    # avoids an artificial mass max-age cull when a synchronized cohort would
    # otherwise cross max_doe_age_months together.
    doe_ages = [0.0] * (cull.max_doe_age_months + 1)
    if a.herd.does > 0:
        span_lo, span_hi = 24, min(60, cull.max_doe_age_months - 12)
        # Defense past the schema floor (ge=36 guarantees span_hi >= span_lo):
        # never spread over an empty range (ZeroDivisionError).
        slots = list(range(span_lo, span_hi + 1)) or [span_lo]
        per_slot = float(a.herd.does) / len(slots)
        for age in slots:
            doe_ages[age] = per_slot
    bucks = float(a.herd.bucks)

    s_kid = 1.0 - monthly_mortality_rate(mort.kid_pre_weaning)
    s_weaner = 1.0 - monthly_mortality_rate(mort.kid_post_weaning)
    s_grower = 1.0 - monthly_mortality_rate(mort.grower)
    s_adult = 1.0 - monthly_mortality_rate(mort.adult)
    # Same annual -> monthly compounding converter as the mortality classes:
    # twelve months of it remove exactly doe_cull_rate_annual of the pool.
    monthly_cull_rate = monthly_mortality_rate(cull.doe_cull_rate_annual)

    start_month = int(a.meta.start_year_month.split("-")[1])
    f_grower_mid_age = (6 + afb - 1) // 2
    m_grower_mid_age = (6 + sale_age - 1) // 2

    records: list[_MonthRecord] = []

    # Scheduled herd events grouped by simulation month (schema guarantees
    # month <= horizon).
    events_by_month: dict[int, list[HerdEventAssumptions]] = {}
    for event in a.events:
        events_by_month.setdefault(event.month, []).append(event)

    for month in range(1, a.meta.horizon_months + 1):
        calendar_month = (start_month - 1 + (month - 1)) % 12 + 1
        births = deaths = 0.0
        sales_head = sales_revenue = 0.0
        culls_head = cull_revenue = 0.0
        purchases_head = purchase_cost = 0.0
        event_log: list[str] = []

        # Meat price with the seasonal (Eid) uplift for this calendar month.
        uplift = 1.0 + sales.eid_price_uplift if calendar_month == sales.eid_month else 1.0
        meat_price = sales.meat_price_per_kg * uplift

        # --- 0. scheduled herd events (user-programmed purchases/sales) -----
        # Applied at the start of the month, before aging/breeding/mortality,
        # so purchased animals face this month's mortality like newborns do.
        # Purchases are opex (never project cost); adult sales are booked as
        # culls, young-stock sales as meat sales.
        for event in events_by_month.get(month, ()):
            label = _EVENT_LABELS[event.animal_class]
            if event.kind == "purchase":
                n = event.count
                if event.animal_class == "doe":
                    open_ready += n
                    doe_ages[afb] += n
                    default_price = a.herd.doe_purchase_price
                elif event.animal_class == "buck":
                    bucks += n
                    default_price = a.herd.buck_purchase_price
                elif event.animal_class == "female_kid":
                    f_kid[1] += n  # mid-class (age 1), like foundation kids
                    default_price = weight_at_age(1, g, doe_w) * meat_price
                elif event.animal_class == "male_kid":
                    m_kid[1] += n
                    default_price = weight_at_age(1, g, doe_w) * meat_price
                elif event.animal_class == "female_weaner":
                    f_weaner[1] += n  # mid-class (age 4)
                    default_price = weight_at_age(4, g, doe_w) * meat_price
                elif event.animal_class == "male_weaner":
                    m_weaner[1] += n
                    default_price = weight_at_age(4, g, doe_w) * meat_price
                elif event.animal_class == "female_grower":
                    default_price = weight_at_age(f_grower_mid_age, g, doe_w) * meat_price
                    if f_grower:
                        f_grower[len(f_grower) // 2] += n  # mid-class
                    else:  # afb == 6: a grower is already breeding-age
                        open_ready += n
                        doe_ages[afb] += n
                else:  # male_grower
                    default_price = weight_at_age(m_grower_mid_age, g, buck_w) * meat_price
                    if m_grower:
                        m_grower[len(m_grower) // 2] += n  # mid-class
                    else:  # sale_age == 6: already at sale age — resold at once
                        sales_head += n
                        sales_revenue += n * weight_at_age(sale_age, g, buck_w) * meat_price
                price = event.price_per_head if event.price_per_head is not None else default_price
                purchases_head += n
                purchase_cost += n * price
                event_log.append(
                    f"Purchased {n:g} {label} at ₹{price:,.0f}/head (₹{n * price:,.0f})"
                )
            else:  # sale
                requested = event.count
                if event.animal_class == "doe":
                    available = open_ready + sum(open_waiting) + sum(preg) + sum(lact)
                    take = min(requested, available)
                    if take > 0.0:
                        factor = 1.0 - take / available
                        open_ready *= factor
                        open_waiting = _scale(open_waiting, factor)
                        preg = _scale(preg, factor)
                        lact = _scale(lact, factor)
                        doe_ages = _scale(doe_ages, factor)
                    default_price = sales.cull_doe_price_per_kg * doe_w
                elif event.animal_class == "buck":
                    take = min(requested, bucks)
                    bucks -= take
                    default_price = sales.cull_buck_price_per_kg * buck_w
                elif event.animal_class == "female_kid":
                    take = _draw(f_kid, requested)
                    default_price = weight_at_age(1, g, doe_w) * meat_price
                elif event.animal_class == "male_kid":
                    take = _draw(m_kid, requested)
                    default_price = weight_at_age(1, g, doe_w) * meat_price
                elif event.animal_class == "female_weaner":
                    take = _draw(f_weaner, requested)
                    default_price = weight_at_age(4, g, doe_w) * meat_price
                elif event.animal_class == "male_weaner":
                    take = _draw(m_weaner, requested)
                    default_price = weight_at_age(4, g, doe_w) * meat_price
                elif event.animal_class == "female_grower":
                    take = _draw(f_grower, requested)
                    default_price = weight_at_age(f_grower_mid_age, g, doe_w) * meat_price
                else:  # male_grower
                    take = _draw(m_grower, requested)
                    default_price = weight_at_age(m_grower_mid_age, g, buck_w) * meat_price
                price = event.price_per_head if event.price_per_head is not None else default_price
                revenue = take * price
                if event.animal_class in _EVENT_ADULT_CLASSES:
                    culls_head += take
                    cull_revenue += revenue
                else:
                    sales_head += take
                    sales_revenue += revenue
                note = f"Sold {take:g} {label} at ₹{price:,.0f}/head (₹{revenue:,.0f})"
                if take < requested:
                    note += f" — only {take:g} of {requested:g} available"
                event_log.append(note)

        # --- 1. young-stock aging and graduations ---------------------------
        f_kid_out = f_kid[2]
        f_kid = [0.0, f_kid[0], f_kid[1]]
        f_wea_out = f_weaner[2]
        f_weaner = [f_kid_out, f_weaner[0], f_weaner[1]]
        if f_grower:
            f_gro_out = f_grower[-1]
            f_grower = [f_wea_out, *f_grower[:-1]]
        else:
            f_gro_out = f_wea_out

        m_kid_out = m_kid[2]
        m_kid = [0.0, m_kid[0], m_kid[1]]
        m_wea_out = m_weaner[2]
        m_weaner = [m_kid_out, m_weaner[0], m_weaner[1]]
        if m_grower:
            m_gro_out = m_grower[-1]
            m_grower = [m_wea_out, *m_grower[:-1]]
        else:
            m_gro_out = m_wea_out

        # Breeding-age females: retained fraction joins the doe pool (subject to
        # the cap), the surplus is sold as meat at the first-breeding-age weight.
        does_before = open_ready + sum(open_waiting) + sum(preg) + sum(lact)
        retained = f_gro_out * a.herd.female_retention_fraction
        if a.herd.max_breeding_does > 0:
            retained = min(retained, max(0.0, a.herd.max_breeding_does - does_before))
        f_surplus_sold = f_gro_out - retained
        open_ready += retained
        doe_ages[afb] += retained
        if f_surplus_sold > 0.0:
            sales_head += f_surplus_sold
            sales_revenue += f_surplus_sold * weight_at_age(afb, g, doe_w) * meat_price

        # Males exit the grower chain at sale age and are sold for meat.
        if m_gro_out > 0.0:
            sales_head += m_gro_out
            sales_revenue += m_gro_out * weight_at_age(sale_age, g, buck_w) * meat_price

        # --- 2. lactation progression; open does become ready again ---------
        lact_out = lact[-1]
        lact = [0.0, *lact[:-1]]
        if open_waiting:
            waiting_out = open_waiting[-1]
            open_waiting = [lact_out, *open_waiting[:-1]]
            open_ready += waiting_out
        else:
            open_ready += lact_out

        # --- 3. pregnancy progression and kidding ---------------------------
        kidding_does = preg[-1]
        preg = [0.0, *preg[:-1]]
        if kidding_does > 0.0:
            born = kidding_does * r.litter_size * (1.0 - r.stillbirth_rate)
            births += born
            f_born = born * r.sex_ratio_female
            f_kid[0] += f_born
            m_kid[0] += born - f_born
            lact[0] += kidding_does

        # --- 4. breeding of ready open does ---------------------------------
        # No buck, no conception: a herd without a sire cannot breed. Bucks
        # are otherwise only a cost/rotation line (step 6); see the module
        # docstring's approximation list.
        conceived = open_ready * r.conception_rate if bucks > 0.0 else 0.0
        preg[0] += conceived
        open_ready -= conceived

        # --- 5. mortality (all classes, including this month's newborns) -----
        pre = sum(f_kid) + sum(m_kid)
        f_kid, m_kid = _scale(f_kid, s_kid), _scale(m_kid, s_kid)
        deaths += pre - sum(f_kid) - sum(m_kid)

        pre = sum(f_weaner) + sum(m_weaner)
        f_weaner, m_weaner = _scale(f_weaner, s_weaner), _scale(m_weaner, s_weaner)
        deaths += pre - sum(f_weaner) - sum(m_weaner)

        pre = sum(f_grower) + sum(m_grower)
        f_grower, m_grower = _scale(f_grower, s_grower), _scale(m_grower, s_grower)
        deaths += pre - sum(f_grower) - sum(m_grower)

        # Doe pools and the parallel doe_ages array represent the same animals,
        # so only the pools (+ bucks) enter the death count.
        pre = open_ready + sum(open_waiting) + sum(preg) + sum(lact) + bucks
        open_ready *= s_adult
        open_waiting = _scale(open_waiting, s_adult)
        preg = _scale(preg, s_adult)
        lact = _scale(lact, s_adult)
        bucks *= s_adult
        doe_ages = _scale(doe_ages, s_adult)
        deaths += pre - (open_ready + sum(open_waiting) + sum(preg) + sum(lact) + bucks)

        # --- 6. culling and buck management ---------------------------------
        # Max-age cull: does aging past max_doe_age_months leave the herd.
        overflow = doe_ages[-1]
        doe_ages = [0.0, *doe_ages[:-1]]
        if overflow > 0.0:
            does_now = open_ready + sum(open_waiting) + sum(preg) + sum(lact)
            if does_now > 0.0:
                factor = 1.0 - min(1.0, overflow / does_now)
                open_ready *= factor
                open_waiting = _scale(open_waiting, factor)
                preg = _scale(preg, factor)
                lact = _scale(lact, factor)
            culls_head += overflow
            cull_revenue += overflow * sales.cull_doe_price_per_kg * doe_w

        # Rate-based doe cull, applied from month 13 (foundation-year grace).
        # The annual fraction compounds monthly like every other annual rate in
        # the model (a plain rate/12 removed only 18.3% of the does for a
        # documented 20% policy, and 64.8% for a "cull everything" 1.0).
        if month >= 13:
            does_now = open_ready + sum(open_waiting) + sum(preg) + sum(lact)
            culled = does_now * monthly_cull_rate
            if culled > 0.0:
                factor = 1.0 - monthly_cull_rate
                open_ready *= factor
                open_waiting = _scale(open_waiting, factor)
                preg = _scale(preg, factor)
                lact = _scale(lact, factor)
                doe_ages = _scale(doe_ages, factor)
                culls_head += culled
                cull_revenue += culled * sales.cull_doe_price_per_kg * doe_w

        # Buck rotation: cull the whole sire battery, then re-staff per ratio.
        if bucks > 0.0 and month % (cull.buck_rotation_years * 12) == 0:
            culls_head += bucks
            cull_revenue += bucks * sales.cull_buck_price_per_kg * buck_w
            bucks = 0.0
        does_now = open_ready + sum(open_waiting) + sum(preg) + sum(lact)
        needed_bucks = math.ceil(does_now / cull.buck_doe_ratio) if does_now > 0.0 else 0
        if a.herd.auto_purchase_bucks and bucks < needed_bucks:
            buy = needed_bucks - bucks
            purchases_head += buy
            purchase_cost += buy * a.herd.buck_purchase_price
            bucks += buy

        # --- 7. feed, opex and revenue accounting ---------------------------
        open_total = open_ready + sum(open_waiting)
        preg_total = sum(preg)
        lact_total = sum(lact)
        f_kid_total, m_kid_total = sum(f_kid), sum(m_kid)
        f_wea_total, m_wea_total = sum(f_weaner), sum(m_weaner)
        f_gro_total, m_gro_total = sum(f_grower), sum(m_grower)
        total_herd = (
            f_kid_total
            + m_kid_total
            + f_wea_total
            + m_wea_total
            + f_gro_total
            + m_gro_total
            + open_total
            + preg_total
            + lact_total
            + bucks
        )

        feed_total = combine_feed(
            [
                class_feed(
                    f_kid_total + m_kid_total,
                    weight_at_age(1, g, doe_w),
                    feed.dmi_kid_creep,
                    feed.concentrate_share_kid_creep,
                    feed,
                ),
                class_feed(
                    f_wea_total + m_wea_total,
                    weight_at_age(4, g, doe_w),
                    feed.dmi_weaner,
                    feed.concentrate_share_weaner,
                    feed,
                ),
                class_feed(
                    f_gro_total,
                    weight_at_age(f_grower_mid_age, g, doe_w),
                    feed.dmi_grower,
                    feed.concentrate_share_grower,
                    feed,
                ),
                class_feed(
                    m_gro_total,
                    weight_at_age(m_grower_mid_age, g, buck_w),
                    feed.dmi_grower,
                    feed.concentrate_share_grower,
                    feed,
                ),
                class_feed(
                    open_total,
                    doe_w,
                    feed.dmi_doe_maintenance,
                    feed.concentrate_share_doe_maintenance,
                    feed,
                ),
                class_feed(
                    preg_total,
                    doe_w,
                    feed.dmi_doe_pregnant,
                    feed.concentrate_share_doe_pregnant,
                    feed,
                ),
                class_feed(
                    lact_total,
                    doe_w,
                    feed.dmi_doe_lactating,
                    feed.concentrate_share_doe_lactating,
                    feed,
                ),
                class_feed(bucks, buck_w, feed.dmi_buck, feed.concentrate_share_buck, feed),
            ]
        )
        fodder_surplus = cultivated_green_supply_kg(feed) - feed_total.green_dm_kg

        milk_revenue = (
            lact_total
            * (sales.lactation_milk_litres / r.lactation_months)
            * sales.milk_price_per_litre
        )
        manure_revenue = (does_now + bucks) * sales.manure_income_per_adult_per_year / 12.0
        vet_cost = total_herd * costs.vet_per_animal_per_year / 12.0
        labourers = math.ceil(total_herd / costs.labour_per_head_threshold) if total_herd > 0 else 0
        labour_cost = max(1, labourers) * costs.labour_per_month if total_herd > 0 else 0.0
        young_value_kg = (
            (f_kid_total + m_kid_total) * weight_at_age(1, g, doe_w)
            + (f_wea_total + m_wea_total) * weight_at_age(4, g, doe_w)
            + f_gro_total * weight_at_age(f_grower_mid_age, g, doe_w)
            + m_gro_total * weight_at_age(m_grower_mid_age, g, buck_w)
        )
        stock_value = (
            does_now * a.herd.doe_purchase_price
            + bucks * a.herd.buck_purchase_price
            # Young stock insured at this month's market value (Eid uplift included).
            + young_value_kg * meat_price
        )
        insurance_cost = stock_value * costs.insurance_pct_stock_value_annual / 12.0

        records.append(
            _MonthRecord(
                month=month,
                calendar_month=calendar_month,
                f_kids=f_kid_total,
                f_weaners=f_wea_total,
                f_growers=f_gro_total,
                open_does=open_total,
                pregnant_does=preg_total,
                lactating_does=lact_total,
                m_kids=m_kid_total,
                m_weaners=m_wea_total,
                m_growers=m_gro_total,
                bucks=bucks,
                total_herd=total_herd,
                births=births,
                deaths=deaths,
                sales_head=sales_head,
                sales_revenue=sales_revenue,
                culls_head=culls_head,
                cull_revenue=cull_revenue,
                milk_revenue=milk_revenue,
                manure_revenue=manure_revenue,
                purchases_head=purchases_head,
                purchase_cost=purchase_cost,
                feed_green_kg=feed_total.green_kg,
                feed_dry_kg=feed_total.dry_kg,
                feed_concentrate_kg=feed_total.concentrate_kg,
                feed_cost=feed_total.cost,
                vet_cost=vet_cost,
                labour_cost=labour_cost,
                insurance_cost=insurance_cost,
                misc_cost=costs.misc_overhead_per_month,
                fodder_surplus_kg=fodder_surplus,
                events=event_log,
            )
        )

    # --- pass 2: project cost, debt service, cash flows, metrics -------------
    horizon = a.meta.horizon_months
    year1 = records[:12]
    avg_monthly_opex = sum(
        rec.feed_cost + rec.vet_cost + rec.labour_cost + rec.insurance_cost + rec.misc_cost
        for rec in year1
    ) / len(year1)

    # v1: shed/equipment capacity from the *initial* adult + grower headcount.
    capacity_places = float(
        a.herd.does + a.herd.bucks + a.herd.female_growers + a.herd.male_growers
    )
    shed_cost = capacity_places * costs.shed_cost_per_animal_place
    equipment_cost = capacity_places * costs.equipment_cost_per_animal
    if fin.initial_stock_cost > 0.0:
        stock_cost = fin.initial_stock_cost
    else:
        young_kg = (
            float(a.herd.female_kids + a.herd.male_kids) * weight_at_age(1, g, doe_w)
            + float(a.herd.female_weaners + a.herd.male_weaners) * weight_at_age(4, g, doe_w)
            + float(a.herd.female_growers) * weight_at_age(f_grower_mid_age, g, doe_w)
            + float(a.herd.male_growers) * weight_at_age(m_grower_mid_age, g, buck_w)
        )
        stock_cost = (
            a.herd.does * a.herd.doe_purchase_price
            + a.herd.bucks * a.herd.buck_purchase_price
            + young_kg * sales.meat_price_per_kg
        )
    working_capital = fin.working_capital_months * avg_monthly_opex
    project_cost = shed_cost + equipment_cost + stock_cost + working_capital
    loan_amount = fin.loan_fraction_of_project_cost * project_cost
    subsidy_amount = fin.subsidy_fraction * project_cost
    equity = project_cost - loan_amount - subsidy_amount

    schedule = amortization_schedule(
        loan_amount, fin.interest_rate_annual, fin.loan_term_months, fin.moratorium_months
    )

    months: list[MonthlyRow] = []
    cumulative = -equity
    # If the loan outlives the horizon, the balance still owed at the end is a
    # real claim on the promoter; with no terminal asset value (v1), it is
    # repaid in the final month as extra principal — part of that month's debt
    # service, so the annual P&L and DSCR see the balloon.
    terminal_balance = schedule[horizon - 1].closing_balance if horizon < len(schedule) else 0.0
    for rec in records:
        debt_service = schedule[rec.month - 1].payment if rec.month <= len(schedule) else 0.0
        if rec.month == horizon:
            debt_service += terminal_balance
        net_cash = rec.revenue - rec.opex - debt_service
        cumulative += net_cash
        months.append(
            MonthlyRow(
                month=rec.month,
                calendar_month=rec.calendar_month,
                f_kids=rec.f_kids,
                f_weaners=rec.f_weaners,
                f_growers=rec.f_growers,
                open_does=rec.open_does,
                pregnant_does=rec.pregnant_does,
                lactating_does=rec.lactating_does,
                m_kids=rec.m_kids,
                m_weaners=rec.m_weaners,
                m_growers=rec.m_growers,
                bucks=rec.bucks,
                total_herd=rec.total_herd,
                births=rec.births,
                deaths=rec.deaths,
                sales_head=rec.sales_head,
                sales_revenue=rec.sales_revenue,
                culls_head=rec.culls_head,
                cull_revenue=rec.cull_revenue,
                milk_revenue=rec.milk_revenue,
                manure_revenue=rec.manure_revenue,
                purchases_head=rec.purchases_head,
                purchase_cost=rec.purchase_cost,
                feed_green_kg=rec.feed_green_kg,
                feed_dry_kg=rec.feed_dry_kg,
                feed_concentrate_kg=rec.feed_concentrate_kg,
                feed_cost=rec.feed_cost,
                vet_cost=rec.vet_cost,
                labour_cost=rec.labour_cost,
                insurance_cost=rec.insurance_cost,
                misc_cost=rec.misc_cost,
                debt_service=debt_service,
                net_cash_flow=net_cash,
                cumulative_cash_flow=cumulative,
                fodder_surplus_kg=rec.fodder_surplus_kg,
                events=rec.events,
            )
        )

    # Annual P&L in 12-month blocks (last block may be partial).
    annual_pl: list[AnnualPLRow] = []
    flows = [-equity]
    # Gross benefit / cost series for the benefit-cost ratio. Keeping them
    # separate from the net ``flows`` is what makes the BCR an independent
    # metric instead of a restatement of NPV's sign (see finance.bcr).
    benefit_flows = [0.0]
    cost_flows = [equity]
    times = [0.0]
    for start in range(0, horizon, 12):
        block = months[start : start + 12]
        year = start // 12 + 1
        interest = sum(schedule[m.month - 1].interest for m in block if m.month <= len(schedule))
        principal = sum(schedule[m.month - 1].principal for m in block if m.month <= len(schedule))
        if terminal_balance > 0.0 and block[-1].month == horizon:
            # The terminal balloon is principal repaid in the final month; the
            # block's debt_service (= sum of monthly rows) already carries it.
            principal += terminal_balance
        meat = sum(m.sales_revenue for m in block)
        cull_rev = sum(m.cull_revenue for m in block)
        milk = sum(m.milk_revenue for m in block)
        manure = sum(m.manure_revenue for m in block)
        feed_cost = sum(m.feed_cost for m in block)
        vet = sum(m.vet_cost for m in block)
        labour = sum(m.labour_cost for m in block)
        insurance = sum(m.insurance_cost for m in block)
        misc = sum(m.misc_cost for m in block)
        purchases = sum(m.purchase_cost for m in block)
        debt = sum(m.debt_service for m in block)
        total_revenue = meat + cull_rev + milk + manure
        total_opex = feed_cost + vet + labour + insurance + misc + purchases
        net_cash = sum(m.net_cash_flow for m in block)
        annual_pl.append(
            AnnualPLRow(
                year=year,
                meat_revenue=meat,
                cull_revenue=cull_rev,
                milk_revenue=milk,
                manure_revenue=manure,
                total_revenue=total_revenue,
                feed_cost=feed_cost,
                vet_cost=vet,
                labour_cost=labour,
                insurance_cost=insurance,
                misc_cost=misc,
                stock_purchases=purchases,
                total_opex=total_opex,
                ebitda=total_revenue - total_opex,
                interest=interest,
                principal=principal,
                debt_service=debt,
                net_cash_flow=net_cash,
            )
        )
        flows.append(net_cash)
        benefit_flows.append(total_revenue)
        cost_flows.append(total_opex + debt)
        times.append(block[-1].month / 12.0)

    dscr_per_year = [
        (row.ebitda / row.debt_service) if row.debt_service > 0.0 else 0.0 for row in annual_pl
    ]
    active_dscr = [
        d for d, row in zip(dscr_per_year, annual_pl, strict=True) if row.debt_service > 0.0
    ]
    # None (not 0.0) when no year carries debt service: a DSCR of exactly zero
    # — or any negative one — is a real, catastrophic debt year, and a sentinel
    # that collides with it made consumers report "no debt in the horizon" for
    # a farm that cannot pay a rupee of its instalment from operations.
    avg_dscr = sum(active_dscr) / len(active_dscr) if active_dscr else None
    min_dscr = min(active_dscr) if active_dscr else None

    cum_series = [-equity, *[m.cumulative_cash_flow for m in months]]

    n_years = len(annual_pl)
    annual_green = [
        sum(m.feed_green_kg for m in months[y * 12 : (y + 1) * 12]) for y in range(n_years)
    ]
    annual_dry = [sum(m.feed_dry_kg for m in months[y * 12 : (y + 1) * 12]) for y in range(n_years)]
    annual_conc = [
        sum(m.feed_concentrate_kg for m in months[y * 12 : (y + 1) * 12]) for y in range(n_years)
    ]
    annual_feed_cost = [
        sum(m.feed_cost for m in months[y * 12 : (y + 1) * 12]) for y in range(n_years)
    ]
    # Land requirement from the horizon-average annual green-DM need. The green
    # DM requirement is the as-fed kg x DM content. The average is taken per
    # month and annualised: a horizon that is not a whole number of years ends
    # in a partial block, and dividing the ragged sum by n_years counted that
    # stub as a full year (a 121-month run reported 8% less land than a
    # 120-month one, and 13 months 45% less than 12).
    avg_annual_green_dm = (
        sum(g_kg * feed.green_dm_pct for g_kg in annual_green) * 12.0 / len(months)
        if months
        else 0.0
    )
    land_acres = avg_annual_green_dm / (feed.fodder_yield_t_dm_per_acre_year * 1000.0)

    return _CoreResult(
        records=records,
        months=months,
        annual_pl=annual_pl,
        amortization=schedule,
        feed_summary=FeedSummary(
            annual_green_kg=annual_green,
            annual_dry_kg=annual_dry,
            annual_concentrate_kg=annual_conc,
            annual_feed_cost=annual_feed_cost,
            land_requirement_acres=land_acres,
            fodder_deficit_months=sum(1 for m in months if m.fodder_surplus_kg < 0.0),
        ),
        project_cost=project_cost,
        shed_cost=shed_cost,
        equipment_cost=equipment_cost,
        stock_cost=stock_cost,
        working_capital=working_capital,
        loan_amount=loan_amount,
        subsidy_amount=subsidy_amount,
        equity=equity,
        npv=npv(fin.discount_rate_annual, flows, times),
        irr=irr(flows, times),
        bcr=bcr(fin.discount_rate_annual, benefit_flows, cost_flows, times),
        dscr_per_year=dscr_per_year,
        avg_dscr=avg_dscr,
        min_dscr=min_dscr,
        payback_month=payback_month(cum_series),
    )


def break_even_meat_price(a: SimulationAssumptions) -> float | None:
    """Meat price (₹/kg) at which NPV = 0, by bisection on a price multiplier.

    Only the meat price is scaled (cull/milk/manure prices are held at base).
    Returns ``None`` when even 5x the base price cannot lift NPV to zero, and
    0.0 when NPV is already non-negative with meat revenue zeroed out.
    """
    base_price = a.sales.meat_price_per_kg

    def npv_at(multiplier: float) -> float:
        variant = a.model_copy(deep=True)
        variant.sales.meat_price_per_kg = base_price * multiplier
        return _run_core(variant).npv

    if npv_at(0.0) >= 0.0:
        return 0.0
    if npv_at(5.0) < 0.0:
        return None
    lo, hi = 0.0, 5.0
    for _ in range(50):
        mid = (lo + hi) / 2.0
        if npv_at(mid) >= 0.0:
            hi = mid
        else:
            lo = mid
    return ((lo + hi) / 2.0) * base_price


def run_simulation(
    assumptions: SimulationAssumptions,
    *,
    with_break_even: bool = True,
    with_monte_carlo: bool = False,
    with_sensitivity: bool = False,
) -> SimulationResult:
    """Run the deterministic simulation and assemble the full result model."""
    core = _run_core(assumptions)
    metrics = ViabilityMetrics(
        project_cost=core.project_cost,
        loan_amount=core.loan_amount,
        subsidy_amount=core.subsidy_amount,
        equity=core.equity,
        npv=core.npv,
        irr=core.irr,
        bcr=core.bcr,
        dscr_per_year=core.dscr_per_year,
        avg_dscr=core.avg_dscr,
        min_dscr=core.min_dscr,
        payback_month=core.payback_month,
        break_even_meat_price_per_kg=(
            break_even_meat_price(assumptions) if with_break_even else None
        ),
    )
    result = SimulationResult(
        months=core.months,
        annual_pl=core.annual_pl,
        metrics=metrics,
        amortization=[
            AmortizationRowModel(
                month=row.month,
                opening_balance=row.opening_balance,
                payment=row.payment,
                interest=row.interest,
                principal=row.principal,
                closing_balance=row.closing_balance,
            )
            for row in core.amortization
        ],
        feed_summary=core.feed_summary,
        project_cost_breakdown=ProjectCostBreakdown(
            shed_cost=core.shed_cost,
            equipment_cost=core.equipment_cost,
            stock_cost=core.stock_cost,
            working_capital=core.working_capital,
        ),
    )
    if with_monte_carlo or with_sensitivity:
        from .montecarlo import run_monte_carlo, run_sensitivity

        if with_monte_carlo:
            result.monte_carlo = run_monte_carlo(assumptions)
        if with_sensitivity:
            result.sensitivity = run_sensitivity(assumptions)
    # Explanations are built last so the risk section can see MC/sensitivity.
    from .explain import build_metric_explanations, build_narrative_report

    result.metric_explanations = build_metric_explanations(assumptions, result)
    result.narrative_report = build_narrative_report(assumptions, result)
    return result
