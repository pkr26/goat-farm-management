"""Assumption model for the bio-economic herd simulation.

Every field carries a default, so ``SimulationAssumptions()`` is a complete,
valid Osmanabadi stall-fed NABARD-style run (50 does + 2 bucks, 120 months).
All sub-models forbid extra keys so API payloads fail loudly on typos.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Group(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MetaAssumptions(_Group):
    """Run horizon and calendar anchoring."""

    horizon_months: int = Field(default=120, ge=12, le=240)
    # "YYYY-MM"; simulation month 1 falls in this calendar month. Used only for
    # the seasonal (Eid) price uplift.
    start_year_month: str = "2026-08"


class HerdAssumptions(_Group):
    """Starting stock and replacement/purchase policy."""

    does: int = Field(default=50, ge=0)
    bucks: int = Field(default=2, ge=0)
    female_growers: int = Field(default=0, ge=0)
    male_growers: int = Field(default=0, ge=0)
    female_weaners: int = Field(default=0, ge=0)
    male_weaners: int = Field(default=0, ge=0)
    female_kids: int = Field(default=0, ge=0)
    male_kids: int = Field(default=0, ge=0)
    # Fraction of female growers reaching first-breeding age that are kept as
    # replacements; the rest are sold as meat. NABARD models use ~0.5.
    female_retention_fraction: float = Field(default=0.5, ge=0.0, le=1.0)
    # Cap on the breeding-doe pool; 0 = unlimited. Default 50 holds the flock
    # at the NABARD 50+2 unit size (model projects keep the breeding flock
    # constant and sell surplus replacements); set 0 for unconstrained growth.
    max_breeding_does: int = Field(default=50, ge=0)
    doe_purchase_price: float = Field(default=8000.0, ge=0.0)  # ₹, NABARD unit-cost tables
    buck_purchase_price: float = Field(default=12000.0, ge=0.0)  # ₹, NABARD unit-cost tables
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

    conception_rate: float = Field(default=0.85, ge=0.0, le=1.0)  # per service, ICAR herd models
    gestation_months: int = Field(default=5, ge=1, le=7)  # ~150 days
    lactation_months: int = Field(default=3, ge=1, le=8)
    # Months a doe waits after lactation before rebreeding (post-partum anoestrus).
    months_open_before_breeding: int = Field(default=2, ge=0, le=12)
    litter_size: float = Field(default=1.6, ge=0.5, le=4.0)  # kids born per kidding
    sex_ratio_female: float = Field(default=0.5, ge=0.0, le=1.0)
    age_at_first_breeding_months: int = Field(default=12, ge=6, le=30)
    stillbirth_rate: float = Field(default=0.02, ge=0.0, le=0.5)


class MortalityAssumptions(_Group):
    """Annual mortality fractions per class (converted to monthly compounding rates)."""

    kid_pre_weaning: float = Field(default=0.10, ge=0.0, le=0.9)  # FAO/ICAR small-ruminant models
    kid_post_weaning: float = Field(default=0.05, ge=0.0, le=0.9)
    grower: float = Field(default=0.04, ge=0.0, le=0.9)
    adult: float = Field(default=0.05, ge=0.0, le=0.9)


class CullingAssumptions(_Group):
    """Disposal and sire-replacement policy."""

    # Annual fraction of breeding does culled; applied from simulation month 13
    # (the foundation stock gets one full year grace), NABARD/TNAU convention.
    doe_cull_rate_annual: float = Field(default=0.20, ge=0.0, le=1.0)
    max_doe_age_months: int = Field(default=72, ge=24, le=180)
    buck_rotation_years: int = Field(default=3, ge=1, le=10)  # avoid inbreeding
    buck_doe_ratio: int = Field(default=25, ge=1, le=100)  # 1 buck per 25 does


class GrowthAssumptions(_Group):
    """Live-weight curve and sale-age policy.

    ``weight_by_age_months`` gives live weight (kg) at ages 0..12 months; from
    month 13 the curve approaches the adult weight linearly, reaching it at 24
    months. Default table is an Osmanabadi stall-fed curve (2.5 kg birth weight,
    ~2 kg/month pre-yearling gain) per ICAR/NBAGR breed descriptors.
    """

    birth_weight_kg: float = Field(default=2.5, gt=0.0)
    adult_weight_doe_kg: float = Field(default=32.0, gt=0.0)
    adult_weight_buck_kg: float = Field(default=34.0, gt=0.0)
    weight_by_age_months: list[float] = Field(
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
    )
    # Age at which surplus males are sold for meat. Must be >= 6 so males pass
    # through the grower chain (weaning at 3, grower from 6). Osmanabadi
    # stall-fed kids are marketed at 9-12 months; 12 gives the yearling finish
    # (~26.5 kg) whose extra weight outweighs the added feed at default prices.
    sale_age_months: int = Field(default=12, ge=6, le=24)


class SalesAssumptions(_Group):
    """Prices and non-meat revenue streams (₹)."""

    meat_price_per_kg: float = Field(default=350.0, ge=0.0)  # ₹/kg live weight
    cull_doe_price_per_kg: float = Field(default=180.0, ge=0.0)
    cull_buck_price_per_kg: float = Field(default=200.0, ge=0.0)
    # Calendar month (1-12) in which the Bakrid price uplift applies; 0 disables it.
    eid_month: int = Field(default=0, ge=0, le=12)
    eid_price_uplift: float = Field(default=0.30, ge=0.0, le=2.0)
    milk_price_per_litre: float = Field(default=30.0, ge=0.0)
    # Total litres per lactation per doe; 0 for meat breeds (Osmanabadi),
    # ~110 Sirohi, ~175 Beetal, ~200 Jamunapari (NBAGR descriptors).
    lactation_milk_litres: float = Field(default=0.0, ge=0.0)
    manure_income_per_adult_per_year: float = Field(default=900.0, ge=0.0)  # ₹, TNAU budgets


class FeedAssumptions(_Group):
    """Dry-matter intake, ration split, feed prices and fodder production."""

    # DMI as a fraction of body weight, per physiological state (ICAR feeding standards).
    dmi_kid_creep: float = Field(default=0.015, gt=0.0, le=0.10)
    dmi_weaner: float = Field(default=0.03, gt=0.0, le=0.10)
    dmi_grower: float = Field(default=0.035, gt=0.0, le=0.10)
    dmi_doe_maintenance: float = Field(default=0.03, gt=0.0, le=0.10)
    dmi_doe_pregnant: float = Field(default=0.035, gt=0.0, le=0.10)
    dmi_doe_lactating: float = Field(default=0.045, gt=0.0, le=0.10)
    dmi_buck: float = Field(default=0.035, gt=0.0, le=0.10)
    # Per-class concentrate share of DM (ICAR feeding standards / TNAU rations);
    # the remainder is green:dry fodder in a fixed 2:1 DM ratio, so each class's
    # shares sum to 1. Creep feed is mostly concentrate; dry/mtenance does get
    # almost none.
    concentrate_share_kid_creep: float = Field(default=0.60, ge=0.0, le=1.0)
    concentrate_share_weaner: float = Field(default=0.20, ge=0.0, le=1.0)
    concentrate_share_grower: float = Field(default=0.20, ge=0.0, le=1.0)
    concentrate_share_doe_maintenance: float = Field(default=0.05, ge=0.0, le=1.0)
    concentrate_share_doe_pregnant: float = Field(default=0.15, ge=0.0, le=1.0)
    concentrate_share_doe_lactating: float = Field(default=0.20, ge=0.0, le=1.0)
    concentrate_share_buck: float = Field(default=0.15, ge=0.0, le=1.0)
    # DM content of each feed, as-fed basis.
    green_dm_pct: float = Field(default=0.25, gt=0.0, le=1.0)
    dry_dm_pct: float = Field(default=0.88, gt=0.0, le=1.0)
    concentrate_dm_pct: float = Field(default=0.90, gt=0.0, le=1.0)
    # Prices per kg as-fed (₹). Green fodder is costed at home-grown production
    # cost (~₹1/kg, TNAU fodder budgets — the land requirement is reported
    # separately); ₹2-3/kg applies when all green is purchased at market.
    green_price_per_kg: float = Field(default=1.0, ge=0.0)
    dry_price_per_kg: float = Field(default=5.0, ge=0.0)  # paddy straw ₹4-6/kg
    concentrate_price_per_kg: float = Field(default=25.0, ge=0.0)  # commercial goat feed ₹22-28
    # Fraction of total DM obtained free from grazing (0 = stall-fed, ~0.3 semi-intensive).
    grazing_dm_fraction: float = Field(default=0.0, ge=0.0, le=1.0)
    # Cultivated fodder: area and DM yield (6 t DM/acre/yr ≈ hybrid napier/lucerne, TNAU).
    cultivated_fodder_acres: float = Field(default=0.0, ge=0.0)
    fodder_yield_t_dm_per_acre_year: float = Field(default=6.0, gt=0.0)


class CostsAssumptions(_Group):
    """Recurring and capital costs (₹)."""

    vet_per_animal_per_year: float = Field(default=250.0, ge=0.0)  # NABARD/TNAU budgets
    labour_per_month: float = Field(default=10000.0, ge=0.0)
    # One labourer per this many head; labour count scales up with herd size.
    labour_per_head_threshold: int = Field(default=75, ge=1)
    insurance_pct_stock_value_annual: float = Field(default=0.04, ge=0.0, le=0.25)
    misc_overhead_per_month: float = Field(default=2000.0, ge=0.0)
    shed_cost_per_animal_place: float = Field(default=4500.0, ge=0.0)  # NABARD unit costs
    equipment_cost_per_animal: float = Field(default=500.0, ge=0.0)


class FinanceAssumptions(_Group):
    """Project financing (NABARD refinance structure)."""

    # Explicit stock cost (₹); 0 = auto-computed from starting herd counts × prices.
    initial_stock_cost: float = Field(default=0.0, ge=0.0)
    loan_fraction_of_project_cost: float = Field(default=0.85, ge=0.0, le=1.0)
    interest_rate_annual: float = Field(default=0.11, ge=0.0, le=0.5)
    loan_term_months: int = Field(default=72, ge=1, le=180)
    # Interest-only period at the start of the loan (NABARD goat schemes: ~12 months).
    moratorium_months: int = Field(default=12, ge=0, le=60)
    # Capital subsidy as a fraction of project cost; reduces the promoter's equity.
    subsidy_fraction: float = Field(default=0.0, ge=0.0, le=0.9)
    discount_rate_annual: float = Field(default=0.12, ge=0.0, le=0.5)
    # Months of operating cost held as working capital inside the project cost.
    working_capital_months: int = Field(default=3, ge=0, le=24)


class RiskVariable(_Group):
    """Triangular spread (multiplier on the base value) for one Monte Carlo variable."""

    enabled: bool = True
    low: float = Field(default=0.8, gt=0.0)
    high: float = Field(default=1.2, gt=0.0)


class RiskAssumptions(_Group):
    """Monte Carlo controls and per-variable triangular spreads."""

    monte_carlo_runs: int = Field(default=500, ge=1, le=2000)
    seed: int = 42
    meat_price: RiskVariable = Field(default_factory=lambda: RiskVariable(low=0.80, high=1.20))
    feed_price: RiskVariable = Field(default_factory=lambda: RiskVariable(low=0.90, high=1.25))
    adult_mortality: RiskVariable = Field(default_factory=lambda: RiskVariable(low=0.60, high=1.80))
    kid_mortality: RiskVariable = Field(default_factory=lambda: RiskVariable(low=0.60, high=1.60))
    litter_size: RiskVariable = Field(default_factory=lambda: RiskVariable(low=0.85, high=1.10))
    conception_rate: RiskVariable = Field(default_factory=lambda: RiskVariable(low=0.80, high=1.05))


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
    count: float = Field(gt=0.0)  # head (expected-value float, like all counts)
    price_per_head: float | None = Field(default=None, ge=0.0)  # ₹; None = default valuation


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
    events: list[HerdEventAssumptions] = Field(default_factory=list)

    @model_validator(mode="after")
    def _events_within_horizon(self) -> "SimulationAssumptions":
        for event in self.events:
            if event.month > self.meta.horizon_months:
                raise ValueError(
                    f"event month {event.month} exceeds the simulation horizon "
                    f"({self.meta.horizon_months} months)"
                )
        return self
