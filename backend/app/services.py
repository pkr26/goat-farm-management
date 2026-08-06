"""Domain flows shared by routers and tests (Phase 2).

Every function takes an AsyncSession and farm-scoped entities; callers commit.
All business data is farm-scoped by construction (farm_id copied from the
parent entities).

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

import math
import re
from datetime import date, timedelta
from typing import Any, cast

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import (
    MAX_FAILED_CYCLES_BEFORE_CULL,
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
from .permissions import TASK_CATEGORY_ROLE_MAP
from .utils import add_months, today, utcnow


def move_animal(
    db: AsyncSession,
    animal: Animal,
    to_bucket: str,
    reason: str = "",
    created_by_id: int | None = None,
) -> None:
    """Record a BucketMove and update the animal's current bucket.
    Non-ACTIVE (sold/dead/culled) animals never move — they are out of the
    herd lifecycle, so task side effects and forged posts must not relocate
    them. (Synchronous: only session-state mutation, no I/O.)"""
    if animal.status != AnimalStatus.ACTIVE.value:
        return
    if animal.current_bucket == to_bucket:
        return
    db.add(
        BucketMove(
            animal_id=animal.id,
            from_bucket=animal.current_bucket,
            to_bucket=to_bucket,
            reason=reason or None,
            created_by_id=created_by_id,
        )
    )
    animal.current_bucket = to_bucket


async def skip_pending_tasks_for_animal(db: AsyncSession, farm_id: int, animal_id: int) -> None:
    """Cancel an animal's pending tasks (death/sale/cull): a dead or sold
    animal must not keep generating work (vaccines, moves, kidding due)."""
    for task in await _pending_tasks_for(db, farm_id, animal_id=animal_id):
        task.status = TaskStatus.SKIPPED.value


async def _pending_tasks_for(db: AsyncSession, farm_id: int, **filters: object) -> list[Task]:
    stmt = select(Task).where(Task.farm_id == farm_id, Task.status == TaskStatus.PENDING.value)
    for key, value in filters.items():
        stmt = stmt.where(getattr(Task, key) == value)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _default_role_id_for_category(
    db: AsyncSession, farm_id: int, category: str
) -> int | None:
    """Map a task category to the farm's preset role (MOVER/VET/...) if seeded."""
    role_code = TASK_CATEGORY_ROLE_MAP.get(category)
    if not role_code:
        return None
    result = await db.execute(select(Role).where(Role.farm_id == farm_id, Role.code == role_code))
    role = result.scalars().first()
    return role.id if role else None


async def _add_task(
    db: AsyncSession,
    farm_id: int,
    title: str,
    due_date: date,
    category: TaskCategory,
    animal_id: int | None = None,
    purchase_batch_id: int | None = None,
    breeding_record_id: int | None = None,
) -> Task:
    task = Task(
        farm_id=farm_id,
        title=title,
        due_date=due_date,
        category=category.value,
        animal_id=animal_id,
        purchase_batch_id=purchase_batch_id,
        breeding_record_id=breeding_record_id,
        auto_generated=True,
        assigned_role_id=await _default_role_id_for_category(db, farm_id, category.value),
    )
    db.add(task)
    return task


async def _load_doe(db: AsyncSession, br: BreedingRecord) -> Animal:
    """``br.doe``, loaded explicitly (async sessions forbid implicit lazy loads).
    doe_id is a non-nullable FK, so a missing row means corrupt data."""
    doe = await db.get(Animal, br.doe_id)
    if doe is None:
        raise ValueError(f"Breeding record {br.id} references missing doe {br.doe_id}")
    return doe


async def _kidding_record_of(db: AsyncSession, br: BreedingRecord) -> KiddingRecord | None:
    """``br.kidding_record`` (one-to-one), loaded explicitly (async sessions
    forbid implicit lazy loads)."""
    result = await db.execute(
        select(KiddingRecord).where(KiddingRecord.breeding_record_id == br.id)
    )
    return result.scalars().first()


# ---------------------------------------------------------------------------
# Breeding
# ---------------------------------------------------------------------------
async def breeding_candidate_does(db: AsyncSession, farm: Farm) -> list[Animal]:
    """Does eligible for a new breeding record: breeding-ready per SPEC rules,
    plus does already in the BREEDING bucket that are not pregnant (re-breeding).
    A doe with an unresolved breeding (PENDING, or confirmed and not yet
    kidded) is NOT eligible — one active pregnancy per doe."""
    does_result = await db.execute(
        select(Animal)
        .options(
            # is_breeding_ready / is_currently_pregnant read these relationships.
            selectinload(Animal.weight_records),
            selectinload(Animal.breedings_as_doe).selectinload(BreedingRecord.kidding_record),
        )
        .where(
            Animal.farm_id == farm.id,
            Animal.sex == "F",
            Animal.status == AnimalStatus.ACTIVE.value,
        )
        .order_by(Animal.tag_number)
    )
    does = list(does_result.scalars().all())
    open_result = await db.execute(
        select(BreedingRecord)
        .options(selectinload(BreedingRecord.kidding_record))
        .where(
            BreedingRecord.farm_id == farm.id,
            BreedingRecord.outcome.in_(
                [BreedingOutcome.PENDING.value, BreedingOutcome.CONFIRMED_PREGNANT.value]
            ),
        )
    )
    busy_doe_ids = {
        r.doe_id
        for r in open_result.scalars()
        if r.outcome == BreedingOutcome.PENDING.value or not r.kidding_record
    }
    return [
        d
        for d in does
        if d.id not in busy_doe_ids
        and (
            d.is_breeding_ready
            or (
                d.current_bucket == Bucket.BREEDING.value
                and not d.is_currently_pregnant
                and (d.age_months or 0) >= 10
            )
        )
    ]


