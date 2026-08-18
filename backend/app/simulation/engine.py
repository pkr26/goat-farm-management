"""Deterministic monthly sex-age-structured cohort engine (GBADs/CIRAD-EcoRum style).

All animal counts are expected values (floats); nothing is rounded inside the
loop. The female pipeline is::

    f_kid -> f_weaner -> f_grower (6 m .. first-breeding age-1)
    -> open does -> pregnant (gestation months) -> lactating -> open ...

Males follow f/m kid -> weaner -> grower and are sold for meat at
``growth.sale_age_months``. Breeding-age female growers are retained into the
doe pool at ``herd.female_retention_fraction`` (subject to
``herd.max_breeding_does``); the surplus is sold as meat.

Documented model approximations:
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
- Conception is constrained by the available sire service capacity
  (``bucks * buck_doe_ratio``). A zero-buck herd cannot conceive, and an
  undersupplied battery serves a proportional share of ready does.
- Weaning is modelled at month 3 (the kid class spans ages 0-2) versus the
  operational system's day 60, and the default meat sale age is 12 months
  versus the farm's 8-9 month marketing — the projection weans and sells
  systematically later than live records enforce.
- Shed/equipment capacity follows ``costs.capacity_basis`` and defaults to the
  projected physical peak plus a configurable reserve.
- Cull removals (rate-based and max-age) are taken proportionally from all doe
  reproductive pools; doe ages are tracked in a parallel cohort array whose
  total always equals the pooled doe count.
- Working capital is ``working_capital_months`` x the average monthly opex of
  simulation year 1 (excluding scheduled event purchases).
- Straight-line depreciation, optional tax-loss carry-forward and recoverable
  terminal livestock/facility/working-capital values are explicit. When the
  loan term outlives the horizon, the balance still owed is charged in the
  final month as extra principal.
"""

import hashlib
import json
import math
from dataclasses import dataclass, field
from functools import cached_property

from .assumptions import (
    MAX_MONEY,
    GrowthAssumptions,
    HerdEventAssumptions,
    SimulationAssumptions,
)
from .feed import class_feed, combine_feed
from .finance import (
    AmortizationRow,
    amortization_schedule,
    bcr,
    mirr,
    npv,
    payback_month,
)
from .finance import (
    irr as _irr_of_flows,
)
from .market import (
    annual_growth_multiplier,
    cultivated_green_supply_kg_dm_for_month,
    feed_prices_for_month,
    meat_price_for_month,
    other_revenue_growth,
)
from .results import (
    AmortizationRowModel,
    AnnualPLRow,
    FeedSummary,
    MonthlyRow,
    ProjectCostBreakdown,
    SimulationResult,
    TerminalValueBreakdown,
    ViabilityMetrics,
)
from .shocks import MonthlyShockPath

MODEL_VERSION = "3.0.0"


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
        return growth.birth_weight_kg
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
    meat_price_per_kg: float
    culls_head: float
    cull_revenue: float
    milk_revenue: float
    manure_revenue: float
    purchases_head: float
    purchase_cost: float
    feed_green_kg: float
    feed_homegrown_green_kg: float
    feed_purchased_green_kg: float
    feed_dry_kg: float
    feed_concentrate_kg: float
    feed_cost: float
    vet_cost: float
    labour_cost: float
    insurance_cost: float
    misc_cost: float
    selling_cost: float
    fodder_surplus_kg: float
    fodder_stock_kg_dm: float
    fodder_waste_kg_dm: float
    stock_value: float
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
            + self.selling_cost
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
    capacity_places: float
    projected_peak_head: float
    terminal_value_breakdown: TerminalValueBreakdown
    loan_amount: float
    subsidy_amount: float
    equity: float
    npv: float
    # The monthly cash-flow series IRR is solved from. Stored rather than
    # solved eagerly: Monte Carlo (up to 2000 passes), the break-even search
    # (52) and the sensitivity tornado (17) all read only ``npv``, and IRR is
    # by far the most expensive metric here. Computing it on demand keeps it
    # off every one of those hot paths.
    cash_flows: list[float]
    discount_times: list[float]
    mirr: float | None
    bcr: float | None
    dscr_per_year: list[float]
    avg_dscr: float | None
    min_dscr: float | None
    payback_month: int | None
    terminal_value: float
    tax_total: float
    accounting_profit_total: float
    minimum_cash_balance: float
    minimum_cash_month: int
    additional_working_capital_required: float
    operating_margin: float | None

    @cached_property
    def irr(self) -> float | None:
        return _irr_of_flows(self.cash_flows, self.discount_times)


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


def _pool_avg_weight(
    pool: list[float],
    base_age: int,
    growth: GrowthAssumptions,
    adult_weight_kg: float,
    fallback_age: int,
) -> float:
    """Mean live weight of an age-indexed pool, for pricing a proportional draw.

    ``_draw`` removes head proportionally across every age slot, so a draw's
    mean weight is the pool's mean weight. Pricing it at one hard-coded
    mid-class age was only ever right for freshly *placed* stock: a pool filled
    organically by promotions has whatever distribution the run produced, and a
    grower chain spans up to 24 monthly slots. ``pool`` is indexed from
    ``base_age``; an empty pool has no composition to average, so it keeps the
    placement age (nothing is drawn from it anyway).
    """
    total = sum(pool)
    if total <= 0.0:
        return weight_at_age(fallback_age, growth, adult_weight_kg)
    return (
        sum(
            count * weight_at_age(base_age + offset, growth, adult_weight_kg)
            for offset, count in enumerate(pool)
        )
        / total
    )


