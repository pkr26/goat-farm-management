"""Seeded triangular Monte Carlo and one-at-a-time (OAT) sensitivity drivers.

Each Monte Carlo run draws one triangular multiplier (mode = 1.0, i.e. the base
value) per enabled risk variable, applies it to a deep copy of the assumptions
and re-runs the deterministic core. Draws happen in a fixed order from a
single ``random.Random(seed)`` stream, so results are exactly reproducible for
a given seed.
"""

import math
import random
import statistics
from collections.abc import Callable

from .assumptions import SimulationAssumptions
from .engine import _run_core
from .results import MonteCarloResult, PercentileBand, SensitivityItem

_Mutator = Callable[[SimulationAssumptions], None]

# Fixed draw order (also the tornado-report order for the risk variables).
_DRAW_ORDER = (
    "meat_price",
    "feed_price",
    "adult_mortality",
    "kid_mortality",
    "litter_size",
    "conception_rate",
)


def percentile(values: list[float], p: float) -> float:
    """Linear-interpolation percentile (numpy 'linear' method); p in [0, 1]."""
    if not values:
        return 0.0
    xs = sorted(values)
    k = (len(xs) - 1) * p
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return xs[lo]
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _apply_draws(a: SimulationAssumptions, draws: dict[str, float]) -> SimulationAssumptions:
    """Apply Monte Carlo multipliers to a deep copy of the assumptions."""
    variant = a.model_copy(deep=True)
    variant.sales.meat_price_per_kg *= draws["meat_price"]
    variant.feed.green_price_per_kg *= draws["feed_price"]
    variant.feed.dry_price_per_kg *= draws["feed_price"]
    variant.feed.concentrate_price_per_kg *= draws["feed_price"]
    # Kid mortality scales both pre- and post-weaning rates.
    variant.mortality.kid_pre_weaning = min(
        0.9, variant.mortality.kid_pre_weaning * draws["kid_mortality"]
    )
    variant.mortality.kid_post_weaning = min(
        0.9, variant.mortality.kid_post_weaning * draws["kid_mortality"]
    )
    variant.mortality.adult = min(0.9, variant.mortality.adult * draws["adult_mortality"])
    # Litter size is capped at the schema maximum even under extreme draws.
    variant.reproduction.litter_size = min(
        4.0, variant.reproduction.litter_size * draws["litter_size"]
    )
    # Preserve a schema-valid base of 1.0 when this risk is disabled. The old
    # 0.98 cap changed deterministic assumptions even for a multiplier of 1.
    variant.reproduction.conception_rate = min(
        1.0, variant.reproduction.conception_rate * draws["conception_rate"]
    )
    return variant


def _histogram(values: list[float], bins: int = 20) -> tuple[list[int], list[float]]:
    """Equal-width histogram; returns (counts, edges) with len(edges) = bins + 1."""
    lo, hi = min(values), max(values)
    if hi == lo:
        hi = lo + 1.0
    width = (hi - lo) / bins
    edges = [lo + i * width for i in range(bins + 1)]
    counts = [0] * bins
    for v in values:
        idx = min(int((v - lo) / width), bins - 1)
        counts[idx] += 1
    return counts, edges


def run_monte_carlo(a: SimulationAssumptions) -> MonteCarloResult:
    """Run the seeded Monte Carlo and aggregate percentiles / NPV statistics."""
    rng = random.Random(a.risk.seed)
    runs = a.risk.monte_carlo_runs
    risk_vars = {
        "meat_price": a.risk.meat_price,
        "feed_price": a.risk.feed_price,
        "adult_mortality": a.risk.adult_mortality,
        "kid_mortality": a.risk.kid_mortality,
        "litter_size": a.risk.litter_size,
        "conception_rate": a.risk.conception_rate,
    }

    herd_paths: list[list[float]] = []
    cash_paths: list[list[float]] = []
    npvs: list[float] = []
    for _ in range(runs):
        draws: dict[str, float] = {}
        for name in _DRAW_ORDER:
            var = risk_vars[name]
            draws[name] = rng.triangular(var.low, var.high, 1.0) if var.enabled else 1.0
        core = _run_core(_apply_draws(a, draws))
        herd_paths.append([m.total_herd for m in core.months])
        cash_paths.append([m.cumulative_cash_flow for m in core.months])
        npvs.append(core.npv)

    def band(paths: list[list[float]]) -> PercentileBand:
        columns = [[path[m] for path in paths] for m in range(len(paths[0]))]
        return PercentileBand(
            p5=[percentile(col, 0.05) for col in columns],
            p25=[percentile(col, 0.25) for col in columns],
            p50=[percentile(col, 0.50) for col in columns],
            p75=[percentile(col, 0.75) for col in columns],
            p95=[percentile(col, 0.95) for col in columns],
        )

    counts, edges = _histogram(npvs)
    return MonteCarloResult(
        runs=runs,
        seed=a.risk.seed,
        herd_percentiles=band(herd_paths),
        cash_percentiles=band(cash_paths),
        npv_mean=statistics.mean(npvs),
        npv_std=statistics.pstdev(npvs),
        npv_p5=percentile(npvs, 0.05),
        npv_p50=percentile(npvs, 0.50),
        npv_p95=percentile(npvs, 0.95),
        prob_npv_negative=sum(1 for v in npvs if v < 0.0) / runs,
        npv_histogram_counts=counts,
        npv_histogram_edges=edges,
    )


