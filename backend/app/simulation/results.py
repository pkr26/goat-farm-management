"""Pydantic result models for the simulation engine.

Kept separate from the engine so the API layer can serialise a
``SimulationResult`` directly without importing engine internals.
"""

from pydantic import BaseModel, Field


class EventFill(BaseModel):
    """One scheduled event executed this month, with what actually happened.

    Sales can only take what the herd has: ``filled`` may be below
    ``requested`` and the difference is the plan's shortfall for that month.
    """

    month: int
    kind: str  # "purchase" | "sale"
    animal_class: str
    requested: float
    filled: float
    shortfall: float  # requested - filled, >= 0
    price_per_head: float  # ₹ actually paid/received
    revenue: float  # filled × price (₹ received for sales, ₹ spent for purchases)


class MonthlyRow(BaseModel):
    """One simulation month: end-of-month headcounts and that month's flows."""

    month: int  # 1-based simulation month
    calendar_month: int  # 1-12, from meta.start_year_month
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
    births: float  # head born alive this month
    deaths: float  # head
    sales_head: float  # meat sales (surplus males + surplus females)
    sales_revenue: float  # ₹
    meat_price_per_kg: float  # realized gross market price before selling costs
    culls_head: float
    cull_revenue: float  # ₹
    milk_revenue: float  # ₹
    manure_revenue: float  # ₹
    purchases_head: float  # scheduled stock plus automatic replacement bucks
    purchase_cost: float  # ₹ (young-stock purchases; trading animals, expensed)
    # ₹ cash spent this month buying BREEDING does/bucks (scheduled events and
    # automatic sire restocking). Capitalized on the breeding-livestock asset
    # account and depreciated straight-line over
    # costs.breeding_stock_useful_life_months; the cash outlay itself still
    # lands in this month's cash flow, and EBITDA/tax exclude it.
    breeding_stock_capex: float
    feed_green_kg: float  # as-fed
    feed_homegrown_green_kg: float  # as-fed green requirement supplied on-farm
    feed_purchased_green_kg: float  # as-fed green requirement bought at market
    feed_dry_kg: float
    feed_concentrate_kg: float
    feed_cost: float  # ₹
    vet_cost: float
    labour_cost: float
    insurance_cost: float
    misc_cost: float
    selling_cost: float
    depreciation: float  # non-cash, for EBIT/tax reporting
    tax: float  # cash tax paid in this month
    terminal_value: float  # closing livestock/assets/WC recovery, final month only
    debt_service: float  # ₹, interest + principal; the final month also carries
    # the outstanding loan balance (terminal balloon, as principal) when the
    # loan term outlives the horizon
    # ₹, revenues - opex - debt service (so the final month also carries the
    # outstanding loan balance when the loan term outlives the horizon).
    net_cash_flow: float
    cumulative_cash_flow: float  # ₹, including the month-0 equity outflow
    cash_balance: float  # operating liquidity; terminal WC recovery is not counted twice
    fodder_surplus_kg: float  # green DM: cultivated supply - requirement (negative = deficit)
    fodder_stock_kg_dm: float  # closing usable stored green-fodder DM
    fodder_waste_kg_dm: float  # storage loss plus production above storage capacity
    # Litres of water the herd drank this month (per-class daily demand over
    # the month; see FeedAssumptions for the Deccan-summer calibration).
    water_litres: float
    # Human-readable log of scheduled herd events applied this month
    # (SimulationAssumptions.events); empty when nothing was scheduled.
    events: list[str] = Field(default_factory=list)
    # The same events as structured records: requested vs actually filled.
    # The narrative log above is for humans; this is for the planner, which
    # must know a sale came up short without parsing prose.
    event_fills: list[EventFill] = Field(default_factory=list)


class AnnualPLRow(BaseModel):
    """Annual profit-and-loss aggregation (12-month blocks)."""

    year: int  # 1-based
    meat_revenue: float
    cull_revenue: float
    milk_revenue: float
    manure_revenue: float
    total_revenue: float
    feed_cost: float
    vet_cost: float
    labour_cost: float
    insurance_cost: float
    misc_cost: float
    selling_cost: float
    stock_purchases: float  # young-stock purchase cash (trading animals)
    # ₹ cash spent buying breeding does/bucks during the year; capitalized,
    # so it is excluded from total_opex/EBITDA and recovered through the
    # depreciation line and the terminal breeding-stock book value.
    breeding_stock_capex: float
    total_opex: float
    ebitda: float
    depreciation: float
    ebit: float
    interest: float
    profit_before_tax: float
    tax: float
    profit_after_tax: float
    principal: float
    debt_service: float
    terminal_value: float
    net_cash_flow: float


