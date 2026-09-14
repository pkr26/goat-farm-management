"""Correlated Monte Carlo, event shocks and OAT sensitivity drivers.

Each run uses a Gaussian copula to preserve every configured triangular
marginal while introducing documented market, climate and disease dependence.
Monthly disease, drought and market-crash events are then layered onto that
run-level state. A single ``random.Random(seed)`` stream and fixed draw order
keep results exactly reproducible.
"""

import math
import random
import statistics
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .assumptions import (
    MAX_FODDER_YIELD_T,
    MAX_MONEY,
    MIN_FODDER_YIELD_T,
    RiskVariable,
    SimulationAssumptions,
)
from .engine import _run_core
from .results import MonteCarloResult, PercentileBand, SensitivityItem
from .shocks import MonthlyShockPath

_Mutator = Callable[[SimulationAssumptions], None]
_Reader = Callable[[SimulationAssumptions], float]
_Labeller = Callable[[float, float], str]

# Nonparametric bootstrap resamples behind the Monte Carlo confidence
# intervals. Bounded so a 2000-run MC stays cheap: 4 statistics x 400
# resamples of at most 2000 values.
_BOOTSTRAP_RESAMPLES = 400

# Between/within split of the configured price-risk variance when the
# within-run annual process is on: the persistent run-level draw carries
# (1 - share) of the configured triangular log-spread and the annual AR(1)
# shocks carry share, so realized annual multipliers keep the marginal
# dispersion the low/mode/high risk definition promises.
_WITHIN_RUN_VARIANCE_SHARE = 0.5

# Sub-seed for the bootstrap RNG: derived from the run seed so a given seed
# reproduces the same intervals while the bootstrap stream stays independent
# of the run-draw stream that produces the NPVs being resampled.
_BOOTSTRAP_SEED_SALT = 0xB0057EED

# Fixed draw order (also the tornado-report order for the risk variables).
_DRAW_ORDER = (
    "meat_price",
    "feed_price",
    "adult_mortality",
    "kid_mortality",
    "litter_size",
    "conception_rate",
    "fodder_yield",
    "operating_cost",
    # Last: pre-existing seeded runs draw the same values for the eight
    # variables above; dairy runs additionally consume the milk draw.
    "milk_price",
)

# Unit-length-ish loadings on three independent latent factors. Multiplying
# every loading by risk.correlation_strength gives a positive-semidefinite
# factor model by construction, avoiding user-supplied invalid correlation
# matrices while retaining domain-plausible co-movement.
_FACTOR_LOADINGS: dict[str, tuple[float, float, float]] = {
    # market, climate, disease
    "meat_price": (0.80, 0.00, 0.00),
    # Milk shares the market factor with meat (dairy procurement cycles track
    # feed/food inflation) with a slightly tighter loading.
    "milk_price": (0.70, 0.00, 0.00),
    "feed_price": (0.45, 0.60, 0.00),
    "adult_mortality": (0.00, 0.20, 0.70),
    "kid_mortality": (0.00, 0.25, 0.80),
    "litter_size": (0.00, 0.00, -0.35),
    # Negative, like every other adverse-climate response here: a positive
    # climate factor is a drought (it raises feed price and mortality and cuts
    # fodder yield), so it must depress conception, not lift it.
    "conception_rate": (0.00, -0.10, -0.60),
    "fodder_yield": (0.00, -0.80, 0.00),
    "operating_cost": (0.20, 0.30, 0.20),
}


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


def _bootstrap_percentile_ci(
    values: list[float],
    p: float,
    rng: random.Random,
    resamples: int = _BOOTSTRAP_RESAMPLES,
) -> tuple[float, float] | None:
    """Nonparametric bootstrap 95% CI for the p-th percentile of ``values``.

    Resampling with replacement from the per-run outcomes estimates how much
    the reported percentile itself is worth given only ``len(values)`` runs.
    None for fewer than two runs (a single outcome has no sampling
    distribution to resample).
    """
    n = len(values)
    if n < 2:
        return None
    estimates: list[float] = []
    for _ in range(resamples):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        estimates.append(percentile(sample, p))
    estimates.sort()
    return percentile(estimates, 0.025), percentile(estimates, 0.975)


