"""Health: events log, record event (single animal / bucket / purchase
batch), per-animal vaccination schedule from seeded templates.

Port of v1 app/routers/health.py."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Select, false, func, or_, select
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from ..deps import CurrentFarm, CurrentMembership, CurrentUser, DbSession, require_perm
from ..models import (
    Animal,
    AnimalStatus,
    HealthEvent,
    PurchaseBatch,
    Task,
    TaskCategory,
    TaskStatus,
    User,
)
from ..schemas.common import MAX_INT32_ID
from ..schemas.health import (
    HealthAnimalOptionListOut,
    HealthAnimalOptionOut,
    HealthEventIn,
    HealthEventListOut,
    HealthEventOut,
    HealthPurchaseBatchOptionListOut,
    HealthPurchaseBatchOptionOut,
    MovementRestrictionClearIn,
    ScheduleOut,
    ScheduleRowOut,
)
from ..services import (
    complete_task,
    record_health_event,
    target_matches_template,
    template_name_for_task,
    vaccination_schedule_for_animal,
    validated_template_name,
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
    offset: Annotated[int, Query(ge=0)] = 0,
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
    q: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=HEALTH_LOOKUP_MAX_LIMIT)] = (HEALTH_LOOKUP_DEFAULT_LIMIT),
    offset: Annotated[int, Query(ge=0)] = 0,
) -> HealthPurchaseBatchOptionListOut:
    """Targetable batch summaries without exposing the purchase ledger.

    Batches with no active animals are omitted because the health write path
    cannot apply an event to them.
    """
    active_count = (
        select(func.count(Animal.id))
        .where(
            Animal.farm_id == farm.id,
            Animal.purchase_batch_id == PurchaseBatch.id,
            Animal.status == AnimalStatus.ACTIVE.value,
        )
        .correlate(PurchaseBatch)
        .scalar_subquery()
    )
    filters = [PurchaseBatch.farm_id == farm.id, active_count > 0]
    if q and q.strip():
        raw = q.strip()
        numeric = raw.removeprefix("#")
        explicit_id = raw.startswith("#") and numeric.isascii() and numeric.isdigit()
        if explicit_id:
            filters.append(
                PurchaseBatch.id == int(numeric) if int(numeric) <= MAX_INT32_ID else false()
            )
        else:
            supplier_match = PurchaseBatch.supplier.ilike(_escaped_contains(raw), escape="\\")
            if numeric.isascii() and numeric.isdigit() and int(numeric) <= MAX_INT32_ID:
                filters.append(or_(supplier_match, PurchaseBatch.id == int(numeric)))
            else:
                filters.append(supplier_match)

    total = (
        await db.execute(select(func.count()).select_from(PurchaseBatch).where(*filters))
    ).scalar_one()
    result = await db.execute(
        select(PurchaseBatch, active_count.label("active_animal_count"))
        .where(*filters)
        .order_by(PurchaseBatch.date.desc(), PurchaseBatch.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return HealthPurchaseBatchOptionListOut(
        batches=[
            HealthPurchaseBatchOptionOut(
                id=batch.id,
                date=batch.date,
                supplier=batch.supplier,
                count=batch.count,
                active_animal_count=active_animal_count,
            )
            for batch, active_animal_count in result.all()
        ],
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
    # Account deletion takes the same User -> domain-row order before it
    # anonymizes audit references, so a concurrent self-delete cannot race
    # this new foreign-key attribution into an IntegrityError.
    await db.execute(select(User.id).where(User.id == user.id).with_for_update())
    animal = (
        (
            await db.execute(select(Animal).where(Animal.id == animal_id).with_for_update())
        ).scalar_one_or_none()
        if 1 <= animal_id <= MAX_INT32_ID
        else None
    )
    if animal is None or animal.farm_id != farm.id:
        raise HTTPException(status_code=404, detail="Animal not found")
    if not animal.movement_restricted and not animal.suspected_scheduled_disease:
        raise HTTPException(status_code=409, detail="Animal has no active movement restriction")
    animal.restriction_cleared_at = utcnow()
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
    offset: Annotated[int, Query(ge=0)] = 0,
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


@router.post("/events", status_code=201)
async def record_event(
    payload: HealthEventIn,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    membership: CurrentMembership,
    perms: MANAGE,
) -> list[HealthEventOut]:
    """One HealthEvent row per targeted ACTIVE animal.

    A task-linked event must match the task's exact scope, health type and
    seeded schedule template. This prevents a generic health note from
    completing an unrelated quarantine vaccine duty.
    """
    # Lock a linked task before animals. Quarantine release locks its release
    # task then prerequisite tasks then animals; keeping that order avoids a
    # release-vs-vaccine completion deadlock.
    task: Task | None = None
    if payload.task_id is not None and payload.task_id <= MAX_INT32_ID:
        task = (
            await db.execute(select(Task).where(Task.id == payload.task_id).with_for_update())
        ).scalar_one_or_none()
    base = select(Animal).where(
        Animal.farm_id == farm.id, Animal.status == AnimalStatus.ACTIVE.value
    )
    batch_id = None
    stmt: Select[tuple[Animal]] | None = None
    # Ids above the int4 PK ceiling cannot exist — they simply match nothing
    # (→ the 400 below), never an asyncpg int32 DataError (500).
    if payload.scope == "animal":
        if payload.animal_id is not None and payload.animal_id <= MAX_INT32_ID:
            stmt = base.where(Animal.id == payload.animal_id)
    elif payload.scope == "bucket":
        stmt = base.where(Animal.current_bucket == payload.bucket)
    elif payload.purchase_batch_id is not None:  # scope == "batch"
        batch = (
            await db.get(PurchaseBatch, payload.purchase_batch_id)
            if payload.purchase_batch_id <= MAX_INT32_ID
            else None
        )
        if batch is not None and batch.farm_id == farm.id:
            batch_id = batch.id
            stmt = base.where(Animal.purchase_batch_id == batch_id)
    animals: list[Animal] = []
    if stmt is not None:
        animals = list((await db.execute(stmt.with_for_update())).scalars().all())
    if not animals:
        raise HTTPException(status_code=400, detail="No active animals match the given scope")

    event_date = payload.date or today(farm.timezone)
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

    template_name: str | None
    try:
        template_name = await validated_template_name(
            db, (payload.schedule_template_name or "").strip() or None, payload.type
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if template_name is not None and not target_matches_template(
        payload.disease_target or "", template_name
    ):
        raise HTTPException(
            status_code=422,
            detail="Disease target does not match the selected schedule template",
        )

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
        if not visible_to(task, user, farm, membership):
            raise HTTPException(status_code=403, detail="This duty is not assigned to you")
        if task.category != payload.type:
            raise HTTPException(
                status_code=422, detail="Health event type must match the linked task"
            )
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
            template_name = expected_template
        await complete_task(db, task, user)

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
    await db.commit()
    outs = []
    for event in events:
        out = HealthEventOut.model_validate(event)
        out.animal_tag = tags.get(event.animal_id) if event.animal_id is not None else None
        outs.append(out)
    return outs


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
