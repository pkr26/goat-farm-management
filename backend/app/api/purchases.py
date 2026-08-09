"""Purchases: batches + 45-day quarantine protocol tracker."""

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import CurrentFarm, CurrentMembership, CurrentUser, DbSession, require_perm
from ..models import Animal, PurchaseBatch, Task, TaskStatus
from ..schemas.common import MAX_INT32_ID, MAX_PAGE_OFFSET
from ..schemas.purchases import (
    PurchaseBatchDetailOut,
    PurchaseBatchIn,
    PurchaseBatchListOut,
    PurchaseBatchOut,
)
from ..schemas.summaries import PurchaseQuarantineAnimalOut, QuarantineScheduleTaskOut
from ..services import (
    IdempotencyKey,
    create_purchase_batch,
    execute_idempotent,
    require_farm_not_future,
)
from ..utils import today
from ._shared import TASK_LOADS, animal_computed_facts, animal_out, task_out, visible_to

router = APIRouter(prefix="/api/purchases", tags=["purchases"])

PurchasesView = Annotated[set[str], Depends(require_perm("purchases.view"))]
PurchasesManage = Annotated[set[str], Depends(require_perm("purchases.manage"))]


async def _batch_out(db: AsyncSession, batches: Sequence[PurchaseBatch]) -> list[PurchaseBatchOut]:
    """ORM batches → schema, enriched with animals-created and open-task counts
    (batched aggregate queries, not per-row lazy loads)."""
    if not batches:
        return []
    ids = [batch.id for batch in batches]
    animal_rows = await db.execute(
        select(Animal.purchase_batch_id, func.count())
        .where(Animal.purchase_batch_id.in_(ids))
        .group_by(Animal.purchase_batch_id)
    )
    animals_created: dict[int | None, int] = {}
    for batch_id, n in animal_rows.all():
        animals_created[batch_id] = n
    task_rows = await db.execute(
        select(Task.purchase_batch_id, func.count())
        .where(Task.purchase_batch_id.in_(ids), Task.status == TaskStatus.PENDING.value)
        .group_by(Task.purchase_batch_id)
    )
    open_tasks: dict[int | None, int] = {}
    for batch_id, n in task_rows.all():
        open_tasks[batch_id] = n
    outs = []
    for batch in batches:
        out = PurchaseBatchOut.model_validate(batch)
        out.animals_created = animals_created.get(batch.id, 0)
        out.open_tasks = open_tasks.get(batch.id, 0)
        outs.append(out)
    return outs


@router.get("")
async def list_batches(
    db: DbSession,
    farm: CurrentFarm,
    perms: PurchasesView,
    q: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
) -> PurchaseBatchListOut:
    """Searched, paginated purchase batches for this farm, newest first.

    Text searches supplier names literally (LIKE wildcards are escaped); a
    numeric query, with an optional leading ``#``, also matches an exact batch
    id. This keeps selectors bounded without hiding old purchase batches.
    """
    base = select(PurchaseBatch).where(PurchaseBatch.farm_id == farm.id)
    if q and q.strip():
        raw = q.strip()
        escaped = raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        supplier_match = PurchaseBatch.supplier.ilike(f"%{escaped}%", escape="\\")
        numeric = raw.removeprefix("#")
        if numeric.isascii() and numeric.isdigit() and int(numeric) <= MAX_INT32_ID:
            base = base.where(or_(supplier_match, PurchaseBatch.id == int(numeric)))
        else:
            base = base.where(supplier_match)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    result = await db.execute(
        base.order_by(PurchaseBatch.date.desc(), PurchaseBatch.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return PurchaseBatchListOut(
        batches=await _batch_out(db, list(result.scalars().all())),
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/new", status_code=201)
async def create_batch(
    payload: PurchaseBatchIn,
    response: Response,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: PurchasesManage,
    idempotency_key: IdempotencyKey = None,
) -> PurchaseBatchOut:
    """Create a batch: stub animals into QUARANTINE, generate the 45-day
    quarantine task schedule and book the purchase expense."""

    async def mutate() -> PurchaseBatchOut:
        try:
            require_farm_not_future(payload.date, farm, "purchase date")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        try:
            batch = await create_purchase_batch(
                db,
                farm,
                payload.date,
                (payload.supplier or "").strip(),
                payload.count,
                payload.avg_age_months,
                payload.avg_weight_kg,
                payload.total_price,
                (payload.notes or "").strip(),
                payload.create_animals,
                created_by_id=user.id,
                sex=payload.sex,
            )
        except ValueError as exc:  # backstop — schema re-checks the same invariants
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except IntegrityError:
            # A generated batch tag collided with a farm tag despite its
            # cryptographically random batch nonce. The outer idempotency
            # transaction rolls the whole batch/claim back.
            raise HTTPException(
                status_code=409,
                detail="A generated animal tag already exists on this farm — please retry.",
            ) from None
        await db.flush()
        return (await _batch_out(db, [batch]))[0]

    return await execute_idempotent(
        db,
        http_response=response,
        key=idempotency_key,
        farm_id=farm.id,
        actor_id=user.id,
        operation="POST /api/purchases/new",
        payload=payload,
        path_identity={},
        success_status=201,
        response_type=PurchaseBatchOut,
        mutate=mutate,
    )


@router.get("/{batch_id}")
async def batch_detail(
    batch_id: int,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    membership: CurrentMembership,
    perms: PurchasesView,
) -> PurchaseBatchDetailOut:
    """One batch with its quarantine schedule and purpose-scoped animals.

    The schedule itself belongs to procurement, but task assignment and
    completion attribution remain task-module data. Likewise, a batch animal
    can be tracked through quarantine without granting its complete profile.
    """
    batch = await db.get(PurchaseBatch, batch_id) if 1 <= batch_id <= MAX_INT32_ID else None
    if batch is None or batch.farm_id != farm.id:
        raise HTTPException(status_code=404, detail="Purchase batch not found")
    task_stmt = select(Task).where(Task.farm_id == farm.id, Task.purchase_batch_id == batch.id)
    if "tasks.view" in perms:
        task_stmt = task_stmt.options(*TASK_LOADS)
    task_result = await db.execute(task_stmt.order_by(Task.due_date, Task.id))
    animal_stmt = select(Animal).where(Animal.purchase_batch_id == batch.id)
    animal_result = await db.execute(animal_stmt.order_by(Animal.tag_number))
    tasks = list(task_result.scalars().all())
    animals = list(animal_result.scalars().all())
    reference_date = today(farm.timezone)
    computed = (
        await animal_computed_facts(db, animals, reference_date, farm.timezone)
        if "animals.view" in perms
        else {}
    )
    return PurchaseBatchDetailOut(
        batch=(await _batch_out(db, [batch]))[0],
        animals=[
            (
                animal_out(
                    animal,
                    reference_date,
                    farm.timezone,
                    permissions=perms,
                    computed=computed[animal.id],
                )
                if "animals.view" in perms
                else PurchaseQuarantineAnimalOut.model_validate(animal)
            )
            for animal in animals
        ],
        tasks=[
            (
                task_out(task)
                if "tasks.view" in perms and await visible_to(db, task, user, farm, membership)
                else QuarantineScheduleTaskOut.model_validate(task)
            )
            for task in tasks
        ],
    )
