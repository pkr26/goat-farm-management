"""Health: events log, record event (single animal / bucket / purchase
batch), per-animal vaccination schedule from seeded templates.

Port of v1 app/routers/health.py."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import false, func, or_, select
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from ..deps import CurrentFarm, CurrentMembership, CurrentUser, DbSession, require_perm
from ..models import (
    Animal,
    AnimalStatus,
    Bucket,
    Farm,
    HealthEvent,
    HealthEventType,
    MovementRestrictionAction,
    PurchaseBatch,
    Task,
    TaskCategory,
    TaskStatus,
)
from ..schemas.common import MAX_INT32_ID, MAX_PAGE_OFFSET
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
    MovementRestrictionActionOut,
    MovementRestrictionClearIn,
    MovementRestrictionHistoryOut,
    ScheduleOut,
    ScheduleRowOut,
)
from ..schemas.summaries import AnimalIdentityOut
from ..services import (
    IdempotencyKey,
    complete_task,
    execute_idempotent,
    inferred_schedule_template,
    record_health_event,
    require_animal_event_chronology,
    require_farm_not_future,
    target_matches_template,
    template_name_for_task,
    vaccination_schedule_for_animal,
    validated_template,
)
from ..utils import today, utcnow
from ._shared import visible_to

router = APIRouter(prefix="/api/health", tags=["health"])

VIEW = Annotated[set[str], Depends(require_perm("health.view"))]
MANAGE = Annotated[set[str], Depends(require_perm("health.manage"))]

HEALTH_LOOKUP_DEFAULT_LIMIT = 50
HEALTH_LOOKUP_MAX_LIMIT = 100


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


@router.get("/animals")
async def health_animal_options(
    db: DbSession,
    farm: CurrentFarm,
    _perms: VIEW,
    q: Annotated[str | None, Query(max_length=60)] = None,
    limit: Annotated[int, Query(ge=1, le=HEALTH_LOOKUP_MAX_LIMIT)] = (HEALTH_LOOKUP_DEFAULT_LIMIT),
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
) -> HealthAnimalOptionListOut:
    """Farm-local active-animal summaries for health schedules and events.

    A numeric query, optionally prefixed by ``#``, resolves an exact selected
    id without requiring access to the full animal profile endpoint.
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
                current_bucket=animal.current_bucket,
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
    q: Annotated[str | None, Query(max_length=20)] = None,
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
        raise HTTPException(
            status_code=409,
            detail="Movement restriction was superseded; refresh the current episode",
        )
    if not animal.movement_restricted and not animal.suspected_scheduled_disease:
        raise HTTPException(status_code=409, detail="Animal has no active movement restriction")
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
) -> tuple[list[int], list[AnimalIdentityOut]]:
    target_limit = MAX_BULK_BUCKET_TARGETS if target.scope == "bucket" else MAX_BULK_HEALTH_TARGETS
    filters: list[ColumnElement[bool]] = [
        Animal.farm_id == farm_id,
        Animal.status == AnimalStatus.ACTIVE.value,
    ]
    if target.scope == "bucket":
        filters.append(Animal.current_bucket == target.bucket)
    else:
        filters.append(Animal.purchase_batch_id == target.purchase_batch_id)
        if target.task_id is not None:
            if target.task_id > MAX_INT32_ID:
                raise HTTPException(status_code=409, detail="Linked health task is unavailable")
            task = (
                await db.execute(
                    select(Task).where(
                        Task.id == target.task_id,
                        Task.farm_id == farm_id,
                        Task.status == TaskStatus.PENDING.value,
                        Task.category.in_(
                            (TaskCategory.VACCINE.value, TaskCategory.DEWORMING.value)
                        ),
                    )
                )
            ).scalar_one_or_none()
            if task is None:
                raise HTTPException(
                    status_code=409, detail="Linked health task is not pending or compatible"
                )
            if task.purchase_batch_id != target.purchase_batch_id:
                raise HTTPException(
                    status_code=422, detail="Health preview scope must match the linked batch"
                )
            # Match the writer's authoritative target predicate exactly so a
            # reviewed preview can be submitted unchanged with this task id.
            filters.append(Animal.current_bucket == Bucket.QUARANTINE.value)
    rows = list(
        (
            await db.execute(
                select(Animal.id, Animal.tag_number, Animal.name)
                .where(*filters)
                .order_by(Animal.id)
                .limit(target_limit + 1)
            )
        ).all()
    )
    if not rows:
        raise HTTPException(status_code=400, detail="No active animals match the given scope")
    if len(rows) > target_limit:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Bulk health scope exceeds {target_limit} animals; "
                "split it into smaller reviewed sets"
            ),
        )
    ids = [int(row.id) for row in rows]
    return ids, [
        AnimalIdentityOut(id=row.id, tag_number=row.tag_number, name=row.name) for row in rows
    ]


