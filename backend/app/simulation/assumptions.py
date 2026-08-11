"""Assumption model for the bio-economic herd simulation.

Every field carries a default, so ``SimulationAssumptions()`` is a complete,
valid Osmanabadi stall-fed NABARD-style run (50 does + 2 bucks, 120 months).
All sub-models forbid extra keys so API payloads fail loudly on typos.
"""

import re
from itertools import pairwise
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Every numeric assumption must be finite and preserve its JSON type: NaN/inf
# would propagate through a
# run and crash JSON serialization of the result (500) — reject at 422 time.
FiniteFloat = Annotated[float, Field(strict=True, allow_inf_nan=False)]

# Head-count ceiling for starting cohorts / scheduled event counts: far above
# any real farm, small enough that cohort arithmetic can't overflow float64.
MAX_HEAD = 100_000

# Ceiling for the labour-scaling divisor only: float-exact (< 2**53) so the
# engine's ``total_herd / threshold`` stays finite, huge so stored scenarios
# that predate any bound keep revalidating. See CostsAssumptions.
MAX_LABOUR_PER_HEAD_THRESHOLD = 10**15

# Magnitude caps mirror schemas/common.py: finite-but-huge inputs (1e308)
# pass the NaN/inf guard yet overflow derived math (revenue = head × kg × ₹)
# into inf/NaN, which then crashes JSON serialization of the run (500).
# ₹1e9 (100 crore) for money/prices and 1000 kg for live weights are far past
# anything the domain can legitimately reach.
MAX_MONEY = 1_000_000_000
MAX_WEIGHT_KG = 1000
# ~500x the highest-milk goat breed (Jamunapari, ~200 l/lactation).
MAX_LACTATION_LITRES = 100_000
# A million acres / a thousand tonnes DM per acre: orders of magnitude past
# real fodder cultivation, small enough to keep area × yield products finite.
MAX_FODDER_ACRES = 1_000_000
MAX_FODDER_YIELD_T = 1000
# Monte Carlo spread multipliers: a 100x swing is already absurd.
MAX_RISK_MULTIPLIER = 100.0
# Price/yield seasonality is deliberately bounded more tightly than an
# arbitrary Monte Carlo spread: a 10x month-to-month multiplier is already well
# beyond a useful farm-planning input while still allowing severe stress cases.
MAX_SEASONAL_MULTIPLIER = 10.0

# A live weight in kg, bounded so head × kg × ₹ products stay finite.
WeightKg = Annotated[FiniteFloat, Field(gt=0.0, le=MAX_WEIGHT_KG)]