async def create_breeding_record(
    db: AsyncSession,
    farm: Farm,
    doe: Animal,
    buck: Animal,
    breeding_date: date,
    heat_cycle_number: int = 1,
    created_by_id: int | None = None,
) -> BreedingRecord:
    history = await db.execute(
        select(BreedingRecord)
        .options(selectinload(BreedingRecord.kidding_record))
        .where(BreedingRecord.doe_id == doe.id)
    )
    unresolved = [
        r
        for r in history.scalars()
        if r.outcome == BreedingOutcome.PENDING.value
        or (r.outcome == BreedingOutcome.CONFIRMED_PREGNANT.value and not r.kidding_record)
    ]
    if unresolved:
        raise ValueError(f"{doe.tag_number} already has an unresolved breeding/pregnancy")
    ultrasound_date = planned_ultrasound_date(breeding_date)
    br = BreedingRecord(
        farm_id=farm.id,
        doe_id=doe.id,
        buck_id=buck.id,
        breeding_date=breeding_date,
        method=BreedingMethod.NATURAL.value,
        heat_cycle_number=heat_cycle_number,
        ultrasound_date=ultrasound_date,
        outcome=BreedingOutcome.PENDING.value,
        created_by_id=created_by_id,
    )
    db.add(br)
    await db.flush()
    await _add_task(
        db,
        farm.id,
        f"Ultrasound check: {doe.tag_number} (bred {breeding_date.strftime('%d-%m')})",
        ultrasound_date,
        TaskCategory.ULTRASOUND,
        animal_id=doe.id,
        breeding_record_id=br.id,
    )
    move_animal(db, doe, Bucket.BREEDING.value, "Bred", created_by_id=created_by_id)
    await db.flush()
    return br


async def record_ultrasound_result(
    db: AsyncSession, br: BreedingRecord, pregnant: bool, kid_count: int | None = None
) -> BreedingRecord:
    """Record ultrasound outcome. Pregnant → CONFIRMED_PREGNANT + 3 follow-up
    tasks + move to PREGNANCY_EARLY. Not pregnant → FAILED + cull check.

    Idempotent: only a PENDING record accepts a result — a double submission
    (or forged replay) must not spawn a second set of follow-up tasks."""
    if br.outcome != BreedingOutcome.PENDING.value:
        return br
    br.ultrasound_done = True
    br.pregnant = pregnant
    br.kid_count_detected = kid_count if pregnant else None
    doe = await _load_doe(db, br)

    for task in await _pending_tasks_for(
        db, br.farm_id, breeding_record_id=br.id, category=TaskCategory.ULTRASOUND.value
    ):
        task.status = TaskStatus.DONE.value

    if pregnant:
        br.outcome = BreedingOutcome.CONFIRMED_PREGNANT.value
        doe.cull_candidate = False  # she conceived — previous failures forgiven
        ekd = expected_kidding_date(br.breeding_date)
        br.expected_kidding_date = ekd
        move_animal(db, doe, Bucket.PREGNANCY_EARLY.value, "Ultrasound confirmed pregnant")
        await _add_task(
            db,
            br.farm_id,
            f"Pre-kidding ET+TT vaccine: {doe.tag_number}",
            ekd - timedelta(days=40),
            TaskCategory.VACCINE,
            animal_id=doe.id,
            breeding_record_id=br.id,
        )
        await _add_task(
            db,
            br.farm_id,
            f"Move {doe.tag_number} to DELIVERY (kidding in ~2 weeks)",
            ekd - timedelta(days=15),
            TaskCategory.BUCKET_MOVE,
            animal_id=doe.id,
            breeding_record_id=br.id,
        )
        await _add_task(
            db,
            br.farm_id,
            f"Kidding due: {doe.tag_number}",
            ekd,
            TaskCategory.KIDDING_DUE,
            animal_id=doe.id,
            breeding_record_id=br.id,
        )
    else:
        br.outcome = BreedingOutcome.FAILED.value
        # Sessions run autoflush=False: flush so _update_cull_candidate's
        # query sees THIS failure (otherwise the flag lags one cycle behind).
        # This flush-before-check order is load-bearing — do not reorder.
        await db.flush()
        await _update_cull_candidate(db, doe)

    await db.flush()
    return br


