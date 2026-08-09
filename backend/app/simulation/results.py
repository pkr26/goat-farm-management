"""Pydantic result models for the simulation engine.

Kept separate from the engine so the API layer can serialise a
``SimulationResult`` directly without importing engine internals.
"""

from pydantic import BaseModel, Field


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
    culls_head: float
    cull_revenue: float  # ₹
    milk_revenue: float  # ₹
    manure_revenue: float  # ₹
    purchases_head: float  # bucks bought
    purchase_cost: float  # ₹
    feed_green_kg: float  # as-fed
    feed_dry_kg: float
    feed_concentrate_kg: float
    feed_cost: float  # ₹
    vet_cost: float
    labour_cost: float
    insurance_cost: float
    misc_cost: float
    debt_service: float  # ₹, interest + principal; the final month also carries
    # the outstanding loan balance (terminal balloon, as principal) when the
    # loan term outlives the horizon
    # ₹, revenues - opex - debt service (so the final month also carries the
    # outstanding loan balance when the loan term outlives the horizon).
    net_cash_flow: float
    cumulative_cash_flow: float  # ₹, including the month-0 equity outflow
    fodder_surplus_kg: float  # green DM: cultivated supply - requirement (negative = deficit)
    # Human-readable log of scheduled herd events applied this month
    # (SimulationAssumptions.events); empty when nothing was scheduled.
    events: list[str] = Field(default_factory=list)


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
    stock_purchases: float
    total_opex: float
    ebitda: float
    interest: float
    principal: float
    debt_service: float
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
    irr: float | None  # None when the flows admit no single well-defined IRR
    bcr: float | None  # PV(gross benefits) / PV(gross costs); None without costs
    dscr_per_year: list[float]  # 0 for years without debt service
    avg_dscr: float | None  # over years with debt service > 0; None when there are none
    min_dscr: float | None  # weakest such year; None when there are none
    payback_month: int | None  # first month cumulative cash >= 0; None if never
    break_even_meat_price_per_kg: float | None  # meat price making NPV = 0


class FeedSummary(BaseModel):
    """Annual feed totals and the fodder land requirement."""

    annual_green_kg: list[float]  # as-fed, per year
    annual_dry_kg: list[float]
    annual_concentrate_kg: list[float]
    annual_feed_cost: list[float]
    land_requirement_acres: float  # from the horizon-average green DM need
    fodder_deficit_months: int  # months where cultivated supply fell short


class ProjectCostBreakdown(BaseModel):
    """Components of the month-0 project cost (sums to metrics.project_cost)."""

    shed_cost: float  # capacity places x shed_cost_per_animal_place
    equipment_cost: float
    stock_cost: float  # starting herd valuation
    working_capital: float  # working_capital_months x year-1 average monthly opex


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
    npv_mean: float
    npv_std: float
    npv_p5: float
    npv_p50: float
    npv_p95: float
    prob_npv_negative: float
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


class SimulationResult(BaseModel):
    """Full deterministic result; Monte Carlo / sensitivity blocks are optional."""

    months: list[MonthlyRow]
    annual_pl: list[AnnualPLRow]
    metrics: ViabilityMetrics
    amortization: list[AmortizationRowModel]
    feed_summary: FeedSummary
    project_cost_breakdown: ProjectCostBreakdown
    metric_explanations: list[MetricExplanation] = Field(default_factory=list)
    narrative_report: list[ReportSection] = Field(default_factory=list)
    monte_carlo: MonteCarloResult | None = None
    sensitivity: list[SensitivityItem] | None = None
