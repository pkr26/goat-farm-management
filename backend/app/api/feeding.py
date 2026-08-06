"""Feeding: today's 3-shift plan + dispensing log, recipes + allocation
reference, feed inventory (add stock / mix batch)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..deps import CurrentFarm, CurrentUser, DbSession, require_perm
from ..models import FeedingRecord, FeedInventory, FeedRecipe
from ..schemas.common import MAX_INT32_ID
from ..schemas.feeding import (
    BucketAllocationOut,
    DispenseIn,
    FeedingPlanOut,
    FeedingRecordOut,
    FeedInventoryOut,
    FeedRecipeOut,
    FeedSettingIn,
    MixIn,
    PlanLineOut,
    RecipeListOut,
    StockAddIn,
)
from ..services import (
    BUCKET_ALLOCATION_REFERENCE,
    InsufficientFeedError,
    add_feed_stock,
    feeding_plan,
    mix_feed_batch,
    record_dispensing,
    set_daily_kg_per_head,
)
from ..utils import today

router = APIRouter(prefix="/api/feeding", tags=["feeding"])

FeedingView = Annotated[set[str], Depends(require_perm("feeding.view"))]
FeedingManage = Annotated[set[str], Depends(require_perm("feeding.manage"))]


@router.get("/plan")
async def feeding_today(db: DbSession, farm: CurrentFarm, perms: FeedingView) -> FeedingPlanOut:
    """Today's 3-shift plan (bucket × recipe × headcount) + today's dispensing log."""
    plan = await feeding_plan(db, farm)
    result = await db.execute(
        select(FeedingRecord)
        .where(FeedingRecord.farm_id == farm.id, FeedingRecord.date == today())
        .order_by(FeedingRecord.id)
    )
    return FeedingPlanOut(
        lines=[PlanLineOut.model_validate(line) for line in plan],
        records=[FeedingRecordOut.model_validate(record) for record in result.scalars().all()],
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
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: FeedingManage,
) -> FeedingRecordOut:
    """Record feed actually dispensed to a bucket on a shift."""
    code = (payload.recipe_code or "").strip() or None
    # Only recipes that actually exist may be recorded against a feeding.
    if code is not None:
        result = await db.execute(select(FeedRecipe.id).where(FeedRecipe.code == code))
        if result.scalar_one_or_none() is None:
            raise HTTPException(status_code=400, detail=f"Unknown recipe {code}")
    record = await record_dispensing(
        db,
        farm,
        payload.bucket,
        payload.shift,
        code,
        payload.qty_kg,
        payload.date or today(),
        created_by_id=user.id,
    )
    await db.commit()
    return FeedingRecordOut.model_validate(record)


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
            for bucket, allocation in BUCKET_ALLOCATION_REFERENCE
        ],
    )


@router.post("/mix")
async def mix_batch(
    payload: MixIn, db: DbSession, farm: CurrentFarm, perms: FeedingManage
) -> FeedRecipeOut:
    """Mix a batch of a recipe, decrementing inventory per recipe lines."""
    try:
        recipe = await mix_feed_batch(db, farm, payload.recipe_code, payload.batch_kg)
    except InsufficientFeedError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail="; ".join(exc.shortages)) from None
    except ValueError as exc:  # unknown recipe (batch_kg bounds are schema-level)
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from None
    await db.commit()
    return FeedRecipeOut.model_validate(recipe)


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
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: FeedingManage,
) -> FeedInventoryOut:
    """Purchase stock: bump qty on hand, update last price, book a FEED expense."""
    item = await db.get(FeedInventory, item_id) if 1 <= item_id <= MAX_INT32_ID else None
    if item is None or item.farm_id != farm.id:
        raise HTTPException(status_code=404, detail="Feed inventory item not found")
    try:
        await add_feed_stock(
            db, farm, item, payload.qty_kg, payload.price_per_kg, created_by_id=user.id
        )
    except ValueError as exc:  # backstop — the schema already bounds qty/price
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from None
    await db.commit()
    return FeedInventoryOut.model_validate(item)
