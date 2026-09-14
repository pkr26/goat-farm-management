"""Feed allocation rules (pure: no ORM imports).

Single source of truth for which recipe applies to which bucket on a given
day, the SPEC allocation reference tables, shift times, the per-bucket
per-head ration defaults, the creep-ration age ramp and the weight-scaling
class percentages. ``services.feeding`` consumes it for the live daily
plan; ``app.simulation.daily_ops`` consumes it so the simulated feed manifest
matches what the operational feeding plan would order, bucket for bucket.
"""

from __future__ import annotations

from datetime import date

from .enums import Bucket, FeedingShift
from .species import GOAT_PROFILE

DRY_ROUGHAGE = "DRY_ROUGHAGE_ONLY"
# Terminal-band per-kid daily creep allowance: the top step of the ramp below,
# reached at day 46 and held to weaning. ~3% of an 8-12 kg kid's body weight
# as the creep concentrate.
CREEP_KG_PER_HEAD = 0.3
# Creep ration ramp for unweaned kids with their dam in RECOVERY (goat farms).
# Creep concentrate starts at the species' creep_start_days and steps up to the
# terminal allowance; past weaning the kid eats from the grown pens, not the
# creep trough, so the last band is weaning-bounded. Rows are
# (start_day, end_day, kg_per_head); the plan's SQL CASE and this module's
# pure helpers are generated from the same table so the twins cannot drift.
CREEP_BANDS: tuple[tuple[int, int, float], ...] = (
    (GOAT_PROFILE.creep_start_days, 30, 0.1),
    (31, 45, 0.2),
    (46, GOAT_PROFILE.weaning_days, CREEP_KG_PER_HEAD),
)
# Bucks in the BREEDING bucket carry a mating-season condition supplement on
# top of whatever per-head amount the bucket resolves to (flat or scaled).
BUCK_BREEDING_SUPPLEMENT_KG = 0.5
# The virtual quarantine recipe is direct-fed from a seeded raw-inventory row.
# Keeping the ingredient explicit prevents a successful dispensing log from
# creating feed ex nihilo merely because no finished-mix recipe exists.
DRY_ROUGHAGE_INGREDIENT = "Dry jowar stover"
RECIPE_DISPLAY = {
    "FATTENING_50_50": "Fattening 50:50",
    "LACTATING_60_40": "Lactating 60:40",
    "MAINTENANCE_75_25": "Maintenance 75:25",
    "FLUSH_70_30": "Flush 70:30",
    "CREEP": "Creep feed",
    DRY_ROUGHAGE: "Dry roughage only (days 1–3, zero grain)",
}

# SPEC "Feed allocation per bucket" — reference table for the recipes page.
BUCKET_ALLOCATION_REFERENCE = [
    ("QUARANTINE", "Dry roughage only (days 1–3) → transition to MAINTENANCE_75_25"),
    ("FOUNDATION", "LACTATING_60_40"),
    ("BREEDING", "MAINTENANCE_75_25 (incl. dry bucks)"),
    ("PREGNANCY_EARLY", "MAINTENANCE_75_25"),
    ("PREGNANCY_LATE", "LACTATING_60_40"),
    ("DELIVERY", "LACTATING_60_40"),
    ("RECOVERY", "LACTATING_60_40 (lactating)"),
    ("RESTING", "Days 1–10 MAINTENANCE_75_25, days 10–30 FLUSH_70_30"),
    ("MALE_KIDS", "Day ≤90 LACTATING_60_40 (frame-builder), day 91+ FATTENING_50_50"),
    ("FEMALE_KIDS", "LACTATING_60_40"),
]


def bucket_allocation_reference() -> list[tuple[str, str]]:
    return BUCKET_ALLOCATION_REFERENCE


SHIFT_TIMES = {
    FeedingShift.MORNING.value: "6:30 AM (sweep bunks first)",
    FeedingShift.AFTERNOON.value: "1:30 PM",
    FeedingShift.NIGHT.value: "7:30 PM",
}


def recipe_age_days(effective_dob: date | None, ref: date) -> int:
    return (ref - effective_dob).days if effective_dob else 999  # unknown → grown


def creep_band_for(age_days: int) -> tuple[str, float] | None:
    """(label, kg_per_head) of the creep band covering ``age_days``.

    ``None`` outside the creep window: below creep_start_days the kid is
    milk-fed (no creep line at all), and past weaning it eats from the grown
    pens. The SQL twin inside ``services.feeding.feeding_plan`` derives its
    CASE bands from the same ``CREEP_BANDS`` table.
    """
    for start_day, end_day, kg_per_head in CREEP_BANDS:
        if start_day <= age_days <= end_day:
            return f"{start_day}\u2013{end_day} d", kg_per_head
    return None


