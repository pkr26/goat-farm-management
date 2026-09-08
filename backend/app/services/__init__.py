"""Domain flows shared by routers and tests.

Every function takes an AsyncSession and farm-scoped entities; callers commit.
All business data is farm-scoped by construction (farm_id copied from the
parent entities).

Split into per-domain submodules; this package re-exports every
name so existing ``from app.services import ...`` / ``from ..services import
...`` importers keep working unchanged.

Async adaptation notes (vs. the v1 sync services):
- Async sessions forbid implicit lazy loads. Where v1 dereferenced a
  relationship on a caller-passed entity (``br.doe``, ``br.kidding_record``,
  ``doe.breedings_as_doe``), the service now loads the same rows explicitly
  (``_load_doe`` / ``_kidding_record_of`` / a direct select). Where the
  service itself runs a query whose results' relationships are used
  (``Animal.weight_records``/``bucket_moves``/``breedings_as_doe``,
  ``FeedRecipe.lines``), the query carries ``selectinload`` options.
- ``task_scope`` returns a ``Select`` (query builder): callers add ordering /
  pagination and execute it themselves.
- ``move_animal`` and ``recipe_for_animal`` stay synchronous: they perform no
  I/O (session-state mutation / pure python over already-loaded attributes).
"""

# Model names re-exported for backward compatibility: the pre-split services
# module imported these from app.models, so they were reachable as
# ``services.<name>`` (tests/test_schema_parity.py asserts the MAX_* caps).
from ..models import (
    GESTATION_DAYS,
    MAX_AGE_MONTHS,
    MAX_BATCH_COUNT,
    MAX_FAILED_CYCLES_BEFORE_CULL,
    MAX_GESTATION_DAYS,
    MAX_RECUR_DAYS,
    MIN_GESTATION_DAYS,
    SHIFT_SPLIT,
    WEANING_DAYS,
    Animal,
    AnimalSource,
    AnimalStatus,
    BirthType,
    BreedingMethod,
    BreedingOutcome,
    BreedingRecord,
    Bucket,
    BucketDefinition,
    BucketFeedSetting,
    BucketMove,
    Farm,
    FarmMembership,
    FeedingRecord,
    FeedingShift,
    FeedInventory,
    FeedRecipe,
    HealthEvent,
    HealthEventType,
    KiddingRecord,
    KidEntry,
    KidStatus,
    PurchaseBatch,
    Role,
    Task,
    TaskCategory,
    TaskStatus,
    Transaction,
    TransactionCategory,
    TransactionType,
    User,
    VaccineTemplate,
    expected_kidding_date,
    planned_ultrasound_date,
    quarantine_schedule,
)

# Pure feed-allocation rule tables live in models.feed_rules (single source of
# truth); re-exported here so ``from app.services import ...`` keeps working.
from ..models.feed_rules import (
    BUCKET_ALLOCATION_REFERENCE,
    DRY_ROUGHAGE,
    RECIPE_DISPLAY,
    SHIFT_TIMES,
    bucket_allocation_reference,
)
from .animals import (
    TAG_ALPHABET,
    bucket_transition_error,
    generate_unique_tag,
    move_animal,
    require_bucket_transition,
    skip_inactive_animal_tasks_batch,
    skip_pending_tasks_for_animal,
)
from .breeding import (
    breeding_candidate_counts,
    breeding_candidate_page,
    breeding_weights_as_of,
    create_breeding_record,
    doe_has_open_breeding,
    is_breeding_candidate,
    is_buck_breeding_candidate,
    mark_aborted,
    record_ultrasound_result,
)
from .chronology import (
    require_animal_event_chronology,
    require_farm_not_future,
    require_purchase_before_recorded_facts,
    require_status_after_recorded_facts,
)
from .dashboard import ready_to_move_suggestions
from .feeding import (
    InsufficientFeedError,
    add_feed_stock,
    feeding_plan,
    get_daily_kg_per_head,
    mix_feed_batch,
    recipe_for_animal,
    record_dispensing,
    set_daily_kg_per_head,
)
from .finance import monthly_pnl
from .health import (
    canonical_target_for_task,
    inferred_schedule_template,
    place_movement_restriction,
    record_health_event,
    target_matches_task,
    target_matches_template,
    template_name_for_task,
    vaccination_schedule_for_animal,
    validated_template,
)
from .idempotency import IdempotencyKey, RequiredIdempotencyKey, execute_idempotent
from .kidding import KidSpec, LitterSizeError, record_kidding, replan_dam_after_last_kid_death
from .purchases import create_purchase_batch, schedule_quarantine_tasks
from .tasks import (
    ManualTaskCapacityError,
    actionable_pending_task_predicate,
    complete_task,
    create_manual_task,
    find_live_recurring_successor,
    guard_manual_task_capacity_locked,
    lock_manual_task_queue,
    reject_task,
    resolve_personal_task_role_fallback,
    skip_task,
    spawn_next_occurrence,
    task_scope,
    verify_task,
)

