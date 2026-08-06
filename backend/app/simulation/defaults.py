"""Breed presets and production-system variants.

Presets are built by factory functions (preferred, since they return fresh
models) and also collected in ``BREED_PRESETS`` for lookup by name. Numbers are
approximate literature values for Indian conditions; non-obvious figures carry
a short source comment. Weight curves are scaled from the Osmanabadi stall-fed
curve in proportion to each breed's yearling weight (NBAGR breed descriptors).
"""

from typing import Literal

from .assumptions import (
    GrowthAssumptions,
    HerdAssumptions,
    MortalityAssumptions,
    ReproductionAssumptions,
    SalesAssumptions,
    SimulationAssumptions,
)

System = Literal["stall_fed", "semi_intensive"]


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
        variant.mortality.adult = 0.06  # field exposure: parasites, predators
        variant.mortality.kid_pre_weaning = 0.12
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
    """Osmanabadi (default): meat breed, no saleable milk, 50 does + 2 bucks."""
    return apply_system(SimulationAssumptions(), system)


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


PRESET_FACTORIES = {
    "osmanabadi": osmanabadi,
    "sirohi": sirohi,
    "barbari": barbari,
    "jamunapari": jamunapari,
    "beetal": beetal,
    "black_bengal": black_bengal,
    "boer_cross": boer_cross,
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
    "System",
    "apply_system",
    "get_preset",
]
