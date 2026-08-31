"""String enums (values stored verbatim in PostgreSQL text columns)."""

# Kept from the v1 port on purpose: str/Enum mix-ins (not StrEnum) and the
# original punctuation in docstrings/comments/protocol strings.
# ruff: noqa: UP042

import enum


class FarmType(str, enum.Enum):
    GOAT = "GOAT"
    BUFFALO_DAIRY = "BUFFALO_DAIRY"


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
    MILK = "MILK"
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