async def _update_cull_candidate(db: AsyncSession, doe: Animal) -> None:
    """2 consecutive FAILED cycles → cull candidate flag (per SPEC)."""
    result = await db.execute(
        select(BreedingRecord)
        .where(
            BreedingRecord.doe_id == doe.id,
            BreedingRecord.outcome != BreedingOutcome.PENDING.value,
        )
        .order_by(BreedingRecord.breeding_date.desc(), BreedingRecord.id.desc())
    )
    consecutive_failures = 0
    for record in result.scalars():
        if record.outcome == BreedingOutcome.FAILED.value:
            consecutive_failures += 1
        else:
            break
    if consecutive_failures >= MAX_FAILED_CYCLES_BEFORE_CULL:
        doe.cull_candidate = True


async def mark_aborted(db: AsyncSession, br: BreedingRecord) -> BreedingRecord:
    """Pregnancy lost → outcome ABORTED, doe to RESTING, open pregnancy tasks skipped.
    Only a live confirmed pregnancy can abort: a PENDING/FAILED record is a
    no-op, and a pregnancy that already kidded can never be 'aborted' after
    the fact (that falsifies stats and rips the doe out of RECOVERY)."""
    if br.outcome != BreedingOutcome.CONFIRMED_PREGNANT.value:
        return br
    if await _kidding_record_of(db, br) is not None:
        return br
    br.outcome = BreedingOutcome.ABORTED.value
    br.pregnant = False
    move_animal(db, await _load_doe(db, br), Bucket.RESTING.value, "Pregnancy aborted")
    for task in await _pending_tasks_for(db, br.farm_id, breeding_record_id=br.id):
        task.status = TaskStatus.SKIPPED.value
    await db.flush()
    return br


# ---------------------------------------------------------------------------
# Kidding
# ---------------------------------------------------------------------------
async def record_kidding(
    db: AsyncSession,
    farm: Farm,
    br: BreedingRecord,
    kidding_date: date,
    ease: str,
    notes: str,
    kids: list[dict[str, Any]],
    created_by_id: int | None = None,
) -> KiddingRecord:
    """Record a kidding. Alive kids auto-create Animal rows (source=BORN,
    dam/sire linked, bucket=RECOVERY). Doe → RECOVERY; WEANING task at +60d.

    Only a confirmed, not-yet-kidded pregnancy of an ACTIVE doe can kidd:
    a sold/dead doe must not "deliver" new stock onto the farm."""
    if br.outcome != BreedingOutcome.CONFIRMED_PREGNANT.value:
        raise ValueError("Kidding requires a confirmed pregnancy")
    if await _kidding_record_of(db, br) is not None:
        raise ValueError("This pregnancy already has a kidding record")
    doe = await _load_doe(db, br)
    if doe.status != AnimalStatus.ACTIVE.value:
        raise ValueError(f"{doe.tag_number} is {doe.status.lower()} — cannot record a kidding")
    record = KiddingRecord(
        farm_id=farm.id,
        doe_id=doe.id,
        date=kidding_date,
        breeding_record_id=br.id,
        ease=ease,
        notes=notes or None,
        created_by_id=created_by_id,
    )
    db.add(record)
    await db.flush()

    alive_count = sum(1 for k in kids if k["status"] == KidStatus.ALIVE.value)
    birth_type = {1: BirthType.SINGLE, 2: BirthType.TWIN, 3: BirthType.TRIPLET}.get(alive_count)

    # Tags are unique per farm; auto tags ("<doe>-K<n>") collide on a doe's
    # second kidding, so uniquify instead of crashing on the constraint.
    tags_result = await db.execute(select(Animal.tag_number).where(Animal.farm_id == farm.id))
    existing_tags = set(tags_result.scalars().all())

    def _unique_tag(base: str) -> str:
        tag, n = base, 2
        while tag in existing_tags:
            tag = f"{base}-{n}"
            n += 1
        existing_tags.add(tag)
        return tag

    for kid in kids:
        entry = KidEntry(
            kidding_record_id=record.id,
            tag=kid["tag"] or None,
            sex=kid["sex"],
            birth_weight=kid.get("birth_weight"),
            status=kid["status"],
        )
        db.add(entry)
        await db.flush()
        if kid["status"] == KidStatus.ALIVE.value:
            tag = _unique_tag(kid["tag"])
            animal = Animal(
                farm_id=farm.id,
                tag_number=tag,
                sex=kid["sex"],
                breed=doe.breed,
                source=AnimalSource.BORN.value,
                date_of_birth=kidding_date,
                birth_type=birth_type.value if birth_type else None,
                dam_id=doe.id,
                sire_id=br.buck_id,
                birth_weight=kid.get("birth_weight"),
                current_bucket=Bucket.RECOVERY.value,
                status=AnimalStatus.ACTIVE.value,
            )
            db.add(animal)
            await db.flush()
            db.add(
                BucketMove(
                    animal_id=animal.id,
                    from_bucket=None,
                    to_bucket=Bucket.RECOVERY.value,
                    reason="Born",
                )
            )
            entry.tag = tag
            entry.animal_id = animal.id

    # Doe goes to RECOVERY whether she was in DELIVERY or still in PREGNANCY_LATE.
    move_animal(db, doe, Bucket.RECOVERY.value, "Kidded")

    for task in await _pending_tasks_for(
        db, farm.id, breeding_record_id=br.id, category=TaskCategory.KIDDING_DUE.value
    ):
        task.status = TaskStatus.DONE.value
    # Flush before the leftover query: sessions run autoflush=False, and the
    # DB-level PENDING filter below must see the DONE above (else it clobbers
    # the KIDDING_DUE task back to SKIPPED).
    await db.flush()
    # Leftover pre-kidding tasks (ET+TT vaccine, move to DELIVERY) are moot
    # once she has kidded — skip them so they don't linger as overdue noise.
    for task in await _pending_tasks_for(db, farm.id, breeding_record_id=br.id):
        task.status = TaskStatus.SKIPPED.value

    await _add_task(
        db,
        farm.id,
        f"Wean kids of {doe.tag_number}; doe → RESTING",
        kidding_date + timedelta(days=WEANING_DAYS),
        TaskCategory.WEANING,
        animal_id=doe.id,
    )
    await db.flush()
    return record


