"""Health: events log, record event (single animal / bucket / purchase
batch), per-animal vaccination schedule from seeded templates.

Port of v1 app/routers/health.py."""

from datetime import date
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import exists, false, func, or_, select
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from ..deps import CurrentFarm, CurrentMembership, CurrentUser, DbSession, require_perm
from ..models import (
    MAX_WITHDRAWAL_DAYS,
    Animal,
    AnimalStatus,
    Bucket,
    Farm,
    HealthEvent,
    HealthEventType,
    HealthRound,
    HealthRoundCoverage,
    HealthRoundExclusion,
    HealthRoundTarget,
    MovementRestrictionAction,
    PurchaseBatch,
    Task,
    TaskCategory,
    TaskStatus,
    VaccineTemplate,
)
from ..schemas.animals import BucketStr
from ..schemas.common import (
    COMMON_ERROR_RESPONSES,
    MAX_INT32_ID,
    MAX_PAGE_OFFSET,
    PostgresText,
    lifecycle_conflict,
    stale_state_conflict,
    standing_quota,
)
from ..schemas.health import (
    MAX_BULK_BUCKET_TARGETS,
    MAX_BULK_HEALTH_TARGETS,
    HealthAnimalOptionListOut,
    HealthAnimalOptionOut,
    HealthBulkTargetIn,
    HealthBulkTargetPreviewOut,
    HealthEventIn,
    HealthEventListOut,
    HealthEventMutationOut,
    HealthEventOut,
    HealthPurchaseBatchOptionListOut,
    HealthPurchaseBatchOptionOut,
    HealthRoundOut,
    HealthRoundTargetChangeIn,
    HealthRoundTargetOut,
    MovementRestrictionActionOut,
    MovementRestrictionClearIn,
    MovementRestrictionHistoryOut,
    ScheduleOut,
    ScheduleRowOut,
    ScheduleTemplateListOut,
    ScheduleTemplateOut,
)
from ..schemas.summaries import AnimalIdentityOut
from ..services import (
    IdempotencyKey,
    canonical_target_for_task,
    complete_task,
    execute_idempotent,
    inferred_schedule_template,
    lock_manual_task_queue,
    preferred_template_for_target,
    record_health_event,
    require_animal_event_chronology,
    require_farm_not_future,
    target_matches_task,
    target_matches_template,
    template_names_for_task,
    vaccination_schedule_for_animal,
    validated_template,
)
from ..services.health_rounds import (
    add_round_coverage,
    ensure_round_snapshot,
    is_herd_round,
    recorded_components,
    require_round_targets,
    round_counts,
    round_is_complete,
)
from ..utils import today, utcnow
from ._shared import sms_safe_text, visible_to

router = APIRouter(prefix="/api/health", tags=["health"], responses=COMMON_ERROR_RESPONSES)

VIEW = Annotated[set[str], Depends(require_perm("health.view"))]
MANAGE = Annotated[set[str], Depends(require_perm("health.manage"))]

HEALTH_LOOKUP_DEFAULT_LIMIT = 50
HEALTH_LOOKUP_MAX_LIMIT = 100


async def _round_task(
    db: DbSession, farm: CurrentFarm, task_id: int, *, lock: bool = False
) -> Task:
    query = select(Task).where(Task.farm_id == farm.id, Task.id == task_id)
    if lock:
        query = query.with_for_update()
    task = (await db.execute(query)).scalar_one_or_none() if 1 <= task_id <= MAX_INT32_ID else None
    if task is None or not is_herd_round(task):
        raise HTTPException(status_code=404, detail="Herd health duty not found")
    return task


async def _round_out(
    db: DbSession, task: Task, *, limit: int = 100, offset: int = 0
) -> HealthRoundOut:
    round_ = await db.get(HealthRound, task.id)
    components = (
        round_.required_components
        if round_ is not None
        else list(template_names_for_task(task.title, task.category))
    )
    total, excluded, covered, remaining = (
        await round_counts(db, round_) if round_ is not None else (0, 0, 0, 0)
    )
    available = (
        await db.execute(
            select(func.count())
            .select_from(Animal)
            .where(
                Animal.farm_id == task.farm_id,
                Animal.status == AnimalStatus.ACTIVE.value,
                ~exists().where(
                    HealthRoundTarget.task_id == task.id, HealthRoundTarget.animal_id == Animal.id
                ),
            )
        )
    ).scalar_one()
    targets: list[HealthRoundTargetOut] = []
    if round_ is not None:
        rows = (
            await db.execute(
                select(HealthRoundTarget, Animal, HealthRoundExclusion)
                .join(Animal, Animal.id == HealthRoundTarget.animal_id)
                .outerjoin(
                    HealthRoundExclusion,
                    (HealthRoundExclusion.task_id == HealthRoundTarget.task_id)
                    & (HealthRoundExclusion.animal_id == HealthRoundTarget.animal_id),
                )
                .where(HealthRoundTarget.task_id == task.id)
                .order_by(HealthRoundTarget.animal_id)
                .limit(limit)
                .offset(offset)
            )
        ).all()
        coverage: dict[int, list[str]] = {}
        if rows:
            for animal_id, component in (
                await db.execute(
                    select(HealthRoundCoverage.animal_id, HealthRoundCoverage.component).where(
                        HealthRoundCoverage.task_id == task.id,
                        HealthRoundCoverage.animal_id.in_([row[0].animal_id for row in rows]),
                    )
                )
            ).all():
                coverage.setdefault(animal_id, []).append(component)
        for target, animal, exclusion in rows:
            targets.append(
                HealthRoundTargetOut(
                    animal_id=animal.id,
                    animal_tag=animal.tag_number,
                    animal_status=animal.status,
                    current_bucket=animal.current_bucket,
                    covered_components=sorted(coverage.get(animal.id, [])),
                    exclusion_reason=exclusion.reason if exclusion is not None else None,
                    excluded_by_id=exclusion.recorded_by_id if exclusion is not None else None,
                    excluded_at=exclusion.recorded_at if exclusion is not None else None,
                    inclusion_reason=target.inclusion_reason,
                    added_at=target.added_at,
                )
            )
    return HealthRoundOut(
        task_id=task.id,
        task_status=task.status,
        initialized=round_ is not None,
        snapshot_at=round_.snapshot_at if round_ is not None else None,
        required_components=components,
        total_targets=total,
        excluded_targets=excluded,
        covered_targets=covered,
        remaining_units=remaining,
        available_additions=available,
        targets=targets,
        limit=limit,
        offset=offset,
    )


