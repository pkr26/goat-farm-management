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
# Sires need their own explicit floor.  The operational model previously
# treated every active male outside quarantine as a buck, including newborn
# male kids.  Twelve months matches the adult-buck boundary already used by
# the live-herd simulation snapshot; 25 kg is a deliberately conservative
# minimum below the 30 kg Osmanabadi adult-buck default.
MIN_BUCK_BREEDING_AGE_MONTHS = 12
MIN_BUCK_BREEDING_WEIGHT_KG = 25.0
WEANING_DAYS = 60
# Every BucketMove written under ``history_override`` carries this prefix. A
# history correction can round-trip an animal RECOVERY -> anything -> RECOVERY
# without it ever weaning, so "did this kid genuinely leave its birth cohort?"
# is answered by "it has a RECOVERY departure that is NOT an override" — never
# by matching one particular reason string, which silently excluded the
# orphan/early-wean exits that write their own wording.
HISTORY_OVERRIDE_REASON_PREFIX = "[HISTORY OVERRIDE] "
# A kidding with no surviving kids has no weaning event to move the doe out
# of RECOVERY. Keep that maternal recovery period explicit and deterministic.
POSTPARTUM_RECOVERY_DAYS = 14
BUCK_ROTATION_DAYS = 7
BUCK_DOE_RATIO = 20
MEAT_SALE_AGE_MONTHS = (8, 9)
MEAT_SALE_WEIGHT_KG = (24.0, 28.0)
MAX_FAILED_CYCLES_BEFORE_CULL = 2

# Input sanity caps — single source of truth: services enforce
# them in the domain layer, schemas mirror them as Field bounds, and the
# parity test (tests/test_schema_parity.py) keeps all three in lockstep.
MAX_BATCH_COUNT = 1000  # SPEC plans ~50 animals/batch; cap runaway row creation
MAX_AGE_MONTHS = 240  # 20 years — far beyond any goat's lifespan
MAX_RECUR_DAYS = 3650  # sanity cap: ~10 years; larger values overflow date arithmetic
# Ceiling on a medicine withdrawal window. The longest real veterinary
# withdrawal is measured in weeks; two years is generous. The bound exists
# because health events are immutable and an active withdrawal blocks sale
# and cull, so a mistyped year would permanently strand the animal.
MAX_WITHDRAWAL_DAYS = 730
MAX_ANIMAL_TAG_LENGTH = 50
MAX_TASK_TITLE_LENGTH = 200

# Machine-readable pregnancy-loss causes. Notes carry optional local detail;
# the bounded catalog keeps reporting stable across farms and clients.
PREGNANCY_LOSS_CAUSES = (
    "UNKNOWN",
    "DISEASE",
    "INJURY",
    "NUTRITIONAL",
    "TRAUMA",
    "ANIMAL_STATUS_CHANGE",
    "OTHER",
)

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
