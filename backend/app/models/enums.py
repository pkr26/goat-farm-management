"""String enums (values stored verbatim in PostgreSQL text columns)."""

# Kept from the v1 port on purpose: str/Enum mix-ins (not StrEnum) and the
# original punctuation in docstrings/comments/protocol strings.
# ruff: noqa: UP042

import enum


def sql_in_values(vocabulary: type[enum.Enum] | tuple[str, ...]) -> str:
    """Render the quoted IN-list of a vocabulary for a CHECK constraint.

    Single-sourcing: the same enum members back the Python enum classes, the
    model CHECK constraints and (historically) the migration DDL literals.
    Deriving the SQL text here means a vocabulary change surfaces as one
    reviewable enum edit instead of silently diverging between the ORM and
    the database. The rendered bytes are pinned by
    tests/test_domain_check_constraints.py, so a member added without its
    migration fails loudly rather than drifting.
    """
    values = (
        tuple(member.value for member in vocabulary) if isinstance(vocabulary, type) else vocabulary
    )
    return ", ".join(f"'{value}'" for value in values)


class Bucket(str, enum.Enum):
    QUARANTINE = "QUARANTINE"
    FOUNDATION = "FOUNDATION"
    BREEDING = "BREEDING"
    PREGNANCY_EARLY = "PREGNANCY_EARLY"
    PREGNANCY_LATE = "PREGNANCY_LATE"
    DELIVERY = "DELIVERY"
    RECOVERY = "RECOVERY"
    RESTING = "RESTING"
    MALE_KIDS = "MALE_KIDS"
    FEMALE_KIDS = "FEMALE_KIDS"


class AnimalStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    SOLD = "SOLD"
    DEAD = "DEAD"
    CULLED = "CULLED"


class AnimalSource(str, enum.Enum):
    BORN = "BORN"
    PURCHASED = "PURCHASED"


class Sex(str, enum.Enum):
    M = "M"
    F = "F"


class BirthType(str, enum.Enum):
    SINGLE = "SINGLE"
    TWIN = "TWIN"
    TRIPLET = "TRIPLET"
    QUADRUPLET = "QUADRUPLET"
    MULTIPLET = "MULTIPLET"  # 5+ live kids (schema caps a kidding at 10)


class BreedingMethod(str, enum.Enum):
    NATURAL = "NATURAL"
    AI = "AI"  # artificial insemination, conventional semen
    AI_SEXED = "AI_SEXED"  # sexed semen (~90% female)


class BreedingOutcome(str, enum.Enum):
    PENDING = "PENDING"
    CONFIRMED_PREGNANT = "CONFIRMED_PREGNANT"
    FAILED = "FAILED"
    ABORTED = "ABORTED"
    # Terminal: the doe left the herd (sold/dead/culled) before her pregnancy
    # check, so this service can never be assessed. Distinct from FAILED — a
    # negative scan is a recorded fact, this is the permanent absence of one.
    UNASSESSED = "UNASSESSED"


class KiddingEase(str, enum.Enum):
    NORMAL = "NORMAL"
    ASSISTED = "ASSISTED"
    DIFFICULT = "DIFFICULT"
    CAESAREAN = "CAESAREAN"


class KidStatus(str, enum.Enum):
    ALIVE = "ALIVE"
    STILLBORN = "STILLBORN"
    DIED = "DIED"


class HealthEventType(str, enum.Enum):
    VACCINE = "VACCINE"
    DEWORMING = "DEWORMING"
    TREATMENT = "TREATMENT"
    FOOTBATH = "FOOTBATH"
    VITAMIN = "VITAMIN"
    EXAM = "EXAM"
    FECAL_EXAM = "FECAL_EXAM"


# Bounded mortality vocabulary for death recording and reporting. No column
# binds it yet (wave 0 vocabulary): the mortality write path lands with its
# own migration, so reports stay stable across farms and clients meanwhile.
class MortalityCause(str, enum.Enum):
    PNEUMONIA = "PNEUMONIA"
    DIARRHOEA = "DIARRHOEA"
    COLIBACILLOSIS = "COLIBACILLOSIS"
    ENTEROTOXAEMIA = "ENTEROTOXAEMIA"
    PPR_SUSPECTED = "PPR_SUSPECTED"
    FMD_SUSPECTED = "FMD_SUSPECTED"
    GOAT_POX_SUSPECTED = "GOAT_POX_SUSPECTED"
    PARASITISM = "PARASITISM"
    COCCIDIOSIS = "COCCIDIOSIS"
    NUTRITIONAL = "NUTRITIONAL"
    HEAT_STRESS = "HEAT_STRESS"
    PREDATION = "PREDATION"
    ACCIDENT = "ACCIDENT"
    DYSTOCIA = "DYSTOCIA"
    OLD_AGE = "OLD_AGE"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