# ---------------------------------------------------------------------------
# Purchases & quarantine
# ---------------------------------------------------------------------------
MAX_BATCH_COUNT = 1000  # SPEC plans ~50 animals/batch; cap runaway row creation
MAX_AGE_MONTHS = 240  # 20 years — far beyond any goat's lifespan


async def create_purchase_batch(
    db: AsyncSession,
    farm: Farm,
    batch_date: date,
    supplier: str,
    count: int,
    avg_age_months: float | None,
    avg_weight_kg: float | None,
    total_price: float | None,
    notes: str,
    create_animals: bool,
    created_by_id: int | None = None,
) -> PurchaseBatch:
    """Create a purchase batch, optionally stub N animals into QUARANTINE,
    auto-generate the 45-day quarantine task schedule, and book the expense."""
    if count < 1:
        raise ValueError("Batch count must be at least 1")
    if count > MAX_BATCH_COUNT:
        raise ValueError(f"Batch count is capped at {MAX_BATCH_COUNT} per batch")
    if total_price is not None and total_price < 0:
        raise ValueError("Total price cannot be negative")
    if avg_age_months is not None and not 0 <= avg_age_months <= MAX_AGE_MONTHS:
        raise ValueError(f"Average age must be between 0 and {MAX_AGE_MONTHS} months")
    if avg_weight_kg is not None and avg_weight_kg < 0:
        raise ValueError("Average weight cannot be negative")
    batch = PurchaseBatch(
        farm_id=farm.id,
        date=batch_date,
        supplier=supplier or None,
        count=count,
        avg_age_months=avg_age_months,
        avg_weight_kg=avg_weight_kg,
        total_price=total_price,
        notes=notes or None,
    )
    db.add(batch)
    await db.flush()

    if create_animals:
        per_head = round(total_price / count, 2) if total_price else None
        for i in range(1, count + 1):
            animal = Animal(
                farm_id=farm.id,
                tag_number=f"B{batch.id}-{i:03d}",
                sex="F",
                source=AnimalSource.PURCHASED.value,
                current_bucket=Bucket.QUARANTINE.value,
                status=AnimalStatus.ACTIVE.value,
                purchase_date=batch_date,
                purchase_price=per_head,
                seller_name=supplier or None,
                purchase_batch_id=batch.id,
                estimated_dob=(
                    add_months(batch_date, -int(avg_age_months)) if avg_age_months else None
                ),
            )
            db.add(animal)
            await db.flush()
            db.add(
                BucketMove(
                    animal_id=animal.id,
                    from_bucket=None,
                    to_bucket=Bucket.QUARANTINE.value,
                    reason=f"Purchase batch #{batch.id}",
                )
            )

    for item in quarantine_schedule(batch):
        await _add_task(
            db,
            farm.id,
            cast(str, item["title"]),
            cast(date, item["due_date"]),
            TaskCategory(cast(str, item["category"])),
            purchase_batch_id=batch.id,
        )

    if total_price:
        db.add(
            Transaction(
                farm_id=farm.id,
                date=batch_date,
                type=TransactionType.EXPENSE.value,
                category=TransactionCategory.ANIMAL_PURCHASE.value,
                amount=total_price,
                notes=f"Purchase batch #{batch.id}: {count} animals"
                + (f" from {supplier}" if supplier else ""),
                created_by_id=created_by_id,
            )
        )
    await db.flush()
    return batch


