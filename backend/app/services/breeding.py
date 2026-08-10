"""Breeding."""

from datetime import date, timedelta
from typing import Literal

from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ..models import (
    BREEDING_READY_BUCKETS,
    MAX_FAILED_CYCLES_BEFORE_CULL,
    MAX_GESTATION_DAYS,
    MIN_BREEDING_AGE_MONTHS,
    MIN_BREEDING_WEIGHT_KG,
    MIN_BUCK_BREEDING_AGE_MONTHS,
    MIN_BUCK_BREEDING_WEIGHT_KG,
    PREGNANCY_LOSS_CAUSES,
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
    WeightRecord,
    expected_kidding_date,
    planned_ultrasound_date,
)
from ..utils import add_months, today, utcnow
from ._common import (
    _add_task,
    _clear_task_rejection,
    _kidding_record_of,
    _load_doe,
    _pending_tasks_for,
)
from .animals import move_animal
from .health import PRE_KIDDING_VACCINE_TITLE

BreedingCandidateKind = Literal["doe", "buck"]

# Upper bound of BreedingRecord.heat_cycle_number (ck_breeding_records_heat_cycle
# and the BreedingCreateIn Field bound both cap the column at 99).
MAX_HEAT_CYCLE_NUMBER = 99


def _latest_weight_as_of(reference_date: date) -> ColumnElement[float]:
    """Correlated SQL equivalent of ``Animal.latest_weight_kg_on``."""
    latest_recorded = (
        select(WeightRecord.weight_kg)
        .where(
            WeightRecord.animal_id == Animal.id,
            WeightRecord.date <= reference_date,
        )
        .order_by(WeightRecord.date.desc(), WeightRecord.id.desc())
        .limit(1)
        .correlate(Animal)
        .scalar_subquery()
    )
    effective_dob = func.coalesce(Animal.date_of_birth, Animal.estimated_dob)
    birth_weight_available = case(
        (effective_dob <= reference_date, Animal.birth_weight),
        else_=None,
    )
    return func.coalesce(latest_recorded, birth_weight_available)


def _candidate_filters(
    farm_id: int,
    kind: BreedingCandidateKind,
    reference_date: date,
) -> tuple[ColumnElement[bool], ...]:
    """SQL equivalent of the canonical doe/buck eligibility predicates."""
    effective_dob = func.coalesce(Animal.date_of_birth, Animal.estimated_dob)
    common: tuple[ColumnElement[bool], ...] = (
        Animal.farm_id == farm_id,
        Animal.status == AnimalStatus.ACTIVE.value,
        Animal.movement_restricted.is_(False),
        Animal.suspected_scheduled_disease.is_(False),
    )
    if kind == "buck":
        age_cutoff = add_months(reference_date, -MIN_BUCK_BREEDING_AGE_MONTHS)
        return (
            *common,
            Animal.sex == "M",
            Animal.current_bucket.in_([Bucket.FOUNDATION.value, Bucket.BREEDING.value]),
            effective_dob.is_not(None),
            effective_dob <= age_cutoff,
            _latest_weight_as_of(reference_date) >= MIN_BUCK_BREEDING_WEIGHT_KG,
        )

    age_cutoff = add_months(reference_date, -MIN_BREEDING_AGE_MONTHS)
    unresolved_pregnancy = (
        select(BreedingRecord.id)
        .outerjoin(KiddingRecord, KiddingRecord.breeding_record_id == BreedingRecord.id)
        .where(
            BreedingRecord.farm_id == farm_id,
            BreedingRecord.doe_id == Animal.id,
            or_(
                BreedingRecord.outcome == BreedingOutcome.PENDING.value,
                and_(
                    BreedingRecord.outcome == BreedingOutcome.CONFIRMED_PREGNANT.value,
                    KiddingRecord.id.is_(None),
                ),
            ),
        )
        .correlate(Animal)
        .exists()
    )
    return (
        *common,
        Animal.sex == "F",
        Animal.current_bucket.in_(
            [*(bucket.value for bucket in BREEDING_READY_BUCKETS), Bucket.BREEDING.value]
        ),
        effective_dob.is_not(None),
        effective_dob <= age_cutoff,
        _latest_weight_as_of(reference_date) >= MIN_BREEDING_WEIGHT_KG,
        ~unresolved_pregnancy,
    )


def _literal_candidate_search(q: str | None) -> ColumnElement[bool] | None:
    needle = (q or "").strip()
    if not needle:
        return None
    escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    return or_(
        Animal.tag_number.ilike(pattern, escape="\\"),
        Animal.name.ilike(pattern, escape="\\"),
    )


