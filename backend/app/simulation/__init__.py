"""Bio-economic goat herd simulation engine (pure Python, no FastAPI/SQLAlchemy).

Public surface:

- :class:`SimulationAssumptions` and its nested sub-models (assumptions.py)
- breed presets: ``BREED_PRESETS``, ``PRESET_FACTORIES``, ``get_preset``,
  ``apply_system`` (defaults.py)
- :func:`run_simulation`, :func:`break_even_meat_price`,
  :func:`monthly_mortality_rate`, :func:`weight_at_age` (engine.py)
- :func:`run_monte_carlo`, :func:`run_sensitivity`, :func:`percentile`
  (montecarlo.py)
- feed helpers: :func:`class_feed`, :func:`combine_feed`,
  :func:`cultivated_green_supply_kg`, :func:`land_requirement_acres`,
  :class:`FeedBreakdown` (feed.py)
- finance helpers: :func:`monthly_emi`, :func:`amortization_schedule`,
  :func:`npv`, :func:`irr`, :func:`bcr`, :func:`payback_month`,
  :class:`AmortizationRow` (finance.py)
- :func:`herd_cohorts`: bucket a farm's live herd into starting cohorts
  (snapshot.py)
- result models (results.py)
"""

from .assumptions import (
    CostsAssumptions,
    CullingAssumptions,
    FeedAssumptions,
    FinanceAssumptions,
    GrowthAssumptions,
    HerdAssumptions,
    MetaAssumptions,
    MortalityAssumptions,
    OptimizationAssumptions,
    ReproductionAssumptions,
    RiskAssumptions,
    RiskVariable,
    SalesAssumptions,
    SimulationAssumptions,
)
from .backward_planner import (
    BackwardPlanReport,
    PlannerAction,
    PlannerMonthRow,
    PlannerTarget,
    RequirementChain,
    RequirementStep,
    build_backward_plan,
)
from .daily_ops import (
    AnimalStartSpec,
    DailyOpsInput,
    DailyOpsParams,
    DailyOpsResult,
    build_daily_ledger,
    run_daily_ops,
)
from .defaults import (
    BREED_PRESETS,
    PRESET_FACTORIES,
    SYSTEMS,
    apply_system,
    get_preset,
)
from .engine import (
    break_even_meat_price,
    monthly_mortality_rate,
    run_simulation,
    weight_at_age,
)
from .feed import (
    DAYS_PER_MONTH,
    FeedBreakdown,
    class_feed,
    combine_feed,
    cultivated_green_supply_kg,
    land_requirement_acres,
)
from .finance import (
    AmortizationRow,
    amortization_schedule,
    bcr,
    irr,
    mirr,
    monthly_emi,
    npv,
    payback_month,
)
from .montecarlo import percentile, run_monte_carlo, run_sensitivity
from .results import (
    AmortizationRowModel,
    AnnualPLRow,
    FeedSummary,
    MonteCarloResult,
    MonthlyRow,
    OptimizationCandidate,
    OptimizationResult,
    PercentileBand,
    SensitivityItem,
    SimulationResult,
    TerminalValueBreakdown,
    ViabilityMetrics,
)
from .snapshot import herd_cohorts

__all__ = [
    "BREED_PRESETS",
    "DAYS_PER_MONTH",
    "PRESET_FACTORIES",
    "SYSTEMS",
    "AmortizationRow",
    "AmortizationRowModel",
    "AnimalStartSpec",
    "AnnualPLRow",
    "BackwardPlanReport",
    "CostsAssumptions",
    "CullingAssumptions",
    "DailyOpsInput",
    "DailyOpsParams",
    "DailyOpsResult",
    "FeedAssumptions",
    "FeedBreakdown",
    "FeedSummary",
    "FinanceAssumptions",
    "GrowthAssumptions",
    "HerdAssumptions",
    "MetaAssumptions",
    "MonteCarloResult",
    "MonthlyRow",
    "MortalityAssumptions",
    "OptimizationAssumptions",
    "OptimizationCandidate",
    "OptimizationResult",
    "PercentileBand",
    "PlannerAction",
    "PlannerMonthRow",
    "PlannerTarget",
    "ReproductionAssumptions",
    "RequirementChain",
    "RequirementStep",
    "RiskAssumptions",
    "RiskVariable",
    "SalesAssumptions",
    "SensitivityItem",
    "SimulationAssumptions",
    "SimulationResult",
    "TerminalValueBreakdown",
    "ViabilityMetrics",
    "amortization_schedule",
    "apply_system",
    "bcr",
    "break_even_meat_price",
    "build_backward_plan",
    "build_daily_ledger",
    "class_feed",
    "combine_feed",
    "cultivated_green_supply_kg",
    "get_preset",
    "herd_cohorts",
    "irr",
    "land_requirement_acres",
    "mirr",
    "monthly_emi",
    "monthly_mortality_rate",
    "npv",
    "payback_month",
    "percentile",
    "run_daily_ops",
    "run_monte_carlo",
    "run_sensitivity",
    "run_simulation",
    "weight_at_age",
]