def creep_daily_kg(age_days: int) -> float:
    """Per-kid daily creep allowance at ``age_days`` (kg, as-fed)."""
    band = creep_band_for(age_days)
    return band[1] if band is not None else 0.0


def creep_band_label(age_days: int) -> str | None:
    """Display label of the creep band covering ``age_days`` ("14–30 d")."""
    band = creep_band_for(age_days)
    return band[0] if band is not None else None


def recipe_for_context(
    bucket: str,
    effective_dob: date | None,
    ref: date,
    bucket_days: int,
    *,
    is_dependent_kid: bool = False,
) -> str:
    """Recipe rules over only the four fields today's plan actually needs."""
    if bucket == Bucket.QUARANTINE.value:
        return DRY_ROUGHAGE if bucket_days < 3 else "MAINTENANCE_75_25"
    if bucket == Bucket.RECOVERY.value and is_dependent_kid:
        # An unweaned kid with its dam gets the creep line, never the doe's
        # full lactating TMR (~4-5x a kid's intake).
        return "CREEP"
    if bucket in (
        Bucket.FOUNDATION.value,
        Bucket.FEMALE_KIDS.value,
        Bucket.PREGNANCY_LATE.value,
        Bucket.RECOVERY.value,
        Bucket.DELIVERY.value,
    ):
        return "LACTATING_60_40"
    if bucket in (Bucket.BREEDING.value, Bucket.PREGNANCY_EARLY.value):
        return "MAINTENANCE_75_25"
    if bucket == Bucket.RESTING.value:
        return "FLUSH_70_30" if bucket_days >= 10 else "MAINTENANCE_75_25"
    if bucket == Bucket.MALE_KIDS.value:
        age_days = recipe_age_days(effective_dob, ref)
        return "LACTATING_60_40" if age_days <= 90 else "FATTENING_50_50"
    return "MAINTENANCE_75_25"


# Default per-head daily rations (kg) per bucket, mirrored from the seeded
# BucketDefinition rows (seed.BUCKET_DEFINITIONS). A farm's BucketFeedSetting
# rows override these operationally; the daily simulation uses the defaults so
# a run is reproducible everywhere.
GOAT_BUCKET_KG_PER_HEAD: dict[str, float] = {
    Bucket.QUARANTINE.value: 1.1,
    Bucket.FOUNDATION.value: 1.2,
    Bucket.BREEDING.value: 1.2,
    Bucket.PREGNANCY_EARLY.value: 1.2,
    Bucket.PREGNANCY_LATE.value: 1.4,
    Bucket.DELIVERY.value: 1.5,
    Bucket.RECOVERY.value: 1.5,
    Bucket.RESTING.value: 1.2,
    Bucket.MALE_KIDS.value: 1.0,
    Bucket.FEMALE_KIDS.value: 1.0,
}

# As-fed daily ration as a percentage of live body weight per bucket
# (husbandry-standards reference). The daily plan scales the bucket's mean
# latest weight by this factor, clamped to [0.5×, 2.0×] the flat per-head
# default above (or the farm's BucketFeedSetting override, which defines the
# flat default operationally); buckets with no weighing at all keep the flat
# default. Creep is excluded: a kid's allowance is the age-band ramp, and the
# breeding-buck supplement is additive, not a percentage.
BUCKET_CLASS_PCT: dict[str, float] = {
    Bucket.QUARANTINE.value: 3.0,
    Bucket.FOUNDATION.value: 3.25,
    Bucket.BREEDING.value: 3.0,
    Bucket.PREGNANCY_EARLY.value: 3.0,
    Bucket.PREGNANCY_LATE.value: 3.5,
    Bucket.DELIVERY.value: 4.0,
    Bucket.RECOVERY.value: 4.0,
    Bucket.RESTING.value: 3.0,
    Bucket.MALE_KIDS.value: 3.25,
    Bucket.FEMALE_KIDS.value: 3.25,
}

# One building per bucket (the daily simulation's physical layout): the
# display names mirror the seeded BucketDefinition labels.
GOAT_BUILDING_NAMES: dict[str, str] = {
    Bucket.QUARANTINE.value: "Quarantine Ward",
    Bucket.FOUNDATION.value: "Foundation / Grow-out",
    Bucket.BREEDING.value: "Breeding Bucket",
    Bucket.PREGNANCY_EARLY.value: "Pregnancy A",
    Bucket.PREGNANCY_LATE.value: "Pregnancy B",
    Bucket.DELIVERY.value: "Delivery Ward",
    Bucket.RECOVERY.value: "Recovery Ward",
    Bucket.RESTING.value: "Resting / Dry-off + Flush",
    Bucket.MALE_KIDS.value: "Male Kids Growing",
    Bucket.FEMALE_KIDS.value: "Female Kids Growing",
}
