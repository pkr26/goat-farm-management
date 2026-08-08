"""Full data model. All entities defined up-front per SPEC.

Split into per-domain submodules (audit 4-M1); this package re-exports every
name so existing ``from app.models import ...`` / ``from ..models import ...``
importers keep working unchanged.
"""

from ..db import Base
from .animals import Animal, BucketDefinition, BucketFeedSetting, BucketMove, WeightRecord
from .breeding import BreedingRecord, KiddingRecord, KidEntry
from .constants import (
    BREEDING_READY_BUCKETS,
    BUCK_DOE_RATIO,
    BUCK_ROTATION_DAYS,
    GESTATION_DAYS,
    KIDDING_WINDOW_DAYS,
    MAX_AGE_MONTHS,
    MAX_BATCH_COUNT,
    MAX_FAILED_CYCLES_BEFORE_CULL,
    MAX_GESTATION_DAYS,
    MAX_RECUR_DAYS,
    MEAT_SALE_AGE_MONTHS,
    MEAT_SALE_WEIGHT_KG,
    MIN_BREEDING_AGE_MONTHS,
    MIN_BREEDING_WEIGHT_KG,
    MIN_GESTATION_DAYS,
    SHIFT_SPLIT,
    ULTRASOUND_AFTER_BREEDING_DAYS,
    VERIFICATION_REQUIRED_CATEGORIES,
    WEANING_DAYS,
)
from .core import Farm, FarmMembership, RefreshSession, Role, User
from .enums import (
    AnimalSource,
    AnimalStatus,
    BirthType,
    BreedingMethod,
    BreedingOutcome,
    Bucket,
    FeedingShift,
    HealthEventType,
    IngredientCategory,
    KiddingEase,
    KidStatus,
    Sex,
    TaskCategory,
    TaskStatus,
    TransactionCategory,
    TransactionType,
)
from .feeding import FeedingRecord, FeedInventory, FeedRecipe, FeedRecipeLine
from .finance import Transaction
from .health import HealthEvent, VaccineTemplate
from .helpers import (
    QUARANTINE_PROTOCOL,
    QuarantineTaskSpec,
    conception_rate,
    expected_kidding_date,
    planned_ultrasound_date,
    quarantine_schedule,
)
from .purchases import PurchaseBatch
from .simulation import SimulationScenario
from .tasks import Task

__all__ = [
    "BREEDING_READY_BUCKETS",
    "BUCK_DOE_RATIO",
    "BUCK_ROTATION_DAYS",
    "GESTATION_DAYS",
    "KIDDING_WINDOW_DAYS",
    "MAX_AGE_MONTHS",
    "MAX_BATCH_COUNT",
    "MAX_FAILED_CYCLES_BEFORE_CULL",
    "MAX_GESTATION_DAYS",
    "MAX_RECUR_DAYS",
    "MEAT_SALE_AGE_MONTHS",
    "MEAT_SALE_WEIGHT_KG",
    "MIN_BREEDING_AGE_MONTHS",
    "MIN_BREEDING_WEIGHT_KG",
    "MIN_GESTATION_DAYS",
    "QUARANTINE_PROTOCOL",
    "SHIFT_SPLIT",
    "ULTRASOUND_AFTER_BREEDING_DAYS",
    "VERIFICATION_REQUIRED_CATEGORIES",
    "WEANING_DAYS",
    "Animal",
    "AnimalSource",
    "AnimalStatus",
    "Base",
    "BirthType",
    "BreedingMethod",
    "BreedingOutcome",
    "BreedingRecord",
    "Bucket",
    "BucketDefinition",
    "BucketFeedSetting",
    "BucketMove",
    "Farm",
    "FarmMembership",
    "FeedInventory",
    "FeedRecipe",
    "FeedRecipeLine",
    "FeedingRecord",
    "FeedingShift",
    "HealthEvent",
    "HealthEventType",
    "IngredientCategory",
    "KidEntry",
    "KidStatus",
    "KiddingEase",
    "KiddingRecord",
    "PurchaseBatch",
    "QuarantineTaskSpec",
    "RefreshSession",
    "Role",
    "Sex",
    "SimulationScenario",
    "Task",
    "TaskCategory",
    "TaskStatus",
    "Transaction",
    "TransactionCategory",
    "TransactionType",
    "User",
    "VaccineTemplate",
    "WeightRecord",
    "conception_rate",
    "expected_kidding_date",
    "planned_ultrasound_date",
    "quarantine_schedule",
]
