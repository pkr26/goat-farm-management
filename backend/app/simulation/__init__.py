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
    ReproductionAssumptions,
    RiskAssumptions,
    RiskVariable,
    SalesAssumptions,
    SimulationAssumptions,
)
from .defaults import (
    BREED_PRESETS,
    PRESET_FACTORIES,
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
    PercentileBand,
    SensitivityItem,
    SimulationResult,
    ViabilityMetrics,
)

__all__ = [
    "BREED_PRESETS",
    "DAYS_PER_MONTH",
    "PRESET_FACTORIES",
    "AmortizationRow",
    "AmortizationRowModel",
    "AnnualPLRow",
    "CostsAssumptions",
    "CullingAssumptions",
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
    "PercentileBand",
    "ReproductionAssumptions",
    "RiskAssumptions",
    "RiskVariable",
    "SalesAssumptions",
    "SensitivityItem",
    "SimulationAssumptions",
    "SimulationResult",
    "ViabilityMetrics",
    "amortization_schedule",
    "apply_system",
    "bcr",
    "break_even_meat_price",
    "class_feed",
    "combine_feed",
    "cultivated_green_supply_kg",
    "get_preset",
    "irr",
    "land_requirement_acres",
    "monthly_emi",
    "monthly_mortality_rate",
    "npv",
    "payback_month",
    "percentile",
    "run_monte_carlo",
    "run_sensitivity",
    "run_simulation",
    "weight_at_age",
]
