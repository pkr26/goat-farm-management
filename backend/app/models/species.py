"""Goat biology profile: bounds, vocabulary, and lifecycle shape.

The product is goat-only: every service that needs species biology numbers
reads them from the single ``GOAT_PROFILE`` below. The lifecycle bucket graph
(models.lifecycle.LEGAL_BUCKET_TRANSITIONS) is shared across the app.

Values are per-breed commercial practice for the species this app models:
Osmanabadi goats (Navipet, Telangana).
"""

from __future__ import annotations

from dataclasses import dataclass

GOAT = "GOAT"


@dataclass(frozen=True)
class SpeciesProfile:
    default_breed: str
    # Vocabulary used in generated task titles and API error strings.
    parturition: str  # "kidding"
    young: str  # "kid"
    young_plural: str  # "kids"
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
    # Gestation day when PREGNANCY_EARLY becomes PREGNANCY_LATE (goat SPEC:
    # day 100). Mirrors the seeded bucket-definition exit rules, which are the
    # operator-facing promise.
    pregnancy_late_day: int
    # Days before the expected parturition when the dam moves to DELIVERY.
    # Goats enter the kidding pen ~2 weeks out.
    prepartum_move_lead_days: int
    # Goat kids stay with the doe (RECOVERY) until weaning.
    young_stay_with_dam: bool
    # Days after kidding before a new service may be recorded; goats are
    # governed by the postpartum recovery window itself.
    voluntary_waiting_days: int
    # Consecutive FAILED services before the cull-candidate flag fires.
    failed_services_before_cull: int
    # Upper bound on recorded litter size per parturition.
    max_litter_size: int
    # Sanity band for one newborn's recorded birth weight. A fabricated kid
    # weight must not permanently satisfy the breeding weight gates (birth
    # weight coalesces into "latest weight").
    birth_weight_kg_range: tuple[float, float]
    # Cap on any single recorded live weight on the adult scale.
    max_adult_weight_kg: float
    # Sanity band on one animal's total recorded yield per day (unused for
    # goats; kept for profile-shape stability).
    max_daily_milk_litres: float
    # Husbandry-standards scheduling knobs. Defaulted (unlike the v1 fields
    # above) so later profile additions stay additive; GOAT_PROFILE still
    # pins each value explicitly for the parity test.
    # Daily watch begins this many days before the expected kidding.
    kidding_watch_start_days: int = 5
    # Birthing-kit preparation duty due this many days before expected kidding.
    birthing_kit_lead_days: int = 7
    # Dam-care + stall-cleanout tasks due kidding + 1 day.
    postpartum_care_lead_days: int = 1
    # Day of lactation when creep feed starts for the kids.
    creep_start_days: int = 14
    # Minimum RESTING days before re-entry to BREEDING; equals the flush
    # switch day (dry-off before it, flush after).
    min_rest_flush_days: int = 10
    # Bucks past this age are rotated out of the mating squad.
    buck_rotation_age_months: int = 36


GOAT_PROFILE = SpeciesProfile(
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
    pregnancy_late_day=100,  # SPEC: "Pregnancy A (day 35-100)" exits at day 100
    prepartum_move_lead_days=15,  # kidding pen ~2 weeks before due
    young_stay_with_dam=True,
    voluntary_waiting_days=14,
    failed_services_before_cull=2,
    max_litter_size=4,
    birth_weight_kg_range=(0.5, 8.0),
    max_adult_weight_kg=150.0,
    max_daily_milk_litres=0.0,
    kidding_watch_start_days=5,
    birthing_kit_lead_days=7,
    postpartum_care_lead_days=1,
    creep_start_days=14,
    min_rest_flush_days=10,
    buck_rotation_age_months=36,
)
