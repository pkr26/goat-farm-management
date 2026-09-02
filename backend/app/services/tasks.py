"""Tasks / duties."""

# MAX_RECUR_DAYS lives in models.py.

from collections.abc import Sequence
from datetime import date, timedelta
from uuid import uuid4

from sqlalchemy import Select, and_, func, literal, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ..core.config import get_settings
from ..models import (
    HISTORY_OVERRIDE_REASON_PREFIX,
    MAX_RECUR_DAYS,
    Animal,
    AnimalStatus,
    BreedingOutcome,
    BreedingRecord,
    Bucket,
    BucketMove,
    Farm,
    FarmMembership,
    KiddingRecord,
    KidEntry,
    PurchaseBatch,
    Role,
    Task,
    TaskCategory,
    TaskStatus,
    User,
    quarantine_schedule,
    species_profile,
)
from ..utils import today, utcnow
from ._common import _clear_task_rejection
from .animals import bucket_transition_error, move_animal

# Namespace for the per-farm manual-duty-queue mutex. Advisory lock keys are
# global to the database, so every acquisition of this counter must pass it.
MANUAL_TASK_QUEUE_LOCK_NAMESPACE = 4711


class ManualTaskCapacityError(ValueError):
    """The farm has no free slot in its bounded manual-duty queue."""


async def lock_manual_task_queue(db: AsyncSession, farm: Farm) -> None:
    """Take the transaction-scoped per-farm manual-duty queue mutex.

    This is deliberately an advisory lock rather than a lock on ``farms``.
    Task inserts take an FK KEY SHARE lock on that row, while animal-first
    domain writes may insert tasks after locking an Animal. A Farm row lock
    would therefore add the inverse Farm -> Animal edge and permit deadlocks.
    Keeping the primitive in the service layer also lets form-linked recurring
    duties use exactly the same mutex as the generic task routes.
    """
    await db.execute(
        select(
            func.pg_advisory_xact_lock(
                literal(MANUAL_TASK_QUEUE_LOCK_NAMESPACE),
                literal(farm.id),
            )
        )
    )


async def guard_manual_task_capacity_locked(db: AsyncSession, farm: Farm) -> None:
    """Require a free manual PENDING slot while ``lock_manual_task_queue`` is held."""
    limit = get_settings().max_pending_manual_tasks_per_farm
    bounded = (
        select(Task.id)
        .where(
            Task.farm_id == farm.id,
            Task.auto_generated.is_(False),
            Task.status == TaskStatus.PENDING.value,
        )
        .limit(limit)
        .subquery()
    )
    count = (await db.execute(select(func.count()).select_from(bounded))).scalar_one()
    if count >= limit:
        raise ManualTaskCapacityError(
            "This farm has reached its pending manual-duty limit; "
            "complete or skip existing duties first"
        )


def actionable_pending_task_predicate() -> ColumnElement[bool]:
    """A pending duty that can still be acted on through the task UI.

    Animal status workflows normally sweep their pending tasks. The bounded
    correlated EXISTS also hides legacy/out-of-process residue without joining
    or hydrating animals, while unlinked operational duties remain actionable.
    """
    active_animal = (
        select(Animal.id)
        .where(
            Animal.id == Task.animal_id,
            Animal.farm_id == Task.farm_id,
            Animal.status == AnimalStatus.ACTIVE.value,
        )
        .exists()
    )
    return and_(
        Task.status == TaskStatus.PENDING.value,
        or_(Task.animal_id.is_(None), active_animal),
    )


