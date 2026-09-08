"""Out-builders and visibility helpers shared by the API routers.

These lived as near-verbatim private copies in `tasks.py` / `dashboard.py` /
`breeding.py` and were imported across routers (`kidding.py`, `health.py`).
This module is the single source of truth; names are public-style because the
routers share them.
"""

from collections.abc import Set
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import (
    BREEDING_READY_BUCKETS,
    Animal,
    AnimalStatus,
    BreedingOutcome,
    BreedingRecord,
    BucketMove,
    Farm,
    FarmMembership,
    KiddingRecord,
    Sex,
    Task,
    TaskCategory,
    TaskStatus,
    User,
    WeightRecord,
)
from ..models.species import GOAT_PROFILE
from ..schemas.animals import AnimalOut
from ..schemas.breeding import BreedingRecordOut
from ..schemas.tasks import TaskOut
from ..utils import DEFAULT_BUSINESS_TIMEZONE, business_date

# Display enrichment needs these loaded up front — async forbids lazy loads.
TASK_LOADS = (
    selectinload(Task.assigned_role),
    selectinload(Task.assigned_user),
    selectinload(Task.animal),
)


@dataclass(frozen=True)
class AnimalComputedFacts:
    """Bounded SQL-derived facts for read-heavy animal views."""

    latest_weight_kg: float | None
    days_in_current_bucket: int
    is_currently_pregnant: bool


async def animal_computed_facts(
    db: AsyncSession,
    animals: list[Animal],
    reference_date: date,
    timezone_name: str,
) -> dict[int, AnimalComputedFacts]:
    """Load summary facts for any bounded animal page in three bounded queries.

    ``AnimalOut`` historically derived these values by hydrating every lifetime
    weight, bucket move, and breeding row for every animal in a response.  The
    DISTINCT ON queries below fetch only the latest relevant row per animal;
    pregnancy is represented by a set of matching doe ids.
    """
    if not animals:
        return {}
    animal_ids = [animal.id for animal in animals]

    weight_rows = await db.execute(
        select(WeightRecord.animal_id, WeightRecord.weight_kg)
        .where(
            WeightRecord.animal_id.in_(animal_ids),
            WeightRecord.date <= reference_date,
        )
        .distinct(WeightRecord.animal_id)
        .order_by(
            WeightRecord.animal_id,
            WeightRecord.date.desc(),
            WeightRecord.id.desc(),
        )
    )
    latest_weights = {animal_id: float(weight) for animal_id, weight in weight_rows.all()}

    move_rows = await db.execute(
        select(BucketMove.animal_id, BucketMove.effective_date)
        .where(BucketMove.animal_id.in_(animal_ids))
        .distinct(BucketMove.animal_id)
        .order_by(
            BucketMove.animal_id,
            BucketMove.moved_at.desc(),
            BucketMove.id.desc(),
        )
    )
    latest_moves: dict[int, date] = {
        animal_id: effective_date for animal_id, effective_date in move_rows.all()
    }

    pregnancy_rows = await db.execute(
        select(BreedingRecord.doe_id)
        .outerjoin(KiddingRecord, KiddingRecord.breeding_record_id == BreedingRecord.id)
        .where(
            BreedingRecord.doe_id.in_(animal_ids),
            BreedingRecord.outcome == BreedingOutcome.CONFIRMED_PREGNANT.value,
            KiddingRecord.id.is_(None),
        )
        .distinct()
    )
    pregnant_ids = set(pregnancy_rows.scalars())

    facts: dict[int, AnimalComputedFacts] = {}
    for animal in animals:
        latest_weight = latest_weights.get(animal.id)
        if latest_weight is None:
            dob = animal.date_of_birth or animal.estimated_dob
            latest_weight = (
                animal.birth_weight
                if animal.birth_weight is not None and (dob is None or dob <= reference_date)
                else None
            )
        bucket_reference: datetime | date | None = latest_moves.get(animal.id) or animal.created_at
        if isinstance(bucket_reference, datetime):
            bucket_start = business_date(bucket_reference, timezone_name)
        elif isinstance(bucket_reference, date):
            bucket_start = bucket_reference
        else:
            bucket_start = reference_date
        facts[animal.id] = AnimalComputedFacts(
            latest_weight_kg=latest_weight,
            days_in_current_bucket=max((reference_date - bucket_start).days, 0),
            is_currently_pregnant=animal.id in pregnant_ids,
        )
    return facts


