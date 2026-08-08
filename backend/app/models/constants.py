"""Breed constants (Osmanabadi, per SPEC) and enum-derived policy constants."""

from .enums import Bucket, FeedingShift, TaskCategory

GESTATION_DAYS = 150
KIDDING_WINDOW_DAYS = (145, 155)
# Sanity band for RECORDING a kidding (services.record_kidding): the SPEC
# window above drives planning (expected dates, due lists); for after-the-fact
# record-keeping any plausible gestation is accepted, but a "kidding" days or
# years post-breeding is a data-entry error, not an event.
MIN_GESTATION_DAYS = 100
MAX_GESTATION_DAYS = 200
ULTRASOUND_AFTER_BREEDING_DAYS = 32
MIN_BREEDING_AGE_MONTHS = 10
MIN_BREEDING_WEIGHT_KG = 22.0
WEANING_DAYS = 60
BUCK_ROTATION_DAYS = 7
BUCK_DOE_RATIO = 20
MEAT_SALE_AGE_MONTHS = (8, 9)
MEAT_SALE_WEIGHT_KG = (24.0, 28.0)
MAX_FAILED_CYCLES_BEFORE_CULL = 2

# Input sanity caps — single source of truth (AUDIT 4-M4): services enforce
# them in the domain layer, schemas mirror them as Field bounds, and the
# parity test (tests/test_schema_parity.py) keeps all three in lockstep.
MAX_BATCH_COUNT = 1000  # SPEC plans ~50 animals/batch; cap runaway row creation
MAX_AGE_MONTHS = 240  # 20 years — far beyond any goat's lifespan
MAX_RECUR_DAYS = 3650  # sanity cap: ~10 years; larger values overflow date arithmetic

# Task categories whose DONE state means "awaiting verification" by a role
# holding tasks.verify (e.g. cleaner marks done → cleaner manager verifies).
VERIFICATION_REQUIRED_CATEGORIES = (TaskCategory.CLEANING.value,)


# Buckets where a doe is eligible to become breeding-ready (per SPEC).
BREEDING_READY_BUCKETS = (Bucket.FOUNDATION, Bucket.FEMALE_KIDS, Bucket.RESTING)

# Feeding schedule split: 40% 6:30 AM / 20% 1:30 PM / 40% 7:30 PM.
SHIFT_SPLIT = {
    FeedingShift.MORNING: 0.40,
    FeedingShift.AFTERNOON: 0.20,
    FeedingShift.NIGHT: 0.40,
}