@router.get("/rounds/{task_id}")
async def health_round_progress(
    task_id: int,
    db: DbSession,
    farm: CurrentFarm,
    _perms: VIEW,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
) -> HealthRoundOut:
    """Read-only progress; legacy completed duties have no invented cohort."""
    return await _round_out(db, await _round_task(db, farm, task_id), limit=limit, offset=offset)


async def _writable_round_task(
    db: DbSession,
    farm: CurrentFarm,
    task_id: int,
    user: CurrentUser,
    membership: CurrentMembership,
) -> Task:
    task = await _round_task(db, farm, task_id, lock=True)
    if task.status != TaskStatus.PENDING.value:
        raise lifecycle_conflict(detail="Only a pending health round can change")
    if not await visible_to(db, task, user, farm, membership, lock_assignee=True):
        raise HTTPException(status_code=403, detail="This duty is not assigned to you")
    return task


@router.post("/rounds/{task_id}/start")
async def start_health_round(
    task_id: int,
    db: DbSession,
    farm: CurrentFarm,
    user: CurrentUser,
    membership: CurrentMembership,
    _perms: MANAGE,
) -> HealthRoundOut:
    # Snapshot foreign keys take Animal KEY SHARE locks. Acquire them before
    # the Task, matching lifecycle writers, so another pen's status/event
    # mutation cannot hold Animal while waiting for our Task lock.
    await lock_manual_task_queue(db, farm)
    await db.execute(
        select(Animal.id)
        .where(Animal.farm_id == farm.id, Animal.status == AnimalStatus.ACTIVE.value)
        .order_by(Animal.id)
        .with_for_update(read=True, key_share=True)
    )
    task = await _writable_round_task(db, farm, task_id, user, membership)
    try:
        await ensure_round_snapshot(db, task)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await db.commit()
    return await _round_out(db, task)


