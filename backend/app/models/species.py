"""Per-species profiles: biology bounds, vocabulary, and lifecycle shape.

A farm's ``farm_type`` selects one of these profiles; every service that
currently hardcodes goat biology reads its numbers from here instead. The
lifecycle bucket graph (services.animals.LEGAL_BUCKET_TRANSITIONS) is shared:
each species only re-labels the same ten stage codes and adjusts timing.

Values are per-breed commercial practice for the species this app models:
Osmanabadi goats (Navipet, Telangana) and Murrah buffalo dairy (same region).
Buffalo figures follow ICAR/NDRI literature and the farm's own protocol notes:
~310-day gestation, pregnancy diagnosis ~60 days after service, heifers bred
at 24 months / >=340 kg (AFC ~35 months, inside the published well-managed
range), calves weaned off milk by ~day 90, fresh dams rejoin the milking
string ~10 days after calving, first AI at calving + 60 days (VWP), cull
review after 3 failed services.
"""

from __future__ import annotations

from dataclasses import dataclass

GOAT = "GOAT"
BUFFALO_DAIRY = "BUFFALO_DAIRY"
FARM_TYPES = (GOAT, BUFFALO_DAIRY)

FARM_TYPE_LABELS = {
    GOAT: "Goat farm (meat)",
    BUFFALO_DAIRY: "Buffalo dairy farm (milk)",
}


@dataclass(frozen=True)
class SpeciesProfile:
    farm_type: str
    default_breed: str
    # Vocabulary used in generated task titles and API error strings.
    parturition: str  # "kidding" / "calving"
    young: str  # "kid" / "calf"
    young_plural: str  # "kids" / "calves"
    # Biology.
    gestation_days: int
    parturition_window_days: tuple[int, int]  # planning window around the mean
    min_gestation_days: int  # recording sanity band (after-the-fact entries)
    max_gestation_days: int
    pregnancy_check_after_service_days: int  # planned scan/PD task
    min_breeding_age_months: int
    min_breeding_weight_kg: float
    min_sire_breeding_age_months: int
    min_sire_breeding_weight_kg: float
    weaning_days: int
    postpartum_recovery_days: int
    # Goat kids stay with the doe (RECOVERY) until weaning; dairy calves are
    # separated within 24h and raised in the calf shed, so the dam's fresh-pen
    # exit never depends on calf survival.
    young_stay_with_dam: bool
    # Days after calving before a new service may be recorded (voluntary
    # waiting period). Buffaloes need ~60 days for uterine involution; goats
    # are governed by the postpartum recovery window itself.
    voluntary_waiting_days: int
    # Consecutive FAILED services before the cull-candidate flag fires
    # (goat SPEC: 2; dairy protocol: 3 services before cull review).
    failed_services_before_cull: int
    # Upper bound on recorded litter size per parturition.
    max_litter_size: int
    # Sanity band on one animal's total recorded yield per day (dairy only).
    max_daily_milk_litres: float


GOAT_PROFILE = SpeciesProfile(
    farm_type=GOAT,
    default_breed="Osmanabadi",
    parturition="kidding",
    young="kid",
    young_plural="kids",
    gestation_days=150,
    parturition_window_days=(145, 155),
    min_gestation_days=100,
    max_gestation_days=200,
    pregnancy_check_after_service_days=32,
    min_breeding_age_months=10,
    min_breeding_weight_kg=22.0,
    min_sire_breeding_age_months=12,
    min_sire_breeding_weight_kg=25.0,
    weaning_days=60,
    postpartum_recovery_days=14,
    young_stay_with_dam=True,
    voluntary_waiting_days=14,
    failed_services_before_cull=2,
    max_litter_size=4,
    max_daily_milk_litres=0.0,
)

BUFFALO_DAIRY_PROFILE = SpeciesProfile(
    farm_type=BUFFALO_DAIRY,
    default_breed="Murrah",
    parturition="calving",
    young="calf",
    young_plural="calves",
    gestation_days=310,
    parturition_window_days=(300, 325),
    min_gestation_days=270,
    max_gestation_days=350,
    pregnancy_check_after_service_days=60,
    # 24 months matches the simulation preset and keeps AFC ≈ 35 months,
    # inside the published well-managed Murrah range (36-40 mo).
    min_breeding_age_months=24,
    min_breeding_weight_kg=340.0,
    min_sire_breeding_age_months=24,
    min_sire_breeding_weight_kg=350.0,
    weaning_days=90,
    postpartum_recovery_days=10,
    young_stay_with_dam=False,
    voluntary_waiting_days=60,
    failed_services_before_cull=3,
    max_litter_size=2,
    max_daily_milk_litres=40.0,
)

SPECIES_PROFILES: dict[str, SpeciesProfile] = {
    GOAT_PROFILE.farm_type: GOAT_PROFILE,
    BUFFALO_DAIRY_PROFILE.farm_type: BUFFALO_DAIRY_PROFILE,
}


def species_profile(farm_type: str | None) -> SpeciesProfile:
    """Profile for a farm type. Unknown/legacy values fall back to GOAT —
    every farm created before the column existed is a goat farm."""
    return SPECIES_PROFILES.get(farm_type or GOAT, GOAT_PROFILE)