def _candidate_ids_stmt(
    farm_id: int,
    kind: BreedingCandidateKind,
    reference_date: date,
    q: str | None = None,
) -> Select[tuple[int]]:
    filters = list(_candidate_filters(farm_id, kind, reference_date))
    search = _literal_candidate_search(q)
    if search is not None:
        filters.append(search)
    return select(Animal.id).where(*filters)


async def breeding_candidate_page(
    db: AsyncSession,
    farm: Farm,
    kind: BreedingCandidateKind,
    *,
    q: str | None,
    limit: int,
    offset: int,
    reference_date: date | None = None,
) -> tuple[list[tuple[Animal, float | None]], int]:
    """Return one bounded eligible page and the full matching SQL count.

    The latest applicable weight is selected as one scalar per page row.
    Lifetime weight histories never cross the database boundary; unresolved
    pregnancy and all eligibility filtering remain in SQL.
    """
    when = reference_date or today(farm.timezone)
    id_stmt = _candidate_ids_stmt(farm.id, kind, when, q)
    total = (await db.execute(select(func.count()).select_from(id_stmt.subquery()))).scalar_one()
    filters = list(_candidate_filters(farm.id, kind, when))
    search = _literal_candidate_search(q)
    if search is not None:
        filters.append(search)
    latest_weight = _latest_weight_as_of(when).label("latest_weight_kg")
    result = await db.execute(
        select(Animal, latest_weight)
        .where(*filters)
        .order_by(Animal.tag_number, Animal.id)
        .offset(offset)
        .limit(limit)
    )
    return [(animal, weight) for animal, weight in result.all()], total


async def breeding_candidate_counts(
    db: AsyncSession,
    farm: Farm,
    reference_date: date | None = None,
) -> tuple[int, int]:
    """Count eligible does and bucks in one bounded aggregate query."""
    when = reference_date or today(farm.timezone)
    doe_count = (
        select(func.count())
        .select_from(_candidate_ids_stmt(farm.id, "doe", when).subquery())
        .scalar_subquery()
    )
    buck_count = (
        select(func.count())
        .select_from(_candidate_ids_stmt(farm.id, "buck", when).subquery())
        .scalar_subquery()
    )
    row = (await db.execute(select(doe_count, buck_count))).one()
    return int(row[0]), int(row[1])


def is_breeding_candidate(
    doe: Animal,
    *,
    latest_weight_kg: float | None,
    has_open_breeding: bool,
    reference_date: date,
) -> bool:
    """Canonical picker and write-path predicate for a doe.

    Re-service after a failed cycle is allowed from BREEDING, but it retains
    the exact same age/weight/pregnancy requirements as first service.
    """
    age = doe.age_months_on(reference_date)
    return bool(
        not has_open_breeding
        and doe.sex == "F"
        and doe.status == AnimalStatus.ACTIVE.value
        and not doe.movement_restricted
        and not doe.suspected_scheduled_disease
        and doe.current_bucket in {*BREEDING_READY_BUCKETS, Bucket.BREEDING.value}
        and age is not None
        and age >= MIN_BREEDING_AGE_MONTHS
        and latest_weight_kg is not None
        and latest_weight_kg >= MIN_BREEDING_WEIGHT_KG
    )


def is_buck_breeding_candidate(
    buck: Animal, *, latest_weight_kg: float | None, reference_date: date
) -> bool:
    """Canonical sire predicate using a bounded latest-weight scalar."""
    age = buck.age_months_on(reference_date)
    return bool(
        buck.sex == "M"
        and buck.status == AnimalStatus.ACTIVE.value
        and not buck.movement_restricted
        and not buck.suspected_scheduled_disease
        and buck.current_bucket in {Bucket.FOUNDATION.value, Bucket.BREEDING.value}
        and age is not None
        and age >= MIN_BUCK_BREEDING_AGE_MONTHS
        and latest_weight_kg is not None
        and latest_weight_kg >= MIN_BUCK_BREEDING_WEIGHT_KG
    )


async def breeding_weights_as_of(
    db: AsyncSession, animal_ids: list[int], reference_date: date
) -> dict[int, float | None]:
    """Return one latest applicable weight scalar for each bounded animal id."""
    rows = (
        await db.execute(
            select(Animal.id, _latest_weight_as_of(reference_date).label("latest_weight_kg"))
            .where(Animal.id.in_(animal_ids))
            .order_by(Animal.id)
        )
    ).all()
    return {animal_id: weight for animal_id, weight in rows}