async def _change_round_targets(
    task_id: int,
    payload: HealthRoundTargetChangeIn,
    db: DbSession,
    farm: CurrentFarm,
    user: CurrentUser,
    membership: CurrentMembership,
    *,
    exclude: bool,
) -> HealthRoundOut:
    # Match the event and lifecycle writers' ANIMAL -> TASK order. A recurring
    # task can spawn a successor, so take the queue mutex before either lock.
    await lock_manual_task_queue(db, farm)
    animals = (
        (
            await db.execute(
                select(Animal)
                .where(Animal.farm_id == farm.id, Animal.id.in_(payload.animal_ids))
                .order_by(Animal.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    if len(animals) != len(payload.animal_ids):
        raise HTTPException(status_code=404, detail="Round animal not found")
    task = await _writable_round_task(db, farm, task_id, user, membership)
    round_ = await db.get(HealthRound, task.id)
    if exclude and task.due_date > today(farm.timezone):
        # An exclusion can itself finish the round. Apply the same due-date
        # fence as linked treatment evidence before changing its cohort.
        raise lifecycle_conflict(detail="This linked health duty is not due yet")
    if round_ is None:
        raise stale_state_conflict(detail="Start the herd round before changing targets")
    for animal in animals:
        target = await db.get(HealthRoundTarget, (task.id, animal.id))
        if exclude:
            if target is None:
                raise stale_state_conflict(detail="Animal is not a declared round target")
            previous = await db.get(HealthRoundExclusion, (task.id, animal.id))
            if previous is not None:
                if previous.reason != payload.reason or previous.recorded_by_id != user.id:
                    raise stale_state_conflict(detail="Round exclusion is already recorded")
                continue
            db.add(
                HealthRoundExclusion(
                    farm_id=farm.id,
                    task_id=task.id,
                    animal_id=animal.id,
                    reason=payload.reason,
                    recorded_by_id=user.id,
                )
            )
        else:
            if animal.status != AnimalStatus.ACTIVE.value:
                raise lifecycle_conflict(detail="Only an active animal can join a round")
            if target is not None:
                if target.inclusion_reason == payload.reason and target.added_by_id == user.id:
                    continue
                raise stale_state_conflict(detail="Animal is already a round target")
            db.add(
                HealthRoundTarget(
                    farm_id=farm.id,
                    task_id=task.id,
                    animal_id=animal.id,
                    inclusion_reason=payload.reason,
                    added_by_id=user.id,
                )
            )
    await db.flush()
    if exclude and await round_is_complete(db, round_):
        await complete_task(db, task, user)
    await db.commit()
    return await _round_out(db, task)


@router.post("/rounds/{task_id}/targets")
async def add_health_round_targets(
    task_id: int,
    payload: HealthRoundTargetChangeIn,
    db: DbSession,
    farm: CurrentFarm,
    user: CurrentUser,
    membership: CurrentMembership,
    _perms: MANAGE,
) -> HealthRoundOut:
    return await _change_round_targets(task_id, payload, db, farm, user, membership, exclude=False)


@router.post("/rounds/{task_id}/exclusions")
async def exclude_health_round_targets(
    task_id: int,
    payload: HealthRoundTargetChangeIn,
    db: DbSession,
    farm: CurrentFarm,
    user: CurrentUser,
    membership: CurrentMembership,
    _perms: MANAGE,
) -> HealthRoundOut:
    return await _change_round_targets(task_id, payload, db, farm, user, membership, exclude=True)


def _event_out(event: HealthEvent) -> HealthEventOut:
    """Out model + animal tag. `animal` must be selectinloaded (async
    forbids lazy loads)."""
    out = HealthEventOut.model_validate(event)
    out.animal_tag = event.animal.tag_number if event.animal else None
    return out


def _escaped_contains(raw: str) -> str:
    """Literal, case-insensitive SQL substring pattern (no wildcard injection)."""
    escaped = raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@router.get("/schedule-templates")
async def schedule_templates(
    db: DbSession,
    farm: CurrentFarm,
    _perms: VIEW,
) -> ScheduleTemplateListOut:
    """The seeded programme items a health event may cite.

    ``validated_template`` accepts ONLY an exact ``vaccine_templates.name`` for
    a VACCINE/DEWORMING event, and ``next_due_date`` requires a schedule name —
    so without this list the recording form was a free-text box whose every
    value 422s unless the operator already knew one of the seeded names.
    Fixed global reference data, scoped to the farm's species; the farm
    dependency keeps it behind the same tenant auth as the rest of the module.
    """
    rows = (await db.execute(select(VaccineTemplate).order_by(VaccineTemplate.name))).scalars()
    return ScheduleTemplateListOut(
        templates=[
            ScheduleTemplateOut(
                id=row.id,
                name=row.name,
                timing_note=row.timing_note,
                # Mirrors validated_template's rule: the deworming programme
                # belongs to DEWORMING events, every other item to VACCINE.
                event_type=(
                    HealthEventType.DEWORMING.value
                    if row.name == "Deworming"
                    else HealthEventType.VACCINE.value
                ),
            )
            for row in rows
        ]
    )


@router.get("/animals")
async def health_animal_options(
    db: DbSession,
    farm: CurrentFarm,
    _perms: VIEW,
    q: Annotated[PostgresText | None, Query(max_length=60)] = None,
    limit: Annotated[int, Query(ge=1, le=HEALTH_LOOKUP_MAX_LIMIT)] = (HEALTH_LOOKUP_DEFAULT_LIMIT),
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
) -> HealthAnimalOptionListOut:
    """Farm-local active-animal summaries for health schedules and events.

    A numeric query, optionally prefixed by ``#``, resolves an exact selected
    id without requiring access to the full animal profile endpoint.
    ``q`` semantics (2026-09-28 audit — they deliberately differ per
    router): ``#123`` resolves id 123 only; bare ``123`` matches the id OR
    a tag/name substring (purchases exact-matches bare digits too, animals
    never resolves an id).
    """
    stmt = select(Animal).where(
        Animal.farm_id == farm.id,
        Animal.status == AnimalStatus.ACTIVE.value,
    )
    if q and q.strip():
        raw = q.strip()
        numeric = raw.removeprefix("#")
        explicit_id = raw.startswith("#") and numeric.isascii() and numeric.isdigit()
        if explicit_id:
            stmt = stmt.where(
                Animal.id == int(numeric) if int(numeric) <= MAX_INT32_ID else false()
            )
        else:
            pattern = _escaped_contains(raw)
            matches: list[ColumnElement[bool]] = [
                Animal.tag_number.ilike(pattern, escape="\\"),
                Animal.name.ilike(pattern, escape="\\"),
            ]
            if numeric.isascii() and numeric.isdigit() and int(numeric) <= MAX_INT32_ID:
                matches.append(Animal.id == int(numeric))
            stmt = stmt.where(or_(*matches))

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    result = await db.execute(
        stmt.order_by(Animal.current_bucket, Animal.tag_number, Animal.id)
        .offset(offset)
        .limit(limit)
    )
    return HealthAnimalOptionListOut(
        animals=[
            HealthAnimalOptionOut(
                id=animal.id,
                tag_number=animal.tag_number,
                name=animal.name,
                # CHECK-constrained to the Bucket vocabulary.
                current_bucket=cast(BucketStr, animal.current_bucket),
                movement_restricted=bool(
                    animal.movement_restricted or animal.suspected_scheduled_disease
                ),
                restriction_version=animal.restriction_version,
            )
            for animal in result.scalars()
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/purchase-batches")
async def health_purchase_batch_options(
    db: DbSession,
    farm: CurrentFarm,
    _perms: MANAGE,
    q: Annotated[PostgresText | None, Query(max_length=20)] = None,
    limit: Annotated[int, Query(ge=1, le=HEALTH_LOOKUP_MAX_LIMIT)] = (HEALTH_LOOKUP_DEFAULT_LIMIT),
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
) -> HealthPurchaseBatchOptionListOut:
    """Opaque targetable batch ids without exposing the purchase ledger.

    Only the id needed by the health write and the current quarantine target
    count are returned. Supplier, purchase date, original count and price are
    procurement data and require ``purchases.view``. Search is deliberately
    exact-id-only so a health-only role cannot probe supplier names.
    """
    filters = [
        Animal.farm_id == farm.id,
        Animal.purchase_batch_id.is_not(None),
        Animal.status == AnimalStatus.ACTIVE.value,
        Animal.current_bucket == Bucket.QUARANTINE.value,
    ]
    if q and q.strip():
        raw = q.strip()
        numeric = raw.removeprefix("#")
        if numeric.isascii() and numeric.isdigit() and int(numeric) <= MAX_INT32_ID:
            filters.append(Animal.purchase_batch_id == int(numeric))
        else:
            filters.append(false())

    # Start at the small, live target set rather than scanning every historical
    # purchase and evaluating a correlated animal count for each.  The parent
    # join is retained as tenant-defense-in-depth even though composite FKs
    # also enforce the same farm ownership at the database boundary.
    targetable_batches = (
        select(
            Animal.purchase_batch_id.label("id"),
            func.count(Animal.id).label("active_quarantine_animal_count"),
        )
        .join(
            PurchaseBatch,
            (PurchaseBatch.id == Animal.purchase_batch_id) & (PurchaseBatch.farm_id == farm.id),
        )
        .where(*filters)
        .group_by(Animal.purchase_batch_id)
        .subquery()
    )
    total = (await db.execute(select(func.count()).select_from(targetable_batches))).scalar_one()
    result = await db.execute(
        select(
            targetable_batches.c.id,
            targetable_batches.c.active_quarantine_animal_count,
        )
        .order_by(targetable_batches.c.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return HealthPurchaseBatchOptionListOut(
        batches=[
            HealthPurchaseBatchOptionOut(
                id=batch_id,
                active_quarantine_animal_count=active_quarantine_animal_count,
            )
            for batch_id, active_quarantine_animal_count in result.all()
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/restrictions/{animal_id}")
async def movement_restriction_history(
    animal_id: int,
    db: DbSession,
    farm: CurrentFarm,
    _perms: VIEW,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
) -> MovementRestrictionHistoryOut:
    animal = (
        (
            await db.execute(
                select(Animal).where(Animal.id == animal_id, Animal.farm_id == farm.id)
            )
        ).scalar_one_or_none()
        if 1 <= animal_id <= MAX_INT32_ID
        else None
    )
    if animal is None:
        raise HTTPException(status_code=404, detail="Animal not found")
    action_filter = (
        MovementRestrictionAction.farm_id == farm.id,
        MovementRestrictionAction.animal_id == animal.id,
    )
    total = (
        await db.execute(
            select(func.count()).select_from(MovementRestrictionAction).where(*action_filter)
        )
    ).scalar_one()
    actions = list(
        (
            await db.execute(
                select(MovementRestrictionAction)
                .where(*action_filter)
                .order_by(
                    MovementRestrictionAction.acted_at.desc(),
                    MovementRestrictionAction.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
        ).scalars()
    )
    return MovementRestrictionHistoryOut(
        animal_id=animal.id,
        restriction_version=animal.restriction_version,
        active=bool(animal.movement_restricted or animal.suspected_scheduled_disease),
        actions=[MovementRestrictionActionOut.model_validate(action) for action in actions],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/restrictions/{animal_id}/clear", status_code=204)
async def clear_movement_restriction(
    animal_id: int,
    payload: MovementRestrictionClearIn,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: MANAGE,
) -> None:
    """Record a factual authority/veterinary clearance and release a hold.

    This does not diagnose or treat an animal. It only makes a previously
    recorded operational restriction reversible through an attributed,
    referenced action instead of a database edit.
    """
    # User deletion is a tombstone: the attributed actor row persists, so no
    # cross-domain User lock is needed before the animal episode lock.
    animal = (
        (
            await db.execute(
                select(Animal)
                .where(Animal.id == animal_id, Animal.farm_id == farm.id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if 1 <= animal_id <= MAX_INT32_ID
        else None
    )
    if animal is None or animal.farm_id != farm.id:
        raise HTTPException(status_code=404, detail="Animal not found")
    if animal.restriction_version != payload.expected_restriction_version:
        raise lifecycle_conflict(
            detail="Movement restriction was superseded; refresh the current episode",
        )
    if not animal.movement_restricted and not animal.suspected_scheduled_disease:
        raise lifecycle_conflict(detail="Animal has no active movement restriction")
    if animal.suspected_scheduled_disease:
        # Two-person rule for statutory holds (mirroring duty verification,
        # with the same owner exemption): the worker who recorded the
        # suspicion cannot also supply the clearance reference that releases
        # it. The episode's PLACED action is the durable attribution; a
        # missing row (manual DB damage only) fails open to the route's
        # normal permission gate rather than freezing the hold forever.
        placed_by_id = (
            await db.execute(
                select(MovementRestrictionAction.acted_by_id)
                .where(
                    MovementRestrictionAction.farm_id == farm.id,
                    MovementRestrictionAction.animal_id == animal.id,
                    MovementRestrictionAction.restriction_version == animal.restriction_version,
                    MovementRestrictionAction.action == "PLACED",
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if placed_by_id == user.id and farm.owner_id != user.id:
            raise lifecycle_conflict(
                detail="Someone else must clear this scheduled-disease hold",
            )
    cleared_at = utcnow()
    db.add(
        MovementRestrictionAction(
            farm_id=farm.id,
            animal_id=animal.id,
            restriction_version=animal.restriction_version,
            action="CLEARED",
            acted_at=cleared_at,
            acted_by_id=user.id,
            action_reference=payload.clearance_reference,
            disease_target=animal.suspected_disease,
            health_event_id=None,
        )
    )
    animal.restriction_cleared_at = cleared_at
    animal.restriction_cleared_by_id = user.id
    animal.restriction_clearance_reference = payload.clearance_reference
    animal.movement_restricted = False
    animal.suspected_scheduled_disease = False
    animal.restriction_reason = None
    # Like restriction_reason, these columns describe the CURRENT episode
    # only; leaving them set would present a cleared animal as still under an
    # active suspicion already reported to the authority. The historical fact
    # is not lost — it stays on the HealthEvent that recorded the suspicion
    # (cited by the PLACED action via health_event_id) and on the CLEARED
    # action's disease_target captured above.
    animal.suspected_disease = None
    # ...but that is only true when a HealthEvent actually recorded it. The mortality path (POST
    # /api/animals/{id}/status with suspected_scheduled_disease) places the hold with
    # health_event_id=None and writes no HealthEvent at all, so the animal column is the ONLY copy
    # of a statutorily mandated notification date. Nulling it there destroyed the record
    # irrecoverably, health events being immutable. The probe must be scoped to THIS episode's
    # PLACED action: an animal-lifetime probe ("any HealthEvent ever notified") let an earlier,
    # event-backed episode satisfy the guard and destroy a later mortality episode's only copy.
    placed_health_event_id = (
        await db.execute(
            select(MovementRestrictionAction.health_event_id)
            .where(
                MovementRestrictionAction.farm_id == farm.id,
                MovementRestrictionAction.animal_id == animal.id,
                MovementRestrictionAction.restriction_version == animal.restriction_version,
                MovementRestrictionAction.action == "PLACED",
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    notification_is_recorded_elsewhere = (
        placed_health_event_id is not None
        and (
            await db.execute(
                select(HealthEvent.id).where(
                    HealthEvent.farm_id == farm.id,
                    HealthEvent.id == placed_health_event_id,
                    HealthEvent.authority_notified_at.is_not(None),
                )
            )
        ).scalar_one_or_none()
        is not None
    )
    if notification_is_recorded_elsewhere:
        animal.authority_notified_at = None
    await db.commit()


@router.get("/events")
async def list_events(
    db: DbSession,
    farm: CurrentFarm,
    perms: VIEW,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
) -> HealthEventListOut:
    """A requested page of health events, newest first, with its full count."""
    where = HealthEvent.farm_id == farm.id
    total = (
        await db.execute(select(func.count()).select_from(HealthEvent).where(where))
    ).scalar_one()
    result = await db.execute(
        select(HealthEvent)
        .options(selectinload(HealthEvent.animal))
        .where(where)
        .order_by(HealthEvent.date.desc(), HealthEvent.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return HealthEventListOut(
        events=[_event_out(e) for e in result.scalars()],
        total=total,
        limit=limit,
        offset=offset,
    )


async def _bulk_target_snapshot(
    db: DbSession,
    farm_id: int,
    target: HealthBulkTargetIn,
    *,
    # Required: the old `or today()` fallback evaluated the deployment-default timezone, not the
    # farm's business calendar — the same fix bucket_transition_error's reference_date got. The only
    # caller passes today(farm.timezone).
    reference_date: date,
) -> tuple[list[int], list[AnimalIdentityOut], list[int | None]]:
    target_limit = MAX_BULK_BUCKET_TARGETS if target.scope == "bucket" else MAX_BULK_HEALTH_TARGETS
    filters: list[ColumnElement[bool]] = [
        Animal.farm_id == farm_id,
        Animal.status == AnimalStatus.ACTIVE.value,
    ]
    if target.scope == "bucket":
        filters.append(Animal.current_bucket == target.bucket)
    else:
        # BoundedId admits ids up to 2**62 but purchase_batch_id is an int4
        # column: comparing an impossible id in SQL would make asyncpg raise a
        # DataError (500). Same ceiling guard as the writer's batch lookup —
        # such an id can never exist, so it simply matches no animals (400).
        filters.append(
            Animal.purchase_batch_id == target.purchase_batch_id
            if target.purchase_batch_id is not None and target.purchase_batch_id <= MAX_INT32_ID
            else false()
        )
        # Batch scope always means the batch's ACTIVE QUARANTINE animals — the
        # exact set /purchase-batches advertises as the selectable count and
        # the set the writer's stability check accepts — for linked and
        # unlinked writes alike, so a reviewed preview can be submitted
        # unchanged.
        filters.append(Animal.current_bucket == Bucket.QUARANTINE.value)
    if target.task_id is not None:
        if target.task_id > MAX_INT32_ID:
            raise lifecycle_conflict(detail="Linked health task is unavailable")
        task = (
            await db.execute(
                select(Task).where(
                    Task.id == target.task_id,
                    Task.farm_id == farm_id,
                    Task.status == TaskStatus.PENDING.value,
                    Task.category.in_((TaskCategory.VACCINE.value, TaskCategory.DEWORMING.value)),
                )
            )
        ).scalar_one_or_none()
        if task is None:
            raise lifecycle_conflict(detail="Linked health task is not pending or compatible")
        if is_herd_round(task):
            round_ = await db.get(HealthRound, task.id)
            if round_ is None:
                raise stale_state_conflict(detail="Start the herd round before reviewing targets")
            component = target.round_component
            if component not in round_.required_components:
                raise HTTPException(status_code=422, detail="Select a required round component")
            filters.extend(
                [
                    exists().where(
                        HealthRoundTarget.task_id == task.id,
                        HealthRoundTarget.animal_id == Animal.id,
                    ),
                    ~exists().where(
                        HealthRoundExclusion.task_id == task.id,
                        HealthRoundExclusion.animal_id == Animal.id,
                    ),
                    ~exists().where(
                        HealthRoundCoverage.task_id == task.id,
                        HealthRoundCoverage.animal_id == Animal.id,
                        HealthRoundCoverage.component == component,
                    ),
                ]
            )
        elif target.scope != "batch" or task.purchase_batch_id != target.purchase_batch_id:
            raise HTTPException(
                status_code=422, detail="Health preview scope must match the linked batch"
            )
        elif target.round_component is not None:
            raise HTTPException(status_code=422, detail="Round component requires a herd duty")
    # Full entities (the age computation needs effective_dob): scalars(), or
    # each row is a one-column Row whose attribute access raises KeyError.
    rows = list(
        (
            await db.execute(
                select(Animal).where(*filters).order_by(Animal.id).limit(target_limit + 1)
            )
        ).scalars()
    )
    if not rows:
        raise HTTPException(status_code=400, detail="No active animals match the given scope")
    if len(rows) > target_limit:
        raise standing_quota(
            detail=(
                f"Bulk health scope exceeds {target_limit} animals; "
                "split it into smaller reviewed sets"
            ),
        )
    ids = [int(row.id) for row in rows]
    ages = [animal.age_months_on(reference_date) for animal in rows]
    return (
        ids,
        [
            AnimalIdentityOut(id=animal.id, tag_number=animal.tag_number, name=animal.name)
            for animal in rows
        ],
        ages,
    )


@router.post("/events/preview")
async def preview_bulk_event_targets(
    payload: HealthBulkTargetIn,
    db: DbSession,
    farm: CurrentFarm,
    _perms: MANAGE,
) -> HealthBulkTargetPreviewOut:
    """Return the exact, bounded active-animal snapshot a bulk write must present."""
    ids, identities, ages = await _bulk_target_snapshot(
        db, farm.id, payload, reference_date=today(farm.timezone)
    )
    return HealthBulkTargetPreviewOut(
        scope=payload.scope,
        bucket=payload.bucket,
        purchase_batch_id=payload.purchase_batch_id,
        task_id=payload.task_id,
        round_component=payload.round_component,
        target_animal_ids=ids,
        target_animals=identities,
        target_animal_ages_months=ages,
        target_count=len(ids),
        max_targets=(
            MAX_BULK_BUCKET_TARGETS if payload.scope == "bucket" else MAX_BULK_HEALTH_TARGETS
        ),
    )


async def _lock_event_targets(
    db: DbSession,
    farm: Farm,
    payload: HealthEventIn,
    *,
    linked_batch_id: int | None = None,
) -> tuple[list[Animal], PurchaseBatch | None, int | None]:
    if payload.scope == "animal":
        animal = (
            (
                await db.execute(
                    select(Animal)
                    .where(Animal.id == payload.animal_id, Animal.farm_id == farm.id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if payload.animal_id is not None and payload.animal_id <= MAX_INT32_ID
            else None
        )
        if animal is None or animal.status != AnimalStatus.ACTIVE.value:
            raise HTTPException(status_code=400, detail="No active animals match the given scope")
        return [animal], None, None

    expected = payload.expected_animal_ids
    if not expected:
        raise lifecycle_conflict(
            detail="Bulk health events require a non-empty reviewed target snapshot",
        )
    target_limit = MAX_BULK_BUCKET_TARGETS if payload.scope == "bucket" else MAX_BULK_HEALTH_TARGETS
    if len(expected) > target_limit:
        raise standing_quota(
            detail=f"Bulk health events are limited to {target_limit} animals",
        )
    if len(set(expected)) != len(expected):
        raise lifecycle_conflict(
            detail="Reviewed target snapshot contains duplicate animal ids",
        )
    if any(animal_id > MAX_INT32_ID for animal_id in expected):
        raise stale_state_conflict(detail="Reviewed target snapshot is stale")

    expected_ids = sorted(expected)
    # A linked quarantine protocol duty covers the whole authoritative batch,
    # not an arbitrary reviewed subset. Lock every ACTIVE batch animal in
    # canonical id order before the linked Task, derive the QUARANTINE set,
    # and compare exact ids. A
    # normal unlinked bulk ledger entry intentionally keeps snapshot semantics
    # (late entrants are not silently added), so this stronger rule is scoped
    # only to a compatible task's purchase batch.
    if linked_batch_id is not None:
        if payload.scope != "batch" or payload.purchase_batch_id != linked_batch_id:
            raise HTTPException(
                status_code=422,
                detail="Health event scope must match the linked batch",
            )
        locked_batch_animals = list(
            (
                await db.execute(
                    select(Animal)
                    .where(
                        Animal.farm_id == farm.id,
                        Animal.purchase_batch_id == linked_batch_id,
                        Animal.status == AnimalStatus.ACTIVE.value,
                    )
                    .order_by(Animal.id)
                    .with_for_update()
                )
            ).scalars()
        )
        # Lock every ACTIVE animal in the batch, then derive the current
        # quarantine cohort from the locked rows. Locking only rows whose
        # bucket was already QUARANTINE left a predicate phantom: an active
        # history-corrected outsider could move back into QUARANTINE after the
        # snapshot but before this linked protocol task committed. The move
        # and health paths now serialize on that outsider as well, so either
        # its move wins and makes the reviewed ids stale, or the event
        # completes before it enters the target cohort.
        animals = [
            animal
            for animal in locked_batch_animals
            if animal.current_bucket == Bucket.QUARANTINE.value
        ]
        if not animals or [animal.id for animal in animals] != expected_ids:
            raise stale_state_conflict(
                detail=(
                    "Reviewed target snapshot does not match the linked batch's "
                    "active quarantine animals"
                ),
            )
        linked_batch = (
            await db.execute(
                select(PurchaseBatch).where(
                    PurchaseBatch.id == linked_batch_id,
                    PurchaseBatch.farm_id == farm.id,
                )
            )
        ).scalar_one_or_none()
        if linked_batch is None:
            raise stale_state_conflict(detail="Reviewed target snapshot is stale")
        return animals, linked_batch, linked_batch.id

    animals = list(
        (
            await db.execute(
                select(Animal)
                .where(Animal.farm_id == farm.id, Animal.id.in_(expected_ids))
                .order_by(Animal.id)
                .with_for_update()
            )
        ).scalars()
    )
    if [animal.id for animal in animals] != expected_ids:
        raise stale_state_conflict(detail="Reviewed target snapshot is stale")

    batch: PurchaseBatch | None = None
    batch_id: int | None = None
    if payload.scope == "bucket":
        stable = all(
            animal.status == AnimalStatus.ACTIVE.value and animal.current_bucket == payload.bucket
            for animal in animals
        )
    else:
        batch = (
            (
                await db.execute(
                    select(PurchaseBatch).where(
                        PurchaseBatch.id == payload.purchase_batch_id,
                        PurchaseBatch.farm_id == farm.id,
                    )
                )
            ).scalar_one_or_none()
            if payload.purchase_batch_id is not None and payload.purchase_batch_id <= MAX_INT32_ID
            else None
        )
        batch_id = batch.id if batch is not None else None
        # Batch scope targets the batch's ACTIVE QUARANTINE set — the count
        # /purchase-batches advertised when the operator chose the batch and
        # the set the preview snapshot presented for review. An animal
        # released from quarantine between preview and write has left that
        # set, so the snapshot is stale rather than silently dosing an
        # already-released animal.
        stable = batch_id is not None and all(
            animal.status == AnimalStatus.ACTIVE.value
            and animal.purchase_batch_id == batch_id
            and animal.current_bucket == Bucket.QUARANTINE.value
            for animal in animals
        )
    if not stable:
        raise stale_state_conflict(detail="Reviewed target snapshot is stale")
    return animals, batch, batch_id


async def _record_event_mutation(
    payload: HealthEventIn,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    membership: CurrentMembership,
) -> HealthEventMutationOut:
    """One HealthEvent row per targeted ACTIVE animal.

    A task-linked event must match the task's exact scope, health type and
    seeded schedule template. This prevents a generic health note from
    completing an unrelated quarantine vaccine duty.
    """
    # Canonical mutation order is ANIMAL(S, ascending id) -> TASK. Animal
    # status changes take the same order before skipping linked duties. Taking
    # the task first here let a status change hold the animal while waiting on
    # this task, as this request held the task while waiting on that animal --
    # a genuine PostgreSQL deadlock rather than a harmless serialization.
    linked_batch_id: int | None = None
    if payload.task_id is not None and payload.task_id <= MAX_INT32_ID:
        linked_task = (
            await db.execute(
                select(Task.purchase_batch_id, Task.recur_days).where(
                    Task.id == payload.task_id,
                    Task.farm_id == farm.id,
                    Task.category.in_((TaskCategory.VACCINE.value, TaskCategory.DEWORMING.value)),
                )
            )
        ).one_or_none()
        if linked_task is not None:
            linked_batch_id = linked_task.purchase_batch_id
            if linked_task.recur_days is not None:
                # Completing a recurring form-linked duty is a net-zero manual
                # queue transition (PENDING -> DONE plus one PENDING successor),
                # so it does not need a free capacity slot. It does need the
                # same transaction-scoped queue mutex as the generic task
                # completion route: take FARM before ANIMAL(S) -> TASK so every
                # recurring successor and count-changing manual-task mutation
                # shares one lock order.
                await lock_manual_task_queue(db, farm)
    animals, batch, batch_id = await _lock_event_targets(
        db,
        farm,
        payload,
        linked_batch_id=linked_batch_id,
    )
    task: Task | None = None
    round_: HealthRound | None = None
    round_components: list[str] = []
    if payload.task_id is not None and payload.task_id <= MAX_INT32_ID:
        task = (
            await db.execute(
                select(Task)
                .where(Task.id == payload.task_id, Task.farm_id == farm.id)
                .with_for_update()
            )
        ).scalar_one_or_none()

    event_date = payload.date or today(farm.timezone)
    try:
        require_farm_not_future(event_date, farm, "health event date")
        for animal in animals:
            require_animal_event_chronology(animal, event_date, "Health event")
        if batch is not None and event_date < batch.date:
            raise ValueError("Health event cannot predate the purchase batch")
        if payload.product_manufactured_on is not None:
            # Product provenance naturally predates some animals and even
            # their acquisition; only the product/event timeline applies.
            require_farm_not_future(
                payload.product_manufactured_on, farm, "product_manufactured_on"
            )
        for field_name, value in (
            ("authority_notified_at", payload.authority_notified_at),
            ("isolation_started_at", payload.isolation_started_at),
        ):
            if value is not None:
                require_farm_not_future(value, farm, field_name)
                for animal in animals:
                    require_animal_event_chronology(animal, value, field_name)
        if (
            payload.product_manufactured_on is not None
            and payload.product_manufactured_on > event_date
        ):
            raise ValueError("product manufacture date cannot follow the health event")
        if payload.vaccine_valid_until is not None and payload.vaccine_valid_until < event_date:
            raise ValueError("vaccine validity cannot predate the health event")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if payload.next_due_date is not None and payload.next_due_date <= event_date:
        raise HTTPException(
            status_code=422, detail="next_due_date must be after the health event date"
        )
    if payload.next_due_date is not None and (payload.next_due_date - event_date).days > 3650:
        # Same re-check-after-farm-date-resolution pattern as withdrawal_until: the schema-level
        # pair checks only ran when the client supplied an explicit event date, so a far
        # next_due_date against the defaulted business date used to reach
        # ck_health_events_next_due_horizon and surface as a raw IntegrityError 500.
        raise HTTPException(
            status_code=422,
            detail="next_due_date cannot be more than 10 years after the health event date",
        )
    if payload.product_expires_on is not None and payload.product_expires_on < event_date:
        raise HTTPException(
            status_code=422, detail="product expiry cannot predate the health event"
        )
    if payload.withdrawal_until is not None and payload.withdrawal_until < event_date:
        raise HTTPException(
            status_code=422,
            detail="withdrawal_until cannot be before the health event date",
        )
    if (
        payload.withdrawal_until is not None
        and (payload.withdrawal_until - event_date).days > MAX_WITHDRAWAL_DAYS
    ):
        # HealthEventIn can only compare this pair when the client supplied an
        # explicit event date.  The operational default is the farm's business
        # date, so enforce the same ceiling again after resolving it here.
        raise HTTPException(
            status_code=422,
            detail=(
                f"withdrawal_until cannot be more than {MAX_WITHDRAWAL_DAYS} days "
                "after the health event date"
            ),
        )

    disease_target = (payload.disease_target or "").strip()
    requested_template = (payload.schedule_template_name or "").strip() or None
    template_name: str | None
    template = None
    if payload.type in (HealthEventType.VACCINE.value, HealthEventType.DEWORMING.value):
        try:
            template = await validated_template(db, requested_template, payload.type)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        template_name = str(template.name) if template is not None else None
        if template_name is not None and not target_matches_template(disease_target, template_name):
            raise HTTPException(
                status_code=422,
                detail="Disease target does not match the selected schedule template",
            )
    else:
        # A treatment/footbath/vitamin/exam/fecal-exam follow-up has no seeded
        # programme item, but next_due_date still requires a stated schedule
        # and authority (ck_health_events_next_due_provenance). Keep that
        # provenance as free text: binding it to a VaccineTemplate is what the
        # DB forbids (ck_health_events_schedule_template_type — a template id
        # exists only for VACCINE/DEWORMING, so validated_template rejects
        # EXAM/FECAL_EXAM exactly like TREATMENT), not naming the schedule.
        template_name = requested_template

    if payload.task_id is not None:
        if (
            task is None
            or task.farm_id != farm.id
            or task.status != TaskStatus.PENDING.value
            or task.category not in (TaskCategory.VACCINE.value, TaskCategory.DEWORMING.value)
        ):
            raise lifecycle_conflict(detail="Linked health task is not pending or compatible")
        # Same assignment rule as the duties page — the record form is not a
        # backdoor around task RBAC. The 403 aborts before the event is saved.
        if not await visible_to(db, task, user, farm, membership, lock_assignee=True):
            raise HTTPException(status_code=403, detail="This duty is not assigned to you")
        if task.category != payload.type:
            raise HTTPException(
                status_code=422, detail="Health event type must match the linked task"
            )
        if event_date < task.due_date:
            raise lifecycle_conflict(detail="This linked health duty is not due yet")
        if task.purchase_batch_id is not None:
            if payload.scope != "batch" or batch_id != task.purchase_batch_id:
                raise HTTPException(
                    status_code=422, detail="Health event scope must match the linked batch"
                )
        elif task.animal_id is not None:
            if payload.scope != "animal" or payload.animal_id != task.animal_id:
                raise HTTPException(
                    status_code=422, detail="Health event scope must match the linked animal"
                )
        else:
            # Herd duties accumulate per-animal evidence across reviewed pens.
            if payload.scope not in ("bucket", "batch"):
                raise HTTPException(
                    status_code=422,
                    detail=("A herd-level round closes via a bucket- or batch-scoped health event"),
                )
        expected_templates = template_names_for_task(task.title, task.category)
        if expected_templates:
            if template_name is not None and template_name not in expected_templates:
                raise HTTPException(
                    status_code=422, detail="Health template does not match the linked task"
                )
            if not target_matches_task(disease_target, task.title, task.category):
                raise HTTPException(
                    status_code=422, detail="Disease target does not match the linked task"
                )
            chosen_template = template_name or preferred_template_for_target(
                disease_target, expected_templates
            )
            try:
                template = await validated_template(db, chosen_template, payload.type)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
            template_name = chosen_template
            if not disease_target:
                # A blank form records the chosen item; it cannot manufacture
                # the other half of a multi-component herd round.
                disease_target = (
                    chosen_template
                    if is_herd_round(task)
                    else canonical_target_for_task(task.title, task.category) or ""
                )
        if is_herd_round(task):
            round_ = await db.get(HealthRound, task.id)
            if round_ is None:
                raise stale_state_conflict(
                    detail="Start the herd round before recording its components"
                )
            try:
                round_components = recorded_components(round_, disease_target, template_name)
                await require_round_targets(
                    db, round_, [animal.id for animal in animals], round_components
                )
            except ValueError as exc:
                raise stale_state_conflict(detail=str(exc)) from None

    if template is None:
        template = await inferred_schedule_template(
            db,
            payload.type,
            (payload.product_name or "").strip(),
            disease_target,
        )
    template_id = template.id if template is not None else None

    tags = {animal.id: animal.tag_number for animal in animals}
    events = await record_health_event(
        db,
        farm,
        animals,
        event_date,
        payload.type,
        (payload.product_name or "").strip(),
        disease_target,
        (payload.dose or "").strip(),
        (payload.route or "").strip(),
        (payload.vet_name or "").strip(),
        payload.cost,
        payload.next_due_date,
        template_name,
        template_id,
        (payload.next_due_authority or "").strip(),
        (payload.product_lot or "").strip(),
        payload.product_manufactured_on,
        payload.product_expires_on,
        payload.vaccine_valid_until,
        (payload.certificate_number or "").strip(),
        (payload.official_tag_number or "").strip(),
        (payload.administered_by or "").strip(),
        payload.withdrawal_until,
        payload.suspected_scheduled_disease,
        payload.authority_notified_at,
        payload.isolation_started_at,
        (payload.notes or "").strip(),
        purchase_batch_id=batch_id,
        created_by_id=user.id,
    )
    if round_ is not None:
        await add_round_coverage(db, round_, events, round_components)
    if task is not None and (round_ is None or await round_is_complete(db, round_)):
        await complete_task(db, task, user)
    outs = []
    for event in events:
        out = HealthEventOut.model_validate(event)
        out.animal_tag = tags.get(event.animal_id) if event.animal_id is not None else None
        outs.append(out)
    if payload.suspected_scheduled_disease and events:
        from ..services.notifications.outbox import enqueue_alert

        target = sms_safe_text(payload.disease_target) or "scheduled disease"
        await enqueue_alert(
            db,
            farm.id,
            "MOVEMENT_RESTRICTION",
            f"Herdly: suspected {target} recorded in the health log — "
            "movement restriction placed. Check the restricted animals.",
            f"movement-restriction:health:{events[0].id}",
        )
    return HealthEventMutationOut(root=outs)


@router.post("/events", status_code=201)
async def record_event(
    payload: HealthEventIn,
    response: Response,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    membership: CurrentMembership,
    _perms: MANAGE,
    idempotency_key: IdempotencyKey = None,
) -> HealthEventMutationOut:
    """Record an individual event or an exact reviewed bulk target snapshot.

    A durable idempotency key makes retries return the first committed event
    set without re-evaluating mutable bucket/batch membership.
    """

    async def mutate() -> HealthEventMutationOut:
        return await _record_event_mutation(payload, db, user, farm, membership)

    result = await execute_idempotent(
        db,
        http_response=response,
        key=idempotency_key,
        farm_id=farm.id,
        actor_id=user.id,
        operation="POST /api/health/events",
        payload=payload,
        path_identity={},
        success_status=201,
        response_type=HealthEventMutationOut,
        mutate=mutate,
    )
    if (
        payload.suspected_scheduled_disease
        and response.headers.get("Idempotency-Replayed") != "true"
    ):
        # Alert hook: a scheduled-disease suspicion places a movement restriction on every targeted
        # animal (record_health_event). Best effort, own session — the committed health write must
        # not fail on it. Replayed idempotent requests skip the fan-out; the day-dedupe would absorb
        # a repeat anyway.
        from ..services.notifications import emit_alert

        # disease_target is worker-enterable free text interpolated into the SMS body and the dedupe
        # payload — URL-ish tokens are neutralized before they reach emit_alert.
        target = sms_safe_text(payload.disease_target) or "scheduled disease"
        await emit_alert(
            farm.id,
            "MOVEMENT_RESTRICTION",
            f"Herdly: suspected {target} recorded in the health log — "
            "movement restriction placed. Check the restricted animals.",
            f"movement-restriction:health:{result.root[0].id}",
        )
    return result


@router.get("/schedule/{animal_id}")
async def vaccination_schedule(
    animal_id: int, db: DbSession, farm: CurrentFarm, perms: VIEW
) -> ScheduleOut:
    """Per-animal vaccination schedule computed from seeded templates."""
    animal = await db.get(Animal, animal_id) if 1 <= animal_id <= MAX_INT32_ID else None
    if animal is None or animal.farm_id != farm.id:
        raise HTTPException(status_code=404, detail="Animal not found")
    rows = await vaccination_schedule_for_animal(db, animal)
    return ScheduleOut(
        animal_id=animal.id,
        rows=[
            ScheduleRowOut(
                template_id=row["template"].id,
                template_name=row["template"].name,
                timing_note=row["template"].timing_note,
                first_due=row["first_due"],
                booster_due=row["booster_due"],
                last_done=row["last_done"],
                next_due=row["next_due"],
                status=row["status"],
            )
            for row in rows
        ],
    )