def animal_out(
    animal: Animal,
    reference_date: date,
    timezone_name: str = DEFAULT_BUSINESS_TIMEZONE,
    *,
    permissions: Set[str],
    computed: AnimalComputedFacts | None = None,
) -> AnimalOut:
    """Serialize an animal using the caller's effective farm permissions.

    The model properties retain a sensible India-default for pure/domain use,
    but an API response has an explicit farm context and must not leak that
    default into farms configured in another timezone.  Permission input is
    mandatory and keyword-only: a new AnimalOut call site cannot silently
    bypass field-level authorization by relying on an owner/full-data default.
    """
    # ``from_attributes`` would read the ORM model's computed @properties
    # before we can overlay bounded SQL facts. Those descriptors traverse lazy
    # history relationships and can trigger MissingGreenlet in async requests.
    computed_field_names = {
        "age_months",
        "latest_weight_kg",
        "is_breeding_ready",
        "is_currently_pregnant",
        "days_in_current_bucket",
    }
    values: dict[str, object] = {
        field_name: getattr(animal, field_name)
        for field_name in AnimalOut.model_fields
        if field_name not in computed_field_names
    }
    age_months = animal.age_months_on(reference_date)
    profile = GOAT_PROFILE
    if computed is None:
        latest_weight_kg = animal.latest_weight_kg_on(reference_date)
        days_in_current_bucket = animal.days_in_current_bucket_on(reference_date, timezone_name)
        is_currently_pregnant = animal.is_currently_pregnant
        is_breeding_ready = animal.is_breeding_ready_on(reference_date)
    else:
        latest_weight_kg = computed.latest_weight_kg
        days_in_current_bucket = computed.days_in_current_bucket
        is_currently_pregnant = computed.is_currently_pregnant
        is_breeding_ready = bool(
            animal.sex == Sex.F.value
            and animal.status == AnimalStatus.ACTIVE.value
            and not animal.movement_restricted
            and not animal.suspected_scheduled_disease
            and animal.current_bucket in {bucket.value for bucket in BREEDING_READY_BUCKETS}
            and age_months is not None
            and age_months >= profile.min_breeding_age_months
            and computed.latest_weight_kg is not None
            and computed.latest_weight_kg >= profile.min_breeding_weight_kg
            and not computed.is_currently_pregnant
        )
    values.update(
        age_months=age_months,
        latest_weight_kg=latest_weight_kg,
        days_in_current_bucket=days_in_current_bucket,
        is_currently_pregnant=is_currently_pregnant,
        is_breeding_ready=is_breeding_ready,
    )
    out = AnimalOut.model_validate(values)

    if not ({"finance.view", "purchases.view"} & permissions):
        out.purchase_price = None
    if "finance.view" not in permissions:
        out.sale_price = None
    if "purchases.view" not in permissions:
        # Supplier identity and acquisition timing belong to procurement,
        # independently of permission to see aggregate financial values.
        out.purchase_date = None
        out.seller_name = None
    if "breeding.view" not in permissions:
        out.cull_candidate = False
        out.is_breeding_ready = False
        out.is_currently_pregnant = False
    if "health.view" not in permissions:
        # Expose the effective operational hold, not just its persisted
        # general-restriction half: move guards also block a scheduled-disease
        # suspicion.  The mover can act safely without learning why.
        out.movement_restricted = bool(
            animal.movement_restricted or animal.suspected_scheduled_disease
        )
        out.restriction_reason = None
        out.suspected_scheduled_disease = False
        out.suspected_disease = None
        out.authority_notified_at = None
        out.restriction_cleared_at = None
        out.restriction_cleared_by_id = None
        out.restriction_clearance_reference = None
        # The episode counter is clinical history too: a non-zero value
        # discloses that scheduled-disease holds were opened (and how many),
        # even long after clearance. Its only legitimate use is the
        # clear-restriction optimistic-concurrency token, and that whole flow
        # requires health permissions — so fail closed alongside the rest.
        out.restriction_version = 0
        out.mortality_cause = None
        out.mortality_reported_at = None
        # Free text has no enforceable domain classification and commonly
        # carries clinical or commercial narrative; fail closed.
        out.notes = None
    return out


def task_action_url(task: Task) -> str | None:
    """Frontend path of the form that closes this duty, when completing it
    means recording data (v1's task_action_url, paths unchanged)."""
    if task.category == TaskCategory.ULTRASOUND.value and task.breeding_record_id:
        return f"/breeding/{task.breeding_record_id}/ultrasound"
    if task.category == TaskCategory.KIDDING_DUE.value and task.breeding_record_id:
        return f"/kidding/new?breeding_id={task.breeding_record_id}"
    if task.category in (TaskCategory.VACCINE.value, TaskCategory.DEWORMING.value) and (
        task.animal_id or task.purchase_batch_id
    ):
        params = f"task_id={task.id}"
        if task.animal_id:
            params += f"&animal_id={task.animal_id}"
        if task.purchase_batch_id:
            params += f"&purchase_batch_id={task.purchase_batch_id}"
        return f"/health/new?{params}"
    return None