# ---------------------------------------------------------------------------
# Tasks / duties
# ---------------------------------------------------------------------------
MAX_RECUR_DAYS = 3650  # sanity cap: ~10 years; larger values overflow date arithmetic


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
                Animal.purchase_batch_id == task.purchase_batch_id,
                Animal.current_bucket == Bucket.QUARANTINE.value,
                Animal.status == AnimalStatus.ACTIVE.value,
            )
        )
        for animal in batch_result.scalars():
            move_animal(db, animal, Bucket.FOUNDATION.value, "45-day quarantine complete")

    elif task.category == TaskCategory.BUCKET_MOVE.value and task.animal_id:
        linked_animal = await db.get(Animal, task.animal_id)
        if linked_animal and linked_animal.current_bucket == Bucket.PREGNANCY_LATE.value:
            move_animal(db, linked_animal, Bucket.DELIVERY.value, "~2 weeks before due date")

    elif task.category == TaskCategory.WEANING.value and task.animal_id:
        doe = await db.get(Animal, task.animal_id)
        if doe:
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


# ---------------------------------------------------------------------------
# Health / vaccination schedule
# ---------------------------------------------------------------------------
async def record_health_event(
    db: AsyncSession,
    farm: Farm,
    animals: list[Animal],
    event_date: date,
    event_type: str,
    product_name: str,
    disease_target: str,
    dose: str,
    route: str,
    vet_name: str,
    total_cost: float | None,
    next_due_date: date | None,
    notes: str,
    purchase_batch_id: int | None = None,
    created_by_id: int | None = None,
) -> list[HealthEvent]:
    """Create one HealthEvent row per animal; total cost divided evenly.
    The first animal absorbs the rounding remainder so the split sums back
    to the recorded total; an explicit ₹0 cost is stored as 0.00, not NULL."""
    costs: list[float | None]
    if total_cost is not None and animals:
        per = round(total_cost / len(animals), 2)
        costs = [per] * len(animals)
        costs[0] = round(total_cost - per * (len(animals) - 1), 2)
    else:
        costs = [None] * len(animals)
    events = []
    for animal, per_animal_cost in zip(animals, costs, strict=True):
        event = HealthEvent(
            farm_id=farm.id,
            animal_id=animal.id,
            purchase_batch_id=purchase_batch_id,
            date=event_date,
            type=event_type,
            product_name=product_name or None,
            disease_target=disease_target or None,
            dose=dose or None,
            route=route or None,
            vet_name=vet_name or None,
            cost=per_animal_cost,
            next_due_date=next_due_date,
            notes=notes or None,
            created_by_id=created_by_id,
        )
        db.add(event)
        events.append(event)
    await db.flush()
    return events