async def _guard_quarantine_release(
    db: AsyncSession,
    task: Task,
    locked_animals: list[Animal],
) -> list[Animal]:
    """Lock and validate a batch before its final quarantine move.

    A release is an auditable gate, not simply the passage of 45 calendar
    days: every preceding generated duty must have a completed record and no
    active animal may carry a recorded movement restriction/disease hold.
    """
    if not task.auto_generated or task.purchase_batch_id is None:
        raise ValueError("Movement side effects require an authoritative generated duty")
    batch = (
        await db.execute(
            select(PurchaseBatch).where(
                PurchaseBatch.id == task.purchase_batch_id,
                PurchaseBatch.farm_id == task.farm_id,
            )
        )
    ).scalar_one_or_none()
    if batch is None:
        raise ValueError("The quarantine release duty has no matching purchase batch")
    protocol_farm = await db.get(Farm, task.farm_id)
    release_spec = next(
        (
            item
            for item in quarantine_schedule(
                batch, protocol_farm.farm_type if protocol_farm is not None else "GOAT"
            )
            if item["category"] == TaskCategory.BUCKET_MOVE.value
        ),
        None,
    )
    if release_spec is None or task.due_date != release_spec["due_date"]:
        raise ValueError("The quarantine release duty does not match the recorded protocol date")

    prerequisites = list(
        (
            await db.execute(
                select(Task)
                .where(
                    Task.farm_id == task.farm_id,
                    Task.purchase_batch_id == task.purchase_batch_id,
                    Task.id != task.id,
                )
                .order_by(Task.id)
                .with_for_update()
            )
        ).scalars()
    )
    incomplete = [
        item
        for item in prerequisites
        if item.status not in (TaskStatus.DONE.value, TaskStatus.VERIFIED.value)
    ]
    if incomplete:
        raise ValueError("All recorded quarantine prerequisite tasks must be completed first")
    # The router locks every ACTIVE animal in the batch, including animals an
    # owner historically reclassified out of QUARANTINE. That wider lock set
    # closes the gap where such an animal could race back into QUARANTINE after
    # the target snapshot but before the release committed, leaving it stranded
    # behind an already-DONE release duty. Only the animals actually awaiting
    # release are validated/moved here; a prior historical classification is
    # otherwise left untouched.
    animals = [
        animal for animal in locked_animals if animal.current_bucket == Bucket.QUARANTINE.value
    ]
    if any(animal.movement_restricted or animal.suspected_scheduled_disease for animal in animals):
        raise ValueError(
            "Quarantine release is blocked by a recorded movement restriction or disease hold"
        )
    return animals