__all__ = [
    "BUCKET_ALLOCATION_REFERENCE",
    "DRY_ROUGHAGE",
    "GESTATION_DAYS",
    "MAX_AGE_MONTHS",
    "MAX_BATCH_COUNT",
    "MAX_FAILED_CYCLES_BEFORE_CULL",
    "MAX_GESTATION_DAYS",
    "MAX_RECUR_DAYS",
    "MIN_GESTATION_DAYS",
    "RECIPE_DISPLAY",
    "SHIFT_SPLIT",
    "SHIFT_TIMES",
    "TAG_ALPHABET",
    "WEANING_DAYS",
    "Animal",
    "AnimalSource",
    "AnimalStatus",
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
    "FeedingRecord",
    "FeedingShift",
    "HealthEvent",
    "HealthEventType",
    "IdempotencyKey",
    "InsufficientFeedError",
    "KidEntry",
    "KidSpec",
    "KidStatus",
    "KiddingRecord",
    "LitterSizeError",
    "ManualTaskCapacityError",
    "PurchaseBatch",
    "RequiredIdempotencyKey",
    "Role",
    "Task",
    "TaskCategory",
    "TaskStatus",
    "Transaction",
    "TransactionCategory",
    "TransactionType",
    "User",
    "VaccineTemplate",
    "actionable_pending_task_predicate",
    "add_feed_stock",
    "breeding_candidate_counts",
    "breeding_candidate_page",
    "breeding_weights_as_of",
    "bucket_allocation_reference",
    "bucket_transition_error",
    "canonical_target_for_task",
    "complete_task",
    "create_breeding_record",
    "create_manual_task",
    "create_purchase_batch",
    "doe_has_open_breeding",
    "execute_idempotent",
    "expected_kidding_date",
    "feeding_plan",
    "find_live_recurring_successor",
    "generate_unique_tag",
    "get_daily_kg_per_head",
    "guard_manual_task_capacity_locked",
    "inferred_schedule_template",
    "is_breeding_candidate",
    "is_buck_breeding_candidate",
    "lock_manual_task_queue",
    "mark_aborted",
    "mix_feed_batch",
    "monthly_pnl",
    "move_animal",
    "place_movement_restriction",
    "planned_ultrasound_date",
    "quarantine_schedule",
    "ready_to_move_suggestions",
    "recipe_for_animal",
    "record_dispensing",
    "record_health_event",
    "record_kidding",
    "record_ultrasound_result",
    "reject_task",
    "replan_dam_after_last_kid_death",
    "require_animal_event_chronology",
    "require_bucket_transition",
    "require_farm_not_future",
    "require_purchase_before_recorded_facts",
    "require_status_after_recorded_facts",
    "resolve_personal_task_role_fallback",
    "schedule_quarantine_tasks",
    "set_daily_kg_per_head",
    "skip_inactive_animal_tasks_batch",
    "skip_pending_tasks_for_animal",
    "skip_task",
    "spawn_next_occurrence",
    "target_matches_task",
    "target_matches_template",
    "task_scope",
    "template_name_for_task",
    "vaccination_schedule_for_animal",
    "validated_template",
    "verify_task",
]