def assignee_label(user: User | None) -> str | None:
    """Non-identifying operational name for a task's assignee.

    ``User.display_name`` falls back to the login email for a worker created
    without a name, but task rows travel much further than the roster: the
    awaiting-verification queue is farm-wide for any ``tasks.verify`` holder,
    while the roster that legitimately carries emails is behind ``team.manage``.
    Duties therefore name the worker, never their email address.
    """
    if user is None:
        return None
    if user.deleted_at is not None:
        return user.display_name  # already the "Deleted account" tombstone label
    return user.name or f"Worker #{user.id}"


def task_out(task: Task) -> TaskOut:
    """Out model + display enrichment. Callers must have loaded the
    assigned_role / assigned_user / animal relationships (TASK_LOADS)."""
    out = TaskOut.model_validate(task)
    out.assigned_role_name = task.assigned_role.name if task.assigned_role else None
    out.assigned_user_name = assignee_label(task.assigned_user)
    out.animal_tag = task.animal.tag_number if task.animal else None
    out.needs_verification = task.needs_verification
    out.action_url = task_action_url(task)
    return out


def breeding_out(br: BreedingRecord) -> BreedingRecordOut:
    """Response model for a record whose doe/buck/kidding_record were
    eager-loaded (async sessions forbid implicit lazy loads)."""
    return BreedingRecordOut(
        id=br.id,
        doe_id=br.doe_id,
        buck_id=br.buck_id,
        semen_sire_name=br.semen_sire_name,
        breeding_date=br.breeding_date,
        method=br.method,
        heat_cycle_number=br.heat_cycle_number,
        ultrasound_date=br.ultrasound_date,
        ultrasound_result_date=br.ultrasound_result_date,
        ultrasound_done=br.ultrasound_done,
        pregnant=br.pregnant,
        kid_count_detected=br.kid_count_detected,
        expected_kidding_date=br.expected_kidding_date,
        outcome=br.outcome,
        loss_date=br.loss_date,
        loss_cause=br.loss_cause,
        loss_notes=br.loss_notes,
        loss_recorded_by_id=br.loss_recorded_by_id,
        loss_recorded_at=br.loss_recorded_at,
        has_kidding=br.kidding_record is not None,
        doe_tag=br.doe.tag_number,
        buck_tag=br.buck.tag_number if br.buck is not None else None,
    )


async def visible_to(
    db: AsyncSession,
    task: Task,
    user: User,
    farm: Farm,
    membership: FarmMembership | None,
    *,
    lock_assignee: bool = False,
) -> bool:
    """Return the object-level equivalent of ``services.tasks.task_scope``.

    A role stored beside a named worker is a continuity fallback, not a second
    live assignment. Same-role peers see it only after the assignee becomes
    inactive or their global account is tombstoned. ``lock_assignee`` pins the
    Membership then User rows for mutations, so reactivation/deactivation and
    account deletion serialize on one defensible authorization decision. This
    lock order matches worker lifecycle routes.
    """
    if farm.owner_id == user.id:
        return True
    if task.assigned_user_id == user.id:
        return True
    if membership is None or membership.role_id is None:
        return False
    if task.assigned_user_id is None:
        return task.assigned_role_id == membership.role_id
    if task.status != TaskStatus.PENDING.value:
        return False

    assignee_membership_statement = select(FarmMembership).where(
        FarmMembership.farm_id == task.farm_id,
        FarmMembership.user_id == task.assigned_user_id,
    )
    if lock_assignee:
        assignee_membership_statement = assignee_membership_statement.with_for_update(read=True)
    assignee_membership = (await db.execute(assignee_membership_statement)).scalar_one_or_none()
    assignee_user_statement = select(User.deleted_at).where(User.id == task.assigned_user_id)
    if lock_assignee:
        assignee_user_statement = assignee_user_statement.with_for_update(read=True)
    assignee_deleted_at = (await db.execute(assignee_user_statement)).scalar_one_or_none()
    if (
        assignee_deleted_at is None
        and assignee_membership is not None
        and assignee_membership.is_active
    ):
        return False
    fallback_role_id = task.assigned_role_id or (
        assignee_membership.role_id if assignee_membership is not None else None
    )
    return fallback_role_id == membership.role_id