async def _litter_has_surviving_kid(
    db: AsyncSession, farm_id: int, kids: Sequence[KidEntry]
) -> bool:
    """Whether any kid of this litter is still in the herd.

    ``KidEntry.status`` is an immutable birth fact — selling or culling a kid
    never rewrites it — whereas ``replan_dam_after_last_kid_death`` decides the
    dam's postpartum duty from *live* herd status. Judging survivorship from
    the birth record here made the two disagree the moment a sibling left the
    herd alive: the producer skipped weaning and scheduled a postpartum duty
    that this guard could never accept, stranding the dam in RECOVERY with an
    uncompletable task. Both sides must therefore ask the same question.
    """
    # A stillborn carries no Animal row; a pre-auto-creation ALIVE row may also
    # have none, and its animal's fate is unknowable — fail closed there.
    if any(kid.animal_id is None and kid.status == "ALIVE" for kid in kids):
        return True
    animal_ids = [kid.animal_id for kid in kids if kid.animal_id is not None]
    if not animal_ids:
        return False
    survivor = (
        await db.execute(
            select(Animal.id)
            .where(
                Animal.farm_id == farm_id,
                Animal.id.in_(animal_ids),
                Animal.status == AnimalStatus.ACTIVE.value,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return survivor is not None


async def _guard_generated_movement_task(
    db: AsyncSession,
    task: Task,
    linked_animal: Animal,
) -> tuple[KiddingRecord | None, list[KidEntry]]:
    """Verify that an animal-movement duty came from its recorded workflow."""
    if not task.auto_generated or task.breeding_record_id is None:
        raise ValueError("Movement side effects require an authoritative generated duty")
    movement_farm = await db.get(Farm, task.farm_id)
    movement_profile = species_profile(
        movement_farm.farm_type if movement_farm is not None else "GOAT"
    )
    breeding = (
        await db.execute(
            select(BreedingRecord).where(
                BreedingRecord.id == task.breeding_record_id,
                BreedingRecord.farm_id == task.farm_id,
                BreedingRecord.doe_id == linked_animal.id,
            )
        )
    ).scalar_one_or_none()
    if breeding is None:
        raise ValueError("The movement duty has no matching breeding record")
    kidding = (
        await db.execute(
            select(KiddingRecord).where(
                KiddingRecord.farm_id == task.farm_id,
                KiddingRecord.breeding_record_id == breeding.id,
                KiddingRecord.doe_id == linked_animal.id,
            )
        )
    ).scalar_one_or_none()
    kids: list[KidEntry] = []
    if kidding is not None:
        kids = list(
            (
                await db.execute(
                    select(KidEntry)
                    .where(
                        KidEntry.farm_id == task.farm_id,
                        KidEntry.kidding_record_id == kidding.id,
                    )
                    .order_by(KidEntry.id)
                )
            ).scalars()
        )

    if linked_animal.current_bucket == Bucket.RECOVERY.value:
        # Goat: the dam's RECOVERY exit only exists once no kid still depends
        # on her. Dairy: calves are separated at birth, so the fresh-pen exit
        # duty is valid regardless of calf survival.
        if kidding is None:
            raise ValueError("The postpartum movement duty has no eligible kidding record")
        if movement_profile.young_stay_with_dam and await _litter_has_surviving_kid(
            db, task.farm_id, kids
        ):
            raise ValueError("The postpartum movement duty has no eligible kidding record")
        mortality_dates = [
            kid.mortality_reported_at for kid in kids if kid.mortality_reported_at is not None
        ]
        recovery_anchor = max([kidding.date, *mortality_dates])
        if task.due_date != recovery_anchor + timedelta(
            days=movement_profile.postpartum_recovery_days
        ):
            raise ValueError("The postpartum movement duty does not match the recovery date")
    elif (
        kidding is not None
        or breeding.outcome != BreedingOutcome.CONFIRMED_PREGNANT.value
        or breeding.expected_kidding_date is None
        or task.due_date
        != breeding.expected_kidding_date
        # Goat: pre-kidding pen move ~2 weeks out. Dairy: the dry-group /
        # calving-pen move rides the dry-off point ~60 days before calving.
        - timedelta(days=15 if movement_profile.young_stay_with_dam else 60)
    ):
        raise ValueError("The delivery movement duty does not match the recorded pregnancy")
    return kidding, kids


async def _guard_generated_weaning_task(db: AsyncSession, task: Task) -> set[int]:
    """Return this duty's litter after validating its species weaning-day
    provenance.

    ``animal_id`` identifies the dam, not the litter. New duties retain their
    breeding link; the due-date lookup remains for legacy rows created before
    that link was recorded. Returning the KidEntry animal ids prevents a stale
    duty for one kidding from weaning every RECOVERY child the doe has ever had.
    """
    if not task.auto_generated or task.animal_id is None:
        raise ValueError("Weaning side effects require an authoritative generated duty")
    weaning_farm = await db.get(Farm, task.farm_id)
    weaning_profile = species_profile(
        weaning_farm.farm_type if weaning_farm is not None else "GOAT"
    )
    kidding_filters = [
        KiddingRecord.farm_id == task.farm_id,
        KiddingRecord.doe_id == task.animal_id,
        KiddingRecord.date == task.due_date - timedelta(days=weaning_profile.weaning_days),
    ]
    if task.breeding_record_id is not None:
        kidding_filters.append(KiddingRecord.breeding_record_id == task.breeding_record_id)
    kidding_id = (
        await db.execute(
            select(KiddingRecord.id).where(*kidding_filters).order_by(KiddingRecord.id).limit(1)
        )
    ).scalar_one_or_none()
    if kidding_id is None:
        raise ValueError("The weaning duty does not match a recorded kidding milestone")
    linked_ids = (
        await db.execute(
            select(KidEntry.animal_id).where(
                KidEntry.farm_id == task.farm_id,
                KidEntry.kidding_record_id == kidding_id,
                KidEntry.animal_id.is_not(None),
            )
        )
    ).scalars()
    return {animal_id for animal_id in linked_ids if animal_id is not None}


async def complete_task(
    db: AsyncSession,
    task: Task,
    user: User | None = None,
    *,
    locked_animals: list[Animal] | None = None,
    reference_date: date | None = None,
) -> Task:
    """Mark done (attributed) and apply side effects:
    - Day-45 quarantine BUCKET_MOVE (batch-linked) → release batch animals to FOUNDATION
    - BUCKET_MOVE (animal-linked, pregnancy) → move doe to DELIVERY
    - BUCKET_MOVE (no-survivor kidding) → move recovered doe to RESTING
    - WEANING → kids to MALE_KIDS/FEMALE_KIDS by sex, dam to RESTING
    - recurring duty (recur_days) → spawn the next occurrence (but a
      verification-required category spawns on verify/skip, its terminal
      transitions, never here — see the guard below)

    For categories needing verification (CLEANING) DONE means "awaiting
    verification" — a tasks.verify holder turns it into VERIFIED.

    Idempotent: acting on a task that is not PENDING is a no-op, so a replayed
    completion (double submit, health form posting an old task_id) cannot
    re-apply side effects or spawn duplicate recurring occurrences.
    """
    if task.status != TaskStatus.PENDING.value:
        return task
    affected_animals = locked_animals or []
    animals_by_id = {animal.id: animal for animal in affected_animals}
    release_animals: list[Animal] | None = None
    movement_animal: Animal | None = None
    postpartum_doe: Animal | None = None
    weaning_kids: list[Animal] | None = None
    weaning_doe: Animal | None = None
    weaning_doe_can_rest = False
    movement_date = reference_date
    if task.category in (TaskCategory.BUCKET_MOVE.value, TaskCategory.WEANING.value):
        # Task completion is an event on the farm's business calendar. Never
        # let move_animal's legacy/default timezone decide whether an evening
        # completion belongs to today or tomorrow for this particular farm.
        if movement_date is None:
            movement_farm = await db.get(Farm, task.farm_id)
            if movement_farm is None:  # pragma: no cover - FK boundary
                raise ValueError("The duty's farm no longer exists")
            movement_date = today(movement_farm.timezone)
    if task.category == TaskCategory.BUCKET_MOVE.value and task.purchase_batch_id:
        if locked_animals is None:
            raise ValueError("Task completion animals were not pre-locked")
        release_animals = await _guard_quarantine_release(db, task, affected_animals)
    elif task.category == TaskCategory.BUCKET_MOVE.value:
        if locked_animals is None:
            raise ValueError("Task completion animals were not pre-locked")
        linked_animal = animals_by_id.get(task.animal_id) if task.animal_id is not None else None
        if linked_animal is None or linked_animal.farm_id != task.farm_id:
            raise ValueError("The animal linked to this movement duty is unavailable")
        movement_animal = linked_animal
        kidding, kids = await _guard_generated_movement_task(db, task, linked_animal)
        if linked_animal.current_bucket == Bucket.RECOVERY.value:
            recovery_farm = await db.get(Farm, task.farm_id)
            recovery_profile = species_profile(
                recovery_farm.farm_type if recovery_farm is not None else "GOAT"
            )
            # Goat: the dam's RECOVERY exit only exists once no kid still
            # depends on her. Dairy: calves are separated at birth, so the
            # fresh-pen exit is valid regardless of calf survival — mirror
            # the species gate _guard_generated_movement_task applies.
            if recovery_profile.young_stay_with_dam and (
                kidding is None or await _litter_has_surviving_kid(db, task.farm_id, kids)
            ):
                raise ValueError("Postpartum recovery duty is invalid while a kid survives")
            if error := bucket_transition_error(
                linked_animal,
                Bucket.RESTING.value,
                context="postpartum",
                reference_date=movement_date,
            ):
                raise ValueError(error)
            postpartum_doe = linked_animal
        elif linked_animal.current_bucket != Bucket.DELIVERY.value:
            if linked_animal.current_bucket not in (
                Bucket.PREGNANCY_LATE.value,
                Bucket.PREGNANCY_EARLY.value,
            ):
                raise ValueError("The linked animal is not in a pregnancy bucket")
            if error := bucket_transition_error(
                linked_animal,
                Bucket.DELIVERY.value,
                context="delivery",
                reference_date=movement_date,
            ):
                raise ValueError(error)
    elif task.category == TaskCategory.WEANING.value:
        if locked_animals is None:
            raise ValueError("Task completion animals were not pre-locked")
        litter_animal_ids = await _guard_generated_weaning_task(db, task)
        weaning_doe = animals_by_id.get(task.animal_id) if task.animal_id is not None else None
        if weaning_doe is None or weaning_doe.farm_id != task.farm_id:
            raise ValueError("The animal linked to this weaning duty is unavailable")
        weaning_farm = await db.get(Farm, task.farm_id)
        weaning_profile = species_profile(
            weaning_farm.farm_type if weaning_farm is not None else "GOAT"
        )
        if weaning_profile.young_stay_with_dam:
            weaning_kids = [
                animal
                for animal in affected_animals
                if animal.id in litter_animal_ids
                and animal.dam_id == weaning_doe.id
                and animal.status == AnimalStatus.ACTIVE.value
                and animal.current_bucket == Bucket.RECOVERY.value
            ]
        else:
            # Dairy: calves were separated into the calf shed at birth, so the
            # day-90 milk-weaning moves each surviving heifer FEMALE_KIDS →
            # FOUNDATION (the duty title's promise); males stay in MALE_KIDS
            # until their own sale path.
            weaning_kids = [
                animal
                for animal in affected_animals
                if animal.id in litter_animal_ids
                and animal.dam_id == weaning_doe.id
                and animal.status == AnimalStatus.ACTIVE.value
                and animal.current_bucket
                in (Bucket.FEMALE_KIDS.value, Bucket.MALE_KIDS.value)
            ]
        # A retained older duty may be completed after the doe has another live
        # litter (supported by historical correction). Wean only the linked
        # litter and leave the dam in RECOVERY until every other *dependent*
        # kid's own milestone is complete. A farm-born adult retains dam_id and
        # can later return to RECOVERY for her own kidding; a genuine earlier
        # RECOVERY exit proves she is no longer dependent on this doe. History
        # overrides are corrections, not proof of weaning, and are excluded by
        # the same criterion used by final-kid-death replanning.
        other_recovery_ids = [
            animal.id
            for animal in affected_animals
            if animal.dam_id == weaning_doe.id
            and animal.id not in litter_animal_ids
            and animal.status == AnimalStatus.ACTIVE.value
            and animal.current_bucket == Bucket.RECOVERY.value
        ]
        other_dependent_id: int | None = None
        if other_recovery_ids:
            other_dependent_id = (
                await db.execute(
                    select(Animal.id)
                    .where(
                        Animal.id.in_(other_recovery_ids),
                        ~select(BucketMove.id)
                        .where(
                            BucketMove.animal_id == Animal.id,
                            BucketMove.from_bucket == Bucket.RECOVERY.value,
                            func.coalesce(BucketMove.reason, "").not_like(
                                f"{HISTORY_OVERRIDE_REASON_PREFIX}%"
                            ),
                        )
                        .correlate(Animal)
                        .exists(),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
        weaning_doe_can_rest = other_dependent_id is None

        def _weaning_target(animal: Animal) -> str | None:
            """Post-weaning bucket for one young animal (None → no move)."""
            if weaning_profile.young_stay_with_dam:
                return Bucket.MALE_KIDS.value if animal.sex == "M" else Bucket.FEMALE_KIDS.value
            # Dairy: heifers graduate to FOUNDATION at milk-weaning; bull
            # calves have no graduation bucket and simply stay in MALE_KIDS.
            return Bucket.FOUNDATION.value if animal.sex == "F" else None

        moving_kids = [kid for kid in weaning_kids if _weaning_target(kid) is not None]
        candidates = [*moving_kids]
        # The dam is pre-flight-checked exactly when the completion can move
        # her (DELIVERY/RECOVERY → RESTING below). Goat dams wean out of
        # RECOVERY with their kids. A dairy dam already left the fresh pen at
        # +10 days and — following her own 60-day VWP — is typically re-bred
        # (BREEDING/PREGNANCY_*) by the day-90 milk-weaning; this duty then
        # graduates only her calves, so demanding a "weaning" RESTING
        # transition for her would 409 the standard protocol flow.
        if weaning_doe_can_rest and weaning_doe.current_bucket in (
            Bucket.DELIVERY.value,
            Bucket.RECOVERY.value,
        ):
            candidates.insert(0, weaning_doe)
        if any(
            bucket_transition_error(
                animal,
                Bucket.RESTING.value
                if animal.id == weaning_doe.id
                else (_weaning_target(animal) or animal.current_bucket),
                context="weaning",
                reference_date=movement_date,
            )
            for animal in candidates
        ):
            raise ValueError("Weaning is blocked by an animal lifecycle or movement-hold state")
    task.status = TaskStatus.DONE.value
    task.completed_by_id = user.id if user else None
    task.completed_at = utcnow()
    _clear_task_rejection(task)

    if task.category == TaskCategory.BUCKET_MOVE.value and task.purchase_batch_id:
        for animal in release_animals or []:
            move_animal(
                db,
                animal,
                Bucket.FOUNDATION.value,
                "45-day quarantine complete",
                created_by_id=user.id if user else None,
                context="quarantine_release",
                reference_date=movement_date,
            )

    elif (
        task.category == TaskCategory.BUCKET_MOVE.value
        and task.animal_id
        and task.auto_generated
        and task.breeding_record_id
    ):
        linked_animal = postpartum_doe or movement_animal
        if postpartum_doe is not None:
            move_animal(
                db,
                postpartum_doe,
                Bucket.RESTING.value,
                "Postpartum recovery complete; no surviving kids",
                created_by_id=user.id if user else None,
                context="postpartum",
                reference_date=movement_date,
            )
        # EARLY is accepted too: the EARLY→LATE transition is only a dashboard
        # suggestion, so a doe whose owner skipped it would otherwise see this
        # duty go green while she silently stays in PREGNANCY_EARLY.
        elif (
            linked_animal
            and linked_animal.farm_id == task.farm_id  # farm guard
            and linked_animal.current_bucket
            in (
                Bucket.PREGNANCY_LATE.value,
                Bucket.PREGNANCY_EARLY.value,
            )
        ):
            move_animal(
                db,
                linked_animal,
                Bucket.DELIVERY.value,
                "~2 weeks before due date",
                created_by_id=user.id if user else None,
                context="delivery",
                reference_date=movement_date,
            )

    elif task.category == TaskCategory.WEANING.value and task.animal_id:
        doe = weaning_doe
        if doe and doe.farm_id == task.farm_id:  # farm guard
            for kid in weaning_kids or []:
                if weaning_profile.young_stay_with_dam:
                    target = Bucket.MALE_KIDS.value if kid.sex == "M" else Bucket.FEMALE_KIDS.value
                else:
                    # Dairy: heifers graduate FEMALE_KIDS → FOUNDATION; bull
                    # calves stay in MALE_KIDS until their own sale path.
                    if kid.sex != "F":
                        continue
                    target = Bucket.FOUNDATION.value
                move_animal(
                    db,
                    kid,
                    target,
                    f"Weaned (day {weaning_profile.weaning_days})",
                    created_by_id=user.id if user else None,
                    context="weaning",
                    reference_date=movement_date,
                )
            if weaning_doe_can_rest and doe.current_bucket in (
                Bucket.DELIVERY.value,
                Bucket.RECOVERY.value,
            ):
                move_animal(
                    db,
                    doe,
                    Bucket.RESTING.value,
                    "Kids weaned",
                    created_by_id=user.id if user else None,
                    context="weaning",
                    reference_date=movement_date,
                )

    if task.recur_days and not task.needs_verification:
        # For verification-required categories DONE is NOT terminal: a reject
        # can send this occurrence back to PENDING, and a spawn here plus a
        # reject → re-action on a later business day anchors a SECOND
        # successor on a different due date — which the
        # (farm_id, recurring_series_id, due_date) dedup can never collapse —
        # permanently doubling the series' cadence. Those series spawn their
        # successor on the terminal transitions instead: verify_task and
        # skip_task.
        await spawn_next_occurrence(db, task)

    await db.flush()
    return task


async def resolve_personal_task_role_fallback(db: AsyncSession, task: Task) -> int | None:
    """Lazily repair one pre-D9 personal task from its retained membership."""
    if task.assigned_user_id is None or task.assigned_role_id is not None:
        return task.assigned_role_id
    role_id = (
        await db.execute(
            select(FarmMembership.role_id)
            .where(
                FarmMembership.farm_id == task.farm_id,
                FarmMembership.user_id == task.assigned_user_id,
            )
            .with_for_update(read=True)
        )
    ).scalar_one_or_none()
    if role_id is None:
        raise ValueError("Personal duty has no retained membership role")
    role = (
        await db.execute(
            select(Role).where(
                Role.id == role_id,
                Role.farm_id == task.farm_id,
            )
        )
    ).scalar_one_or_none()
    if role is None:  # pragma: no cover - protected by the membership's composite FK
        raise ValueError("Personal duty's retained membership role no longer exists")
    # Keep the already-loaded relationship coherent with the repaired FK. The
    # task routers serialize their response from this same ORM instance; setting
    # only assigned_role_id returned a contradictory payload (non-null id with
    # assigned_role_name=null) until the client performed a second GET.
    task.assigned_role_id = role.id
    task.assigned_role = role
    return role.id


async def find_live_recurring_successor(db: AsyncSession, task: Task) -> Task | None:
    """Return another PENDING occurrence in this task's recurrence series."""
    if task.recurring_series_id is None:
        return None
    return (
        await db.execute(
            select(Task)
            .where(
                Task.farm_id == task.farm_id,
                Task.recurring_series_id == task.recurring_series_id,
                Task.id != task.id,
                Task.status == TaskStatus.PENDING.value,
            )
            .order_by(Task.due_date, Task.id)
            .limit(1)
        )
    ).scalar_one_or_none()


async def spawn_next_occurrence(db: AsyncSession, task: Task) -> Task:
    """Create the next occurrence of a recurring duty: same assignment and
    category, due recur_days after max(the current due_date, the farm's
    business date), fresh PENDING state. Callers serialize recurring
    transitions with ``lock_manual_task_queue`` before taking Animal/Task row
    locks. Reuse any already-live successor for the series, then also dedupe on
    (farm_id, recurring_series_id, due_date) via uq_task_recurring_series_due,
    so two occurrences of one series acted on concurrently (both anchoring on
    the same date) don't pile up duplicates, and two serialized actions that
    straddle a farm-local midnight do not mint successors on two different
    dates. Two same-titled parallel series stay independent."""
    if task.recur_days is None or not 1 <= task.recur_days <= MAX_RECUR_DAYS:
        return task  # absurd recurrence (legacy data): don't explode date math
    if task.recurring_series_id is None:
        # Backfill legacy recurring rows lazily as well as in the migration so
        # imports/tests built directly from metadata remain safe.
        task.recurring_series_id = str(uuid4())
    # Sessions disable autoflush. Persist the caller's terminal transition
    # before looking for another PENDING occurrence in the series; otherwise a
    # caller closing multiple retained occurrences in one transaction can see
    # its own just-closed row through the database predicate and mistakenly
    # treat that stale version as the live successor.
    await db.flush()
    live_successor = await find_live_recurring_successor(db, task)
    if live_successor is not None:
        return live_successor
    # Anchor the next occurrence on max(due, today): otherwise a worker who
    # catches up 10 daily-cleaning tasks after leave spawns 10 already-overdue
    # rows in a cascade, one per completion.
    farm = await db.get(Farm, task.farm_id)
    business_today = today(farm.timezone) if farm is not None else today()
    recurrence_anchor = max(task.due_date, business_today)
    if recurrence_anchor > date.max - timedelta(days=task.recur_days):
        raise ValueError("Recurring duty cannot schedule a representable next date")
    due = recurrence_anchor + timedelta(days=task.recur_days)
    assigned_role_id = task.assigned_role_id
    if task.assigned_user_id is not None and assigned_role_id is None:
        # Compatibility for a pre-D9 personal recurrence encountered before a
        # background repair pass. The retained membership is the authoritative
        # role fallback even while inactive. SHARE keeps a concurrent role
        # reassignment from changing that answer between lookup and insert.
        assigned_role_id = await resolve_personal_task_role_fallback(db, task)
        if assigned_role_id is None:  # pragma: no cover - guarded above
            raise ValueError("Personal recurring duty has no retained membership role")
    # Two overdue/rejected occurrences in the same series can be completed
    # concurrently and both anchor on the same future date. A SELECT followed
    # by ORM INSERT races at the unique constraint and turns one valid request
    # into an IntegrityError/500. Let PostgreSQL arbitrate atomically, then
    # load either this transaction's insert or the already-existing winner.
    inserted_id = (
        await db.execute(
            pg_insert(Task)
            .values(
                farm_id=task.farm_id,
                title=task.title,
                due_date=due,
                status=TaskStatus.PENDING.value,
                category=task.category,
                animal_id=task.animal_id,
                purchase_batch_id=task.purchase_batch_id,
                breeding_record_id=task.breeding_record_id,
                auto_generated=task.auto_generated,
                assigned_role_id=assigned_role_id,
                assigned_user_id=task.assigned_user_id,
                recur_days=task.recur_days,
                recurring_series_id=task.recurring_series_id,
            )
            .on_conflict_do_nothing(constraint="uq_task_recurring_series_due")
            .returning(Task.id)
        )
    ).scalar_one_or_none()
    if inserted_id is not None:
        inserted = await db.get(Task, inserted_id)
        if inserted is not None:
            return inserted
    existing = (
        await db.execute(
            select(Task).where(
                Task.farm_id == task.farm_id,
                Task.due_date == due,
                Task.recurring_series_id == task.recurring_series_id,
            )
        )
    ).scalar_one()
    return existing


async def skip_task(db: AsyncSession, task: Task, user: User, reason: str | None = None) -> Task:
    """User skips a PENDING duty → SKIPPED, attributed; a recurring duty's
    series continues."""
    task.status = TaskStatus.SKIPPED.value
    task.skipped_by_id = user.id
    task.skipped_at = utcnow()
    task.skip_reason = (reason or "").strip() or None
    _clear_task_rejection(task)
    if task.recur_days:
        # A skipped occurrence must not kill the series.
        await spawn_next_occurrence(db, task)
    await db.flush()
    return task


async def verify_task(
    db: AsyncSession,
    task: Task,
    user: User,
    *,
    spawn_successor: bool | None = None,
) -> Task:
    """Verifier confirms a DONE duty → VERIFIED (final state).

    A recurring verification-required occurrence spawns its successor HERE,
    on its terminal transition, not on completion: DONE for those categories
    only means "awaiting verification", so a completion-time spawn plus a
    reject → re-action on a later business day minted a second successor with
    a different due date that the series dedup could not collapse (the series'
    cadence doubled forever). Callers that can spawn must take the canonical
    FARM -> ANIMAL -> TASK locks first — the successor insert takes FK KEY
    SHARE on the linked animal. ``spawn_successor`` lets the route withhold
    the spawn (e.g. the linked animal is no longer active, so the swept series
    must not regrow an unactionable PENDING row); ``None`` means "spawn
    whenever the duty recurs".
    """
    task.status = TaskStatus.VERIFIED.value
    task.verified_by_id = user.id
    task.verified_at = utcnow()
    _clear_task_rejection(task)
    if spawn_successor is None:
        spawn_successor = task.recur_days is not None
    if task.recur_days and spawn_successor:
        await spawn_next_occurrence(db, task)
    await db.flush()
    return task


async def reject_task(db: AsyncSession, task: Task, user: User, note: str) -> Task:
    """Verifier sends a DONE duty back to PENDING with a note for the worker,
    attributed. completed_by/at are kept as a record of the rejected attempt."""
    # PENDING is the exact state ck_tasks_user_assignment_has_role constrains,
    # so a pre-D9 personal duty is repaired here rather than trusting the caller
    # to have done it — spawn_next_occurrence guards its PENDING insert the same
    # way. Raises ValueError when no retained membership role remains.
    await resolve_personal_task_role_fallback(db, task)
    task.status = TaskStatus.PENDING.value
    task.verification_note = note or None
    task.rejected_by_id = user.id
    task.rejected_at = utcnow()
    await db.flush()
    return task


async def create_manual_task(
    db: AsyncSession,
    farm: Farm,
    title: str,
    due_date: date,
    category: str,
    animal_id: int | None = None,
    assigned_role_id: int | None = None,
    assigned_user_id: int | None = None,
    recur_days: int | None = None,
) -> Task:
    """Owner/manager-created duty (not auto-generated)."""
    task = Task(
        farm_id=farm.id,
        title=title,
        due_date=due_date,
        category=category,
        animal_id=animal_id,
        auto_generated=False,
        assigned_role_id=assigned_role_id,
        assigned_user_id=assigned_user_id,
        recur_days=recur_days,
        recurring_series_id=str(uuid4()) if recur_days else None,
    )
    db.add(task)
    await db.flush()
    return task


async def task_scope(db: AsyncSession, farm: Farm, user: User) -> Select[tuple[Task]]:
    """Base select of tasks visible to this user on this farm.

    Owner sees everything. A worker sees role-only duties for their role and
    duties assigned to them personally. A named assignment falls back to its
    recorded role only after that assignee is inactive or tombstoned;
    unassigned duties stay owner-visible only.

    Returns the Select (query builder), not rows: callers add ordering /
    pagination and execute it themselves
    (``(await db.execute(await task_scope(...))).scalars()``).
    """
    stmt = select(Task).where(Task.farm_id == farm.id)
    if farm.owner_id == user.id:
        return stmt
    result = await db.execute(
        select(FarmMembership).where(
            FarmMembership.farm_id == farm.id,
            FarmMembership.user_id == user.id,
            FarmMembership.is_active.is_(True),
        )
    )
    membership = result.scalars().first()
    conditions = [Task.assigned_user_id == user.id]
    if membership and membership.role_id:
        assignee_is_available = (
            select(FarmMembership.id)
            .join(User, User.id == FarmMembership.user_id)
            .where(
                FarmMembership.farm_id == Task.farm_id,
                FarmMembership.user_id == Task.assigned_user_id,
                FarmMembership.is_active.is_(True),
                User.deleted_at.is_(None),
            )
            .exists()
        )
        retained_assignee_has_viewer_role = (
            select(FarmMembership.id)
            .where(
                FarmMembership.farm_id == Task.farm_id,
                FarmMembership.user_id == Task.assigned_user_id,
                FarmMembership.role_id == membership.role_id,
            )
            .exists()
        )
        conditions.append(
            or_(
                # A genuine role duty has no named worker.
                and_(
                    Task.assigned_user_id.is_(None),
                    Task.assigned_role_id == membership.role_id,
                ),
                # Personal-duty fallback is an availability mechanism, not a
                # second simultaneous assignment. The durable assigned role
                # applies only while the named worker cannot serve it.
                and_(
                    Task.status == TaskStatus.PENDING.value,
                    Task.assigned_user_id.is_not(None),
                    ~assignee_is_available,
                    or_(
                        Task.assigned_role_id == membership.role_id,
                        and_(
                            Task.assigned_role_id.is_(None),
                            retained_assignee_has_viewer_role,
                        ),
                    ),
                ),
            )
        )
    return stmt.where(or_(*conditions))
