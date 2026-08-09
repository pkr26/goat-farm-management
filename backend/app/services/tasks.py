"""Tasks / duties."""

# MAX_RECUR_DAYS lives in models.py.

from datetime import date, timedelta
from uuid import uuid4

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ..models import (
    MAX_RECUR_DAYS,
    POSTPARTUM_RECOVERY_DAYS,
    WEANING_DAYS,
    Animal,
    AnimalStatus,
    BreedingOutcome,
    BreedingRecord,
    Bucket,
    Farm,
    FarmMembership,
    KiddingRecord,
    KidEntry,
    PurchaseBatch,
    Task,
    TaskCategory,
    TaskStatus,
    User,
    quarantine_schedule,
)
from ..utils import today, utcnow
from .animals import bucket_transition_error, move_animal


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
    release_spec = next(
        (
            item
            for item in quarantine_schedule(batch)
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
    animals = locked_animals
    if any(animal.movement_restricted or animal.suspected_scheduled_disease for animal in animals):
        raise ValueError(
            "Quarantine release is blocked by a recorded movement restriction or disease hold"
        )
    return animals


async def _guard_generated_movement_task(
    db: AsyncSession,
    task: Task,
    linked_animal: Animal,
) -> tuple[KiddingRecord | None, list[KidEntry]]:
    """Verify that an animal-movement duty came from its recorded workflow."""
    if not task.auto_generated or task.breeding_record_id is None:
        raise ValueError("Movement side effects require an authoritative generated duty")
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
        if kidding is None or any(kid.status == "ALIVE" for kid in kids):
            raise ValueError("The postpartum movement duty has no eligible kidding record")
        mortality_dates = [
            kid.mortality_reported_at for kid in kids if kid.mortality_reported_at is not None
        ]
        recovery_anchor = max([kidding.date, *mortality_dates])
        if task.due_date != recovery_anchor + timedelta(days=POSTPARTUM_RECOVERY_DAYS):
            raise ValueError("The postpartum movement duty does not match the recovery date")
    elif (
        kidding is not None
        or breeding.outcome != BreedingOutcome.CONFIRMED_PREGNANT.value
        or breeding.expected_kidding_date is None
        or task.due_date != breeding.expected_kidding_date - timedelta(days=15)
    ):
        raise ValueError("The delivery movement duty does not match the recorded pregnancy")
    return kidding, kids


async def _guard_generated_weaning_task(db: AsyncSession, task: Task) -> None:
    """Require a recorded kidding whose day-60 milestone matches the task."""
    if not task.auto_generated or task.animal_id is None:
        raise ValueError("Weaning side effects require an authoritative generated duty")
    kidding_id = (
        await db.execute(
            select(KiddingRecord.id)
            .where(
                KiddingRecord.farm_id == task.farm_id,
                KiddingRecord.doe_id == task.animal_id,
                KiddingRecord.date == task.due_date - timedelta(days=WEANING_DAYS),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if kidding_id is None:
        raise ValueError("The weaning duty does not match a recorded kidding milestone")


async def complete_task(
    db: AsyncSession,
    task: Task,
    user: User | None = None,
    *,
    locked_animals: list[Animal] | None = None,
) -> Task:
    """Mark done (attributed) and apply side effects:
    - Day-45 quarantine BUCKET_MOVE (batch-linked) → release batch animals to FOUNDATION
    - BUCKET_MOVE (animal-linked, pregnancy) → move doe to DELIVERY
    - BUCKET_MOVE (no-survivor kidding) → move recovered doe to RESTING
    - WEANING → kids to MALE_KIDS/FEMALE_KIDS by sex, dam to RESTING
    - recurring duty (recur_days) → spawn the next occurrence

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
            if kidding is None or any(kid.status == "ALIVE" for kid in kids):  # pragma: no cover
                raise ValueError("Postpartum recovery duty is invalid while a kid survives")
            if error := bucket_transition_error(
                linked_animal, Bucket.RESTING.value, context="postpartum"
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
                linked_animal, Bucket.DELIVERY.value, context="delivery"
            ):
                raise ValueError(error)
    elif task.category == TaskCategory.WEANING.value:
        if locked_animals is None:
            raise ValueError("Task completion animals were not pre-locked")
        await _guard_generated_weaning_task(db, task)
        weaning_doe = animals_by_id.get(task.animal_id) if task.animal_id is not None else None
        if weaning_doe is None or weaning_doe.farm_id != task.farm_id:
            raise ValueError("The animal linked to this weaning duty is unavailable")
        weaning_kids = [
            animal
            for animal in affected_animals
            if animal.dam_id == weaning_doe.id
            and animal.status == AnimalStatus.ACTIVE.value
            and animal.current_bucket == Bucket.RECOVERY.value
        ]
        candidates = [weaning_doe, *weaning_kids]
        if any(
            bucket_transition_error(
                animal,
                Bucket.RESTING.value
                if animal.id == weaning_doe.id
                else (Bucket.MALE_KIDS.value if animal.sex == "M" else Bucket.FEMALE_KIDS.value),
                context="weaning",
            )
            for animal in candidates
        ):
            raise ValueError("Weaning is blocked by an animal lifecycle or movement-hold state")
    task.status = TaskStatus.DONE.value
    task.completed_by_id = user.id if user else None
    task.completed_at = utcnow()
    task.verification_note = None

    if task.category == TaskCategory.BUCKET_MOVE.value and task.purchase_batch_id:
        for animal in release_animals or []:
            move_animal(
                db,
                animal,
                Bucket.FOUNDATION.value,
                "45-day quarantine complete",
                created_by_id=user.id if user else None,
                context="quarantine_release",
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
            )

    elif task.category == TaskCategory.WEANING.value and task.animal_id:
        doe = weaning_doe
        if doe and doe.farm_id == task.farm_id:  # farm guard
            for kid in weaning_kids or []:
                target = Bucket.MALE_KIDS.value if kid.sex == "M" else Bucket.FEMALE_KIDS.value
                move_animal(
                    db,
                    kid,
                    target,
                    "Weaned (day 60)",
                    created_by_id=user.id if user else None,
                    context="weaning",
                )
            if doe.current_bucket in (Bucket.DELIVERY.value, Bucket.RECOVERY.value):
                move_animal(
                    db,
                    doe,
                    Bucket.RESTING.value,
                    "Kids weaned",
                    created_by_id=user.id if user else None,
                    context="weaning",
                )

    if task.recur_days:
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
    task.assigned_role_id = role_id
    return role_id


async def spawn_next_occurrence(db: AsyncSession, task: Task) -> Task:
    """Create the next occurrence of a recurring duty: same assignment and
    category, due recur_days after the current due_date, fresh PENDING state.
    Dedupes per series (title + due_date + assignment) so a reject →
    re-complete loop doesn't pile up duplicates, while two same-titled
    parallel series stay independent."""
    if not task.recur_days or task.recur_days > MAX_RECUR_DAYS:
        return task  # absurd recurrence (legacy data): don't explode date math
    if task.recurring_series_id is None:
        # Backfill legacy recurring rows lazily as well as in the migration so
        # imports/tests built directly from metadata remain safe.
        task.recurring_series_id = str(uuid4())
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
    if task.recur_days:
        # A skipped occurrence must not kill the series.
        await spawn_next_occurrence(db, task)
    await db.flush()
    return task


async def verify_task(db: AsyncSession, task: Task, user: User) -> Task:
    """Verifier confirms a DONE duty → VERIFIED (final state)."""
    task.status = TaskStatus.VERIFIED.value
    task.verified_by_id = user.id
    task.verified_at = utcnow()
    task.verification_note = None
    await db.flush()
    return task


async def reject_task(db: AsyncSession, task: Task, note: str) -> Task:
    """Verifier sends a DONE duty back to PENDING with a note for the worker.
    completed_by/at are kept as a record of the rejected attempt."""
    task.status = TaskStatus.PENDING.value
    task.verification_note = note or None
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
