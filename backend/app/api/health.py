"""Health: events log, record event (single animal / bucket / purchase
batch), per-animal vaccination schedule from seeded templates.

Port of v1 app/routers/health.py."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Select, select
from sqlalchemy.orm import selectinload

from ..deps import CurrentFarm, CurrentMembership, CurrentUser, DbSession, require_perm
from ..models import (
    Animal,
    AnimalStatus,
    HealthEvent,
    PurchaseBatch,
    Task,
    TaskCategory,
    TaskStatus,
)
from ..schemas.common import MAX_INT32_ID
from ..schemas.health import HealthEventIn, HealthEventOut, ScheduleOut, ScheduleRowOut
from ..services import complete_task, record_health_event, vaccination_schedule_for_animal
from ..utils import today
from .tasks import _visible_to

router = APIRouter(prefix="/api/health", tags=["health"])

VIEW = Annotated[set[str], Depends(require_perm("health.view"))]
MANAGE = Annotated[set[str], Depends(require_perm("health.manage"))]


def _event_out(event: HealthEvent) -> HealthEventOut:
    """Out model + animal tag. `animal` must be selectinloaded (async
    forbids lazy loads)."""
    out = HealthEventOut.model_validate(event)
    out.animal_tag = event.animal.tag_number if event.animal else None
    return out


@router.get("/events")
async def list_events(db: DbSession, farm: CurrentFarm, perms: VIEW) -> list[HealthEventOut]:
    """Latest 100 events on the farm, newest first (v1 ordering)."""
    result = await db.execute(
        select(HealthEvent)
        .options(selectinload(HealthEvent.animal))
        .where(HealthEvent.farm_id == farm.id)
        .order_by(HealthEvent.date.desc(), HealthEvent.id.desc())
        .limit(100)
    )
    return [_event_out(e) for e in result.scalars()]


@router.post("/events", status_code=201)
async def record_event(
    payload: HealthEventIn,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    membership: CurrentMembership,
    perms: MANAGE,
) -> list[HealthEventOut]:
    """One HealthEvent row per targeted ACTIVE animal; the total cost is
    split evenly inside record_health_event (first animal absorbs the
    rounding remainder). A linked VACCINE/DEWORMING task_id is completed;
    any other task_id is ignored while the event is still recorded, as v1
    did."""
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
        animals = list((await db.execute(stmt)).scalars().all())
    if not animals:
        raise HTTPException(status_code=400, detail="No active animals match the given scope")

    tags = {animal.id: animal.tag_number for animal in animals}
    events = await record_health_event(
        db,
        farm,
        animals,
        payload.date or today(),
        payload.type,
        (payload.product_name or "").strip(),
        (payload.disease_target or "").strip(),
        (payload.dose or "").strip(),
        (payload.route or "").strip(),
        (payload.vet_name or "").strip(),
        payload.cost,
        payload.next_due_date,
        (payload.notes or "").strip(),
        purchase_batch_id=batch_id,
        created_by_id=user.id,
    )
    if payload.task_id is not None:
        task = await db.get(Task, payload.task_id) if payload.task_id <= MAX_INT32_ID else None
        # Only vaccine/deworming duties may be closed through the health
        # form; any other smuggled task_id is ignored (event still recorded).
        if (
            task is not None
            and task.farm_id == farm.id
            and task.status == TaskStatus.PENDING.value
            and task.category in (TaskCategory.VACCINE.value, TaskCategory.DEWORMING.value)
        ):
            # Same assignment rule as the duties page — the record form is not
            # a backdoor around task RBAC. The 403 aborts the request before
            # commit, so the event itself is not persisted either.
            if not _visible_to(task, user, farm, membership):
                raise HTTPException(status_code=403, detail="This duty is not assigned to you")
            await complete_task(db, task, user)
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