def run_sensitivity(a: SimulationAssumptions) -> list[SensitivityItem]:
    """OAT (tornado) sensitivity of NPV: +/-20% on one parameter at a time.

    ``sale_age_months`` is varied by +/-2 months instead of a percentage. The
    list is sorted by impact (largest absolute delta first) for tornado plots.
    """
    base_npv = _run_core(a).npv

    def npv_with(mutate: _Mutator) -> float:
        variant = a.model_copy(deep=True)
        mutate(variant)
        return _run_core(variant).npv

    cases: list[tuple[str, _Mutator, _Mutator]] = [
        (
            "meat_price",
            lambda v: setattr(v.sales, "meat_price_per_kg", v.sales.meat_price_per_kg * 0.8),
            lambda v: setattr(v.sales, "meat_price_per_kg", v.sales.meat_price_per_kg * 1.2),
        ),
        (
            "feed_prices",
            lambda v: _scale_feed_prices(v, 0.8),
            lambda v: _scale_feed_prices(v, 1.2),
        ),
        (
            "kid_pre_weaning_mortality",
            lambda v: setattr(v.mortality, "kid_pre_weaning", v.mortality.kid_pre_weaning * 0.8),
            # Clamp at 0.9 (schema ceiling): a base > 0.833 × 1.2 overshoots 1.0
            # and monthly_mortality_rate(1 - >1) → math.pow(negative, 1/12) 500s.
            lambda v: setattr(
                v.mortality, "kid_pre_weaning", min(0.9, v.mortality.kid_pre_weaning * 1.2)
            ),
        ),
        (
            "litter_size",
            lambda v: setattr(v.reproduction, "litter_size", v.reproduction.litter_size * 0.8),
            # Clamp at 4.0 (schema ceiling); mirrors _apply_draws' Monte Carlo clamp.
            lambda v: setattr(
                v.reproduction, "litter_size", min(4.0, v.reproduction.litter_size * 1.2)
            ),
        ),
        (
            "conception_rate",
            lambda v: setattr(
                v.reproduction, "conception_rate", min(0.98, v.reproduction.conception_rate * 0.8)
            ),
            lambda v: setattr(
                v.reproduction, "conception_rate", min(0.98, v.reproduction.conception_rate * 1.2)
            ),
        ),
        (
            "sale_age_months",
            lambda v: setattr(v.growth, "sale_age_months", max(6, v.growth.sale_age_months - 2)),
            lambda v: setattr(v.growth, "sale_age_months", min(24, v.growth.sale_age_months + 2)),
        ),
        (
            "labour_cost",
            lambda v: setattr(v.costs, "labour_per_month", v.costs.labour_per_month * 0.8),
            lambda v: setattr(v.costs, "labour_per_month", v.costs.labour_per_month * 1.2),
        ),
        (
            "interest_rate",
            lambda v: setattr(
                v.finance, "interest_rate_annual", v.finance.interest_rate_annual * 0.8
            ),
            lambda v: setattr(
                v.finance, "interest_rate_annual", v.finance.interest_rate_annual * 1.2
            ),
        ),
    ]

    items = [
        SensitivityItem(
            parameter=name,
            delta_npv_low=npv_with(low) - base_npv,
            delta_npv_high=npv_with(high) - base_npv,
        )
        for name, low, high in cases
    ]
    items.sort(
        key=lambda item: max(abs(item.delta_npv_low), abs(item.delta_npv_high)), reverse=True
    )
    return items


def _scale_feed_prices(variant: SimulationAssumptions, factor: float) -> None:
    variant.feed.green_price_per_kg *= factor
    variant.feed.dry_price_per_kg *= factor
    variant.feed.concentrate_price_per_kg *= factor
