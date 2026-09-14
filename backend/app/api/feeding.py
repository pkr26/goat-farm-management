"""Feeding: today's 3-shift plan + dispensing log, recipes + allocation
reference, feed inventory (add stock / mix batch)."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from ..deps import CurrentFarm, CurrentUser, DbSession, require_perm
from ..models import FeedFinishedStock, FeedingRecord, FeedInventory, FeedRecipe
from ..schemas.common import COMMON_ERROR_RESPONSES, MAX_INT32_ID, MAX_PAGE_OFFSET
from ..schemas.feeding import (
    BucketAllocationOut,
    DispenseIn,
    DispensingAggregateOut,
    FeedingHistoryOut,
    FeedingPlanOut,
    FeedingRecordOut,
    FeedInventoryOut,
    FeedRecipeOut,
    FeedSettingIn,
    FinishedFeedStockOut,
    MixIn,
    PlanLineOut,
    RecipeListOut,
    StockAddIn,
)
from ..services import (
    DRY_ROUGHAGE,
    InsufficientFeedError,
    RequiredIdempotencyKey,
    add_feed_stock,
    bucket_allocation_reference,
    execute_idempotent,
    feeding_plan,
    mix_feed_batch,
    record_dispensing,
    require_farm_not_future,
    set_daily_kg_per_head,
)
from ..utils import today

router = APIRouter(prefix="/api/feeding", tags=["feeding"], responses=COMMON_ERROR_RESPONSES)
TODAY_PLAN_RECORD_LIMIT = 200

FeedingView = Annotated[set[str], Depends(require_perm("feeding.view"))]
FeedingManage = Annotated[set[str], Depends(require_perm("feeding.manage"))]


@router.get("/plan")
async def feeding_today(db: DbSession, farm: CurrentFarm, perms: FeedingView) -> FeedingPlanOut:
    """Today's plan plus the latest 200 dispensing records for today.

    ``records_total`` remains the truthful full-day count. Clients needing
    older rows use the paginated ``GET /api/feeding/records`` endpoint.
    """
    plan_date = today(farm.timezone)
    plan = await feeding_plan(db, farm, plan_date)
    record_scope = (
        FeedingRecord.farm_id == farm.id,
        FeedingRecord.date == plan_date,
    )
    records_total = (
        await db.execute(select(func.count(FeedingRecord.id)).where(*record_scope))
    ).scalar_one()
    latest_records = list(
        (
            await db.execute(
                select(FeedingRecord)
                .where(*record_scope)
                .order_by(FeedingRecord.id.desc())
                .limit(TODAY_PLAN_RECORD_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    aggregate_rows = (
        await db.execute(
            select(
                FeedingRecord.bucket,
                FeedingRecord.recipe_code,
                FeedingRecord.shift,
                func.sum(FeedingRecord.qty_kg).label("qty_kg"),
            )
            .where(*record_scope)
            .group_by(
                FeedingRecord.bucket,
                FeedingRecord.recipe_code,
                FeedingRecord.shift,
            )
            .order_by(
                FeedingRecord.bucket,
                FeedingRecord.recipe_code,
                FeedingRecord.shift,
            )
        )
    ).all()
    return FeedingPlanOut(
        lines=[PlanLineOut.model_validate(line) for line in plan],
        # Preserve the existing oldest→newest display order inside the bounded
        # window while selecting the newest rows efficiently in SQL.
        records=[FeedingRecordOut.model_validate(record) for record in reversed(latest_records)],
        records_total=records_total,
        records_limit=TODAY_PLAN_RECORD_LIMIT,
        dispensed_totals=[
            DispensingAggregateOut(
                bucket=bucket,
                recipe_code=recipe_code,
                shift=shift,
                qty_kg=float(
                    Decimal(str(qty_kg)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
                ),
            )
            for bucket, recipe_code, shift, qty_kg in aggregate_rows
        ],
    )


@router.post("/settings", status_code=204)
async def save_setting(
    payload: FeedSettingIn, db: DbSession, farm: CurrentFarm, perms: FeedingManage
) -> None:
    """Override the per-head daily ration (kg) for one bucket."""
    await set_daily_kg_per_head(db, farm.id, payload.bucket, payload.daily_kg_per_head)
    await db.commit()


@router.post("/dispense", status_code=201)
async def dispense(
    payload: DispenseIn,
    response: Response,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: FeedingManage,
    # Required: a dispense has no DB natural key (the same bucket/shift may
    # legitimately be dispensed twice), so the Idempotency-Key is the only
    # replay defense against a double stock debit.
    idempotency_key: RequiredIdempotencyKey,
) -> FeedingRecordOut:
    """Record feed actually dispensed to a bucket on a shift."""

    async def mutate() -> FeedingRecordOut:
        record_date = payload.date or today(farm.timezone)
        try:
            require_farm_not_future(record_date, farm, "dispensing date")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if record_date < farm.created_at.date():
            # Hardening floor (RT-HIJ-4): a dispensing record cannot predate
            # the farm itself. created_at is UTC while record_date is the
            # farm-local business date, so comparing against the UTC date can
            # only loosen the floor by a day — never reject a legit entry.
            raise HTTPException(
                status_code=422,
                detail="Dispensing date cannot be before the farm was created.",
            )
        # DispenseIn declares the recipe required and trims it: a record
        # without one cannot be reconciled with the ration plan and, more
        # importantly, bypasses finished-feed stock deduction. Dry roughage
        # remains an explicit direct-fed recipe below and is debited from
        # seeded dry-stover inventory by the service.
        code = payload.recipe_code
        # Keep state-dependent validation behind the idempotency claim so a
        # replay does not re-evaluate a recipe changed after the first success.
        if code != DRY_ROUGHAGE:
            result = await db.execute(select(FeedRecipe.id).where(FeedRecipe.code == code))
            if result.scalar_one_or_none() is None:
                raise HTTPException(status_code=400, detail=f"Unknown recipe {code}")
        try:
            record = await record_dispensing(
                db,
                farm,
                payload.bucket,
                payload.shift,
                code,
                payload.qty_kg,
                record_date,
                created_by_id=user.id,
            )
        except (InsufficientFeedError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return FeedingRecordOut.model_validate(record)

    return await execute_idempotent(
        db,
        http_response=response,
        key=idempotency_key,
        farm_id=farm.id,
        actor_id=user.id,
        operation="POST /api/feeding/dispense",
        payload=payload,
        path_identity={},
        success_status=201,
        response_type=FeedingRecordOut,
        mutate=mutate,
    )


@router.get("/records")
async def feeding_history(
    db: DbSession,
    farm: CurrentFarm,
    perms: FeedingView,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
) -> FeedingHistoryOut:
    """Paginated dispensing history, including backdated records."""
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(status_code=400, detail="date_from must be on or before date_to")
    stmt = select(FeedingRecord).where(FeedingRecord.farm_id == farm.id)
    if date_from is not None:
        stmt = stmt.where(FeedingRecord.date >= date_from)
    if date_to is not None:
        stmt = stmt.where(FeedingRecord.date <= date_to)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        await db.execute(
            stmt.order_by(FeedingRecord.date.desc(), FeedingRecord.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars()
    return FeedingHistoryOut(
        records=[FeedingRecordOut.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/recipes")
async def list_recipes(db: DbSession, farm: CurrentFarm, perms: FeedingView) -> RecipeListOut:
    """All feed recipes (with lines) + the bucket allocation reference table."""
    result = await db.execute(
        select(FeedRecipe).options(selectinload(FeedRecipe.lines)).order_by(FeedRecipe.id)
    )
    return RecipeListOut(
        recipes=[FeedRecipeOut.model_validate(recipe) for recipe in result.scalars().all()],
        allocation=[
            BucketAllocationOut(bucket=bucket, allocation=allocation)
            for bucket, allocation in bucket_allocation_reference()
        ],
    )


@router.get("/finished-stock")
async def list_finished_stock(
    db: DbSession, farm: CurrentFarm, perms: FeedingView
) -> list[FinishedFeedStockOut]:
    """Ready-to-dispense recipe balances created by successful mixes."""
    result = await db.execute(
        select(FeedFinishedStock, FeedRecipe.name)
        .join(FeedRecipe, FeedRecipe.code == FeedFinishedStock.recipe_code)
        .where(FeedFinishedStock.farm_id == farm.id)
        .order_by(FeedRecipe.name)
    )
    return [
        FinishedFeedStockOut(
            recipe_code=stock.recipe_code,
            recipe_name=recipe_name,
            qty_on_hand=stock.qty_on_hand,
        )
        for stock, recipe_name in result.all()
    ]


@router.post("/mix")
async def mix_batch(
    payload: MixIn,
    response: Response,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: FeedingManage,
    idempotency_key: RequiredIdempotencyKey,
) -> FeedRecipeOut:
    """Mix a batch of a recipe, decrementing inventory per recipe lines."""

    async def mutate() -> FeedRecipeOut:
        try:
            recipe = await mix_feed_batch(db, farm, payload.recipe_code, payload.batch_kg)
        except InsufficientFeedError as exc:
            raise HTTPException(status_code=400, detail="; ".join(exc.shortages)) from None
        except ValueError as exc:  # unknown recipe (batch_kg bounds are schema-level)
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return FeedRecipeOut.model_validate(recipe)

    return await execute_idempotent(
        db,
        http_response=response,
        key=idempotency_key,
        farm_id=farm.id,
        actor_id=user.id,
        operation="POST /api/feeding/mix",
        payload=payload,
        path_identity={},
        success_status=200,
        response_type=FeedRecipeOut,
        mutate=mutate,
    )


@router.get("/inventory")
async def list_inventory(
    db: DbSession, farm: CurrentFarm, perms: FeedingView
) -> list[FeedInventoryOut]:
    """Feed inventory for this farm, ordered by category then ingredient."""
    result = await db.execute(
        select(FeedInventory)
        .where(FeedInventory.farm_id == farm.id)
        .order_by(FeedInventory.category, FeedInventory.ingredient)
    )
    return [FeedInventoryOut.model_validate(item) for item in result.scalars().all()]


@router.post("/inventory/{item_id}/add")
async def add_stock(
    item_id: int,
    payload: StockAddIn,
    response: Response,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: FeedingManage,
    idempotency_key: RequiredIdempotencyKey,
) -> FeedInventoryOut:
    """Purchase stock: bump qty on hand, update last price, book a FEED expense."""

    async def mutate() -> FeedInventoryOut:
        if not 1 <= item_id <= MAX_INT32_ID:
            item = None
        else:
            # FOR UPDATE: a concurrent mix or restock must not overwrite this
            # read-modify-write of the balance (lost update).
            result = await db.execute(
                select(FeedInventory)
                .where(FeedInventory.id == item_id, FeedInventory.farm_id == farm.id)
                .with_for_update()
            )
            item = result.scalar_one_or_none()
        if item is None or item.farm_id != farm.id:
            raise HTTPException(status_code=404, detail="Feed inventory item not found")
        try:
            await add_feed_stock(
                db, farm, item, payload.qty_kg, payload.price_per_kg, created_by_id=user.id
            )
        except ValueError as exc:  # backstop — schema already bounds qty/price
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return FeedInventoryOut.model_validate(item)

    return await execute_idempotent(
        db,
        http_response=response,
        key=idempotency_key,
        farm_id=farm.id,
        actor_id=user.id,
        operation="POST /api/feeding/inventory/{item_id}/add",
        payload=payload,
        path_identity={"item_id": item_id},
        success_status=200,
        response_type=FeedInventoryOut,
        mutate=mutate,
    )
