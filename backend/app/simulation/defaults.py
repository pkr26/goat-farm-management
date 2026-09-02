"""Breed presets and production-system variants.

Presets are built by factory functions (preferred, since they return fresh
models) and also collected in ``BREED_PRESETS`` for lookup by name. Numbers are
approximate literature values for Indian conditions; non-obvious figures carry
a short source comment. Weight curves are scaled from the Osmanabadi stall-fed
curve in proportion to each breed's yearling weight (NBAGR breed descriptors).
"""

from typing import Literal, get_args

from .assumptions import (
    CostsAssumptions,
    CullingAssumptions,
    FeedAssumptions,
    FinanceAssumptions,
    GrowthAssumptions,
    HerdAssumptions,
    MortalityAssumptions,
    ReproductionAssumptions,
    RiskAssumptions,
    RiskVariable,
    SalesAssumptions,
    SimulationAssumptions,
)
from .market import bakrid_festival_months

System = Literal["stall_fed", "semi_intensive"]

# Derived from the Literal (never a hand-maintained copy) so the API layer's
# list of production systems can't drift from the type.
SYSTEMS: list[str] = list(get_args(System))


def apply_system(a: SimulationAssumptions, system: System) -> SimulationAssumptions:
    """Return a copy of ``a`` adjusted for the production system.

    Semi-intensive: ~30% of DM comes free from grazing; field exposure raises
    kid and adult mortality relative to stall feeding (TNAU/ICAR comparisons of
    stall-fed vs grazing systems).
    """
    if system == "stall_fed":
        return a.model_copy(deep=True)
    if system == "semi_intensive":
        variant = a.model_copy(deep=True)
        variant.feed.grazing_dm_fraction = 0.3  # TNAU semi-intensive budgets
        # An uplift, not an assignment: the docstring promises field exposure
        # *raises* mortality relative to stall feeding, but a flat assignment
        # left the already-fragile breeds (Black Bengal sits at 0.12 stall-fed)
        # showing the grazing feed saving with no offsetting loss at all.
        # round() keeps the stall-fed floors exact (0.05 + 0.01 is
        # 0.060000000000000005 in binary float) while still lifting a breed
        # that already sits at or above them.
        variant.mortality.adult = min(0.9, max(0.06, round(variant.mortality.adult + 0.01, 6)))
        variant.mortality.kid_pre_weaning = min(
            0.9, max(0.12, round(variant.mortality.kid_pre_weaning + 0.02, 6))
        )
        return variant
    raise ValueError(f"unknown production system: {system!r}")


def _osmanabadi_weights() -> list[float]:
    # Osmanabadi stall-fed curve: 2.5 kg birth, ~2 kg/month to yearling (NBAGR).
    return [2.5 + 2.0 * m for m in range(13)]


def _scaled_weights(factor: float, birth_weight: float) -> list[float]:
    """Scale the Osmanabadi curve to another breed's yearling weight."""
    base = _osmanabadi_weights()
    return [birth_weight + (w - base[0]) * factor for w in base]


def osmanabadi(system: System = "stall_fed") -> SimulationAssumptions:
    """Osmanabadi (default): meat breed, no saleable milk, 50 does + 2 bucks.

    The base defaults already carry the Telangana 2025-26 price and cost
    calibration, auto-fill the Bakrid festival months for the run's own
    horizon, and hold males finishing near a festival for the festival sale —
    the largest price event of the year is priced from month 1.
    """
    a = SimulationAssumptions()
    a.sales.festival_sale_months = bakrid_festival_months(
        a.meta.start_year_month, a.meta.horizon_months
    )
    return apply_system(a, system)


def sirohi(system: System = "stall_fed") -> SimulationAssumptions:
    """Sirohi: heavier dual-purpose breed, moderate milk, mostly single kids."""
    a = SimulationAssumptions(
        herd=HerdAssumptions(
            doe_purchase_price=9000.0,  # NABARD unit costs, heavier breed
            buck_purchase_price=14000.0,
        ),
        reproduction=ReproductionAssumptions(
            litter_size=1.4,  # NBAGR: ~70% singles
            # Larger frame matures later than the Osmanabadi floor.
            age_at_first_breeding_months=12,
        ),
        growth=GrowthAssumptions(
            birth_weight_kg=3.0,
            adult_weight_doe_kg=40.0,  # NBAGR breed descriptor
            adult_weight_buck_kg=50.0,
            weight_by_age_months=_scaled_weights(1.18, 3.0),
        ),
        sales=SalesAssumptions(lactation_milk_litres=110.0),  # NBAGR: ~0.7 kg/d x 150 d
    )
    return apply_system(a, system)


