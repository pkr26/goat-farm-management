"""Purchases: batches + 45-day quarantine protocol tracker."""

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import CurrentFarm, CurrentMembership, CurrentUser, DbSession, require_perm
from ..models import Animal, PurchaseBatch, Task, TaskStatus
from ..models.species import GOAT_PROFILE
from ..schemas.common import COMMON_ERROR_RESPONSES, MAX_INT32_ID, MAX_PAGE_OFFSET, PostgresText
from ..schemas.purchases import (
    PurchaseBatchDetailOut,
    PurchaseBatchIn,
    PurchaseBatchListOut,
    PurchaseBatchOut,
)
from ..schemas.summaries import PurchaseQuarantineAnimalOut, QuarantineScheduleTaskOut
from ..services import (
    RequiredIdempotencyKey,
    create_purchase_batch,
    execute_idempotent,
    require_farm_not_future,
)
from ..utils import today
from ._shared import TASK_LOADS, animal_computed_facts, animal_out, task_out, visible_to

router = APIRouter(prefix="/api/purchases", tags=["purchases"], responses=COMMON_ERROR_RESPONSES)

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
    q: Annotated[PostgresText | None, Query(max_length=120)] = None,
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
    idempotency_key: RequiredIdempotencyKey,
) -> PurchaseBatchOut:
    """Create a batch: stub animals into QUARANTINE, generate the 45-day
    quarantine task schedule and book the purchase expense."""

    if payload.create_animals and "animals.create" not in perms:
        # The cascade writes full Animal rows (plus moves and weights); a
        # purchases-only role must not gain herd-register write access.
        raise HTTPException(
            status_code=403,
            detail=(
                "Creating purchase animals requires the animal-creation "
                "permission. Untick 'create animals' or ask the owner."
            ),
        )

    async def mutate() -> PurchaseBatchOut:
        try:
            require_farm_not_future(payload.date, farm, "purchase date")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        # Species-scaled cap: the generic 0-1000 kg band would let a goat
        # purchase average 950 kg/head and fabricate "latest weight" facts.
        adult_cap = GOAT_PROFILE.max_adult_weight_kg
        if payload.avg_weight_kg is not None and payload.avg_weight_kg > adult_cap:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Average weight {payload.avg_weight_kg:g} kg exceeds the credible "
                    f"adult scale for this farm's species ({adult_cap:g} kg cap)"
                ),
            )
        for weight in payload.individual_weights_kg or ():
            # Same species bounds as the average, plus positivity: a WeightRecord
            # row cannot hold 0 kg (ck_weight_records_weight_positive), and one
            # unrecorded head would silently lose its arrival baseline.
            if not 0 < weight <= adult_cap:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Individual arrival weight {weight:g} kg is outside the credible "
                        f"range for this farm's species (0–{adult_cap:g} kg)"
                    ),
                )
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
                origin_market=(payload.origin_market or "").strip() or None,
                transport_hours=payload.transport_hours,
                seller_health_history=(payload.seller_health_history or "").strip() or None,
                individual_weights_kg=payload.individual_weights_kg,
            )
            # Inside the try: deferred stub inserts can surface their unique
            # violations here rather than after the handlers.
            await db.flush()
        except ValueError as exc:  # backstop — schema re-checks the same invariants
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except IntegrityError as exc:
            # Only a generated-tag collision with the farm's tag unique index
            # is the retryable 409; any other constraint failure must surface
            # as itself rather than misdiagnosed retry advice (RT-HIJ-6).
            # Driver layers disagree about where the constraint name lives
            # (asyncpg native errors expose it directly; SQLAlchemy's dbapi
            # adapter does not), so walk the cause chain and fall back to the
            # DETAIL line, which always carries it.
            constraint_name: str | None = None
            linked: BaseException | None = exc
            while linked is not None and constraint_name is None:
                constraint_name = getattr(linked, "constraint_name", None)
                linked = getattr(linked, "orig", None) or linked.__cause__
            if constraint_name is None and "uq_animal_tag_per_farm" in str(exc):
                constraint_name = "uq_animal_tag_per_farm"
            if constraint_name != "uq_animal_tag_per_farm":
                raise
            # The outer idempotency transaction rolls the whole batch/claim
            # back.
            raise HTTPException(
                status_code=409,
                detail="A generated animal tag already exists on this farm — please retry.",
            ) from None
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
    animals_limit: Annotated[int, Query(ge=1, le=200)] = 100,
    animals_offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
) -> PurchaseBatchDetailOut:
    """One batch with its quarantine schedule and purpose-scoped animals.

    The schedule itself belongs to procurement, but task assignment and
    completion attribution remain task-module data. Likewise, a batch animal
    can be tracked through quarantine without granting its complete profile.

    A batch may legitimately hold ``MAX_BATCH_COUNT`` animals, so its animals
    are a bounded page like every other list in the API; the exact occupancy
    stays available as the batch's ``animals_created``. The protocol schedule
    needs no bound — ``QUARANTINE_PROTOCOL`` is 11 steps.
    """
    batch = await db.get(PurchaseBatch, batch_id) if 1 <= batch_id <= MAX_INT32_ID else None
    if batch is None or batch.farm_id != farm.id:
        raise HTTPException(status_code=404, detail="Purchase batch not found")
    task_stmt = select(Task).where(Task.farm_id == farm.id, Task.purchase_batch_id == batch.id)
    if "tasks.view" in perms:
        task_stmt = task_stmt.options(*TASK_LOADS)
    task_result = await db.execute(task_stmt.order_by(Task.due_date, Task.id))
    animal_stmt = select(Animal).where(Animal.purchase_batch_id == batch.id)
    animal_result = await db.execute(
        animal_stmt.order_by(Animal.tag_number, Animal.id)
        .offset(animals_offset)
        .limit(animals_limit)
    )
    tasks = list(task_result.scalars().all())
    animals = list(animal_result.scalars().all())
    # Exact occupancy, not the page length: an offset past the end must still
    # report the real total so a client can page back to it.
    animals_total = (
        await db.execute(
            select(func.count()).select_from(Animal).where(Animal.purchase_batch_id == batch.id)
        )
    ).scalar_one()
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
        animals_total=int(animals_total),
        animals_limit=animals_limit,
        animals_offset=animals_offset,
    )