@router.post("/events/preview")
async def preview_bulk_event_targets(
    payload: HealthBulkTargetIn,
    db: DbSession,
    farm: CurrentFarm,
    _perms: MANAGE,
) -> HealthBulkTargetPreviewOut:
    """Return the exact, bounded active-animal snapshot a bulk write must present."""
    ids, identities = await _bulk_target_snapshot(db, farm.id, payload)
    return HealthBulkTargetPreviewOut(
        scope=payload.scope,
        bucket=payload.bucket,
        purchase_batch_id=payload.purchase_batch_id,
        task_id=payload.task_id,
        target_animal_ids=ids,
        target_animals=identities,
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
        raise HTTPException(
            status_code=409,
            detail="Bulk health events require a non-empty reviewed target snapshot",
        )
    target_limit = MAX_BULK_BUCKET_TARGETS if payload.scope == "bucket" else MAX_BULK_HEALTH_TARGETS
    if len(expected) > target_limit:
        raise HTTPException(
            status_code=409,
            detail=f"Bulk health events are limited to {target_limit} animals",
        )
    if len(set(expected)) != len(expected):
        raise HTTPException(
            status_code=409,
            detail="Reviewed target snapshot contains duplicate animal ids",
        )
    if any(animal_id > MAX_INT32_ID for animal_id in expected):
        raise HTTPException(status_code=409, detail="Reviewed target snapshot is stale")

    expected_ids = sorted(expected)
    # A linked quarantine protocol duty covers the whole authoritative batch,
    # not an arbitrary reviewed subset. Lock that full ACTIVE+QUARANTINE set
    # in canonical id order before the linked Task and compare exact ids. A
    # normal unlinked bulk ledger entry intentionally keeps snapshot semantics
    # (late entrants are not silently added), so this stronger rule is scoped
    # only to a compatible task's purchase batch.
    if linked_batch_id is not None:
        if payload.scope != "batch" or payload.purchase_batch_id != linked_batch_id:
            raise HTTPException(
                status_code=422,
                detail="Health event scope must match the linked batch",
            )
        animals = list(
            (
                await db.execute(
                    select(Animal)
                    .where(
                        Animal.farm_id == farm.id,
                        Animal.purchase_batch_id == linked_batch_id,
                        Animal.status == AnimalStatus.ACTIVE.value,
                        Animal.current_bucket == Bucket.QUARANTINE.value,
                    )
                    .order_by(Animal.id)
                    .with_for_update()
                )
            ).scalars()
        )
        if not animals or [animal.id for animal in animals] != expected_ids:
            raise HTTPException(
                status_code=409,
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
            raise HTTPException(status_code=409, detail="Reviewed target snapshot is stale")
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
        raise HTTPException(status_code=409, detail="Reviewed target snapshot is stale")

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
        stable = batch_id is not None and all(
            animal.status == AnimalStatus.ACTIVE.value and animal.purchase_batch_id == batch_id
            for animal in animals
        )
    if not stable:
        raise HTTPException(status_code=409, detail="Reviewed target snapshot is stale")
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
        linked_batch_id = (
            await db.execute(
                select(Task.purchase_batch_id).where(
                    Task.id == payload.task_id,
                    Task.farm_id == farm.id,
                    Task.category.in_((TaskCategory.VACCINE.value, TaskCategory.DEWORMING.value)),
                )
            )
        ).scalar_one_or_none()
    animals, batch, batch_id = await _lock_event_targets(
        db,
        farm,
        payload,
        linked_batch_id=linked_batch_id,
    )
    task: Task | None = None
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
    if payload.product_expires_on is not None and payload.product_expires_on < event_date:
        raise HTTPException(
            status_code=422, detail="product expiry cannot predate the health event"
        )
    if payload.withdrawal_until is not None and payload.withdrawal_until < event_date:
        raise HTTPException(
            status_code=422,
            detail="withdrawal_until cannot be before the health event date",
        )

    requested_template = (payload.schedule_template_name or "").strip() or None
    template_name: str | None
    template = None
    if payload.type in (HealthEventType.VACCINE.value, HealthEventType.DEWORMING.value):
        try:
            template = await validated_template(db, requested_template, payload.type)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        template_name = str(template.name) if template is not None else None
        if template_name is not None and not target_matches_template(
            payload.disease_target or "", template_name
        ):
            raise HTTPException(
                status_code=422,
                detail="Disease target does not match the selected schedule template",
            )
    else:
        # A treatment/footbath/vitamin follow-up has no seeded programme item,
        # but next_due_date still requires a stated schedule and authority
        # (ck_health_events_next_due_provenance). Keep that provenance as free
        # text: binding it to a VaccineTemplate is what the DB forbids
        # (ck_health_events_schedule_template_type), not naming the schedule.
        template_name = requested_template

    if payload.task_id is not None:
        if (
            task is None
            or task.farm_id != farm.id
            or task.status != TaskStatus.PENDING.value
            or task.category not in (TaskCategory.VACCINE.value, TaskCategory.DEWORMING.value)
        ):
            raise HTTPException(
                status_code=409, detail="Linked health task is not pending or compatible"
            )
        # Same assignment rule as the duties page — the record form is not a
        # backdoor around task RBAC. The 403 aborts before the event is saved.
        if not await visible_to(db, task, user, farm, membership, lock_assignee=True):
            raise HTTPException(status_code=403, detail="This duty is not assigned to you")
        if task.category != payload.type:
            raise HTTPException(
                status_code=422, detail="Health event type must match the linked task"
            )
        if event_date < task.due_date:
            raise HTTPException(status_code=409, detail="This linked health duty is not due yet")
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
            raise HTTPException(
                status_code=422, detail="Linked health task has no supported target"
            )
        expected_template = template_name_for_task(task.title, task.category)
        if expected_template:
            if template_name is not None and template_name != expected_template:
                raise HTTPException(
                    status_code=422, detail="Health template does not match the linked task"
                )
            if not target_matches_template(payload.disease_target or "", expected_template):
                raise HTTPException(
                    status_code=422, detail="Disease target does not match the linked task"
                )
            try:
                template = await validated_template(db, expected_template, payload.type)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
            template_name = expected_template
        await complete_task(db, task, user)

    if template is None:
        template = await inferred_schedule_template(
            db,
            payload.type,
            (payload.product_name or "").strip(),
            (payload.disease_target or "").strip(),
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
        (payload.disease_target or "").strip(),
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
    outs = []
    for event in events:
        out = HealthEventOut.model_validate(event)
        out.animal_tag = tags.get(event.animal_id) if event.animal_id is not None else None
        outs.append(out)
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

    return await execute_idempotent(
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