def barbari(system: System = "stall_fed") -> SimulationAssumptions:
    """Barbari: compact, early-maturing, highly prolific stall-feeding breed."""
    a = SimulationAssumptions(
        herd=HerdAssumptions(
            doe_purchase_price=7000.0,
            buck_purchase_price=10000.0,
        ),
        reproduction=ReproductionAssumptions(
            litter_size=1.8,  # NBAGR: twins common
            age_at_first_breeding_months=10,
        ),
        growth=GrowthAssumptions(
            birth_weight_kg=2.0,
            adult_weight_doe_kg=27.0,  # NBAGR breed descriptor
            adult_weight_buck_kg=30.0,
            weight_by_age_months=_scaled_weights(0.85, 2.0),
            sale_age_months=8,
        ),
        sales=SalesAssumptions(lactation_milk_litres=90.0),  # dual-purpose, ~0.6 kg/d
    )
    return apply_system(a, system)


def jamunapari(system: System = "stall_fed") -> SimulationAssumptions:
    """Jamunapari: large dairy-type breed, high milk, slower maturity."""
    a = SimulationAssumptions(
        herd=HerdAssumptions(
            doe_purchase_price=11000.0,
            buck_purchase_price=16000.0,
        ),
        reproduction=ReproductionAssumptions(
            litter_size=1.3,  # NBAGR: mostly singles
            age_at_first_breeding_months=15,
            lactation_months=6,  # long dairy lactation
        ),
        growth=GrowthAssumptions(
            birth_weight_kg=3.5,
            adult_weight_doe_kg=45.0,  # NBAGR breed descriptor
            adult_weight_buck_kg=55.0,
            weight_by_age_months=_scaled_weights(1.3, 3.5),
        ),
        sales=SalesAssumptions(lactation_milk_litres=200.0),  # NBAGR: ~1.1 kg/d x 180 d
    )
    return apply_system(a, system)


def beetal(system: System = "stall_fed") -> SimulationAssumptions:
    """Beetal: large dual-purpose Punjab breed, good milk and prolificacy."""
    a = SimulationAssumptions(
        herd=HerdAssumptions(
            doe_purchase_price=10000.0,
            buck_purchase_price=15000.0,
        ),
        reproduction=ReproductionAssumptions(
            litter_size=1.6,
            age_at_first_breeding_months=14,
            lactation_months=5,
        ),
        growth=GrowthAssumptions(
            birth_weight_kg=3.2,
            adult_weight_doe_kg=40.0,  # NBAGR breed descriptor
            adult_weight_buck_kg=46.0,
            weight_by_age_months=_scaled_weights(1.2, 3.2),
        ),
        sales=SalesAssumptions(lactation_milk_litres=175.0),  # NBAGR: ~1.2 kg/d x 150 d
    )
    return apply_system(a, system)


def black_bengal(system: System = "stall_fed") -> SimulationAssumptions:
    """Black Bengal: small, very prolific, early-maturing meat/skin breed."""
    a = SimulationAssumptions(
        herd=HerdAssumptions(
            doe_purchase_price=4500.0,  # small-framed breed
            buck_purchase_price=6000.0,
        ),
        reproduction=ReproductionAssumptions(
            litter_size=2.0,  # NBAGR: twins/triplets common
            age_at_first_breeding_months=9,
        ),
        growth=GrowthAssumptions(
            birth_weight_kg=1.5,
            adult_weight_doe_kg=18.0,  # NBAGR breed descriptor
            adult_weight_buck_kg=20.0,
            weight_by_age_months=_scaled_weights(0.55, 1.5),
            sale_age_months=8,
        ),
        mortality=MortalityAssumptions(kid_pre_weaning=0.12),  # small kids, fragile
    )
    return apply_system(a, system)