def _triangular_log_sd(low: float, high: float) -> float:
    """First-order log-space sd of Triangular(low, mode=1, high).

    The delta method maps the level-space variance
    (a^2 + b^2 + c^2 - ab - ac - bc)/18 (a=low, b=mode, c=high) through
    1/mode = 1 — exact for the symmetric spreads the risk variables use and
    within a few percent for the asymmetric ones.
    """
    variance = (low * low + 1.0 + high * high - low - high - low * high) / 18.0
    return math.sqrt(max(0.0, variance))


def _annual_ar1_factors(horizon_months: int, rng: random.Random, rho: float) -> list[float]:
    """Standardized mean-reverting AR(1) factor per simulation month.

    One shock per projection year (x_t = rho * x_{t-1} + sqrt(1 - rho^2) * e_t
    with unit stationary variance); every month of that year carries its
    year's factor. The shocks are ALWAYS drawn (even for disabled risk
    variables — same discipline as ``_correlated_draws``) so toggling one
    variable cannot reseed the others.
    """
    years = max(1, (horizon_months + 11) // 12)
    innovation_scale = math.sqrt(max(0.0, 1.0 - rho * rho))
    x = 0.0
    yearly: list[float] = []
    for _ in range(years):
        x = rho * x + innovation_scale * rng.normalvariate(0.0, 1.0)
        yearly.append(x)
    return [yearly[month // 12] for month in range(horizon_months)]


def _apply_annual_price_variation(
    path: MonthlyShockPath,
    draws: dict[str, float],
    a: SimulationAssumptions,
    rng: random.Random,
) -> dict[str, float]:
    """Layer within-run annual price years onto one Monte Carlo run.

    Returns the EFFECTIVE run-level draws: the meat/feed price draws are
    shrunk toward the base (their log scaled by sqrt(1 - share)) so that the
    persistent level plus the annual AR(1) shocks — each scaled to
    sqrt(share) of the configured triangular log-spread, with the
    log-normal mean correction exp(-s^2/2) — preserve the marginal variance
    the configured risk spreads define. Disabled risk variables skip the
    annual layer entirely (their run draw is the neutral 1.0 and stays 1.0).
    """
    share = _WITHIN_RUN_VARIANCE_SHARE
    effective = dict(draws)
    horizon = a.meta.horizon_months
    rho = a.risk.price_process_rho
    for name in ("meat_price", "feed_price"):
        var = getattr(a.risk, name)
        annual = _annual_ar1_factors(horizon, rng, rho)
        # draws.get: test drivers may inject partial draw dicts; a missing
        # entry is the neutral 1.0 multiplier.
        run_draw = draws.get(name, 1.0)
        if not var.enabled:
            continue
        effective[name] = math.exp(math.log(run_draw) * math.sqrt(1.0 - share))
        sigma = _triangular_log_sd(var.low, var.high)
        scale = math.sqrt(share) * sigma
        correction = math.exp(-0.5 * scale * scale)
        channel = path.meat_price if name == "meat_price" else path.feed_price
        for month in range(horizon):
            channel[month] *= math.exp(scale * annual[month]) * correction
    return effective


def _apply_draws(a: SimulationAssumptions, draws: dict[str, float]) -> SimulationAssumptions:
    """Apply Monte Carlo multipliers to a deep copy of the assumptions."""
    variant = a.model_copy(deep=True)
    variant.sales.meat_price_per_kg = min(
        MAX_MONEY, variant.sales.meat_price_per_kg * draws["meat_price"]
    )
    variant.sales.milk_price_per_litre = min(
        MAX_MONEY, variant.sales.milk_price_per_litre * draws["milk_price"]
    )
    variant.sales.milk_price_per_kg_fat = min(
        MAX_MONEY, variant.sales.milk_price_per_kg_fat * draws["milk_price"]
    )
    # The feed-price draw is a PURCHASED-feed price risk: purchased green,
    # dry and concentrate are all bought at market prices. Home-grown green
    # fodder is deliberately NOT scaled here — its cost is cultivation, whose
    # yield/growth risk is carried by the fodder_yield draw, and the engine's
    # drought/shock channel (feed_prices_for_month) likewise leaves the home
    # green price unshocked. The two risk channels must stay consistent.
    variant.feed.purchased_green_price_per_kg = min(
        MAX_MONEY, variant.feed.purchased_green_price_per_kg * draws["feed_price"]
    )
    variant.feed.dry_price_per_kg = min(
        MAX_MONEY, variant.feed.dry_price_per_kg * draws["feed_price"]
    )
    variant.feed.concentrate_price_per_kg = min(
        MAX_MONEY, variant.feed.concentrate_price_per_kg * draws["feed_price"]
    )
    # Kid mortality scales both pre- and post-weaning rates.
    variant.mortality.kid_pre_weaning = min(
        0.9, variant.mortality.kid_pre_weaning * draws["kid_mortality"]
    )
    variant.mortality.kid_post_weaning = min(
        0.9, variant.mortality.kid_post_weaning * draws["kid_mortality"]
    )
    variant.mortality.adult = min(0.9, variant.mortality.adult * draws["adult_mortality"])
    # The engine already applies the adult-mortality *event* shock to growers
    # (engine.py, s_grower). Leaving the run-level draw off them made every
    # Monte Carlo path finish its sale cohort at the deterministic grower rate,
    # understating the downside of the class that carries the meat revenue.
    variant.mortality.grower = min(0.9, variant.mortality.grower * draws["adult_mortality"])
    # Risk runs are executions of the same public model, not a second hidden
    # model with wider bounds. Clamp both sides of every bounded perturbation.
    variant.reproduction.litter_size = max(
        0.5,
        min(4.0, variant.reproduction.litter_size * draws["litter_size"]),
    )
    # Preserve a schema-valid base of 1.0 when this risk is disabled. The old
    # 0.98 cap changed deterministic assumptions even for a multiplier of 1.
    variant.reproduction.conception_rate = min(
        1.0, variant.reproduction.conception_rate * draws["conception_rate"]
    )
    # Two-sided, like the litter-size clamp above: a base at the schema floor
    # multiplied by a draw below 1 rounds under it, which fails revalidation
    # and 500s from inside the Monte Carlo loop. Clamp to the schema floor
    # (RT-L8-4), not a denormal, so the perturbed variant stays valid.
    variant.feed.fodder_yield_t_dm_per_acre_year = max(
        MIN_FODDER_YIELD_T,
        min(
            MAX_FODDER_YIELD_T,
            variant.feed.fodder_yield_t_dm_per_acre_year * draws["fodder_yield"],
        ),
    )
    operating_factor = draws["operating_cost"]
    variant.costs.vet_per_animal_per_year = min(
        MAX_MONEY, variant.costs.vet_per_animal_per_year * operating_factor
    )
    variant.costs.labour_per_month = min(
        MAX_MONEY, variant.costs.labour_per_month * operating_factor
    )
    variant.costs.misc_overhead_per_month = min(
        MAX_MONEY, variant.costs.misc_overhead_per_month * operating_factor
    )
    variant.sales.transport_cost_per_head = min(
        MAX_MONEY, variant.sales.transport_cost_per_head * operating_factor
    )
    return SimulationAssumptions.model_validate(variant.model_dump())


def _triangular_from_uniform(low: float, high: float, uniform: float) -> float:
    """Inverse CDF for a triangular distribution with fixed mode 1.0."""
    if low == high:
        return low
    mode = 1.0
    split = (mode - low) / (high - low)
    if uniform < split:
        return low + math.sqrt(uniform * (high - low) * (mode - low))
    return high - math.sqrt((1.0 - uniform) * (high - low) * (high - mode))


def _correlated_draws(
    rng: random.Random,
    risk_vars: Mapping[str, RiskVariable],
    correlation_strength: float,
) -> dict[str, float]:
    """Gaussian-copula draws with triangular marginals and fixed ordering."""
    factors = [rng.normalvariate(0.0, 1.0) for _ in range(3)]
    draws: dict[str, float] = {}
    for name in _DRAW_ORDER:
        var = risk_vars[name]
        # Consume the same random values even when a variable is disabled.
        # This preserves common random numbers for every later variable when a
        # user toggles one risk on/off to isolate its effect.
        idiosyncratic_draw = rng.normalvariate(0.0, 1.0)
        if not var.enabled:
            draws[name] = 1.0
            continue
        loadings = _FACTOR_LOADINGS[name]
        systematic = correlation_strength * sum(
            loading * factor for loading, factor in zip(loadings, factors, strict=True)
        )
        systematic_variance = correlation_strength**2 * sum(
            loading * loading for loading in loadings
        )
        idiosyncratic = math.sqrt(max(0.0, 1.0 - systematic_variance)) * idiosyncratic_draw
        normal = systematic + idiosyncratic
        uniform = 0.5 * (1.0 + math.erf(normal / math.sqrt(2.0)))
        draws[name] = _triangular_from_uniform(var.low, var.high, uniform)
    return draws


def _monthly_start_probability(annual_probability: float) -> float:
    return 1.0 - math.pow(1.0 - annual_probability, 1.0 / 12.0)


def _event_shock_path(a: SimulationAssumptions, rng: random.Random) -> MonthlyShockPath:
    """Sample non-overlapping monthly disease, drought and market episodes."""
    horizon = a.meta.horizon_months
    path = MonthlyShockPath.neutral(horizon)
    disease_left = drought_left = crash_left = 0
    disease_count = drought_count = crash_count = 0
    disease_start = _monthly_start_probability(a.risk.disease_outbreak_probability_annual)
    drought_start = _monthly_start_probability(a.risk.drought_probability_annual)
    crash_start = _monthly_start_probability(a.risk.market_crash_probability_annual)
    for month in range(horizon):
        if disease_left == 0 and rng.random() < disease_start:
            disease_left = a.risk.disease_outbreak_duration_months
            disease_count += 1
        if drought_left == 0 and rng.random() < drought_start:
            drought_left = a.risk.drought_duration_months
            drought_count += 1
        if crash_left == 0 and rng.random() < crash_start:
            crash_left = a.risk.market_crash_duration_months
            crash_count += 1
        if disease_left > 0:
            path.adult_mortality[month] *= a.risk.disease_adult_mortality_multiplier
            path.kid_mortality[month] *= a.risk.disease_kid_mortality_multiplier
            path.conception[month] *= a.risk.disease_conception_multiplier
            path.milk_yield[month] *= a.risk.disease_milk_yield_multiplier
            disease_left -= 1
        if drought_left > 0:
            path.fodder_yield[month] *= a.risk.drought_fodder_yield_multiplier
            path.feed_price[month] *= a.risk.drought_feed_price_multiplier
            drought_left -= 1
        if crash_left > 0:
            path.meat_price[month] *= a.risk.market_crash_price_multiplier
            path.milk_price[month] *= a.risk.market_crash_price_multiplier
            crash_left -= 1
    return MonthlyShockPath(
        meat_price=path.meat_price,
        feed_price=path.feed_price,
        fodder_yield=path.fodder_yield,
        adult_mortality=path.adult_mortality,
        kid_mortality=path.kid_mortality,
        conception=path.conception,
        litter_size=path.litter_size,
        operating_cost=path.operating_cost,
        milk_price=path.milk_price,
        milk_yield=path.milk_yield,
        disease_outbreaks=disease_count,
        drought_events=drought_count,
        market_crashes=crash_count,
    )


def _histogram(values: list[float], bins: int = 20) -> tuple[list[int], list[float]]:
    """Equal-width histogram; returns (counts, edges) with len(edges) = bins + 1."""
    lo, hi = min(values), max(values)
    if hi <= lo:
        # Widening by a fixed 1.0 is a no-op once |lo| >= 2**53, which left
        # width at exactly 0.0 and raised ZeroDivisionError on the bin lookup.
        # Pad relative to the magnitude so the range is always representable.
        pad = max(1.0, abs(lo) * 1e-9)
        lo, hi = lo - pad, lo + pad
    width = (hi - lo) / bins
    if width <= 0.0:  # unreachable after the pad; keeps the division total
        return [len(values)] + [0] * (bins - 1), [lo + index for index in range(bins + 1)]
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
        "milk_price": a.risk.milk_price,
        "feed_price": a.risk.feed_price,
        "adult_mortality": a.risk.adult_mortality,
        "kid_mortality": a.risk.kid_mortality,
        "litter_size": a.risk.litter_size,
        "conception_rate": a.risk.conception_rate,
        "fodder_yield": a.risk.fodder_yield,
        "operating_cost": a.risk.operating_cost,
    }

    herd_paths: list[list[float]] = []
    cash_paths: list[list[float]] = []
    liquidity_paths: list[list[float]] = []
    npvs: list[float] = []
    minimum_cash: list[float] = []
    ending_cash: list[float] = []
    liquidity_shortfalls = 0
    weak_dscr_runs = 0
    disease_outbreaks: list[int] = []
    drought_events: list[int] = []
    market_crashes: list[int] = []
    for _ in range(runs):
        draws = _correlated_draws(rng, risk_vars, a.risk.correlation_strength)
        event_path = _event_shock_path(a, rng)
        # Within-run annual price years (fix: a run no longer lives under one
        # flat price multiplier for the whole horizon). Drawn AFTER the run
        # draws and event path so seeded runs keep their pre-existing draw
        # values and shock episodes — common random numbers preserved.
        effective_draws = (
            _apply_annual_price_variation(event_path, draws, a, rng)
            if a.risk.within_run_price_variation
            else draws
        )
        core = _run_core(_apply_draws(a, effective_draws), event_path)
        herd_paths.append([m.total_herd for m in core.months])
        cash_paths.append([m.cumulative_cash_flow for m in core.months])
        liquidity_paths.append([m.cash_balance for m in core.months])
        npvs.append(core.npv)
        minimum_cash.append(core.minimum_cash_balance)
        ending_cash.append(core.months[-1].cash_balance)
        liquidity_shortfalls += int(core.minimum_cash_balance < 0.0)
        weak_dscr_runs += int(core.min_dscr is not None and core.min_dscr < 1.0)
        disease_outbreaks.append(event_path.disease_outbreaks)
        drought_events.append(event_path.drought_events)
        market_crashes.append(event_path.market_crashes)

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
    # Sampling-uncertainty reporting: bootstrap 95% CIs for the headline NPV
    # percentiles and the p5 of the liquidity low point, from a sub-seeded
    # stream derived from the run seed (deterministic per seed); the loss
    # probability carries the analytic binomial standard error
    # sqrt(p(1-p)/n) — exact for a proportion and seed-free by construction.
    bootstrap_rng = random.Random(a.risk.seed ^ _BOOTSTRAP_SEED_SALT)
    npv_p5_ci = _bootstrap_percentile_ci(npvs, 0.05, bootstrap_rng)
    npv_p50_ci = _bootstrap_percentile_ci(npvs, 0.50, bootstrap_rng)
    npv_p95_ci = _bootstrap_percentile_ci(npvs, 0.95, bootstrap_rng)
    minimum_cash_p5_ci = _bootstrap_percentile_ci(minimum_cash, 0.05, bootstrap_rng)
    prob_negative = sum(1 for v in npvs if v < 0.0) / runs
    return MonteCarloResult(
        runs=runs,
        seed=a.risk.seed,
        herd_percentiles=band(herd_paths),
        cash_percentiles=band(cash_paths),
        liquidity_percentiles=band(liquidity_paths),
        npv_mean=statistics.mean(npvs),
        npv_std=statistics.pstdev(npvs),
        npv_p5=percentile(npvs, 0.05),
        npv_p50=percentile(npvs, 0.50),
        npv_p95=percentile(npvs, 0.95),
        prob_npv_negative=prob_negative,
        prob_liquidity_shortfall=liquidity_shortfalls / runs,
        prob_dscr_below_one=weak_dscr_runs / runs,
        npv_p5_ci=npv_p5_ci,
        npv_p50_ci=npv_p50_ci,
        npv_p95_ci=npv_p95_ci,
        prob_npv_negative_se=math.sqrt(prob_negative * (1.0 - prob_negative) / runs),
        minimum_cash_p5=percentile(minimum_cash, 0.05),
        minimum_cash_p50=percentile(minimum_cash, 0.50),
        minimum_cash_p5_ci=minimum_cash_p5_ci,
        ending_cash_p5=percentile(ending_cash, 0.05),
        ending_cash_p50=percentile(ending_cash, 0.50),
        mean_disease_outbreaks=statistics.mean(disease_outbreaks),
        mean_drought_events=statistics.mean(drought_events),
        mean_market_crashes=statistics.mean(market_crashes),
        npv_histogram_counts=counts,
        npv_histogram_edges=edges,
    )


@dataclass(frozen=True)
class _SensitivityCase:
    """One tornado row: how to read, move and describe a single parameter."""

    parameter: str
    read: _Reader
    label: _Labeller
    low: _Mutator
    high: _Mutator


def _pct_label(base: float, applied: float) -> str:
    """Realised move as a percentage of the base value.

    Computed from the value the run actually used, not from the nominal
    multiplier: the high-side clamps mean a "+20%" case can be worth +17.6%,
    or nothing at all when the base already sits on the ceiling.
    """
    if base == 0.0:
        return "+0%"
    return f"{(applied - base) / base * 100.0:+.1f}%"


def _months_label(base: float, applied: float) -> str:
    return f"{applied - base:+.0f} month(s)"


def _scale_milk_price(v: SimulationAssumptions) -> None:
    v.sales.milk_price_per_litre *= 0.8
    v.sales.milk_price_per_kg_fat *= 0.8


def _scale_milk_price_high(v: SimulationAssumptions) -> None:
    v.sales.milk_price_per_litre = min(MAX_MONEY, v.sales.milk_price_per_litre * 1.2)
    v.sales.milk_price_per_kg_fat = min(MAX_MONEY, v.sales.milk_price_per_kg_fat * 1.2)


def run_sensitivity(a: SimulationAssumptions) -> list[SensitivityItem]:
    """OAT (tornado) sensitivity of NPV: +/-20% on one parameter at a time.

    ``sale_age_months`` is varied by +/-2 months instead of a percentage, and
    four cases clamp the high side at a schema ceiling, so every item reports
    the perturbation it actually applied (``label_low`` / ``label_high``)
    rather than leaving readers to assume a flat 20%. The list is sorted by
    impact (largest absolute delta first) for tornado plots.
    """
    base_npv = _run_core(a).npv

    def run_case(mutate: _Mutator, read: _Reader) -> tuple[float, float]:
        """(NPV, value actually applied) for one perturbed variant."""
        variant = a.model_copy(deep=True)
        mutate(variant)
        validated = SimulationAssumptions.model_validate(variant.model_dump())
        return _run_core(validated).npv, read(validated)

    cases: list[_SensitivityCase] = [
        _SensitivityCase(
            "meat_price",
            lambda v: v.sales.meat_price_per_kg,
            _pct_label,
            lambda v: setattr(v.sales, "meat_price_per_kg", v.sales.meat_price_per_kg * 0.8),
            lambda v: setattr(
                v.sales,
                "meat_price_per_kg",
                min(MAX_MONEY, v.sales.meat_price_per_kg * 1.2),
            ),
        ),
        _SensitivityCase(
            # Dairy's dominant price driver; the perturbation applies to both
            # the per-litre and the per-kg-fat price so either pricing basis
            # moves by the same proportion.
            "milk_price",
            lambda v: (
                v.sales.milk_price_per_litre
                if v.sales.milk_price_per_kg_fat <= 0.0
                else v.sales.milk_price_per_kg_fat
            ),
            _pct_label,
            _scale_milk_price,
            _scale_milk_price_high,
        ),
        _SensitivityCase(
            "feed_prices",
            lambda v: (
                v.feed.green_price_per_kg
                + v.feed.purchased_green_price_per_kg
                + v.feed.dry_price_per_kg
                + v.feed.concentrate_price_per_kg
            ),
            _pct_label,
            lambda v: _scale_feed_prices(v, 0.8),
            lambda v: _scale_feed_prices(v, 1.2),
        ),
        _SensitivityCase(
            "kid_pre_weaning_mortality",
            lambda v: v.mortality.kid_pre_weaning,
            _pct_label,
            lambda v: setattr(v.mortality, "kid_pre_weaning", v.mortality.kid_pre_weaning * 0.8),
            # Clamp at 0.9 (schema ceiling): a base > 0.833 × 1.2 overshoots 1.0
            # and monthly_mortality_rate(1 - >1) → math.pow(negative, 1/12) 500s.
            lambda v: setattr(
                v.mortality, "kid_pre_weaning", min(0.9, v.mortality.kid_pre_weaning * 1.2)
            ),
        ),
        _SensitivityCase(
            "litter_size",
            lambda v: v.reproduction.litter_size,
            _pct_label,
            lambda v: setattr(
                v.reproduction,
                "litter_size",
                max(0.5, v.reproduction.litter_size * 0.8),
            ),
            # Clamp at 4.0 (schema ceiling); mirrors _apply_draws' Monte Carlo clamp.
            lambda v: setattr(
                v.reproduction, "litter_size", min(4.0, v.reproduction.litter_size * 1.2)
            ),
        ),
        _SensitivityCase(
            "conception_rate",
            lambda v: v.reproduction.conception_rate,
            _pct_label,
            lambda v: setattr(
                v.reproduction, "conception_rate", v.reproduction.conception_rate * 0.8
            ),
            lambda v: setattr(
                v.reproduction, "conception_rate", min(1.0, v.reproduction.conception_rate * 1.2)
            ),
        ),
        _SensitivityCase(
            "sale_age_months",
            lambda v: float(v.growth.sale_age_months),
            _months_label,
            lambda v: setattr(v.growth, "sale_age_months", max(6, v.growth.sale_age_months - 2)),
            lambda v: setattr(v.growth, "sale_age_months", min(24, v.growth.sale_age_months + 2)),
        ),
        _SensitivityCase(
            "labour_cost",
            lambda v: v.costs.labour_per_month,
            _pct_label,
            lambda v: setattr(v.costs, "labour_per_month", v.costs.labour_per_month * 0.8),
            lambda v: setattr(
                v.costs,
                "labour_per_month",
                min(MAX_MONEY, v.costs.labour_per_month * 1.2),
            ),
        ),
        _SensitivityCase(
            "interest_rate",
            lambda v: v.finance.interest_rate_annual,
            _pct_label,
            lambda v: setattr(
                v.finance, "interest_rate_annual", v.finance.interest_rate_annual * 0.8
            ),
            lambda v: setattr(
                v.finance,
                "interest_rate_annual",
                min(0.5, v.finance.interest_rate_annual * 1.2),
            ),
        ),
    ]

    items: list[SensitivityItem] = []
    for case in cases:
        base_value = case.read(a)
        npv_low, value_low = run_case(case.low, case.read)
        npv_high, value_high = run_case(case.high, case.read)
        items.append(
            SensitivityItem(
                parameter=case.parameter,
                delta_npv_low=npv_low - base_npv,
                delta_npv_high=npv_high - base_npv,
                label_low=case.label(base_value, value_low),
                label_high=case.label(base_value, value_high),
            )
        )
    items.sort(
        key=lambda item: max(abs(item.delta_npv_low), abs(item.delta_npv_high)), reverse=True
    )
    return items


def _scale_feed_prices(variant: SimulationAssumptions, factor: float) -> None:
    # OAT tornado scope note: this sensitivity case perturbs ALL FOUR feed
    # prices (home green included) as a pure parameter sweep. The Monte-Carlo
    # feed_price RISK draw deliberately excludes home green (a cultivation
    # cost whose risk rides the fodder_yield draw); the two scopes differ by
    # design, mirroring market.feed_prices_for_month.
    variant.feed.green_price_per_kg = min(MAX_MONEY, variant.feed.green_price_per_kg * factor)
    variant.feed.purchased_green_price_per_kg = min(
        MAX_MONEY, variant.feed.purchased_green_price_per_kg * factor
    )
    variant.feed.dry_price_per_kg = min(MAX_MONEY, variant.feed.dry_price_per_kg * factor)
    variant.feed.concentrate_price_per_kg = min(
        MAX_MONEY, variant.feed.concentrate_price_per_kg * factor
    )
