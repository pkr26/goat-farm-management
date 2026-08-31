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

    Beyond the base defaults (which already carry the Telangana 2025-26 price
    and cost calibration), the preset fills in the Bakrid festival months for
    the run's own horizon so the largest price event of the year is priced
    from month 1 instead of being a toggle nobody finds.
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
        reproduction=ReproductionAssumptions(litter_size=1.4),  # NBAGR: ~70% singles
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
        reproduction=ReproductionAssumptions(litter_size=1.7),
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
    ₹1.0-1.4 lakh (Hyderabad/Karnal trade), 1,800-2,200 L per 305-day
    lactation (ICAR/NDRI), ~46-49% per-AI conception in field conditions,
    ~310-day gestation, procurement ~₹840-865/kg fat (Vijaya/Sangam 2025-26),
    concentrate ₹24-32/kg, cull buffaloes ~₹180-200/kg live, sheds
    ₹30-50k/animal place, 1 worker per ~20 head. Breeding is AI-first (sexed
    semen for the first two services), so no sire herd is carried.
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
            conception_rate=0.45,  # per AI, field conditions
            gestation_months=10,  # ~310 days
            lactation_months=10,  # 305-day lactation
            months_open_before_breeding=2,  # first AI at ~60 days post-calving
            litter_size=1.0,  # single calf
            sex_ratio_female=0.65,  # sexed semen for the first two services
            stillbirth_rate=0.03,
            age_at_first_breeding_months=22,  # heifers at 340 kg / 22-24 months
        ),
        mortality=MortalityAssumptions(
            kid_pre_weaning=0.10,  # organized Murrah farms ~8%, field higher
            kid_post_weaning=0.04,
            grower=0.03,
            adult=0.025,
        ),
        culling=CullingAssumptions(
            doe_cull_rate_annual=0.18,  # cull discipline: ~5-6 lactation herd life
            max_doe_age_months=132,
            buck_rotation_years=3,
            buck_doe_ratio=100,  # effectively unconstrained (AI)
        ),
        growth=GrowthAssumptions(
            birth_weight_kg=34.0,  # Murrah calf 30-40 kg
            adult_weight_doe_kg=520.0,  # mature Murrah female ~500-550 kg
            adult_weight_buck_kg=600.0,
            # Murrah heifer growth: ~34 kg birth, ~215 kg yearling, reaching
            # 340 kg breeding weight at ~22 months (NDRI growth studies).
            weight_by_age_months=[34.0 + 15.1 * m for m in range(13)],
            sale_age_months=14,  # retained male calves grown for meat
        ),
        sales=SalesAssumptions(
            meat_price_per_kg=190.0,  # buffalo live/cull market ₹180-200/kg
            cull_doe_price_per_kg=190.0,
            cull_buck_price_per_kg=200.0,
            monthly_meat_price_multipliers=[1.0] * 12,
            annual_livestock_price_growth_rate=0.05,
            festival_sale_months=[],
            milk_price_per_litre=55.0,  # fallback per-litre price
            # In-milk second-lactation purchases (12+ L/day at peak) give
            # ~2,200-2,800 L over a 305-day lactation (ICAR recorded herds
            # 2,605 ± 40 kg; field 1,500-2,000 kg). 2,400 with 0.93
            # persistency = ~11.9 L/day in month 1, ~7.9 L/day average.
            lactation_milk_litres=2400.0,
            # Vijaya/Sangam/Amul procurement ₹840-865/kg fat (2025-26); the
            # default blends Phase A cooperative supply with early Phase B
            # bulk sales to schools/restaurants at better-than-procurement
            # rates (direct consumer sale realises ₹80-110/L).
            milk_price_per_kg_fat=950.0,
            milk_fat_pct=6.8,
            milk_persistency_monthly=0.93,  # recorded Murrah persistency ~89-93%
            # Telangana yield seasonality: summer (Mar-Jun) heat-stress trough,
            # winter peak.
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
            annual_milk_price_growth_rate=0.05,
            male_calf_sell_at_birth_fraction=0.9,  # sexed-semen strategy
            male_calf_price_per_head=2500.0,  # week-old bull calf ₹2,000-3,000
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
            cultivated_fodder_acres=10.0,  # the Navipet site plan
            fodder_yield_t_dm_per_acre_year=5.0,  # ~25 t/acre fresh maize fodder
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
            subsidy_fraction=0.02,  # DEDS ceiling ₹8.25 L on a ₹2+ cr project
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
