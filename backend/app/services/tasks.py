"""Tasks / duties."""

# MAX_RECUR_DAYS lives in models.py.

from datetime import date, timedelta
from uuid import uuid4

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    MAX_RECUR_DAYS,
    Animal,
    AnimalStatus,
    Bucket,
    Farm,
    FarmMembership,
    Task,
    TaskCategory,
    TaskStatus,
    User,
)
from ..utils import today, utcnow
from .animals import bucket_transition_error, move_animal


async def _guard_quarantine_release(db: AsyncSession, task: Task) -> list[Animal]:
    """Lock and validate a batch before its final quarantine move.

    A release is an auditable gate, not simply the passage of 45 calendar
    days: every preceding generated duty must have a completed record and no
    active animal may carry a recorded movement restriction/disease hold.
    """
    prerequisites = list(
        (
            await db.execute(
                select(Task)
                .where(
                    Task.farm_id == task.farm_id,
                    Task.purchase_batch_id == task.purchase_batch_id,
                    Task.id != task.id,
                )
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
    animals = list(
        (
            await db.execute(
                select(Animal)
                .where(
                    Animal.farm_id == task.farm_id,
                    Animal.purchase_batch_id == task.purchase_batch_id,
                    Animal.current_bucket == Bucket.QUARANTINE.value,
                    Animal.status == AnimalStatus.ACTIVE.value,
                )
                .order_by(Animal.id)
                .with_for_update()
            )
        ).scalars()
    )
    if any(animal.movement_restricted or animal.suspected_scheduled_disease for animal in animals):
        raise ValueError(
            "Quarantine release is blocked by a recorded movement restriction or disease hold"
        )
    return animals


async def complete_task(db: AsyncSession, task: Task, user: User | None = None) -> Task:
    """Mark done (attributed) and apply side effects:
    - Day-45 quarantine BUCKET_MOVE (batch-linked) → release batch animals to FOUNDATION
    - BUCKET_MOVE (animal-linked, pregnancy) → move doe to DELIVERY
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
    release_animals: list[Animal] | None = None
    weaning_kids: list[Animal] | None = None
    weaning_doe: Animal | None = None
    if task.category == TaskCategory.BUCKET_MOVE.value and task.purchase_batch_id:
        release_animals = await _guard_quarantine_release(db, task)
    elif (
        task.category == TaskCategory.BUCKET_MOVE.value
        and task.animal_id
        and task.auto_generated
        and task.breeding_record_id
    ):
        linked_animal = await db.get(Animal, task.animal_id)
        if linked_animal is None or linked_animal.farm_id != task.farm_id:
            raise ValueError("The animal linked to this movement duty is unavailable")
        if linked_animal.current_bucket != Bucket.DELIVERY.value:
            if linked_animal.current_bucket not in (
                Bucket.PREGNANCY_LATE.value,
                Bucket.PREGNANCY_EARLY.value,
            ):
                raise ValueError("The linked animal is not in a pregnancy bucket")
            if error := bucket_transition_error(linked_animal, Bucket.DELIVERY.value):
                raise ValueError(error)
    elif task.category == TaskCategory.WEANING.value and task.animal_id:
        weaning_doe = await db.get(Animal, task.animal_id)
        if weaning_doe is not None and weaning_doe.farm_id == task.farm_id:
            weaning_kids = list(
                (
                    await db.execute(
                        select(Animal)
                        .where(
                            Animal.farm_id == task.farm_id,
                            Animal.dam_id == weaning_doe.id,
                            Animal.status == AnimalStatus.ACTIVE.value,
                            Animal.current_bucket == Bucket.RECOVERY.value,
                        )
                        .order_by(Animal.id)
                        .with_for_update()
                    )
                ).scalars()
            )
            candidates = [weaning_doe, *weaning_kids]
            if any(
                bucket_transition_error(
                    animal,
                    Bucket.RESTING.value
                    if animal.id == weaning_doe.id
                    else (
                        Bucket.MALE_KIDS.value if animal.sex == "M" else Bucket.FEMALE_KIDS.value
                    ),
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
            move_animal(db, animal, Bucket.FOUNDATION.value, "45-day quarantine complete")

    elif task.category == TaskCategory.BUCKET_MOVE.value and task.animal_id:
        linked_animal = await db.get(Animal, task.animal_id)
        # EARLY is accepted too: the EARLY→LATE transition is only a dashboard
        # suggestion, so a doe whose owner skipped it would otherwise see this
        # duty go green while she silently stays in PREGNANCY_EARLY.
        if (
            linked_animal
            and linked_animal.farm_id == task.farm_id  # farm guard
            and linked_animal.current_bucket
            in (
                Bucket.PREGNANCY_LATE.value,
                Bucket.PREGNANCY_EARLY.value,
            )
        ):
            move_animal(db, linked_animal, Bucket.DELIVERY.value, "~2 weeks before due date")

    elif task.category == TaskCategory.WEANING.value and task.animal_id:
        doe = weaning_doe
        if doe and doe.farm_id == task.farm_id:  # farm guard
            for kid in weaning_kids or []:
                target = Bucket.MALE_KIDS.value if kid.sex == "M" else Bucket.FEMALE_KIDS.value
                move_animal(db, kid, target, "Weaned (day 60)")
            if doe.current_bucket in (Bucket.DELIVERY.value, Bucket.RECOVERY.value):
                move_animal(db, doe, Bucket.RESTING.value, "Kids weaned")

    if task.recur_days:
        await spawn_next_occurrence(db, task)

    await db.flush()
    return task


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
    due = max(task.due_date, business_today) + timedelta(days=task.recur_days)
    result = await db.execute(
        select(Task).where(
            Task.farm_id == task.farm_id,
            Task.due_date == due,
            Task.recurring_series_id == task.recurring_series_id,
        )
    )
    existing = result.scalars().first()
    if existing:
        return existing
    nxt = Task(
        farm_id=task.farm_id,
        title=task.title,
        due_date=due,
        category=task.category,
        animal_id=task.animal_id,
        purchase_batch_id=task.purchase_batch_id,
        breeding_record_id=task.breeding_record_id,
        auto_generated=task.auto_generated,
        assigned_role_id=task.assigned_role_id,
        assigned_user_id=task.assigned_user_id,
        recur_days=task.recur_days,
        recurring_series_id=task.recurring_series_id,
    )
    db.add(nxt)
    await db.flush()
    return nxt


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

    Owner sees everything. A worker sees duties assigned to their role or to
    them personally; unassigned duties stay owner-visible only.

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
        conditions.append(Task.assigned_role_id == membership.role_id)
    return stmt.where(or_(*conditions))