def boer_cross(system: System = "stall_fed") -> SimulationAssumptions:
    """Boer cross: fast-growing terminal meat cross, premium carcass."""
    a = SimulationAssumptions(
        herd=HerdAssumptions(
            doe_purchase_price=10000.0,  # crossbred premium
            buck_purchase_price=18000.0,
        ),
        reproduction=ReproductionAssumptions(
            litter_size=1.7,
            age_at_first_breeding_months=12,
        ),
        growth=GrowthAssumptions(
            birth_weight_kg=3.0,
            adult_weight_doe_kg=40.0,
            adult_weight_buck_kg=50.0,
            # ~2.6 kg/month to yearling: Boer-cross growth rates (ICAR AICRP).
            weight_by_age_months=[3.0 + 2.6 * m for m in range(13)],
            sale_age_months=8,
        ),
        sales=SalesAssumptions(meat_price_per_kg=400.0),  # premium meat cross
    )
    return apply_system(a, system)


def murrah_dairy(system: System = "stall_fed") -> SimulationAssumptions:
    """Murrah buffalo dairy (Navipet, Telangana): milk production, not meat.

    Calibrated to 2025-26 Telangana figures: second-lactation in-milk Murrahs
    ₹1.0-1.4 lakh (Hyderabad/Karnal trade), ~2,100 L per 305-day lactation
    (CIRB breed standard 2,000 kg; NDRI first lactation 1,750-1,850; elite
    recorded herds 2,600+ — 2,100 sits mid-range for purchased proven
    animals), ~46-49% per-AI conception in field conditions, ~310-day
    gestation, procurement ~₹840-865/kg fat (Vijaya/Sangam 2025-26) blended
    with direct sales at ₹900, concentrate ₹24-32/kg, cull buffaloes
    ~₹145-170/kg live (₹300-320/kg carcass at export plants), sheds
    ₹30-50k/animal place, 1 worker per ~20 head. Breeding is AI-first — sexed
    semen for the first two services of each attempt (88-91% female births at
    an ~8-15pp conception penalty) with a 3-service repeat-breeder cull — so
    no sire herd is carried.
    """
    a = SimulationAssumptions(
        herd=HerdAssumptions(
            does=60,  # foundation batch of in-milk second-lactation purchases
            bucks=0,  # AI-first: no sire herd carried
            # The site plan grows the herd toward ~200 milking buffaloes;
            # heifer retention (not the cap) is the growth brake.
            max_breeding_does=0,  # unlimited
            female_retention_fraction=0.75,
            doe_purchase_price=110000.0,
            buck_purchase_price=0.0,
            auto_purchase_bucks=False,
            foundation_doe_age_min_months=42,  # second/third lactation in-milk
            foundation_doe_age_max_months=60,
            foundation_flock_state="mixed",
        ),
        reproduction=ReproductionAssumptions(
            conception_rate=0.45,  # per conventional AI, field conditions
            gestation_months=10,  # ~310 days
            lactation_months=10,  # 305-day lactation
            months_open_before_breeding=2,  # first AI at ~60 days post-calving
            litter_size=1.0,  # single calf
            # AI policy: sexed semen for the first two services of each
            # breeding attempt (90% female births, ~15% conception penalty),
            # conventional semen after; failures past the third service are
            # repeat-breeder culls.
            sex_ratio_female=0.5,
            sexed_semen_services=2,
            sexed_female_fraction=0.90,
            sexed_conception_multiplier=0.85,
            max_services_before_cull=3,
            stillbirth_rate=0.03,
            # Heifers bred at ~345 kg / 24 months (intensively reared; CIRB
            # field AFC averages ~43 months — this herd breeds early on
            # purpose with calf-starter and heifer rearing).
            age_at_first_breeding_months=24,
        ),
        mortality=MortalityAssumptions(
            kid_pre_weaning=0.10,  # organized Murrah farms ~8%, field higher
            kid_post_weaning=0.04,
            grower=0.03,
            adult=0.025,
        ),
        culling=CullingAssumptions(
            # Voluntary/age culls ONLY: the 3-service repeat-breeder rule above
            # already removes ~17%/yr at the modelled conception rates, and this
            # residual ~5% brings TOTAL disposal to ~21%/yr — a ~4.8-lactation
            # herd life, matching the "5-6 lactation" discipline real Murrah
            # farms run. (A flat 18% here on top of the service rule was a
            # one-third-per-year churn that liquidated the herd economics.)
            doe_cull_rate_annual=0.05,
            max_doe_age_months=132,
            buck_rotation_years=3,
            buck_doe_ratio=100,  # effectively unconstrained (AI)
        ),
        growth=GrowthAssumptions(
            # CIRB recorded calf weights: males 31.7 kg, females 30 kg.
            birth_weight_kg=31.0,
            adult_weight_doe_kg=520.0,  # mature Murrah female ~500-550 kg
            adult_weight_buck_kg=600.0,
            # Murrah heifer growth: ~31 kg birth, ~215 kg yearling, ~345 kg at
            # the 24-month breeding age, adult ~520 kg reached around 40
            # months (NDRI growth studies; the old 24-month maturation put
            # heifers ~25-30% above recorded weights).
            weight_by_age_months=[31.0 + 15.3 * m for m in range(13)],
            adult_weight_age_months=40,
            young_male_weight_premium=0.05,
            sale_age_months=14,  # retained male calves grown for meat
        ),
        sales=SalesAssumptions(
            meat_price_per_kg=160.0,  # buffalo live/cull market ₹145-170/kg
            cull_doe_price_per_kg=160.0,
            cull_buck_price_per_kg=170.0,
            monthly_meat_price_multipliers=[1.0] * 12,
            annual_livestock_price_growth_rate=0.05,
            festival_sale_months=[],
            milk_price_per_litre=58.0,  # fallback = procurement ₹850/kg fat @ 6.8%
            # Retained heifer calves are whole-milk fed to ~day 90 per the
            # farm's own protocol (~225 L/calf at 2.5 L/day average): that
            # milk is produced but not sold.
            calf_milk_litres_per_day_per_calf=2.5,
            # In-milk second-lactation purchases give ~2,100 L over a 305-day
            # lactation (CIRB breed standard 2,000 kg; NDRI 1,750-1,850 first
            # lactation; elite recorded herds 2,600+). 2,100 with the Wood
            # curve below averages ~6.9 L/day in milk.
            lactation_milk_litres=2100.0,
            # Wood lactation curve peaking at day 65 of lactation (published
            # Murrah/river-buffalo Wood fits peak day 57-73): ~6.3 L/day in the
            # first month, peak ~10.2 L/day around month 3, ~6.9 L/day average
            # over the 305-day lactation.
            milk_curve_shape="wood",
            milk_peak_day=65.0,
            # Verified procurement basis (Vijaya/Sangam/Amul ₹840-865/kg fat,
            # 2025-26): the fat-based rate is the price of record. Farms with
            # Phase B direct sales at better-than-procurement rates override
            # upward as a deliberate choice — the default must not bake an
            # unverified +6% blend into a lender-facing projection.
            milk_price_per_kg_fat=850.0,
            milk_fat_pct=6.8,
            milk_persistency_monthly=0.93,  # recorded Murrah persistency ~89-93%
            # Telangana yield seasonality: summer (Apr-Jul) heat-stress
            # trough, winter peak.
            monthly_milk_yield_multipliers=[
                1.05,
                1.06,
                1.03,
                0.97,
                0.90,
                0.86,
                0.87,
                0.93,
                0.99,
                1.05,
                1.08,
                1.08,
            ],
            # Lean-season (summer) procurement pays a small premium.
            monthly_milk_price_multipliers=[
                1.00,
                1.00,
                1.02,
                1.04,
                1.06,
                1.07,
                1.06,
                1.03,
                0.99,
                0.97,
                0.98,
                0.98,
            ],
            # Matched to feed inflation (0.06): a decade of 1 pp/yr real
            # squeeze with no procurement pass-through made the flagship
            # dairy preset loss-making by construction — Telangana procurement
            # revisions (Vijaya ₹82→₹85/L within 2025) track feed costs.
            annual_milk_price_growth_rate=0.06,
            male_calf_sell_at_birth_fraction=0.9,  # sexed-semen strategy
            male_calf_price_per_head=1600.0,  # week-old bull calf ₹1,200-1,800
            manure_income_per_adult_per_year=3500.0,  # biogas slurry + gas savings
        ),
        feed=FeedAssumptions(
            dmi_kid_creep=0.010,  # calf starter ~3.5 kg/day at 350 kg calf
            dmi_weaner=0.020,
            dmi_grower=0.022,  # heifers ~2.2% of BW
            dmi_doe_maintenance=0.022,  # dry/milking maintenance
            dmi_doe_pregnant=0.024,
            dmi_doe_lactating=0.030,  # 14-16.5 kg DM at 500-550 kg
            dmi_buck=0.022,
            concentrate_share_kid_creep=0.85,
            concentrate_share_weaner=0.40,
            concentrate_share_grower=0.30,
            concentrate_share_doe_maintenance=0.12,
            concentrate_share_doe_pregnant=0.25,
            # ~6 kg DM concentrate/day for a 10-12 L milker (DairyKnowledge
            # benchmark ration: 6.2 kg conc at ₹20 for ~10 L/day).
            concentrate_share_doe_lactating=0.38,
            concentrate_share_buck=0.15,
            green_dm_pct=0.20,  # maize fodder ~18-22% DM
            green_price_per_kg=0.8,  # home-grown maize fodder
            purchased_green_price_per_kg=2.5,
            dry_price_per_kg=5.0,  # paddy straw ₹4-6/kg
            concentrate_price_per_kg=26.0,  # blended buffalo feed ₹24-32/kg
            annual_feed_price_growth_rate=0.06,  # maize/ethanol structural driver
            # A 60-head unit growing to ~90-100 milking buffalo needs ~300 t
            # green DM/yr. Single-cut maize at 5 t DM/acre would require 60
            # acres; a multi-cut napier/BMR-sorghum fodder plot yields 8-16 t
            # DM/acre/yr (TNAU: 40-80 t/ha green), so 25 acres covers ~80% of
            # the need at home cost and the rest is bought in the lean months.
            # The old 10-acre plan priced ~85% of every green kilogram at the
            # ₹2.5/kg market rate — feed alone then exceeded milk revenue.
            cultivated_fodder_acres=25.0,
            fodder_yield_t_dm_per_acre_year=10.0,
        ),
        costs=CostsAssumptions(
            vet_per_animal_per_year=2000.0,  # private retainer + vaccines
            labour_per_month=16000.0,  # Telangana dairy worker
            labour_per_head_threshold=20,  # mechanical milking
            insurance_pct_stock_value_annual=0.04,
            misc_overhead_per_month=15000.0,
            shed_cost_per_animal_place=40000.0,  # loose housing + milking infra
            equipment_cost_per_animal=20000.0,  # parlour/BMC/collars per place
            shed_useful_life_years=20,
            equipment_useful_life_years=10,
        ),
        finance=FinanceAssumptions(
            loan_fraction_of_project_cost=0.60,
            interest_rate_annual=0.09,  # agri term lending
            loan_term_months=120,
            moratorium_months=12,
            # NPDD/AHIDF-style support shows up as interest subvention in
            # practice; a small capital-subsidy fraction (2%) stays as a
            # conservative placeholder. (The old citation — DEDS — was
            # discontinued ~2020-21 and its ₹8.25 L ceiling never applied to
            # the milch-animal component of a unit this size anyway.)
            subsidy_fraction=0.02,
            working_capital_months=6,  # milk revenue from month 1
        ),
        risk=RiskAssumptions(
            milk_price=RiskVariable(low=0.85, high=1.15),
            litter_size=RiskVariable(low=0.95, high=1.02),  # single-calf species
            kid_mortality=RiskVariable(low=0.60, high=1.80),
        ),
    )
    if system == "semi_intensive":
        # Buffaloes are zero-grazed in this model; semi-intensive only lifts
        # mortality the way field exposure does for goats.
        variant = a.model_copy(deep=True)
        variant.mortality.adult = min(0.9, round(variant.mortality.adult + 0.01, 6))
        variant.mortality.kid_pre_weaning = min(
            0.9, round(variant.mortality.kid_pre_weaning + 0.02, 6)
        )
        return variant
    return a


PRESET_FACTORIES = {
    "osmanabadi": osmanabadi,
    "sirohi": sirohi,
    "barbari": barbari,
    "jamunapari": jamunapari,
    "beetal": beetal,
    "black_bengal": black_bengal,
    "boer_cross": boer_cross,
    "murrah_dairy": murrah_dairy,
}

# Lookup table of stall-fed defaults; use the factories for other systems or
# when a fresh, mutable instance is needed.
BREED_PRESETS: dict[str, SimulationAssumptions] = {
    name: factory() for name, factory in PRESET_FACTORIES.items()
}


def get_preset(breed: str, system: System = "stall_fed") -> SimulationAssumptions:
    """Fresh assumptions for a breed + production system."""
    key = breed.strip().lower().replace(" ", "_").replace("-", "_")
    if key not in PRESET_FACTORIES:
        raise ValueError(f"unknown breed preset: {breed!r} (have {sorted(PRESET_FACTORIES)})")
    return PRESET_FACTORIES[key](system)


__all__ = [
    "BREED_PRESETS",
    "PRESET_FACTORIES",
    "SYSTEMS",
    "System",
    "apply_system",
    "get_preset",
]
