"""Feed allocation rules (pure: no ORM imports).

Single source of truth for which recipe applies to which bucket on a given
day, the SPEC allocation reference tables, shift times and the per-bucket
per-head ration defaults. ``services.feeding`` consumes it for the live daily
plan; ``app.simulation.daily_ops`` consumes it so the simulated feed manifest
matches what the operational feeding plan would order, bucket for bucket.
"""

from __future__ import annotations

from datetime import date

from .enums import Bucket, FeedingShift

DRY_ROUGHAGE = "DRY_ROUGHAGE_ONLY"
# Per-kid daily creep allowance for unweaned kids with their dam in RECOVERY
# (goat farms): ~3% of an 8-12 kg kid's body weight as the creep concentrate.
CREEP_KG_PER_HEAD = 0.3
# The virtual quarantine recipe is direct-fed from a seeded raw-inventory row.
# Keeping the ingredient explicit prevents a successful dispensing log from
# creating feed ex nihilo merely because no finished-mix recipe exists.
DRY_ROUGHAGE_INGREDIENTS = {
    "GOAT": "Dry jowar stover",
    "BUFFALO_DAIRY": "Paddy straw",
}
DRY_ROUGHAGE_INGREDIENT = DRY_ROUGHAGE_INGREDIENTS["GOAT"]
RECIPE_DISPLAY = {
    "FATTENING_50_50": "Fattening 50:50",
    "LACTATING_60_40": "Lactating 60:40",
    "MAINTENANCE_75_25": "Maintenance 75:25",
    "FLUSH_70_30": "Flush 70:30",
    "CREEP": "Creep feed",
    "D_LACTATION_HIGH": "Lactating TMR — High yielders (10+ L/day)",
    "D_LACTATION_MED": "Lactating TMR — Medium yielders (6–10 L/day)",
    "D_DRY_CLOSEUP": "Dry & Close-up TMR (Building B)",
    "D_HEIFER_GROWING": "Growing Heifer TMR (Building C)",
    "D_CALF_STARTER": "Calf Starter (Building D)",
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
DAIRY_BUCKET_ALLOCATION_REFERENCE = [
    ("QUARANTINE", "Dry roughage only (days 1–3) → transition to D_LACTATION_MED"),
    ("FOUNDATION", "D_HEIFER_GROWING"),
    ("BREEDING", "D_LACTATION_MED (milking, open)"),
    ("PREGNANCY_EARLY", "D_LACTATION_MED (milking, pregnant 1–5 mo)"),
    ("PREGNANCY_LATE", "D_LACTATION_MED (milking, pregnant 5–8 mo)"),
    ("DELIVERY", "D_DRY_CLOSEUP (dry period; concentrate in last 3 weeks)"),
    ("RECOVERY", "D_LACTATION_HIGH (fresh, peak-yield push)"),
    ("RESTING", "D_LACTATION_MED (post-fresh transition)"),
    ("MALE_KIDS", "Day ≤90 D_CALF_STARTER, day 91+ D_HEIFER_GROWING"),
    ("FEMALE_KIDS", "Day ≤90 D_CALF_STARTER, day 91+ D_HEIFER_GROWING"),
]
SPECIES_BUCKET_ALLOCATION_REFERENCE = {
    "GOAT": BUCKET_ALLOCATION_REFERENCE,
    "BUFFALO_DAIRY": DAIRY_BUCKET_ALLOCATION_REFERENCE,
}


def bucket_allocation_reference(farm_type: str) -> list[tuple[str, str]]:
    return SPECIES_BUCKET_ALLOCATION_REFERENCE.get(farm_type, BUCKET_ALLOCATION_REFERENCE)


SHIFT_TIMES = {
    FeedingShift.MORNING.value: "6:30 AM (sweep bunks first)",
    FeedingShift.AFTERNOON.value: "1:30 PM",
    FeedingShift.NIGHT.value: "7:30 PM",
}


def recipe_age_days(effective_dob: date | None, ref: date) -> int:
    return (ref - effective_dob).days if effective_dob else 999  # unknown → grown


def dairy_recipe_for_context(
    bucket: str,
    effective_dob: date | None,
    ref: date,
    bucket_days: int,
) -> str:
    """Murrah dairy TMR allocation across the shared bucket codes."""
    if bucket == Bucket.QUARANTINE.value:
        return DRY_ROUGHAGE if bucket_days < 3 else "D_LACTATION_MED"
    if bucket in (Bucket.BREEDING.value, Bucket.PREGNANCY_EARLY.value, Bucket.RESTING.value):
        return "D_LACTATION_MED"
    if bucket == Bucket.PREGNANCY_LATE.value:
        return "D_LACTATION_MED"
    if bucket == Bucket.DELIVERY.value:
        return "D_DRY_CLOSEUP"
    if bucket == Bucket.RECOVERY.value:
        return "D_LACTATION_HIGH"
    if bucket in (Bucket.MALE_KIDS.value, Bucket.FEMALE_KIDS.value):
        age_days = recipe_age_days(effective_dob, ref)
        return "D_CALF_STARTER" if age_days <= 90 else "D_HEIFER_GROWING"
    if bucket == Bucket.FOUNDATION.value:
        return "D_HEIFER_GROWING"
    return "D_LACTATION_MED"


def recipe_for_context(
    bucket: str,
    effective_dob: date | None,
    ref: date,
    bucket_days: int,
    farm_type: str = "GOAT",
    *,
    is_dependent_kid: bool = False,
) -> str:
    """Recipe rules over only the four fields today's plan actually needs."""
    if farm_type != "GOAT":
        return dairy_recipe_for_context(bucket, effective_dob, ref, bucket_days)
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
# BucketDefinition rows (seed.BUCKET_DEFINITIONS / DAIRY_BUCKET_DEFINITIONS).
# A farm's BucketFeedSetting rows override these operationally; the daily
# simulation uses the species defaults so a run is reproducible everywhere.
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
DAIRY_BUCKET_KG_PER_HEAD: dict[str, float] = {
    Bucket.QUARANTINE.value: 25.0,
    Bucket.FOUNDATION.value: 20.0,
    Bucket.BREEDING.value: 28.0,
    Bucket.PREGNANCY_EARLY.value: 28.0,
    Bucket.PREGNANCY_LATE.value: 26.0,
    Bucket.DELIVERY.value: 24.0,
    Bucket.RECOVERY.value: 30.0,
    Bucket.RESTING.value: 28.0,
    Bucket.MALE_KIDS.value: 6.0,
    Bucket.FEMALE_KIDS.value: 6.0,
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
DAIRY_BUILDING_NAMES: dict[str, str] = {
    Bucket.QUARANTINE.value: "Quarantine Ward (Building E)",
    Bucket.FOUNDATION.value: "Growing Heifers (Building C)",
    Bucket.BREEDING.value: "Milking — Open / Awaiting AI (Building A)",
    Bucket.PREGNANCY_EARLY.value: "Milking — Pregnant 1–5 mo (Building A)",
    Bucket.PREGNANCY_LATE.value: "Milking — Pregnant 5–8 mo (Building A)",
    Bucket.DELIVERY.value: "Dry / Close-up + Calving Pens (Building B)",
    Bucket.RECOVERY.value: "Fresh Buffalo Pen (Building A sub-pen)",
    Bucket.RESTING.value: "Milking — Post-fresh Transition (Building A)",
    Bucket.MALE_KIDS.value: "Male Calves (Building D)",
    Bucket.FEMALE_KIDS.value: "Heifer Calves (Building D)",
}
