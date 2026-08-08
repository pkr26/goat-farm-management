"""Tasks / duties."""

# MAX_RECUR_DAYS lives in models.py (AUDIT 4-M4).

from datetime import date, timedelta

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
from ..utils import utcnow
from .animals import move_animal


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
    task.status = TaskStatus.DONE.value
    task.completed_by_id = user.id if user else None
    task.completed_at = utcnow()
    task.verification_note = None

    if task.category == TaskCategory.BUCKET_MOVE.value and task.purchase_batch_id:
        batch_result = await db.execute(
            select(Animal).where(
                # farm_id defense-in-depth (LOW 1-2): task.animal_id /
                # purchase_batch_id are server-assigned today, but a future
                # client-influenced link must never move cross-farm animals.
                Animal.farm_id == task.farm_id,
                Animal.purchase_batch_id == task.purchase_batch_id,
                Animal.current_bucket == Bucket.QUARANTINE.value,
                Animal.status == AnimalStatus.ACTIVE.value,
            )
        )
        for animal in batch_result.scalars():
            move_animal(db, animal, Bucket.FOUNDATION.value, "45-day quarantine complete")

    elif task.category == TaskCategory.BUCKET_MOVE.value and task.animal_id:
        linked_animal = await db.get(Animal, task.animal_id)
        # EARLY is accepted too: the EARLY→LATE transition is only a dashboard
        # suggestion, so a doe whose owner skipped it would otherwise see this
        # duty go green while she silently stays in PREGNANCY_EARLY.
        if (
            linked_animal
            and linked_animal.farm_id == task.farm_id  # LOW 1-2 farm guard
            and linked_animal.current_bucket
            in (
                Bucket.PREGNANCY_LATE.value,
                Bucket.PREGNANCY_EARLY.value,
            )
        ):
            move_animal(db, linked_animal, Bucket.DELIVERY.value, "~2 weeks before due date")

    elif task.category == TaskCategory.WEANING.value and task.animal_id:
        doe = await db.get(Animal, task.animal_id)
        if doe and doe.farm_id == task.farm_id:  # LOW 1-2 farm guard
            kids_result = await db.execute(
                select(Animal).where(
                    Animal.farm_id == task.farm_id,
                    Animal.dam_id == doe.id,
                    Animal.status == AnimalStatus.ACTIVE.value,
                    Animal.current_bucket == Bucket.RECOVERY.value,
                )
            )
            for kid in kids_result.scalars():
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
    due = task.due_date + timedelta(days=task.recur_days)
    result = await db.execute(
        select(Task).where(
            Task.farm_id == task.farm_id,
            Task.title == task.title,
            Task.due_date == due,
            Task.recur_days == task.recur_days,
            Task.assigned_role_id == task.assigned_role_id,
            Task.assigned_user_id == task.assigned_user_id,
            Task.status == TaskStatus.PENDING.value,
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
    )
    db.add(nxt)
    await db.flush()
    return nxt


async def skip_task(db: AsyncSession, task: Task, user: User) -> Task:
    """User skips a PENDING duty → SKIPPED, attributed; a recurring duty's
    series continues (AUDIT 4-M5: every task transition lives in services)."""
    task.status = TaskStatus.SKIPPED.value
    task.skipped_by_id = user.id
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
        auto_generated=False,
        assigned_role_id=assigned_role_id,
        assigned_user_id=assigned_user_id,
        recur_days=recur_days,
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