class _Group(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class MetaAssumptions(_Group):
    """Run horizon and calendar anchoring."""

    horizon_months: int = Field(default=120, ge=12, le=240)
    # "YYYY-MM"; simulation month 1 falls in this calendar month. Used only for
    # the seasonal (Eid) price uplift.
    start_year_month: str = "2026-08"

    @field_validator("start_year_month")
    @classmethod
    def _valid_year_month(cls, value: str) -> str:
        match = re.fullmatch(r"(\d{4})-(\d{2})", value)
        if (
            match is None
            or not 1 <= int(match.group(2)) <= 12
            or not 1900 <= int(match.group(1)) <= 2200
        ):
            raise ValueError("start_year_month must be a real YYYY-MM (e.g. '2026-08')")
        return value


class HerdAssumptions(_Group):
    """Starting stock and replacement/purchase policy."""

    does: int = Field(default=50, ge=0, le=MAX_HEAD)
    bucks: int = Field(default=2, ge=0, le=MAX_HEAD)
    female_growers: int = Field(default=0, ge=0, le=MAX_HEAD)
    male_growers: int = Field(default=0, ge=0, le=MAX_HEAD)
    female_weaners: int = Field(default=0, ge=0, le=MAX_HEAD)
    male_weaners: int = Field(default=0, ge=0, le=MAX_HEAD)
    female_kids: int = Field(default=0, ge=0, le=MAX_HEAD)
    male_kids: int = Field(default=0, ge=0, le=MAX_HEAD)
    # Fraction of female growers reaching first-breeding age that are kept as
    # replacements; the rest are sold as meat. NABARD models use ~0.5.
    female_retention_fraction: FiniteFloat = Field(default=0.5, ge=0.0, le=1.0)
    # Cap on the breeding-doe pool; 0 = unlimited. Default 50 holds the flock
    # at the NABARD 50+2 unit size (model projects keep the breeding flock
    # constant and sell surplus replacements); set 0 for unconstrained growth.
    max_breeding_does: int = Field(default=50, ge=0, le=MAX_HEAD)
    # ₹, NABARD unit-cost tables.
    doe_purchase_price: FiniteFloat = Field(default=8000.0, ge=0.0, le=MAX_MONEY)
    buck_purchase_price: FiniteFloat = Field(default=12000.0, ge=0.0, le=MAX_MONEY)
    # Buy a buck whenever the buck:doe ratio falls below 1:buck_doe_ratio.
    auto_purchase_bucks: bool = True
    # Foundation flock reproductive state: "mixed" spreads the starting does
    # uniformly across the reproductive cycle (realistic purchased flock —
    # some pregnant, some lactating, some open — so sales begin in year 1);
    # "open" starts every doe open and ready to breed in month 1 (clean
    # projection start, used by the golden unit tests).
    foundation_flock_state: Literal["open", "mixed"] = "mixed"


class ReproductionAssumptions(_Group):
    """Breeding biology (monthly resolution)."""

    conception_rate: FiniteFloat = Field(
        default=0.85, ge=0.0, le=1.0
    )  # per service, ICAR herd models
    gestation_months: int = Field(default=5, ge=1, le=7)  # ~150 days
    lactation_months: int = Field(default=3, ge=1, le=8)
    # Months a doe waits after lactation before rebreeding (post-partum anoestrus).
    months_open_before_breeding: int = Field(default=2, ge=0, le=12)
    litter_size: FiniteFloat = Field(default=1.6, ge=0.5, le=4.0)  # kids born per kidding
    sex_ratio_female: FiniteFloat = Field(default=0.5, ge=0.0, le=1.0)
    age_at_first_breeding_months: int = Field(default=12, ge=6, le=30)
    stillbirth_rate: FiniteFloat = Field(default=0.02, ge=0.0, le=0.5)


class MortalityAssumptions(_Group):
    """Annual mortality fractions per class (converted to monthly compounding rates)."""

    kid_pre_weaning: FiniteFloat = Field(
        default=0.10, ge=0.0, le=0.9
    )  # FAO/ICAR small-ruminant models
    kid_post_weaning: FiniteFloat = Field(default=0.05, ge=0.0, le=0.9)
    grower: FiniteFloat = Field(default=0.04, ge=0.0, le=0.9)
    adult: FiniteFloat = Field(default=0.05, ge=0.0, le=0.9)


class CullingAssumptions(_Group):
    """Disposal and sire-replacement policy."""

    # Annual fraction of breeding does culled; applied from simulation month 13
    # (the foundation stock gets one full year grace), NABARD/TNAU convention.
    doe_cull_rate_annual: FiniteFloat = Field(default=0.20, ge=0.0, le=1.0)
    # Floor 36, not 24: the engine spreads foundation does over ages
    # 24..min(60, max_doe_age - 12), which is an empty range below 36
    # (ZeroDivisionError mid-run). 36 keeps every schema-valid run alive.
    max_doe_age_months: int = Field(default=72, ge=36, le=180)
    buck_rotation_years: int = Field(default=3, ge=1, le=10)  # avoid inbreeding
    buck_doe_ratio: int = Field(default=25, ge=1, le=100)  # 1 buck per 25 does


class GrowthAssumptions(_Group):
    """Live-weight curve and sale-age policy.

    ``weight_by_age_months`` gives live weight (kg) at ages 0..12 months; from
    month 13 the curve approaches the adult weight linearly, reaching it at 24
    months. Default table is an Osmanabadi stall-fed curve (2.5 kg birth weight,
    ~2 kg/month pre-yearling gain) per ICAR/NBAGR breed descriptors.
    """

    birth_weight_kg: WeightKg = Field(default=2.5, gt=0.0)
    adult_weight_doe_kg: WeightKg = Field(default=32.0, gt=0.0)
    adult_weight_buck_kg: WeightKg = Field(default=34.0, gt=0.0)
    weight_by_age_months: list[WeightKg] = Field(
        default_factory=lambda: [
            2.5,
            4.5,
            6.5,
            8.5,
            10.5,
            12.5,
            14.5,
            16.5,
            18.5,
            20.5,
            22.5,
            24.5,
            26.5,
        ],
        min_length=13,
        # Exactly ages 0..12. Longer tables previously overrode adult weight
        # because weight_at_age consulted list length before its adult branch.
        max_length=13,
    )
    # Age at which surplus males are sold for meat. Must be >= 6 so males pass
    # through the grower chain (weaning at 3, grower from 6). Osmanabadi
    # stall-fed kids are marketed at 9-12 months; 12 gives the yearling finish
    # (~26.5 kg) whose extra weight outweighs the added feed at default prices.
    sale_age_months: int = Field(default=12, ge=6, le=24)

    @field_validator("weight_by_age_months")
    @classmethod
    def _weight_curve_is_nondecreasing(cls, value: list[float]) -> list[float]:
        if any(later < earlier for earlier, later in pairwise(value)):
            raise ValueError("weight_by_age_months must be nondecreasing from birth to month 12")
        return value

    @model_validator(mode="after")
    def _birth_weight_matches_curve(self) -> "GrowthAssumptions":
        if self.weight_by_age_months[0] != self.birth_weight_kg:
            raise ValueError(
                "birth_weight_kg must equal weight_by_age_months[0] "
                "(both describe age-zero live weight)"
            )
        return self


class SalesAssumptions(_Group):
    """Market prices, seasonality and selling costs (₹).

    The twelve monthly multipliers are indexed by calendar month (January at
    index 0). ``festival_sale_months`` is indexed by simulation month instead,
    because Eid al-Adha moves through the Gregorian calendar. The legacy
    ``eid_month`` input remains supported for existing saved scenarios.
    """

    meat_price_per_kg: FiniteFloat = Field(default=350.0, ge=0.0, le=MAX_MONEY)  # ₹/kg live weight
    cull_doe_price_per_kg: FiniteFloat = Field(default=180.0, ge=0.0, le=MAX_MONEY)
    cull_buck_price_per_kg: FiniteFloat = Field(default=200.0, ge=0.0, le=MAX_MONEY)
    monthly_meat_price_multipliers: list[FiniteFloat] = Field(
        default_factory=lambda: [1.0] * 12,
        min_length=12,
        max_length=12,
    )
    annual_livestock_price_growth_rate: FiniteFloat = Field(default=0.0, gt=-1.0, le=1.0)
    # Calendar month (1-12) in which the Bakrid price uplift applies; 0 disables it.
    eid_month: int = Field(default=0, ge=0, le=12)
    eid_price_uplift: FiniteFloat = Field(default=0.30, ge=0.0, le=2.0)
    # Explicit 1-based simulation months are the accurate way to model a lunar
    # festival over a multi-year Gregorian forecast. Empty keeps legacy/default
    # behaviour; the same uplift is never applied twice in one month.
    festival_sale_months: list[int] = Field(default_factory=list, max_length=40)
    # Direct selling/mandi commission on livestock revenue and per-head
    # transport/handling. Both are reported as selling cost, not netted out of
    # the observed market price, so the revenue bridge remains auditable.
    selling_cost_fraction: FiniteFloat = Field(default=0.0, ge=0.0, le=0.5)
    transport_cost_per_head: FiniteFloat = Field(default=0.0, ge=0.0, le=MAX_MONEY)
    milk_price_per_litre: FiniteFloat = Field(default=30.0, ge=0.0, le=MAX_MONEY)
    # Total litres per lactation per doe; 0 for meat breeds (Osmanabadi),
    # ~110 Sirohi, ~175 Beetal, ~200 Jamunapari (NBAGR descriptors).
    lactation_milk_litres: FiniteFloat = Field(default=0.0, ge=0.0, le=MAX_LACTATION_LITRES)
    # ₹, TNAU budgets.
    manure_income_per_adult_per_year: FiniteFloat = Field(default=900.0, ge=0.0, le=MAX_MONEY)

    @field_validator("monthly_meat_price_multipliers")
    @classmethod
    def _bounded_meat_multipliers(cls, value: list[float]) -> list[float]:
        if any(item <= 0.0 or item > MAX_SEASONAL_MULTIPLIER for item in value):
            raise ValueError(f"monthly multipliers must be > 0 and <= {MAX_SEASONAL_MULTIPLIER:g}")
        return value


class FeedAssumptions(_Group):
    """Dry-matter intake, ration split, feed prices and fodder production."""

    # DMI as a fraction of body weight, per physiological state (ICAR feeding standards).
    dmi_kid_creep: FiniteFloat = Field(default=0.015, gt=0.0, le=0.10)
    dmi_weaner: FiniteFloat = Field(default=0.03, gt=0.0, le=0.10)
    dmi_grower: FiniteFloat = Field(default=0.035, gt=0.0, le=0.10)
    dmi_doe_maintenance: FiniteFloat = Field(default=0.03, gt=0.0, le=0.10)
    dmi_doe_pregnant: FiniteFloat = Field(default=0.035, gt=0.0, le=0.10)
    dmi_doe_lactating: FiniteFloat = Field(default=0.045, gt=0.0, le=0.10)
    dmi_buck: FiniteFloat = Field(default=0.035, gt=0.0, le=0.10)
    # Per-class concentrate share of DM (ICAR feeding standards / TNAU rations);
    # the remainder is green:dry fodder in a fixed 2:1 DM ratio, so each class's
    # shares sum to 1. Creep feed is mostly concentrate; dry/maintenance does
    # get almost none.
    concentrate_share_kid_creep: FiniteFloat = Field(default=0.60, ge=0.0, le=1.0)
    concentrate_share_weaner: FiniteFloat = Field(default=0.20, ge=0.0, le=1.0)
    concentrate_share_grower: FiniteFloat = Field(default=0.20, ge=0.0, le=1.0)
    concentrate_share_doe_maintenance: FiniteFloat = Field(default=0.05, ge=0.0, le=1.0)
    concentrate_share_doe_pregnant: FiniteFloat = Field(default=0.15, ge=0.0, le=1.0)
    concentrate_share_doe_lactating: FiniteFloat = Field(default=0.20, ge=0.0, le=1.0)
    concentrate_share_buck: FiniteFloat = Field(default=0.15, ge=0.0, le=1.0)
    # DM content of each feed, as-fed basis.
    green_dm_pct: FiniteFloat = Field(default=0.25, gt=0.0, le=1.0)
    dry_dm_pct: FiniteFloat = Field(default=0.88, gt=0.0, le=1.0)
    concentrate_dm_pct: FiniteFloat = Field(default=0.90, gt=0.0, le=1.0)
    # Prices per kg as-fed (₹). ``green_price_per_kg`` is the home-grown
    # production cost; a physical shortfall is purchased at the separate market
    # price. Keeping them separate fixes the old model's economically impossible
    # result where zero acres and ample fodder land had exactly the same cost.
    green_price_per_kg: FiniteFloat = Field(default=1.0, ge=0.0, le=MAX_MONEY)
    purchased_green_price_per_kg: FiniteFloat = Field(default=2.5, ge=0.0, le=MAX_MONEY)
    dry_price_per_kg: FiniteFloat = Field(default=5.0, ge=0.0, le=MAX_MONEY)  # paddy straw ₹4-6/kg
    concentrate_price_per_kg: FiniteFloat = Field(
        default=25.0, ge=0.0, le=MAX_MONEY
    )  # commercial goat feed ₹22-28
    annual_feed_price_growth_rate: FiniteFloat = Field(default=0.0, gt=-1.0, le=1.0)
    monthly_green_price_multipliers: list[FiniteFloat] = Field(
        default_factory=lambda: [1.0] * 12,
        min_length=12,
        max_length=12,
    )
    monthly_dry_price_multipliers: list[FiniteFloat] = Field(
        default_factory=lambda: [1.0] * 12,
        min_length=12,
        max_length=12,
    )
    monthly_concentrate_price_multipliers: list[FiniteFloat] = Field(
        default_factory=lambda: [1.0] * 12,
        min_length=12,
        max_length=12,
    )
    # Fraction of total DM obtained free from grazing (0 = stall-fed, ~0.3 semi-intensive).
    grazing_dm_fraction: FiniteFloat = Field(default=0.0, ge=0.0, le=1.0)
    # Cultivated fodder: area and DM yield (6 t DM/acre/yr ≈ hybrid napier/lucerne, TNAU).
    cultivated_fodder_acres: FiniteFloat = Field(default=0.0, ge=0.0, le=MAX_FODDER_ACRES)
    fodder_yield_t_dm_per_acre_year: FiniteFloat = Field(default=6.0, gt=0.0, le=MAX_FODDER_YIELD_T)
    # Monthly production factors (average 1.0 is a full stated annual yield).
    # They need not sum to 12: the engine normalizes them, so the annual yield
    # remains exactly the user's stated agronomic assumption.
    monthly_fodder_yield_multipliers: list[FiniteFloat] = Field(
        default_factory=lambda: [1.0] * 12,
        min_length=12,
        max_length=12,
    )
    initial_fodder_stock_kg_dm: FiniteFloat = Field(default=0.0, ge=0.0, le=MAX_MONEY)
    fodder_storage_capacity_kg_dm: FiniteFloat = Field(default=0.0, ge=0.0, le=MAX_MONEY)
    fodder_storage_loss_fraction_monthly: FiniteFloat = Field(default=0.02, ge=0.0, le=1.0)

    @field_validator(
        "monthly_green_price_multipliers",
        "monthly_dry_price_multipliers",
        "monthly_concentrate_price_multipliers",
        "monthly_fodder_yield_multipliers",
    )
    @classmethod
    def _bounded_monthly_multipliers(cls, value: list[float]) -> list[float]:
        if any(item <= 0.0 or item > MAX_SEASONAL_MULTIPLIER for item in value):
            raise ValueError(f"monthly multipliers must be > 0 and <= {MAX_SEASONAL_MULTIPLIER:g}")
        return value

    @model_validator(mode="after")
    def _initial_fodder_fits_storage(self) -> "FeedAssumptions":
        if self.initial_fodder_stock_kg_dm > self.fodder_storage_capacity_kg_dm:
            raise ValueError("initial_fodder_stock_kg_dm must be <= fodder_storage_capacity_kg_dm")
        return self


class CostsAssumptions(_Group):
    """Recurring and capital costs (₹)."""

    # NABARD/TNAU budgets.
    vet_per_animal_per_year: FiniteFloat = Field(default=250.0, ge=0.0, le=MAX_MONEY)
    labour_per_month: FiniteFloat = Field(default=10000.0, ge=0.0, le=MAX_MONEY)
    # One labourer per this many head; labour count scales up with herd size.
    # The bound exists only so an arbitrary-size JSON integer cannot raise
    # OverflowError in the engine's float division — but this is a divisor,
    # not a herd size: values far above MAX_HEAD are meaningful ("never scale
    # labour with head count") and were accepted before any bound existed, so
    # persisted scenarios carry them. 10**15 stays float-exact (< 2**53) and
    # grandfathers every previously-runnable stored value; MAX_HEAD here made
    # those scenarios retroactively fail revalidation with a 422.
    labour_per_head_threshold: int = Field(default=75, ge=1, le=MAX_LABOUR_PER_HEAD_THRESHOLD)
    insurance_pct_stock_value_annual: FiniteFloat = Field(default=0.04, ge=0.0, le=0.25)
    misc_overhead_per_month: FiniteFloat = Field(default=2000.0, ge=0.0, le=MAX_MONEY)
    operating_cost_growth_rate_annual: FiniteFloat = Field(default=0.0, gt=-1.0, le=1.0)
    # NABARD unit costs.
    shed_cost_per_animal_place: FiniteFloat = Field(default=4500.0, ge=0.0, le=MAX_MONEY)
    equipment_cost_per_animal: FiniteFloat = Field(default=500.0, ge=0.0, le=MAX_MONEY)
    # Capacity can be driven by the projected physical peak (recommended), a
    # user-approved plan, or the opening herd for legacy comparisons.
    capacity_basis: Literal["projected_peak", "planned", "opening_herd"] = "projected_peak"
    planned_capacity_head: int = Field(default=0, ge=0, le=MAX_HEAD)
    capacity_buffer_fraction: FiniteFloat = Field(default=0.10, ge=0.0, le=1.0)
    shed_useful_life_years: int = Field(default=20, ge=1, le=100)
    equipment_useful_life_years: int = Field(default=7, ge=1, le=50)
    shed_residual_fraction: FiniteFloat = Field(default=0.10, ge=0.0, le=1.0)
    equipment_residual_fraction: FiniteFloat = Field(default=0.05, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _planned_capacity_is_present(self) -> "CostsAssumptions":
        if self.capacity_basis == "planned" and self.planned_capacity_head <= 0:
            raise ValueError("planned_capacity_head must be > 0 when capacity_basis is 'planned'")
        return self


class FinanceAssumptions(_Group):
    """Project financing (NABARD refinance structure)."""

    # Explicit stock cost (₹); 0 = auto-computed from starting herd counts × prices.
    initial_stock_cost: FiniteFloat = Field(default=0.0, ge=0.0, le=MAX_MONEY)
    loan_fraction_of_project_cost: FiniteFloat = Field(default=0.85, ge=0.0, le=1.0)
    interest_rate_annual: FiniteFloat = Field(default=0.11, ge=0.0, le=0.5)
    loan_term_months: int = Field(default=72, ge=1, le=180)
    # Interest-only period at the start of the loan (NABARD goat schemes: ~12 months).
    moratorium_months: int = Field(default=12, ge=0, le=60)
    # Capital subsidy as a fraction of project cost; reduces the promoter's equity.
    subsidy_fraction: FiniteFloat = Field(default=0.0, ge=0.0, le=0.9)
    discount_rate_annual: FiniteFloat = Field(default=0.12, ge=0.0, le=0.5)
    # Months of operating cost held as working capital inside the project cost.
    working_capital_months: int = Field(default=3, ge=0, le=24)
    # Tax is configurable rather than hard-coded: farm/entity tax treatment is
    # jurisdiction- and structure-specific. Straight-line depreciation is used
    # for the model accounts and tax shield.
    income_tax_rate: FiniteFloat = Field(default=0.0, ge=0.0, le=0.6)
    tax_loss_carryforward: bool = True
    # A continuing-business forecast still owns livestock, facilities and the
    # working-capital reserve at its horizon. Their recoverable value prevents
    # a short forecast from pretending every closing asset disappears.
    include_terminal_value: bool = True
    terminal_livestock_realization_fraction: FiniteFloat = Field(default=0.90, ge=0.0, le=1.0)
    terminal_asset_realization_fraction: FiniteFloat = Field(default=1.0, ge=0.0, le=1.0)
    terminal_working_capital_recovery_fraction: FiniteFloat = Field(default=1.0, ge=0.0, le=1.0)
    # Used by modified IRR, which remains meaningful for cash-flow patterns
    # where ordinary IRR is ambiguous or has multiple roots.
    reinvestment_rate_annual: FiniteFloat = Field(default=0.08, ge=0.0, le=0.5)

    @model_validator(mode="after")
    def _financing_is_coherent(self) -> "FinanceAssumptions":
        # Loan + subsidy above 100% of the project cost makes the equity
        # negative — the promoter is paid to take the project, which inverts
        # every return metric (month-0 inflow, garbage IRR, instant payback).
        if self.loan_fraction_of_project_cost + self.subsidy_fraction > 1.0:
            raise ValueError(
                "loan_fraction_of_project_cost + subsidy_fraction must be <= 1.0 "
                "(equity cannot be negative)"
            )
        # A moratorium covering the whole term leaves zero EMI months: the
        # schedule pays interest only and the principal silently vanishes from
        # every cash flow.
        if self.moratorium_months >= self.loan_term_months:
            raise ValueError(
                "moratorium_months must be shorter than loan_term_months "
                "(otherwise the principal is never repaid)"
            )
        return self


class RiskVariable(_Group):
    """Triangular spread (multiplier on the base value) for one Monte Carlo variable."""

    enabled: bool = True
    low: FiniteFloat = Field(default=0.8, gt=0.0, le=MAX_RISK_MULTIPLIER)
    high: FiniteFloat = Field(default=1.2, gt=0.0, le=MAX_RISK_MULTIPLIER)

    @model_validator(mode="after")
    def _brackets_mode(self) -> "RiskVariable":
        # The Monte Carlo draws rng.triangular(low, high, 1.0) — the mode is
        # fixed at the base value, so a spread that doesn't bracket 1.0 would
        # silently draw garbage.
        if not self.low <= 1.0 <= self.high:
            raise ValueError("low and high must bracket the base multiplier 1.0")
        return self


class RiskAssumptions(_Group):
    """Monte Carlo controls and per-variable triangular spreads."""

    monte_carlo_runs: int = Field(default=500, ge=1, le=2000)
    # Bounded so the value survives a browser round-trip: OpenAPI "integer"
    # becomes a TypeScript number, and anything past 2**53 silently changes
    # when the page loads a scenario and saves it back, quietly reseeding the
    # run. 2**31-1 is well inside the exactly-representable range.
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    meat_price: RiskVariable = Field(default_factory=lambda: RiskVariable(low=0.80, high=1.20))
    feed_price: RiskVariable = Field(default_factory=lambda: RiskVariable(low=0.90, high=1.25))
    adult_mortality: RiskVariable = Field(default_factory=lambda: RiskVariable(low=0.60, high=1.80))
    kid_mortality: RiskVariable = Field(default_factory=lambda: RiskVariable(low=0.60, high=1.60))
    litter_size: RiskVariable = Field(default_factory=lambda: RiskVariable(low=0.85, high=1.10))
    conception_rate: RiskVariable = Field(default_factory=lambda: RiskVariable(low=0.80, high=1.05))
    fodder_yield: RiskVariable = Field(default_factory=lambda: RiskVariable(low=0.65, high=1.10))
    operating_cost: RiskVariable = Field(default_factory=lambda: RiskVariable(low=0.90, high=1.20))
    # Strength of the model's documented market, climate and disease latent
    # factors. A Gaussian copula maps those correlated factors into each
    # triangular marginal without changing its low/mode/high definition.
    correlation_strength: FiniteFloat = Field(default=0.60, ge=0.0, le=0.95)
    disease_outbreak_probability_annual: FiniteFloat = Field(default=0.10, ge=0.0, le=1.0)
    disease_outbreak_duration_months: int = Field(default=3, ge=1, le=24)
    disease_adult_mortality_multiplier: FiniteFloat = Field(default=2.0, ge=1.0, le=20.0)
    disease_kid_mortality_multiplier: FiniteFloat = Field(default=2.5, ge=1.0, le=20.0)
    disease_conception_multiplier: FiniteFloat = Field(default=0.70, gt=0.0, le=1.0)
    drought_probability_annual: FiniteFloat = Field(default=0.15, ge=0.0, le=1.0)
    drought_duration_months: int = Field(default=4, ge=1, le=24)
    drought_fodder_yield_multiplier: FiniteFloat = Field(default=0.50, gt=0.0, le=1.0)
    drought_feed_price_multiplier: FiniteFloat = Field(default=1.30, ge=1.0, le=20.0)
    market_crash_probability_annual: FiniteFloat = Field(default=0.10, ge=0.0, le=1.0)
    market_crash_duration_months: int = Field(default=3, ge=1, le=24)
    market_crash_price_multiplier: FiniteFloat = Field(default=0.75, gt=0.0, le=1.0)


class OptimizationAssumptions(_Group):
    """Bounded decision search and lender-style feasibility constraints."""

    objective: Literal["balanced", "npv", "liquidity"] = "balanced"
    max_candidates: int = Field(default=120, ge=1, le=300)
    minimum_dscr: FiniteFloat = Field(default=1.20, ge=0.0, le=10.0)
    # None = no ceiling. MAX_MONEY is a *per-input* magnitude cap; reusing it as
    # the "unconstrained" sentinel silently failed every candidate for a large
    # farm, whose derived project cost is a product of several MAX_MONEY-bounded
    # inputs and legitimately exceeds it.
    maximum_project_cost: FiniteFloat | None = Field(default=None, ge=0.0)
    maximum_funding_gap: FiniteFloat | None = Field(default=None, ge=0.0)
    doe_scale_low: FiniteFloat = Field(default=0.75, gt=0.0, le=5.0)
    doe_scale_high: FiniteFloat = Field(default=1.25, gt=0.0, le=5.0)
    doe_scale_steps: int = Field(default=3, ge=1, le=9)
    sale_age_radius_months: int = Field(default=2, ge=0, le=9)
    retention_step: FiniteFloat = Field(default=0.25, ge=0.0, le=1.0)
    loan_fraction_step: FiniteFloat = Field(default=0.15, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _range_order(self) -> "OptimizationAssumptions":
        if self.doe_scale_low > self.doe_scale_high:
            raise ValueError("doe_scale_low must be <= doe_scale_high")
        return self


class HerdEventAssumptions(_Group):
    """One scheduled herd event: a purchase or sale applied at the start of a
    simulation month (before that month's aging, breeding and mortality).

    Purchases are charged as operating cost in that month (not added to the
    project cost); sales are booked as meat/cull revenue. ``price_per_head``
    overrides the default valuation — purchases default to the class purchase
    price (adults) or live-weight meat value (young stock); sales default to
    live-weight meat value for young stock and cull value for adults.
    """

    month: int = Field(ge=1)  # 1-based simulation month; <= meta.horizon_months
    kind: Literal["purchase", "sale"]
    animal_class: Literal[
        "doe",
        "buck",
        "female_kid",
        "male_kid",
        "female_weaner",
        "male_weaner",
        "female_grower",
        "male_grower",
    ]
    count: FiniteFloat = Field(gt=0.0, le=MAX_HEAD)  # head (expected-value float, like all counts)
    # ₹; None = default valuation.
    price_per_head: FiniteFloat | None = Field(default=None, ge=0.0, le=MAX_MONEY)


class SimulationAssumptions(_Group):
    """Full assumption set; ``SimulationAssumptions()`` is a valid default run."""

    meta: MetaAssumptions = Field(default_factory=MetaAssumptions)
    herd: HerdAssumptions = Field(default_factory=HerdAssumptions)
    reproduction: ReproductionAssumptions = Field(default_factory=ReproductionAssumptions)
    mortality: MortalityAssumptions = Field(default_factory=MortalityAssumptions)
    culling: CullingAssumptions = Field(default_factory=CullingAssumptions)
    growth: GrowthAssumptions = Field(default_factory=GrowthAssumptions)
    sales: SalesAssumptions = Field(default_factory=SalesAssumptions)
    feed: FeedAssumptions = Field(default_factory=FeedAssumptions)
    costs: CostsAssumptions = Field(default_factory=CostsAssumptions)
    finance: FinanceAssumptions = Field(default_factory=FinanceAssumptions)
    risk: RiskAssumptions = Field(default_factory=RiskAssumptions)
    optimization: OptimizationAssumptions = Field(default_factory=OptimizationAssumptions)
    # Bounded like every other list input: an unbounded events payload is an
    # unbounded work/payload vector.
    events: list[HerdEventAssumptions] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def _events_within_horizon(self) -> "SimulationAssumptions":
        for event in self.events:
            if event.month > self.meta.horizon_months:
                raise ValueError(
                    f"event month {event.month} exceeds the simulation horizon "
                    f"({self.meta.horizon_months} months)"
                )
        return self

    @model_validator(mode="after")
    def _festival_months_within_horizon(self) -> "SimulationAssumptions":
        if len(set(self.sales.festival_sale_months)) != len(self.sales.festival_sale_months):
            raise ValueError("sales.festival_sale_months must not contain duplicates")
        for month in self.sales.festival_sale_months:
            if month < 1 or month > self.meta.horizon_months:
                raise ValueError(
                    "sales.festival_sale_months must contain 1-based months inside the horizon"
                )
        return self

    @model_validator(mode="after")
    def _breeding_age_within_doe_lifespan(self) -> "SimulationAssumptions":
        # The engine tracks doe ages in an array of max_doe_age_months + 1
        # slots and writes every doe entering the pool at index
        # age_at_first_breeding_months — afb beyond the max age indexes out of
        # range mid-run (and a doe culled before she can first breed is a
        # broken model anyway). The per-field floors already imply this
        # (afb <= 30 < 36 <= max_doe_age); the validator pins the invariant
        # against future bound changes.
        if self.reproduction.age_at_first_breeding_months > self.culling.max_doe_age_months:
            raise ValueError(
                "reproduction.age_at_first_breeding_months must be <= culling.max_doe_age_months"
            )
        return self

    @model_validator(mode="after")
    def _adult_weight_above_yearling_curve(self) -> "SimulationAssumptions":
        # weight_at_age linearly interpolates from weight_by_age_months[12]
        # toward the adult weight over ages 13-23; an adult weight BELOW the
        # yearling weight yields a monotonically DECREASING curve past age 12
        # (a 26.5 kg yearling shrinks to a 1 kg adult) — no crash, but every
        # feed/sale/insurance figure for that class becomes nonsensical.
        table = self.growth.weight_by_age_months
        if len(table) > 12:
            yearling = max(table[:13])
            if self.growth.adult_weight_doe_kg < yearling:
                raise ValueError(
                    f"growth.adult_weight_doe_kg ({self.growth.adult_weight_doe_kg}) "
                    f"must be >= the max weight in weight_by_age_months[0..12] ({yearling})"
                )
            if self.growth.adult_weight_buck_kg < yearling:
                raise ValueError(
                    f"growth.adult_weight_buck_kg ({self.growth.adult_weight_buck_kg}) "
                    f"must be >= the max weight in weight_by_age_months[0..12] ({yearling})"
                )
        return self