class AmortizationRowModel(BaseModel):
    """One row of the loan amortization schedule."""

    month: int
    opening_balance: float
    payment: float
    interest: float
    principal: float
    closing_balance: float


class ViabilityMetrics(BaseModel):
    """NABARD-style project viability metrics."""

    project_cost: float
    loan_amount: float
    subsidy_amount: float
    equity: float  # month-0 promoter outflow
    npv: float  # ₹ at finance.discount_rate_annual
    irr: float | None  # annual appraisal blocks; None without one well-defined root
    mirr: float | None  # timing-accurate monthly modified IRR
    bcr: float | None  # PV(gross benefits) / PV(gross costs); None without costs
    dscr_per_year: list[float]  # 0 for years without debt service
    avg_dscr: float | None  # over principal-repaying years; None when there are none
    min_dscr: float | None  # weakest repaying year; None when there are none
    payback_month: int | None  # first month cumulative cash >= 0; None if never
    break_even_meat_price_per_kg: float | None  # meat price making NPV = 0
    peak_capacity_head: float
    terminal_value: float
    tax_total: float
    accounting_profit_total: float
    minimum_cash_balance: float
    minimum_cash_month: int
    additional_working_capital_required: float
    operating_margin: float | None


class FeedSummary(BaseModel):
    """Annual feed totals and the fodder land requirement."""

    annual_green_kg: list[float]  # as-fed, per year
    annual_homegrown_green_kg: list[float]
    annual_purchased_green_kg: list[float]
    annual_dry_kg: list[float]
    annual_concentrate_kg: list[float]
    annual_feed_cost: list[float]
    annual_fodder_waste_kg_dm: list[float]
    land_requirement_acres: float  # from the horizon-average green DM need
    fodder_deficit_months: int  # months where cultivated supply fell short
    peak_fodder_stock_kg_dm: float
    annual_water_litres: list[float]  # the herd's water demand, per year
    peak_water_litres_per_day: float  # highest daily demand in any month


class ProjectCostBreakdown(BaseModel):
    """Components of the month-0 project cost (sums to metrics.project_cost)."""

    shed_cost: float  # capacity places x shed_cost_per_animal_place
    equipment_cost: float
    stock_cost: float  # starting herd valuation
    working_capital: float  # working_capital_months x year-1 average monthly opex
    capacity_places: float
    capacity_basis: str
    projected_peak_head: float


class TerminalValueBreakdown(BaseModel):
    """Recoverable closing assets included in the final project cash flow.

    ``breeding_stock`` is the residual BOOK value of breeding does/bucks
    capitalized during the run (purchases less straight-line depreciation).
    ``livestock`` then carries the closing herd's market value ABOVE that
    book value (young stock at market value plus the disposal gain/loss on
    the capitalized breeding animals), so the two lines together recover
    exactly the same closing-herd market value the pre-capitalization model
    did — the asset account changes the accounting split, not the cash.
    """

    livestock: float
    shed: float
    equipment: float
    working_capital: float
    breeding_stock: float
    total: float


class MetricExplanation(BaseModel):
    """Plain-language account of one viability metric, with the numbers used."""

    key: str  # e.g. "npv" (matches the ViabilityMetrics field name)
    title: str
    explanation: str
    figures: dict[str, float | str | None] = Field(default_factory=dict)


class ReportSection(BaseModel):
    """One section of the narrative report; paragraphs carry the figures."""

    key: str  # e.g. "revenue_mix"
    title: str
    paragraphs: list[str]
    figures: dict[str, float | str | None] = Field(default_factory=dict)


class PercentileBand(BaseModel):
    """Per-month percentile bands across Monte Carlo runs."""

    p5: list[float]
    p25: list[float]
    p50: list[float]
    p75: list[float]
    p95: list[float]