def _ceil_head_ratio(heads: float, heads_per_unit: int) -> int:
    """Whole units needed for an expected (fractional) animal population.

    Cohort arithmetic repeatedly partitions and recombines floats.  An exact
    policy boundary such as 50 does can consequently arrive as
    ``50.00000000000001``; applying ``ceil`` directly to 2.0000000000000004
    buys a phantom third buck.  Snap only values within a few machine ULPs of
    an integer, preserving ordinary fractional demand (50.01 still needs 3).
    """
    ratio = heads / heads_per_unit
    nearest = round(ratio)
    if abs(ratio - nearest) <= 8 * math.ulp(ratio):
        return nearest
    return math.ceil(ratio)


def _run_core(a: SimulationAssumptions, shock_path: MonthlyShockPath | None = None) -> _CoreResult:
    r = a.reproduction
    mort = a.mortality
    cull = a.culling
    g = a.growth
    sales = a.sales
    feed = a.feed
    costs = a.costs
    fin = a.finance
    shocks = shock_path or MonthlyShockPath.neutral(a.meta.horizon_months)

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
        f_boundary_grower = 0.0
    else:
        # afb == 6: the grower chain is empty, so a starting female grower is
        # already at its graduation boundary. Keep that inventory separate
        # until month-1 graduation: parking it in the last weaner slot made a
        # scheduled weaner sale consume growers while a scheduled grower sale
        # saw an empty pool and sold nothing.
        f_boundary_grower = float(a.herd.female_growers)
    m_kid = [0.0, float(a.herd.male_kids), 0.0]
    m_weaner = [0.0, float(a.herd.male_weaners), 0.0]
    m_grower = [0.0] * (sale_age - 6)  # ages 6..sale_age-1
    if m_grower:
        m_grower[len(m_grower) // 2] = float(a.herd.male_growers)
        m_boundary_grower = 0.0
    else:
        # sale_age == 6: same empty-chain boundary. The male is already at sale
        # age and is sold during month-1 graduation unless an ordered grower
        # event removes him first.
        m_boundary_grower = float(a.herd.male_growers)

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

    def _add_purchased_does(count: float) -> None:
        """Spread bought-in adult does over the mixed-age range, like foundation stock.

        A purchased doe is a proven adult, not a maiden that has just cleared
        the grower chain. Writing her to ``doe_ages[afb]`` gave every scheduled
        purchase the full ``max_doe_age_months - afb`` of breeding life ahead of
        it — five years at the defaults — and made a restocking event look far
        more productive than buying real animals is.
        """
        if count <= 0.0:
            return
        span_lo, span_hi = 24, min(60, cull.max_doe_age_months - 12)
        # Defense past the schema floor (ge=36 guarantees span_hi >= span_lo):
        # never spread over an empty range (ZeroDivisionError).
        slots = list(range(span_lo, span_hi + 1)) or [span_lo]
        per_slot = count / len(slots)
        for age in slots:
            doe_ages[age] += per_slot

    _add_purchased_does(float(a.herd.does))
    bucks = float(a.herd.bucks)

    def _physical_head() -> float:
        """Heads physically present at the current intra-month boundary.

        ``_MonthRecord.total_herd`` is an end-of-month balance after mortality,
        culling and sales.  Capacity has to cover animals while they are still
        present, including ordered start-of-month events, newborns and sires
        procured before service.
        """
        return (
            sum(f_kid)
            + sum(m_kid)
            + sum(f_weaner)
            + sum(m_weaner)
            + sum(f_grower)
            + sum(m_grower)
            + f_boundary_grower
            + m_boundary_grower
            + open_ready
            + sum(open_waiting)
            + sum(preg)
            + sum(lact)
            + bucks
        )

    physical_peak_head = _physical_head()

    # Same annual -> monthly compounding converter as the mortality classes:
    # twelve months of it remove exactly doe_cull_rate_annual of the pool.
    monthly_cull_rate = monthly_mortality_rate(cull.doe_cull_rate_annual)

    start_month = int(a.meta.start_year_month.split("-")[1])
    # Derived from the placement index, never from the class bounds: a grower
    # chain covering ages 6..L-1 is filled at index len//2, i.e. age
    # 6 + (L-6)//2. Computing the valuation age as (6+L-1)//2 instead put it a
    # month lower for every even-length chain — which includes both schema
    # defaults — so opening and event-purchased growers were charged for a
    # lighter animal than the one the engine actually stocked.
    f_grower_mid_age = 6 + (afb - 6) // 2
    m_grower_mid_age = 6 + (sale_age - 6) // 2

    records: list[_MonthRecord] = []
    fodder_stock_kg_dm = feed.initial_fodder_stock_kg_dm

    # Scheduled herd events grouped by simulation month (schema guarantees
    # month <= horizon).
    events_by_month: dict[int, list[HerdEventAssumptions]] = {}
    for event in a.events:
        events_by_month.setdefault(event.month, []).append(event)

    for month in range(1, a.meta.horizon_months + 1):
        shock_index = month - 1
        calendar_month = (start_month - 1 + (month - 1)) % 12 + 1
        births = deaths = 0.0
        sales_head = sales_revenue = 0.0
        culls_head = cull_revenue = 0.0
        purchases_head = purchase_cost = 0.0
        event_log: list[str] = []
        # Bucks bought via a scheduled event this month, tracked separately so
        # the rotation cull below (step 6) can spare them — see that step.
        bucks_purchased_this_month = 0.0
        # Calendar seasonality, nominal escalation, explicit lunar-festival
        # months and Monte Carlo market shocks all meet in one auditable price.
        meat_price = meat_price_for_month(
            sales,
            simulation_month=month,
            calendar_month=calendar_month,
            shock_multiplier=shocks.meat_price[shock_index],
        )
        livestock_growth = other_revenue_growth(sales, month)
        cull_doe_price = sales.cull_doe_price_per_kg * livestock_growth
        cull_buck_price = sales.cull_buck_price_per_kg * livestock_growth
        doe_purchase_price = a.herd.doe_purchase_price * livestock_growth
        buck_purchase_price = a.herd.buck_purchase_price * livestock_growth

        # Shock multipliers operate on annual biological rates and are clamped
        # below one before conversion to a monthly compounding survival rate.
        kid_mortality_shock = shocks.kid_mortality[shock_index]
        adult_mortality_shock = shocks.adult_mortality[shock_index]
        s_kid = 1.0 - monthly_mortality_rate(
            min(0.999999, mort.kid_pre_weaning * kid_mortality_shock)
        )
        s_weaner = 1.0 - monthly_mortality_rate(
            min(0.999999, mort.kid_post_weaning * kid_mortality_shock)
        )
        s_grower = 1.0 - monthly_mortality_rate(min(0.999999, mort.grower * adult_mortality_shock))
        s_adult = 1.0 - monthly_mortality_rate(min(0.999999, mort.adult * adult_mortality_shock))

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
                    _add_purchased_does(n)
                    default_price = doe_purchase_price
                elif event.animal_class == "buck":
                    bucks += n
                    bucks_purchased_this_month += n
                    default_price = buck_purchase_price
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
                    if f_grower:
                        default_price = weight_at_age(f_grower_mid_age, g, doe_w) * meat_price
                        f_grower[len(f_grower) // 2] += n  # mid-class
                    else:
                        # afb == 6: keep the animal at the graduation boundary.
                        # She must still pass through the configured retention
                        # fraction and breeding-doe cap, exactly like the
                        # one-slot afb == 7 chain.
                        default_price = weight_at_age(afb, g, doe_w) * meat_price
                        f_boundary_grower += n
                else:  # male_grower
                    if m_grower:
                        default_price = weight_at_age(m_grower_mid_age, g, buck_w) * meat_price
                        m_grower[len(m_grower) // 2] += n  # mid-class
                    else:
                        # sale_age == 6: this is market-ready stock. Keep it at
                        # the explicit graduation boundary so a later ordered
                        # grower sale can address it; otherwise normal
                        # graduation sells it this month. Value both sides at
                        # the same sale-age weight.
                        default_price = weight_at_age(sale_age, g, buck_w) * meat_price
                        m_boundary_grower += n
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
                    default_price = cull_doe_price * doe_w
                elif event.animal_class == "buck":
                    take = min(requested, bucks)
                    bucks -= take
                    default_price = cull_buck_price * buck_w
                elif event.animal_class == "female_kid":
                    avg_kg = _pool_avg_weight(f_kid, 0, g, doe_w, 1)
                    take = _draw(f_kid, requested)
                    default_price = avg_kg * meat_price
                elif event.animal_class == "male_kid":
                    avg_kg = _pool_avg_weight(m_kid, 0, g, doe_w, 1)
                    take = _draw(m_kid, requested)
                    default_price = avg_kg * meat_price
                elif event.animal_class == "female_weaner":
                    avg_kg = _pool_avg_weight(f_weaner, 3, g, doe_w, 4)
                    take = _draw(f_weaner, requested)
                    default_price = avg_kg * meat_price
                elif event.animal_class == "male_weaner":
                    avg_kg = _pool_avg_weight(m_weaner, 3, g, doe_w, 4)
                    take = _draw(m_weaner, requested)
                    default_price = avg_kg * meat_price
                elif event.animal_class == "female_grower":
                    avg_kg = _pool_avg_weight(f_grower, 6, g, doe_w, f_grower_mid_age)
                    if f_grower:
                        take = _draw(f_grower, requested)
                    else:
                        take = min(requested, f_boundary_grower)
                        f_boundary_grower -= take
                    default_price = avg_kg * meat_price
                else:  # male_grower
                    avg_kg = _pool_avg_weight(m_grower, 6, g, buck_w, m_grower_mid_age)
                    if m_grower:
                        take = _draw(m_grower, requested)
                    else:
                        take = min(requested, m_boundary_grower)
                        m_boundary_grower -= take
                    default_price = avg_kg * meat_price
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

            # Events are ordered.  Capture every boundary so a purchase followed
            # by a same-month sale still funds the places occupied between them.
            physical_peak_head = max(physical_peak_head, _physical_head())

        # --- 1. young-stock aging and graduations ---------------------------
        f_kid_out = f_kid[2]
        f_kid = [0.0, f_kid[0], f_kid[1]]
        f_wea_out = f_weaner[2]
        f_weaner = [f_kid_out, f_weaner[0], f_weaner[1]]
        if f_grower:
            f_gro_out = f_grower[-1]
            f_grower = [f_wea_out, *f_grower[:-1]]
        else:
            f_gro_out = f_wea_out + f_boundary_grower
            f_boundary_grower = 0.0

        m_kid_out = m_kid[2]
        m_kid = [0.0, m_kid[0], m_kid[1]]
        m_wea_out = m_weaner[2]
        m_weaner = [m_kid_out, m_weaner[0], m_weaner[1]]
        if m_grower:
            m_gro_out = m_grower[-1]
            m_grower = [m_wea_out, *m_grower[:-1]]
        else:
            m_gro_out = m_wea_out + m_boundary_grower
            m_boundary_grower = 0.0

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
            effective_litter_size = min(4.0, r.litter_size * shocks.litter_size[shock_index])
            born = kidding_does * effective_litter_size * (1.0 - r.stillbirth_rate)
            births += born
            f_born = born * r.sex_ratio_female
            f_kid[0] += f_born
            m_kid[0] += born - f_born
            lact[0] += kidding_does

        # --- 4. breeding of ready open does ---------------------------------
        # Automatic sire procurement is a pre-service policy. Buying the needed
        # bucks after breeding made an under-supplied flock lose a full cycle
        # even though the same month's accounts said replacement sires arrived.
        does_now = open_ready + sum(open_waiting) + sum(preg) + sum(lact)
        needed_bucks = _ceil_head_ratio(does_now, cull.buck_doe_ratio) if does_now > 0.0 else 0
        if a.herd.auto_purchase_bucks and bucks < needed_bucks:
            buy = needed_bucks - bucks
            purchases_head += buy
            purchase_cost += buy * buck_purchase_price
            bucks += buy
            bucks_purchased_this_month += buy

        # One buck can serve only the configured number of ready does. This
        # turns the buck:doe ratio into a biological constraint rather than a
        # purchase-policy annotation.
        service_capacity = bucks * cull.buck_doe_ratio
        served_does = min(open_ready, service_capacity)
        effective_conception = min(1.0, r.conception_rate * shocks.conception[shock_index])
        conceived = served_does * effective_conception
        preg[0] += conceived
        open_ready -= conceived

        # Births and pre-service sire purchases both happen before mortality.
        # The end-of-month row cannot reconstruct this physical high-water mark.
        physical_peak_head = max(physical_peak_head, _physical_head())

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
        bucks_purchased_this_month *= s_adult
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
            cull_revenue += overflow * cull_doe_price * doe_w

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
                cull_revenue += culled * cull_doe_price * doe_w

        # Buck rotation: cull the standing sire battery, then re-staff per
        # ratio. Cull-then-restaff is one atomic policy — the wholesale cull
        # is only safe because the auto-purchase below rebuys sires the same
        # month. With auto_purchase_bucks disabled there is no automatic
        # replacement path (sires are the user's to manage via scheduled
        # events), and since conception is gated on ``bucks > 0`` (step 4),
        # firing the cull half alone would silently sterilize the projected
        # herd for the rest of the horizon. Skip the rotation instead and
        # keep the battery.
        # A buck bought via a scheduled event *this* month is fresh stock,
        # not a sire due for rotation — exclude it from the cull pool so the
        # user's explicit purchase isn't destroyed the same month it lands.
        if (
            a.herd.auto_purchase_bucks
            and bucks > 0.0
            and month % (cull.buck_rotation_years * 12) == 0
        ):
            cull_pool = max(0.0, bucks - bucks_purchased_this_month)
            culls_head += cull_pool
            cull_revenue += cull_pool * cull_buck_price * buck_w
            bucks -= cull_pool
        does_now = open_ready + sum(open_waiting) + sum(preg) + sum(lact)
        needed_bucks = _ceil_head_ratio(does_now, cull.buck_doe_ratio) if does_now > 0.0 else 0
        if a.herd.auto_purchase_bucks and bucks < needed_bucks:
            buy = needed_bucks - bucks
            purchases_head += buy
            purchase_cost += buy * buck_purchase_price
            bucks += buy

        physical_peak_head = max(physical_peak_head, _physical_head())

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
                *[
                    class_feed(
                        f_count + m_count,
                        weight_at_age(age, g, doe_w),
                        feed.dmi_kid_creep,
                        feed.concentrate_share_kid_creep,
                        feed,
                    )
                    for age, (f_count, m_count) in enumerate(zip(f_kid, m_kid, strict=True))
                ],
                *[
                    class_feed(
                        f_count + m_count,
                        weight_at_age(age, g, doe_w),
                        feed.dmi_weaner,
                        feed.concentrate_share_weaner,
                        feed,
                    )
                    for age, (f_count, m_count) in enumerate(
                        zip(f_weaner, m_weaner, strict=True), start=3
                    )
                ],
                *[
                    class_feed(
                        count,
                        weight_at_age(age, g, doe_w),
                        feed.dmi_grower,
                        feed.concentrate_share_grower,
                        feed,
                    )
                    for age, count in enumerate(f_grower, start=6)
                ],
                *[
                    class_feed(
                        count,
                        weight_at_age(age, g, buck_w),
                        feed.dmi_grower,
                        feed.concentrate_share_grower,
                        feed,
                    )
                    for age, count in enumerate(m_grower, start=6)
                ],
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

        # Physical fodder balance: opening stored DM loses quality, this month's
        # seasonally distributed production arrives, demand consumes home-grown
        # DM first, and only the real shortfall is purchased at market price.
        storage_loss_kg_dm = fodder_stock_kg_dm * feed.fodder_storage_loss_fraction_monthly
        usable_opening_stock = fodder_stock_kg_dm - storage_loss_kg_dm
        cultivated_supply_kg_dm = cultivated_green_supply_kg_dm_for_month(
            feed,
            calendar_month,
            yield_multiplier=shocks.fodder_yield[shock_index],
        )
        available_homegrown_kg_dm = usable_opening_stock + cultivated_supply_kg_dm
        homegrown_green_kg_dm = min(feed_total.green_dm_kg, available_homegrown_kg_dm)
        purchased_green_kg_dm = max(0.0, feed_total.green_dm_kg - homegrown_green_kg_dm)
        unused_homegrown_kg_dm = max(0.0, available_homegrown_kg_dm - homegrown_green_kg_dm)
        fodder_stock_kg_dm = min(unused_homegrown_kg_dm, feed.fodder_storage_capacity_kg_dm)
        fodder_waste_kg_dm = storage_loss_kg_dm + max(
            0.0, unused_homegrown_kg_dm - feed.fodder_storage_capacity_kg_dm
        )
        # This month's own production balance. Including the opening carry-over
        # made the figure the running stock level rather than a surplus, so it
        # compounded every month and contradicted the field's documented
        # meaning; the carry-over is published separately as fodder_stock_kg_dm.
        fodder_surplus = cultivated_supply_kg_dm - feed_total.green_dm_kg
        homegrown_green_kg = homegrown_green_kg_dm / feed.green_dm_pct
        purchased_green_kg = purchased_green_kg_dm / feed.green_dm_pct
        cultivated_green_kg = cultivated_supply_kg_dm / feed.green_dm_pct
        home_green_price, purchased_green_price, dry_price, concentrate_price = (
            feed_prices_for_month(
                feed,
                simulation_month=month,
                calendar_month=calendar_month,
                shock_multiplier=shocks.feed_price[shock_index],
            )
        )
        # Home-grown fodder is charged on what is GROWN, not on what is eaten:
        # green_price_per_kg is a cultivation cost, and seed, irrigation and
        # labour are spent on the whole crop whether or not the herd gets to it.
        # Costing it per kg consumed made storage spoilage and every tonne above
        # storage capacity free, so adding acreage could never cost anything and
        # the acreage decision had no downside to weigh.
        feed_cost = (
            cultivated_green_kg * home_green_price
            + purchased_green_kg * purchased_green_price
            + feed_total.dry_kg * dry_price
            + feed_total.concentrate_kg * concentrate_price
        )

        milk_revenue = (
            lact_total
            * (sales.lactation_milk_litres / r.lactation_months)
            * sales.milk_price_per_litre
            * livestock_growth
        )
        manure_revenue = (
            (does_now + bucks) * sales.manure_income_per_adult_per_year * livestock_growth / 12.0
        )
        operating_cost_growth = (
            annual_growth_multiplier(costs.operating_cost_growth_rate_annual, month)
            * shocks.operating_cost[shock_index]
        )
        vet_cost = total_herd * costs.vet_per_animal_per_year / 12.0 * operating_cost_growth
        labourers = (
            _ceil_head_ratio(total_herd, costs.labour_per_head_threshold) if total_herd > 0 else 0
        )
        labour_cost = (
            max(1, labourers) * costs.labour_per_month * operating_cost_growth
            if total_herd > 0
            else 0.0
        )
        young_value_kg = (
            sum(
                (f_count + m_count) * weight_at_age(age, g, doe_w)
                for age, (f_count, m_count) in enumerate(zip(f_kid, m_kid, strict=True))
            )
            + sum(
                (f_count + m_count) * weight_at_age(age, g, doe_w)
                for age, (f_count, m_count) in enumerate(
                    zip(f_weaner, m_weaner, strict=True), start=3
                )
            )
            + sum(
                count * weight_at_age(age, g, doe_w) for age, count in enumerate(f_grower, start=6)
            )
            + sum(
                count * weight_at_age(age, g, buck_w) for age, count in enumerate(m_grower, start=6)
            )
        )
        stock_value = (
            does_now * doe_purchase_price
            + bucks * buck_purchase_price
            # Young stock insured at this month's market value (Eid uplift included).
            + young_value_kg * meat_price
        )
        insurance_cost = stock_value * costs.insurance_pct_stock_value_annual / 12.0
        selling_cost = (sales_revenue + cull_revenue) * sales.selling_cost_fraction + (
            (sales_head + culls_head) * sales.transport_cost_per_head * operating_cost_growth
        )

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
                meat_price_per_kg=meat_price,
                culls_head=culls_head,
                cull_revenue=cull_revenue,
                milk_revenue=milk_revenue,
                manure_revenue=manure_revenue,
                purchases_head=purchases_head,
                purchase_cost=purchase_cost,
                feed_green_kg=feed_total.green_kg,
                feed_homegrown_green_kg=homegrown_green_kg,
                feed_purchased_green_kg=purchased_green_kg,
                feed_dry_kg=feed_total.dry_kg,
                feed_concentrate_kg=feed_total.concentrate_kg,
                feed_cost=feed_cost,
                vet_cost=vet_cost,
                labour_cost=labour_cost,
                insurance_cost=insurance_cost,
                misc_cost=costs.misc_overhead_per_month * operating_cost_growth,
                selling_cost=selling_cost,
                fodder_surplus_kg=fodder_surplus,
                fodder_stock_kg_dm=fodder_stock_kg_dm,
                fodder_waste_kg_dm=fodder_waste_kg_dm,
                stock_value=stock_value,
                events=event_log,
            )
        )

    # --- pass 2: capacity, accounts, financing, cash and metrics -------------
    horizon = a.meta.horizon_months
    year1 = records[:12]
    avg_monthly_opex = sum(
        rec.feed_cost
        + rec.vet_cost
        + rec.labour_cost
        + rec.insurance_cost
        + rec.misc_cost
        + rec.selling_cost
        for rec in year1
    ) / len(year1)

    opening_head = float(
        a.herd.does
        + a.herd.bucks
        + a.herd.female_kids
        + a.herd.male_kids
        + a.herd.female_weaners
        + a.herd.male_weaners
        + a.herd.female_growers
        + a.herd.male_growers
    )
    projected_peak_head = max(opening_head, physical_peak_head)
    if costs.capacity_basis == "planned":
        capacity_places = float(costs.planned_capacity_head)
    elif costs.capacity_basis == "opening_herd":
        capacity_places = opening_head * (1.0 + costs.capacity_buffer_fraction)
    else:
        capacity_places = projected_peak_head * (1.0 + costs.capacity_buffer_fraction)
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
    terminal_balance = schedule[horizon - 1].closing_balance if horizon < len(schedule) else 0.0

    shed_monthly_depreciation = (
        shed_cost * (1.0 - costs.shed_residual_fraction) / (costs.shed_useful_life_years * 12)
    )
    equipment_monthly_depreciation = (
        equipment_cost
        * (1.0 - costs.equipment_residual_fraction)
        / (costs.equipment_useful_life_years * 12)
    )
    depreciation_by_month = [
        (shed_monthly_depreciation if month <= costs.shed_useful_life_years * 12 else 0.0)
        + (
            equipment_monthly_depreciation
            if month <= costs.equipment_useful_life_years * 12
            else 0.0
        )
        for month in range(1, horizon + 1)
    ]
    accumulated_shed_depreciation = min(
        shed_cost * (1.0 - costs.shed_residual_fraction),
        shed_monthly_depreciation * horizon,
    )
    accumulated_equipment_depreciation = min(
        equipment_cost * (1.0 - costs.equipment_residual_fraction),
        equipment_monthly_depreciation * horizon,
    )
    terminal_breakdown = TerminalValueBreakdown(
        livestock=(
            records[-1].stock_value * fin.terminal_livestock_realization_fraction
            if fin.include_terminal_value
            else 0.0
        ),
        shed=(
            (shed_cost - accumulated_shed_depreciation) * fin.terminal_asset_realization_fraction
            if fin.include_terminal_value
            else 0.0
        ),
        equipment=(
            (equipment_cost - accumulated_equipment_depreciation)
            * fin.terminal_asset_realization_fraction
            if fin.include_terminal_value
            else 0.0
        ),
        working_capital=(
            working_capital * fin.terminal_working_capital_recovery_fraction
            if fin.include_terminal_value
            else 0.0
        ),
        total=0.0,
    )
    terminal_breakdown.total = (
        terminal_breakdown.livestock
        + terminal_breakdown.shed
        + terminal_breakdown.equipment
        + terminal_breakdown.working_capital
    )

    # Tax is assessed at the end of each 12-month block (the final block may be
    # shorter). Losses offset later taxable profits when carry-forward is on.
    tax_by_month = [0.0] * horizon
    loss_pool = 0.0
    for start in range(0, horizon, 12):
        raw_block = records[start : start + 12]
        block_interest = sum(
            schedule[record.month - 1].interest
            for record in raw_block
            if record.month <= len(schedule)
        )
        block_depreciation = sum(depreciation_by_month[start : start + len(raw_block)])
        taxable_profit = (
            sum(record.revenue - record.opex for record in raw_block)
            - block_interest
            - block_depreciation
        )
        if taxable_profit <= 0.0:
            if fin.tax_loss_carryforward:
                loss_pool += -taxable_profit
            taxable_after_losses = 0.0
        else:
            loss_used = min(loss_pool, taxable_profit) if fin.tax_loss_carryforward else 0.0
            loss_pool -= loss_used
            taxable_after_losses = taxable_profit - loss_used
        tax_by_month[start + len(raw_block) - 1] = taxable_after_losses * fin.income_tax_rate

    months: list[MonthlyRow] = []
    cumulative = -equity
    cash_balance = working_capital
    for rec in records:
        debt_service = schedule[rec.month - 1].payment if rec.month <= len(schedule) else 0.0
        if rec.month == horizon:
            debt_service += terminal_balance
        tax = tax_by_month[rec.month - 1]
        terminal_value = terminal_breakdown.total if rec.month == horizon else 0.0
        net_cash = rec.revenue + terminal_value - rec.opex - debt_service - tax
        cumulative += net_cash
        # The opening liquidity balance already contains the funded working-
        # capital reserve. Its terminal recovery belongs in investor cash flow,
        # but adding it here would count the same reserve twice.
        liquidity_cash = net_cash - (
            terminal_breakdown.working_capital if rec.month == horizon else 0.0
        )
        cash_balance += liquidity_cash
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
                meat_price_per_kg=rec.meat_price_per_kg,
                culls_head=rec.culls_head,
                cull_revenue=rec.cull_revenue,
                milk_revenue=rec.milk_revenue,
                manure_revenue=rec.manure_revenue,
                purchases_head=rec.purchases_head,
                purchase_cost=rec.purchase_cost,
                feed_green_kg=rec.feed_green_kg,
                feed_homegrown_green_kg=rec.feed_homegrown_green_kg,
                feed_purchased_green_kg=rec.feed_purchased_green_kg,
                feed_dry_kg=rec.feed_dry_kg,
                feed_concentrate_kg=rec.feed_concentrate_kg,
                feed_cost=rec.feed_cost,
                vet_cost=rec.vet_cost,
                labour_cost=rec.labour_cost,
                insurance_cost=rec.insurance_cost,
                misc_cost=rec.misc_cost,
                selling_cost=rec.selling_cost,
                depreciation=depreciation_by_month[rec.month - 1],
                tax=tax,
                terminal_value=terminal_value,
                debt_service=debt_service,
                net_cash_flow=net_cash,
                cumulative_cash_flow=cumulative,
                cash_balance=cash_balance,
                fodder_surplus_kg=rec.fodder_surplus_kg,
                fodder_stock_kg_dm=rec.fodder_stock_kg_dm,
                fodder_waste_kg_dm=rec.fodder_waste_kg_dm,
                events=rec.events,
            )
        )

    annual_pl: list[AnnualPLRow] = []
    for start in range(0, horizon, 12):
        block = months[start : start + 12]
        year = start // 12 + 1
        interest = sum(schedule[m.month - 1].interest for m in block if m.month <= len(schedule))
        principal = sum(schedule[m.month - 1].principal for m in block if m.month <= len(schedule))
        if terminal_balance > 0.0 and block[-1].month == horizon:
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
        selling = sum(m.selling_cost for m in block)
        purchases = sum(m.purchase_cost for m in block)
        debt = sum(m.debt_service for m in block)
        depreciation = sum(m.depreciation for m in block)
        tax = sum(m.tax for m in block)
        terminal_value = sum(m.terminal_value for m in block)
        total_revenue = meat + cull_rev + milk + manure
        total_opex = feed_cost + vet + labour + insurance + misc + selling + purchases
        ebitda = total_revenue - total_opex
        ebit = ebitda - depreciation
        profit_before_tax = ebit - interest
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
                selling_cost=selling,
                stock_purchases=purchases,
                total_opex=total_opex,
                ebitda=ebitda,
                depreciation=depreciation,
                ebit=ebit,
                interest=interest,
                profit_before_tax=profit_before_tax,
                tax=tax,
                profit_after_tax=profit_before_tax - tax,
                principal=principal,
                debt_service=debt,
                terminal_value=terminal_value,
                net_cash_flow=net_cash,
            )
        )

    # Discount the cash flows when they actually occur. The biological and
    # financing engine is monthly; pushing every receipt and payment to each
    # year-end materially overstates NPV for loss-making early months. Ordinary
    # IRR retains annual appraisal blocks because its exact multiple-root solver
    # is designed for the bounded <=21-term project series; monthly MIRR remains
    # the unambiguous timing-accurate return measure.
    discount_times = [0.0, *[month.month / 12.0 for month in months]]
    cash_flows = [-equity, *[month.net_cash_flow for month in months]]
    benefit_flows = [
        0.0,
        *[
            month.sales_revenue
            + month.cull_revenue
            + month.milk_revenue
            + month.manure_revenue
            + month.terminal_value
            for month in months
        ],
    ]
    cost_flows = [
        equity,
        *[
            month.feed_cost
            + month.vet_cost
            + month.labour_cost
            + month.insurance_cost
            + month.misc_cost
            + month.selling_cost
            + month.purchase_cost
            + month.debt_service
            + month.tax
            for month in months
        ],
    ]

    dscr_per_year = [
        ((row.ebitda - row.tax) / row.debt_service) if row.debt_service > 0.0 else 0.0
        for row in annual_pl
    ]
    active_dscr = [
        value for value, row in zip(dscr_per_year, annual_pl, strict=True) if row.debt_service > 0.0
    ]
    avg_dscr = sum(active_dscr) / len(active_dscr) if active_dscr else None
    min_dscr = min(active_dscr) if active_dscr else None
    cum_series = [-equity, *[month.cumulative_cash_flow for month in months]]

    n_years = len(annual_pl)
    annual_green = [
        sum(m.feed_green_kg for m in months[y * 12 : (y + 1) * 12]) for y in range(n_years)
    ]
    annual_homegrown_green = [
        sum(m.feed_homegrown_green_kg for m in months[y * 12 : (y + 1) * 12])
        for y in range(n_years)
    ]
    annual_purchased_green = [
        sum(m.feed_purchased_green_kg for m in months[y * 12 : (y + 1) * 12])
        for y in range(n_years)
    ]
    annual_dry = [sum(m.feed_dry_kg for m in months[y * 12 : (y + 1) * 12]) for y in range(n_years)]
    annual_conc = [
        sum(m.feed_concentrate_kg for m in months[y * 12 : (y + 1) * 12]) for y in range(n_years)
    ]
    annual_feed_cost = [
        sum(m.feed_cost for m in months[y * 12 : (y + 1) * 12]) for y in range(n_years)
    ]
    annual_fodder_waste = [
        sum(m.fodder_waste_kg_dm for m in months[y * 12 : (y + 1) * 12]) for y in range(n_years)
    ]
    avg_annual_green_dm = (
        sum(green_kg * feed.green_dm_pct for green_kg in annual_green) * 12.0 / len(months)
        if months
        else 0.0
    )
    land_acres = avg_annual_green_dm / (feed.fodder_yield_t_dm_per_acre_year * 1000.0)
    minimum_cash_month, minimum_cash_balance = min(
        [(0, working_capital), *((month.month, month.cash_balance) for month in months)],
        key=lambda item: item[1],
    )
    total_revenue_all = sum(row.total_revenue for row in annual_pl)
    total_ebitda = sum(row.ebitda for row in annual_pl)

    return _CoreResult(
        records=records,
        months=months,
        annual_pl=annual_pl,
        amortization=schedule,
        feed_summary=FeedSummary(
            annual_green_kg=annual_green,
            annual_homegrown_green_kg=annual_homegrown_green,
            annual_purchased_green_kg=annual_purchased_green,
            annual_dry_kg=annual_dry,
            annual_concentrate_kg=annual_conc,
            annual_feed_cost=annual_feed_cost,
            annual_fodder_waste_kg_dm=annual_fodder_waste,
            land_requirement_acres=land_acres,
            fodder_deficit_months=sum(1 for month in months if month.feed_purchased_green_kg > 0.0),
            peak_fodder_stock_kg_dm=max(
                (month.fodder_stock_kg_dm for month in months), default=0.0
            ),
        ),
        project_cost=project_cost,
        shed_cost=shed_cost,
        equipment_cost=equipment_cost,
        stock_cost=stock_cost,
        working_capital=working_capital,
        capacity_places=capacity_places,
        projected_peak_head=projected_peak_head,
        terminal_value_breakdown=terminal_breakdown,
        loan_amount=loan_amount,
        subsidy_amount=subsidy_amount,
        equity=equity,
        npv=npv(fin.discount_rate_annual, cash_flows, discount_times),
        # The same monthly series NPV, BCR and MIRR use. Solving IRR on
        # year-end lumped blocks while NPV discounted the real monthly timing
        # let one response say the project clears the hurdle rate and misses it
        # at the same time.
        cash_flows=cash_flows,
        discount_times=discount_times,
        mirr=mirr(
            cash_flows,
            discount_times,
            fin.interest_rate_annual,
            fin.reinvestment_rate_annual,
        ),
        bcr=bcr(fin.discount_rate_annual, benefit_flows, cost_flows, discount_times),
        dscr_per_year=dscr_per_year,
        avg_dscr=avg_dscr,
        min_dscr=min_dscr,
        payback_month=payback_month(cum_series),
        terminal_value=terminal_breakdown.total,
        tax_total=sum(row.tax for row in annual_pl),
        accounting_profit_total=sum(row.profit_after_tax for row in annual_pl),
        minimum_cash_balance=minimum_cash_balance,
        minimum_cash_month=minimum_cash_month,
        additional_working_capital_required=max(0.0, -minimum_cash_balance),
        operating_margin=(total_ebitda / total_revenue_all if total_revenue_all > 0.0 else None),
    )


