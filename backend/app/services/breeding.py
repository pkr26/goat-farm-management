"""Breeding."""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import (
    MAX_FAILED_CYCLES_BEFORE_CULL,
    Animal,
    AnimalStatus,
    BreedingMethod,
    BreedingOutcome,
    BreedingRecord,
    Bucket,
    Farm,
    KiddingRecord,
    TaskCategory,
    TaskStatus,
    expected_kidding_date,
    planned_ultrasound_date,
)
from ..utils import today, utcnow
from ._common import _add_task, _kidding_record_of, _load_doe, _pending_tasks_for
from .animals import move_animal


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
    # Only the doe ids matter — a scalar join, not full ORM rows.
    open_result = await db.execute(
        select(BreedingRecord.doe_id, BreedingRecord.outcome, KiddingRecord.id)
        .outerjoin(KiddingRecord, KiddingRecord.breeding_record_id == BreedingRecord.id)
        .where(
            BreedingRecord.farm_id == farm.id,
            BreedingRecord.outcome.in_(
                [BreedingOutcome.PENDING.value, BreedingOutcome.CONFIRMED_PREGNANT.value]
            ),
        )
    )
    busy_doe_ids = {
        doe_id
        for doe_id, outcome, kidding_id in open_result.all()
        if outcome == BreedingOutcome.PENDING.value or kidding_id is None
    }
    reference_date = today(farm.timezone)
    return [
        d
        for d in does
        if is_breeding_candidate(
            d,
            has_open_breeding=d.id in busy_doe_ids,
            reference_date=reference_date,
        )
    ]


def is_breeding_candidate(
    doe: Animal, *, has_open_breeding: bool, reference_date: date | None = None
) -> bool:
    """Canonical picker and write-path predicate for a doe.

    Re-service after a failed cycle is allowed from BREEDING, but it retains
    the exact same age/weight/pregnancy requirements as first service.
    """
    return not has_open_breeding and doe.is_breeding_eligible_on(reference_date or today())


async def doe_has_open_breeding(db: AsyncSession, doe_id: int) -> bool:
    """A PENDING, or confirmed-but-not-yet-kidded, breeding exists for the doe."""
    result = await db.execute(
        select(BreedingRecord.outcome, KiddingRecord.id)
        .outerjoin(KiddingRecord, KiddingRecord.breeding_record_id == BreedingRecord.id)
        .where(
            BreedingRecord.doe_id == doe_id,
            BreedingRecord.outcome.in_(
                [BreedingOutcome.PENDING.value, BreedingOutcome.CONFIRMED_PREGNANT.value]
            ),
        )
    )
    return any(
        outcome == BreedingOutcome.PENDING.value or kidding_id is None
        for outcome, kidding_id in result.all()
    )


async def create_breeding_record(
    db: AsyncSession,
    farm: Farm,
    doe: Animal,
    buck: Animal,
    breeding_date: date,
    heat_cycle_number: int = 1,
    created_by_id: int | None = None,
) -> BreedingRecord:
    for animal, role in ((doe, "Doe"), (buck, "Buck")):
        if animal.effective_dob and breeding_date < animal.effective_dob:
            raise ValueError(f"{role} breeding chronology cannot predate its recorded birth date")
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
    db: AsyncSession,
    br: BreedingRecord,
    pregnant: bool,
    kid_count: int | None = None,
    result_date: date | None = None,
    created_by_id: int | None = None,
) -> BreedingRecord:
    """Record ultrasound outcome. Pregnant → CONFIRMED_PREGNANT + 3 follow-up
    tasks + move to PREGNANCY_EARLY. Not pregnant → FAILED + cull check.
    The closed ULTRASOUND duty is attributed to the acting user.

    Idempotent: only a PENDING record accepts a result — a double submission
    (or forged replay) must not spawn a second set of follow-up tasks. The
    caller must hold the breeding row's FOR UPDATE lock (and the doe's, in
    that order — animal → breeding → task is the canonical lock order), so
    this PENDING re-check runs against the latest committed state.
    A sold/dead doe cannot carry a pregnancy: her lingering PENDING record
    rejects the result instead of spawning tasks for a missing animal
    (mirrors record_kidding's ACTIVE guard)."""
    if br.outcome != BreedingOutcome.PENDING.value:
        return br
    doe = await _load_doe(db, br)
    if doe.status != AnimalStatus.ACTIVE.value:
        raise ValueError(
            f"{doe.tag_number} is {doe.status.lower()} — cannot record an ultrasound result"
        )
    if (
        result_date is not None
        and br.ultrasound_date is not None
        and result_date < br.ultrasound_date
    ):
        raise ValueError("Ultrasound result cannot predate its planned check date")
    br.ultrasound_done = True
    br.ultrasound_result_date = result_date
    br.pregnant = pregnant
    br.kid_count_detected = kid_count if pregnant else None

    # Locked + re-checked like skip_pending_tasks_for_animal: a concurrently
    # committed skip of the ultrasound duty must survive, never flip to DONE.
    for task in await _pending_tasks_for(
        db,
        br.farm_id,
        for_update=True,
        breeding_record_id=br.id,
        category=TaskCategory.ULTRASOUND.value,
    ):
        if task.status == TaskStatus.PENDING.value:
            task.status = TaskStatus.DONE.value
            task.completed_by_id = created_by_id
            task.completed_at = utcnow()

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
    the fact (that falsifies stats and wrongly removes the doe from
    RECOVERY). The caller must hold the breeding row's FOR UPDATE lock (and
    the doe's, in that order — animal → breeding → task), so both re-checks
    below run against the latest committed state."""
    if br.outcome != BreedingOutcome.CONFIRMED_PREGNANT.value:
        return br
    if await _kidding_record_of(db, br) is not None:
        return br
    br.outcome = BreedingOutcome.ABORTED.value
    br.pregnant = False
    move_animal(db, await _load_doe(db, br), Bucket.RESTING.value, "Pregnancy aborted")
    # Locked + re-checked like skip_pending_tasks_for_animal: a worker's
    # committed DONE completion must survive — never overwrite it to SKIPPED.
    for task in await _pending_tasks_for(db, br.farm_id, for_update=True, breeding_record_id=br.id):
        if task.status == TaskStatus.PENDING.value:
            task.status = TaskStatus.SKIPPED.value
            task.skipped_by_id = None
            task.skipped_at = utcnow()
            task.skip_reason = "Pregnancy aborted"
    await db.flush()
    return br