async def doe_has_open_breeding(db: AsyncSession, farm_id: int, doe_id: int) -> bool:
    """A PENDING, or confirmed-but-not-yet-kidded, breeding exists for the doe."""
    existing_id = (
        await db.execute(
            select(BreedingRecord.id)
            .outerjoin(KiddingRecord, KiddingRecord.breeding_record_id == BreedingRecord.id)
            .where(
                BreedingRecord.farm_id == farm_id,
                BreedingRecord.doe_id == doe_id,
                or_(
                    BreedingRecord.outcome == BreedingOutcome.PENDING.value,
                    and_(
                        BreedingRecord.outcome == BreedingOutcome.CONFIRMED_PREGNANT.value,
                        KiddingRecord.id.is_(None),
                    ),
                ),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return existing_id is not None


async def _latest_doe_reproductive_boundary(
    db: AsyncSession, farm_id: int, doe_id: int
) -> date | None:
    """Latest factual boundary after which a new service may be recorded.

    The normal breeding endpoint is an operational append-only workflow, not
    a historical insertion API.  A completed failed cycle ends at its actual
    ultrasound result, a lost pregnancy at its loss date, and a delivered
    pregnancy at the kidding date.  Reading all maxima in one statement keeps
    the check constant-query even for a doe with a long reproductive history.

    The caller holds the doe row lock. Ultrasound, loss, and kidding writes
    take that same lock first, so no concurrent terminal fact can appear
    between this snapshot and the new breeding insert.
    """
    breeding_facts = (
        select(
            func.max(BreedingRecord.breeding_date).label("latest_breeding_date"),
            func.max(BreedingRecord.ultrasound_result_date).label("latest_ultrasound_result_date"),
            func.max(BreedingRecord.loss_date).label("latest_loss_date"),
        )
        .where(
            BreedingRecord.farm_id == farm_id,
            BreedingRecord.doe_id == doe_id,
        )
        .subquery()
    )
    latest_kidding = (
        select(func.max(KiddingRecord.date))
        .where(KiddingRecord.farm_id == farm_id, KiddingRecord.doe_id == doe_id)
        .scalar_subquery()
    )
    row = (
        await db.execute(
            select(
                breeding_facts.c.latest_breeding_date,
                breeding_facts.c.latest_ultrasound_result_date,
                breeding_facts.c.latest_loss_date,
                latest_kidding.label("latest_kidding_date"),
            )
        )
    ).one()
    facts = [value for value in row if value is not None]
    return max(facts) if facts else None


async def derived_heat_cycle_number(db: AsyncSession, farm_id: int, doe_id: int) -> int:
    """Cycle index of the doe's next service, derived from her own history.

    A re-service belongs to cycle N+1 only while the preceding cycles FAILED;
    a confirmed pregnancy (kidded or lost) ends the run and the next service
    starts a fresh cycle 1. Client input is not trusted here — no client ever
    sent the field, so every stored record claimed cycle 1 and the reports'
    first-cycle metric degenerated into the overall conception rate. The scan
    is bounded by the column's own 1..99 CHECK.
    """
    outcomes = list(
        (
            await db.execute(
                select(BreedingRecord.outcome)
                .where(
                    BreedingRecord.farm_id == farm_id,
                    BreedingRecord.doe_id == doe_id,
                    BreedingRecord.outcome != BreedingOutcome.PENDING.value,
                )
                .order_by(BreedingRecord.breeding_date.desc(), BreedingRecord.id.desc())
                .limit(MAX_HEAT_CYCLE_NUMBER - 1)
            )
        ).scalars()
    )
    failed_streak = 0
    for outcome in outcomes:
        if outcome != BreedingOutcome.FAILED.value:
            break
        failed_streak += 1
    return min(failed_streak + 1, MAX_HEAT_CYCLE_NUMBER)


async def create_breeding_record(
    db: AsyncSession,
    farm: Farm,
    doe: Animal,
    buck: Animal,
    breeding_date: date,
    created_by_id: int | None = None,
    *,
    doe_latest_weight_kg: float | None,
    has_open_breeding: bool,
) -> BreedingRecord:
    for animal, role in ((doe, "Doe"), (buck, "Buck")):
        if animal.effective_dob and breeding_date < animal.effective_dob:
            raise ValueError(f"{role} breeding chronology cannot predate its recorded birth date")
        if animal.purchase_date and breeding_date < animal.purchase_date:
            raise ValueError(
                f"{role} breeding chronology cannot predate its recorded purchase date"
            )
    if has_open_breeding:
        raise ValueError(f"{doe.tag_number} already has an unresolved breeding/pregnancy")
    latest_boundary = await _latest_doe_reproductive_boundary(db, farm.id, doe.id)
    if latest_boundary is not None and breeding_date <= latest_boundary:
        raise ValueError(
            f"Breeding date must be after {doe.tag_number}'s latest reproductive "
            f"event on {latest_boundary.isoformat()}"
        )
    # Deliberately no ordering check against bucket moves: moves are always
    # stamped with the day they were *recorded* (MoveIn carries no date), so a
    # breeding that physically happened before a same-day move legitimately
    # predates it. Rejecting that ordering made every backdated breeding —
    # which the schema explicitly supports — unrecordable after any move.
    ultrasound_date = planned_ultrasound_date(breeding_date)
    br = BreedingRecord(
        farm_id=farm.id,
        doe_id=doe.id,
        buck_id=buck.id,
        breeding_date=breeding_date,
        method=BreedingMethod.NATURAL.value,
        heat_cycle_number=await derived_heat_cycle_number(db, farm.id, doe.id),
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
    move_animal(
        db,
        doe,
        Bucket.BREEDING.value,
        "Bred",
        created_by_id=created_by_id,
        context="breeding",
        reference_date=breeding_date,
        facts=(doe_latest_weight_kg, False),
    )
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
    if result_date is not None and result_date < br.breeding_date:
        raise ValueError("Pregnancy check result cannot predate the breeding date")
    if (
        pregnant
        and result_date is not None
        and result_date > br.breeding_date + timedelta(days=MAX_GESTATION_DAYS)
    ):
        # Kidding is deliberately bounded to the same maximum gestation and
        # cannot predate its confirmation. Accepting a positive scan after that
        # boundary creates no possible kidding date and permanently strands the
        # doe in an unresolved pregnancy.
        raise ValueError(
            "A positive pregnancy check cannot be recorded after the maximum "
            f"{MAX_GESTATION_DAYS}-day gestation window"
        )
    if (
        result_date is not None
        and br.ultrasound_date is not None
        and result_date < br.ultrasound_date
    ):
        # A scan cannot confirm a pregnancy before the planned check window, so
        # a positive result still has to wait for it. A NEGATIVE result can be
        # factual much earlier: the heat cycle is ~21 days, and a doe seen back
        # in standing heat is evidence the service did not hold. Recording that
        # brings the cycle's check forward to the day it was actually observed
        # instead of forcing the operator to falsify a day-32 scan date (which
        # then pushed the true re-service ~12 days late, shifting the expected
        # kidding date and every duty derived from it).
        if pregnant:
            raise ValueError("Ultrasound result cannot predate its planned check date")
        br.ultrasound_date = result_date
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
            _clear_task_rejection(task)

    if pregnant:
        br.outcome = BreedingOutcome.CONFIRMED_PREGNANT.value
        doe.cull_candidate = False  # she conceived — previous failures forgiven
        ekd = expected_kidding_date(br.breeding_date)
        br.expected_kidding_date = ekd
        move_animal(
            db,
            doe,
            Bucket.PREGNANCY_EARLY.value,
            "Ultrasound confirmed pregnant",
            created_by_id=created_by_id,
            context="ultrasound",
            reference_date=result_date or today(),
            # A disease hold prevents physical/manual transfer, but the
            # authoritative pregnancy fact and its feed/lifecycle cohort must
            # be committed atomically in the same transaction.
            allow_restricted_reclassification=True,
        )
        await _add_task(
            db,
            br.farm_id,
            f"{PRE_KIDDING_VACCINE_TITLE}: {doe.tag_number}",
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


async def mark_unassessed(
    db: AsyncSession, br: BreedingRecord, *, closed_by_id: int
) -> BreedingRecord:
    """Close a service that can never be assessed because the doe left the herd.

    A PENDING record is an open question — "did this service take?" — and only
    an ultrasound answers it. ``record_ultrasound_result`` refuses a
    SOLD/DEAD/CULLED doe, so without a terminal state the row stayed PENDING
    forever: it held the doe's ``uq_breeding_open_pregnancy`` slot and the UI
    kept offering an "Ultrasound result" action that could only ever 409.

    UNASSESSED records exactly that the question went unanswered. Nothing is
    invented about the pregnancy (``ultrasound_done`` stays false, ``pregnant``
    stays NULL), so the row is neither a conception nor a failure to conceive —
    unlike FAILED, which would assert a negative scan that never happened.

    Only a PENDING record moves: a confirmed pregnancy is resolved by
    ``mark_aborted``, and every other outcome is already terminal. The caller
    must hold the doe's and the breeding row's FOR UPDATE locks (animal →
    breeding → task is the canonical lock order).
    """
    if br.outcome != BreedingOutcome.PENDING.value:
        return br
    doe = await _load_doe(db, br)
    if doe.status == AnimalStatus.ACTIVE.value:
        raise ValueError(
            f"{doe.tag_number} is still in the herd — record the ultrasound result instead"
        )
    br.outcome = BreedingOutcome.UNASSESSED.value
    # Locked + re-checked like mark_aborted: a worker's committed DONE
    # completion must survive — never overwrite it to SKIPPED.
    for task in await _pending_tasks_for(db, br.farm_id, for_update=True, breeding_record_id=br.id):
        if task.status == TaskStatus.PENDING.value:
            task.status = TaskStatus.SKIPPED.value
            task.skipped_by_id = closed_by_id
            task.skipped_at = utcnow()
            task.skip_reason = "Doe left the herd before the pregnancy check"
            _clear_task_rejection(task)
    await db.flush()
    return br


async def _update_cull_candidate(db: AsyncSession, doe: Animal) -> None:
    """2 consecutive FAILED cycles → cull candidate flag (per SPEC)."""
    result = await db.execute(
        select(BreedingRecord.outcome)
        .where(
            BreedingRecord.doe_id == doe.id,
            BreedingRecord.outcome != BreedingOutcome.PENDING.value,
        )
        .order_by(BreedingRecord.breeding_date.desc(), BreedingRecord.id.desc())
        .limit(MAX_FAILED_CYCLES_BEFORE_CULL)
    )
    recent_outcomes = list(result.scalars())
    if len(recent_outcomes) == MAX_FAILED_CYCLES_BEFORE_CULL and all(
        outcome == BreedingOutcome.FAILED.value for outcome in recent_outcomes
    ):
        doe.cull_candidate = True


async def mark_aborted(
    db: AsyncSession,
    br: BreedingRecord,
    *,
    loss_date: date,
    loss_cause: str,
    loss_notes: str | None,
    recorded_by_id: int,
) -> BreedingRecord:
    """Record an attributed pregnancy loss and close its operational work.

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
    if loss_cause not in PREGNANCY_LOSS_CAUSES:
        raise ValueError("Unknown pregnancy loss cause")
    if loss_date < br.breeding_date:
        raise ValueError("Pregnancy loss date cannot be before the breeding date")
    if br.ultrasound_result_date is not None and loss_date < br.ultrasound_result_date:
        raise ValueError("Pregnancy loss date cannot be before pregnancy confirmation")
    clean_notes = (loss_notes or "").strip() or None
    if clean_notes is not None and len(clean_notes) > 4_000:
        raise ValueError("Pregnancy loss notes cannot exceed 4000 characters")

    br.outcome = BreedingOutcome.ABORTED.value
    br.pregnant = False
    br.loss_date = loss_date
    br.loss_cause = loss_cause
    br.loss_notes = clean_notes
    br.loss_recorded_by_id = recorded_by_id
    br.loss_recorded_at = utcnow()
    doe = await _load_doe(db, br)
    if doe.status == AnimalStatus.ACTIVE.value:
        move_animal(
            db,
            doe,
            Bucket.RESTING.value,
            "Pregnancy aborted",
            created_by_id=recorded_by_id,
            context="abortion",
            reference_date=loss_date,
            # This is a domain reclassification caused by the recorded loss,
            # not a health clearance or an operator-requested movement.
            allow_restricted_reclassification=True,
        )
    # Locked + re-checked like skip_pending_tasks_for_animal: a worker's
    # committed DONE completion must survive — never overwrite it to SKIPPED.
    for task in await _pending_tasks_for(db, br.farm_id, for_update=True, breeding_record_id=br.id):
        if task.status == TaskStatus.PENDING.value:
            task.status = TaskStatus.SKIPPED.value
            task.skipped_by_id = recorded_by_id
            task.skipped_at = utcnow()
            task.skip_reason = "Pregnancy aborted"
            _clear_task_rejection(task)
    await db.flush()
    return br