async def vaccination_schedule_for_animal(db: AsyncSession, animal: Animal) -> list[dict[str, Any]]:
    """Per-animal vaccination schedule from seeded VaccineTemplates.
    Status: DONE (event recorded) / OVERDUE (due date passed) / UPCOMING."""
    dob = animal.effective_dob
    events_result = await db.execute(
        select(HealthEvent)
        .where(
            HealthEvent.animal_id == animal.id,
            HealthEvent.type.in_([HealthEventType.VACCINE.value, HealthEventType.DEWORMING.value]),
        )
        .order_by(HealthEvent.date)
    )
    events = list(events_result.scalars().all())

    def _matches(template_name: str, event: HealthEvent) -> bool:
        haystack = f"{event.product_name or ''} {event.disease_target or ''}".lower()
        key = template_name.split("(")[0].strip().lower()
        first_word = key.split()[0] if key else ""
        # "Enterotoxaemia (ET)" should also match an event recorded as "ET + TT".
        abbrev_match = re.search(r"\(([^)]+)\)", template_name)
        abbrev = abbrev_match.group(1).strip().lower() if abbrev_match else ""
        return (
            key in haystack
            or (bool(first_word) and first_word in haystack)
            or (bool(abbrev) and re.search(rf"\b{re.escape(abbrev)}\b", haystack) is not None)
        )

    rows: list[dict[str, Any]] = []
    templates_result = await db.execute(select(VaccineTemplate).order_by(VaccineTemplate.id))
    for template in templates_result.scalars():
        if template.first_dose_age_months is None and not template.repeat_months:
            continue  # pregnancy-linked (ET+TT pre-kidding) handled via tasks
        first_due = (
            add_months(dob, template.first_dose_age_months)
            if dob and template.first_dose_age_months is not None
            else None
        )
        booster_due = (
            first_due + timedelta(weeks=template.booster_weeks)
            if first_due and template.booster_weeks
            else None
        )
        done = [e for e in events if _matches(template.name, e)]
        last_done = done[-1].date if done else None
        next_due = None
        if last_done and template.repeat_months:
            next_due = add_months(last_done, int(template.repeat_months))
        if done and not next_due:
            status = "DONE"
        elif done:
            # `not next_due` was handled above, so next_due is set here.
            status = "DONE" if (next_due is not None and next_due >= today()) else "OVERDUE"
        elif first_due is None and dob is None and template.first_dose_age_months is not None:
            # Age-based template but the animal's DOB is unknown → no date can
            # be computed; not the same as "due now" (that's for herd-wide
            # recurring items like Deworming, which have no age-based dose).
            status = "UNKNOWN"
        elif first_due is None:
            # Herd-wide recurring item with no age-based first dose (Deworming)
            # and nothing recorded yet → due now.
            status = "OVERDUE"
        elif first_due < today():
            status = "OVERDUE"
        else:
            status = "UPCOMING"
        rows.append(
            {
                "template": template,
                "first_due": first_due,
                "booster_due": booster_due,
                "last_done": last_done,
                "next_due": next_due,
                "status": status,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Dashboard helpers
# ---------------------------------------------------------------------------
async def ready_to_move_suggestions(db: AsyncSession, farm: Farm) -> list[dict[str, Any]]:
    """Bucket-move suggestions per SPEC thresholds."""
    result = await db.execute(
        select(Animal)
        .options(
            # is_breeding_ready / days_in_current_bucket / _gestation_days read
            # these relationships.
            selectinload(Animal.weight_records),
            selectinload(Animal.bucket_moves),
            selectinload(Animal.breedings_as_doe).selectinload(BreedingRecord.kidding_record),
        )
        .where(Animal.farm_id == farm.id, Animal.status == AnimalStatus.ACTIVE.value)
    )
    animals = list(result.scalars().all())
    suggestions: list[dict[str, Any]] = []

    def _gestation_days(animal: Animal) -> int | None:
        confirmed = [
            r
            for r in animal.breedings_as_doe
            if r.outcome == BreedingOutcome.CONFIRMED_PREGNANT.value and not r.kidding_record
        ]
        if not confirmed:
            return None
        latest = max(confirmed, key=lambda r: (r.breeding_date, r.id or 0))
        return (today() - latest.breeding_date).days

    for animal in animals:
        bucket = animal.current_bucket
        if (
            animal.sex == "F"
            and bucket in (Bucket.FOUNDATION.value, Bucket.FEMALE_KIDS.value)
            and animal.is_breeding_ready
        ):
            suggestions.append(
                {
                    "animal": animal,
                    "to": Bucket.BREEDING.value,
                    "reason": "Breeding-ready (≥10 mo, ≥22 kg)",
                }
            )
        elif (
            animal.sex == "F"
            and bucket == Bucket.RESTING.value
            and animal.days_in_current_bucket >= 30
        ):
            suggestions.append(
                {
                    "animal": animal,
                    "to": Bucket.BREEDING.value,
                    "reason": f"{animal.days_in_current_bucket} days resting (flush done)",
                }
            )
        elif bucket == Bucket.PREGNANCY_EARLY.value:
            day = _gestation_days(animal)
            if day is not None and day >= 100:
                suggestions.append(
                    {
                        "animal": animal,
                        "to": Bucket.PREGNANCY_LATE.value,
                        "reason": f"Gestation day {day} (≥100)",
                    }
                )
        elif bucket == Bucket.PREGNANCY_LATE.value:
            day = _gestation_days(animal)
            if day is not None and day >= 135:
                suggestions.append(
                    {
                        "animal": animal,
                        "to": Bucket.DELIVERY.value,
                        "reason": f"Gestation day {day} (≥135, due soon)",
                    }
                )
        elif animal.sex == "M" and bucket == Bucket.MALE_KIDS.value:
            age, weight = animal.age_months, animal.latest_weight_kg
            if age is not None and age >= 8 and weight is not None and weight >= 24:
                suggestions.append(
                    {
                        "animal": animal,
                        "to": "SELL",
                        "reason": f"{age} mo, {weight:.1f} kg — market ready",
                    }
                )
    return suggestions


# ---------------------------------------------------------------------------
# Feeding
# ---------------------------------------------------------------------------
DRY_ROUGHAGE = "DRY_ROUGHAGE_ONLY"
RECIPE_DISPLAY = {
    "FATTENING_50_50": "Fattening 50:50",
    "LACTATING_60_40": "Lactating 60:40",
    "MAINTENANCE_75_25": "Maintenance 75:25",
    "FLUSH_70_30": "Flush 70:30",
    "CREEP": "Creep feed",
    DRY_ROUGHAGE: "Dry roughage only (days 1–3, zero grain)",
}

# SPEC "Feed allocation per bucket" — reference table for the recipes page.
BUCKET_ALLOCATION_REFERENCE = [
    ("QUARANTINE", "Dry roughage only (days 1–3) → transition to MAINTENANCE_75_25"),
    ("FOUNDATION", "LACTATING_60_40"),
    ("BREEDING", "MAINTENANCE_75_25 (incl. dry bucks)"),
    ("PREGNANCY_EARLY", "MAINTENANCE_75_25"),
    ("PREGNANCY_LATE", "LACTATING_60_40"),
    ("DELIVERY", "LACTATING_60_40"),
    ("RECOVERY", "LACTATING_60_40 (lactating)"),
    ("RESTING", "Days 1–10 MAINTENANCE_75_25, days 10–30 FLUSH_70_30"),
    ("MALE_KIDS", "Day ≤90 LACTATING_60_40 (frame-builder), day 91+ FATTENING_50_50"),
    ("FEMALE_KIDS", "LACTATING_60_40"),
]

SHIFT_TIMES = {
    FeedingShift.MORNING.value: "6:30 AM (sweep bunks first)",
    FeedingShift.AFTERNOON.value: "1:30 PM",
    FeedingShift.NIGHT.value: "7:30 PM",
}


def recipe_for_animal(animal: Animal, ref: date | None = None) -> str:
    """Which TMR recipe applies to this animal today (SPEC allocation rules).

    Pure python (stays synchronous): reads days_in_current_bucket, so the
    animal's bucket_moves must already be loaded — async sessions forbid
    implicit lazy loads (feeding_plan selectinloads them)."""
    ref = ref or today()
    bucket = animal.current_bucket
    if bucket == Bucket.QUARANTINE.value:
        return DRY_ROUGHAGE if animal.days_in_current_bucket < 3 else "MAINTENANCE_75_25"
    if bucket in (
        Bucket.FOUNDATION.value,
        Bucket.FEMALE_KIDS.value,
        Bucket.PREGNANCY_LATE.value,
        Bucket.RECOVERY.value,
        Bucket.DELIVERY.value,
    ):
        return "LACTATING_60_40"
    if bucket in (Bucket.BREEDING.value, Bucket.PREGNANCY_EARLY.value):
        return "MAINTENANCE_75_25"
    if bucket == Bucket.RESTING.value:
        return "FLUSH_70_30" if animal.days_in_current_bucket >= 10 else "MAINTENANCE_75_25"
    if bucket == Bucket.MALE_KIDS.value:
        dob = animal.effective_dob
        age_days = (ref - dob).days if dob else 999  # unknown age → fattening
        return "LACTATING_60_40" if age_days <= 90 else "FATTENING_50_50"
    return "MAINTENANCE_75_25"


async def get_daily_kg_per_head(db: AsyncSession, farm_id: int, bucket_code: str) -> float:
    override_result = await db.execute(
        select(BucketFeedSetting).where(
            BucketFeedSetting.farm_id == farm_id, BucketFeedSetting.bucket == bucket_code
        )
    )
    override = override_result.scalars().first()
    if override:
        return override.daily_kg_per_head
    definition_result = await db.execute(
        select(BucketDefinition).where(BucketDefinition.code == bucket_code)
    )
    definition = definition_result.scalars().first()
    return definition.daily_kg_per_head if definition else 1.2


async def set_daily_kg_per_head(
    db: AsyncSession, farm_id: int, bucket_code: str, kg: float
) -> None:
    result = await db.execute(
        select(BucketFeedSetting).where(
            BucketFeedSetting.farm_id == farm_id, BucketFeedSetting.bucket == bucket_code
        )
    )
    row = result.scalars().first()
    if row:
        row.daily_kg_per_head = kg
    else:
        db.add(BucketFeedSetting(farm_id=farm_id, bucket=bucket_code, daily_kg_per_head=kg))
    await db.flush()


async def feeding_plan(
    db: AsyncSession, farm: Farm, ref: date | None = None
) -> list[dict[str, Any]]:
    """Today's plan: one line per (bucket, recipe) with headcount, daily kg
    (heads × per-head setting) and the 40/20/40 shift split."""
    ref = ref or today()
    animals_result = await db.execute(
        select(Animal)
        # recipe_for_animal reads days_in_current_bucket (bucket_moves).
        .options(selectinload(Animal.bucket_moves))
        .where(Animal.farm_id == farm.id, Animal.status == AnimalStatus.ACTIVE.value)
    )
    animals = list(animals_result.scalars().all())
    groups: dict[tuple[str, str], int] = {}
    for animal in animals:
        key = (animal.current_bucket, recipe_for_animal(animal, ref))
        groups[key] = groups.get(key, 0) + 1

    definitions_result = await db.execute(select(BucketDefinition))
    order = {d.code: d.sort_order for d in definitions_result.scalars()}
    lines = []
    for (bucket_code, recipe_code), heads in sorted(
        groups.items(), key=lambda kv: order.get(kv[0][0], 99)
    ):
        kg_per_head = await get_daily_kg_per_head(db, farm.id, bucket_code)
        daily_kg = round(heads * kg_per_head, 2)
        lines.append(
            {
                "bucket": bucket_code,
                "recipe_code": recipe_code,
                "recipe_name": RECIPE_DISPLAY.get(recipe_code, recipe_code),
                "heads": heads,
                "kg_per_head": kg_per_head,
                "daily_kg": daily_kg,
                "shifts": [
                    {
                        "shift": shift.value,
                        "pct": int(pct * 100),
                        "kg": round(daily_kg * pct, 2),
                        "time": SHIFT_TIMES[shift.value],
                    }
                    for shift, pct in SHIFT_SPLIT.items()
                ],
            }
        )
    return lines


class InsufficientFeedError(Exception):
    def __init__(self, shortages: list[str]):
        self.shortages = shortages
        super().__init__("; ".join(shortages))


async def mix_feed_batch(
    db: AsyncSession, farm: Farm, recipe_code: str, batch_kg: float
) -> FeedRecipe:
    """Mix a batch: decrement inventory per recipe lines. Refuses (no writes)
    if any ingredient is insufficient."""
    recipe_result = await db.execute(
        select(FeedRecipe)
        .options(selectinload(FeedRecipe.lines))
        .where(FeedRecipe.code == recipe_code)
    )
    recipe = recipe_result.scalars().first()
    if recipe is None:
        raise ValueError(f"Unknown recipe {recipe_code}")
    if not math.isfinite(batch_kg) or batch_kg <= 0:
        raise ValueError("Batch size must be a positive finite number")

    shortages = []
    planned: list[tuple[FeedInventory | None, float]] = []
    for line in recipe.lines:
        needed = round(line.kg_per_100kg / 100.0 * batch_kg, 3)
        item_result = await db.execute(
            select(FeedInventory).where(
                FeedInventory.farm_id == farm.id, FeedInventory.ingredient == line.ingredient
            )
        )
        item = item_result.scalars().first()
        on_hand = item.qty_on_hand if item else 0.0
        if on_hand < needed:
            shortages.append(f"{line.ingredient}: need {needed:.1f} kg, have {on_hand:.1f} kg")
        planned.append((item, needed))

    if shortages:
        raise InsufficientFeedError(shortages)

    for item, needed in planned:
        if item:
            item.qty_on_hand = round(item.qty_on_hand - needed, 3)
    await db.flush()
    return recipe


async def add_feed_stock(
    db: AsyncSession,
    farm: Farm,
    item: FeedInventory,
    qty_kg: float,
    price_per_kg: float | None,
    created_by_id: int | None = None,
) -> None:
    """Purchase stock: increase qty, update last price, book a FEED expense."""
    if not math.isfinite(qty_kg) or qty_kg <= 0:
        raise ValueError("Quantity must be a positive finite number")
    if price_per_kg is not None and not math.isfinite(price_per_kg):
        raise ValueError("Price must be finite")
    item.qty_on_hand = round(item.qty_on_hand + qty_kg, 3)
    if price_per_kg:
        item.last_purchase_price_per_kg = price_per_kg
        db.add(
            Transaction(
                farm_id=farm.id,
                date=today(),
                type=TransactionType.EXPENSE.value,
                category=TransactionCategory.FEED.value,
                amount=round(qty_kg * price_per_kg, 2),
                notes=f"Feed purchase: {qty_kg:.1f} kg {item.ingredient}",
                created_by_id=created_by_id,
            )
        )
    await db.flush()


async def record_dispensing(
    db: AsyncSession,
    farm: Farm,
    bucket: str,
    shift: str,
    recipe_code: str | None,
    qty_kg: float,
    dispense_date: date,
    created_by_id: int | None = None,
) -> FeedingRecord:
    record = FeedingRecord(
        farm_id=farm.id,
        date=dispense_date,
        shift=shift,
        bucket=bucket,
        recipe_code=recipe_code or None,
        qty_kg=qty_kg,
        created_by_id=created_by_id,
    )
    db.add(record)
    await db.flush()
    return record


# ---------------------------------------------------------------------------
# Finance
# ---------------------------------------------------------------------------
async def monthly_pnl(db: AsyncSession, farm: Farm, n_months: int = 12) -> list[dict[str, Any]]:
    """Income vs expense by month (and category) for the last n_months,
    most recent first."""
    result = await db.execute(select(Transaction).where(Transaction.farm_id == farm.id))
    txns = list(result.scalars().all())
    months: dict[str, dict[str, Any]] = {}
    for txn in txns:
        key = txn.date.strftime("%Y-%m")
        row = months.setdefault(
            key, {"month": key, "income": 0.0, "expense": 0.0, "categories": {}}
        )
        if txn.type == TransactionType.INCOME.value:
            row["income"] += txn.amount
        else:
            row["expense"] += txn.amount
        cat = row["categories"].setdefault(txn.category, {"income": 0.0, "expense": 0.0})
        cat["income" if txn.type == TransactionType.INCOME.value else "expense"] += txn.amount

    rows = sorted(months.values(), key=lambda r: r["month"], reverse=True)[:n_months]
    for row in rows:
        row["income"] = round(row["income"], 2)
        row["expense"] = round(row["expense"], 2)
        row["net"] = round(row["income"] - row["expense"], 2)
    return rows