class MonteCarloResult(BaseModel):
    """Aggregate of seeded triangular Monte Carlo runs."""

    runs: int
    seed: int
    herd_percentiles: PercentileBand  # total herd per month
    cash_percentiles: PercentileBand  # cumulative cash flow per month
    liquidity_percentiles: PercentileBand  # operating cash balance per month
    npv_mean: float
    npv_std: float
    npv_p5: float
    npv_p50: float
    npv_p95: float
    prob_npv_negative: float
    prob_liquidity_shortfall: float
    prob_dscr_below_one: float
    minimum_cash_p5: float
    minimum_cash_p50: float
    ending_cash_p5: float
    ending_cash_p50: float
    # Monte Carlo sampling-uncertainty reporting: nonparametric bootstrap
    # 95% confidence intervals for the headline percentiles (resamples drawn
    # from a sub-seed derived from the run seed, so a given seed always
    # reproduces the same intervals), and the analytic binomial standard
    # error of the loss probability. None when the run count is too small to
    # bootstrap (a single run has no sampling distribution).
    npv_p5_ci: tuple[float, float] | None = None
    npv_p50_ci: tuple[float, float] | None = None
    npv_p95_ci: tuple[float, float] | None = None
    prob_npv_negative_se: float | None = None
    minimum_cash_p5_ci: tuple[float, float] | None = None
    mean_disease_outbreaks: float
    mean_drought_events: float
    mean_market_crashes: float
    npv_histogram_counts: list[int] = Field(min_length=20, max_length=20)
    npv_histogram_edges: list[float] = Field(min_length=21, max_length=21)


class SensitivityItem(BaseModel):
    """One-at-a-time sensitivity of NPV to a single parameter."""

    parameter: str
    delta_npv_low: float  # NPV(param low) - NPV(base)
    delta_npv_high: float  # NPV(param high) - NPV(base)
    # The perturbation actually applied, in the parameter's own units
    # ("-20%", "-2 month(s)"). Nominal +/-20% is not what every case runs:
    # sale age moves by whole months and four cases clamp at a schema ceiling.
    label_low: str
    label_high: str


class OptimizationCandidate(BaseModel):
    """One feasible or near-feasible management/financing decision set."""

    rank: int
    starting_does: int
    starting_bucks: int
    max_breeding_does: int
    sale_age_months: int
    female_retention_fraction: float
    loan_fraction: float
    # Marketing/breeding-discipline decisions the search also explores (see
    # OptimizationAssumptions' axis radii).
    festival_hold_months: int
    max_services_before_cull: int
    project_cost: float
    capacity_places: float
    projected_peak_head: float
    npv: float
    irr: float | None
    min_dscr: float | None
    minimum_cash_balance: float
    funding_gap: float
    feasible: bool
    constraint_violations: list[str] = Field(default_factory=list)


class OptimizationResult(BaseModel):
    """Bounded search result, ranked under the selected objective."""

    objective: str
    evaluated_candidates: int
    feasible_candidates: int
    baseline: OptimizationCandidate
    recommended: OptimizationCandidate | None
    alternatives: list[OptimizationCandidate] = Field(default_factory=list)


class SimulationResult(BaseModel):
    """Full deterministic result; Monte Carlo / sensitivity blocks are optional."""

    months: list[MonthlyRow]
    annual_pl: list[AnnualPLRow]
    metrics: ViabilityMetrics
    amortization: list[AmortizationRowModel]
    feed_summary: FeedSummary
    project_cost_breakdown: ProjectCostBreakdown
    terminal_value_breakdown: TerminalValueBreakdown
    model_version: str
    assumptions_fingerprint: str
    # Non-fatal model-coverage caveats for THIS run (e.g. a horizon extending
    # past the last year of the embedded Bakrid calendar, so trailing months
    # carry no festival uplift). Empty for fully covered runs.
    warnings: list[str] = Field(default_factory=list)
    metric_explanations: list[MetricExplanation] = Field(default_factory=list)
    narrative_report: list[ReportSection] = Field(default_factory=list)
    monte_carlo: MonteCarloResult | None = None
    sensitivity: list[SensitivityItem] | None = None
    optimization: OptimizationResult | None = None