def break_even_meat_price(a: SimulationAssumptions) -> float | None:
    """Meat price (₹/kg) at which NPV = 0, by bisection on the price.

    Only the meat price is scaled (cull/milk/manure prices are held at base).
    Returns ``None`` when the public schema's maximum price cannot lift NPV to
    zero, and 0.0 when NPV is already non-negative with meat revenue zeroed.
    """
    # Never report a break-even value the public assumptions schema would
    # reject. At the price ceiling, a still-negative project has no usable
    # meat-price-only break-even under this model.
    upper_price = float(MAX_MONEY)

    def npv_at(price: float) -> float:
        variant = a.model_copy(deep=True)
        variant.sales.meat_price_per_kg = price
        return _run_core(variant).npv

    if npv_at(0.0) >= 0.0:
        return 0.0
    if npv_at(upper_price) < 0.0:
        return None
    lo, hi = 0.0, upper_price
    for _ in range(50):
        mid = (lo + hi) / 2.0
        if npv_at(mid) >= 0.0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2.0


def run_simulation(
    assumptions: SimulationAssumptions,
    *,
    with_break_even: bool = True,
    with_monte_carlo: bool = False,
    with_sensitivity: bool = False,
    with_optimization: bool = False,
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
        mirr=core.mirr,
        bcr=core.bcr,
        dscr_per_year=core.dscr_per_year,
        avg_dscr=core.avg_dscr,
        min_dscr=core.min_dscr,
        payback_month=core.payback_month,
        break_even_meat_price_per_kg=(
            break_even_meat_price(assumptions) if with_break_even else None
        ),
        peak_capacity_head=core.capacity_places,
        terminal_value=core.terminal_value,
        tax_total=core.tax_total,
        accounting_profit_total=core.accounting_profit_total,
        minimum_cash_balance=core.minimum_cash_balance,
        minimum_cash_month=core.minimum_cash_month,
        additional_working_capital_required=core.additional_working_capital_required,
        operating_margin=core.operating_margin,
    )
    assumptions_payload = json.dumps(
        assumptions.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
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
            capacity_places=core.capacity_places,
            capacity_basis=assumptions.costs.capacity_basis,
            projected_peak_head=core.projected_peak_head,
        ),
        terminal_value_breakdown=core.terminal_value_breakdown,
        model_version=MODEL_VERSION,
        assumptions_fingerprint=hashlib.sha256(assumptions_payload).hexdigest(),
    )
    if with_monte_carlo or with_sensitivity or with_optimization:
        from .montecarlo import run_monte_carlo, run_sensitivity

        if with_monte_carlo:
            result.monte_carlo = run_monte_carlo(assumptions)
        if with_sensitivity:
            result.sensitivity = run_sensitivity(assumptions)
        if with_optimization:
            from .optimization import run_optimization

            result.optimization = run_optimization(assumptions)
    # Explanations are built last so the risk section can see MC/sensitivity.
    from .explain import build_metric_explanations, build_narrative_report

    result.metric_explanations = build_metric_explanations(
        assumptions, result, break_even_computed=with_break_even
    )
    result.narrative_report = build_narrative_report(assumptions, result)
    return result