# Bounded carcass-disposal vocabulary (husbandry-standards death audit). The
# column predates the enum as free text; the CHECK now pins it so mortality
# reports stay comparable across farms.
class DisposalMethod(str, enum.Enum):
    DEEP_BURIAL = "DEEP_BURIAL"  # deep pit with lime, away from water sources
    BURNING = "BURNING"  # incineration
    RENDERING = "RENDERING"  # licensed rendering plant
    COMPOSTING = "COMPOSTING"
    OTHER = "OTHER"


# Bounded administration-route vocabulary for health events (was free text
# like "SC"/"Oral"; canonical upper-case codes now).
class AdministrationRoute(str, enum.Enum):
    SC = "SC"  # subcutaneous
    IM = "IM"  # intramuscular
    IV = "IV"  # intravenous
    ORAL = "ORAL"  # drench/bolus
    TOPICAL = "TOPICAL"  # pour-on, spray, dip
    INTRANASAL = "INTRANASAL"


# Osmanabadi phenotype vocabulary (~73% of the breed is black; the rest are
# black-patched/brown/white/spotted). Stored lowercase: it is a physical
# description, not a status code.
class CoatColor(str, enum.Enum):
    BLACK = "black"
    BLACK_PATCHED = "black_patched"
    BROWN = "brown"
    WHITE = "white"
    SPOTTED = "spotted"


class IngredientCategory(str, enum.Enum):
    ROUGHAGE_WET = "ROUGHAGE_WET"
    ROUGHAGE_DRY = "ROUGHAGE_DRY"
    CONCENTRATE = "CONCENTRATE"


class FeedingShift(str, enum.Enum):
    MORNING = "MORNING"
    AFTERNOON = "AFTERNOON"
    NIGHT = "NIGHT"


class TransactionType(str, enum.Enum):
    INCOME = "INCOME"
    EXPENSE = "EXPENSE"


class TransactionCategory(str, enum.Enum):
    ANIMAL_SALE = "ANIMAL_SALE"
    ANIMAL_PURCHASE = "ANIMAL_PURCHASE"
    FEED = "FEED"
    MEDICINE = "MEDICINE"
    VET = "VET"
    LABOUR = "LABOUR"
    EQUIPMENT = "EQUIPMENT"
    MANURE = "MANURE"
    OTHER = "OTHER"


class TaskStatus(str, enum.Enum):
    PENDING = "PENDING"
    DONE = "DONE"
    SKIPPED = "SKIPPED"
    VERIFIED = "VERIFIED"  # DONE + confirmed by a verifying role (e.g. Cleaner Manager)


class TaskCategory(str, enum.Enum):
    VACCINE = "VACCINE"
    DEWORMING = "DEWORMING"
    ULTRASOUND = "ULTRASOUND"
    KIDDING_DUE = "KIDDING_DUE"
    WEANING = "WEANING"
    BUCKET_MOVE = "BUCKET_MOVE"
    QUARANTINE = "QUARANTINE"
    FEED = "FEED"
    CLEANING = "CLEANING"
    OTHER = "OTHER"
    # Husbandry-standards categories: workflow duties generated by their
    # domain services, not creatable through the manual-duty endpoint.
    KIDDING_WATCH = "KIDDING_WATCH"
    BIRTHING_KIT = "BIRTHING_KIT"
    HEALTH_CHECK = "HEALTH_CHECK"
    HEAT_WATCH = "HEAT_WATCH"
    HOOF_TRIMMING = "HOOF_TRIMMING"
    SPRAYING = "SPRAYING"
    DISINFECTION = "DISINFECTION"
    WEIGHING = "WEIGHING"
    REBREED = "REBREED"
    BUCK_ROTATION = "BUCK_ROTATION"
    INSURANCE = "INSURANCE"
    # Daily trough round (Deccan summer: lactating does need 10-15 L/day);
    # generated by the cadence sweep, never creatable manually.
    WATER = "WATER"
