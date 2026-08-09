"""Pydantic schemas for the feeding module."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .animals import BucketStr
from .common import NonNegativeMoneyFloat, PastOrTodayDate, QuantityKgFloat, StrictInputModel

ShiftStr = Literal["MORNING", "AFTERNOON", "NIGHT"]


class DispenseIn(StrictInputModel):
    bucket: BucketStr
    shift: ShiftStr
    recipe_code: str | None = Field(default=None, min_length=1, max_length=30)
    qty_kg: QuantityKgFloat
    date: PastOrTodayDate | None = None  # defaults to today


class MixIn(StrictInputModel):
    recipe_code: str = Field(min_length=1, max_length=30)
    batch_kg: QuantityKgFloat


class StockAddIn(StrictInputModel):
    qty_kg: QuantityKgFloat
    # Non-negative (not positive): an explicit ₹0 restock is real data — the
    # service books a ₹0 expense and zeroes the last price.
    price_per_kg: NonNegativeMoneyFloat | None = None


class FeedSettingIn(StrictInputModel):
    bucket: BucketStr
    daily_kg_per_head: QuantityKgFloat


class FeedInventoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ingredient: str
    category: str
    unit: str
    qty_on_hand: float
    reorder_level: float | None
    last_purchase_price_per_kg: float | None


class FinishedFeedStockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    recipe_code: str
    recipe_name: str
    qty_on_hand: float


class FeedRecipeLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ingredient: str
    kg_per_100kg: float
    category: str


class FeedRecipeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str | None
    lines: list[FeedRecipeLineOut] = []


class FeedingRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: date
    shift: str
    bucket: str
    recipe_code: str | None
    qty_kg: float


class FeedingHistoryOut(BaseModel):
    records: list[FeedingRecordOut]
    total: int
    limit: int
    offset: int


class BucketAllocationOut(BaseModel):
    bucket: str
    allocation: str  # SPEC allocation guidance for the bucket


class RecipeListOut(BaseModel):
    recipes: list[FeedRecipeOut]
    allocation: list[BucketAllocationOut]  # services.BUCKET_ALLOCATION_REFERENCE


class PlanLineOut(BaseModel):
    bucket: str
    recipe_code: str
    recipe_name: str
    heads: int
    kg_per_head: float
    daily_kg: float
    shifts: list[dict[str, object]]


class DispensingAggregateOut(BaseModel):
    """Exact full-day quantity for one plan allocation and shift."""

    bucket: str
    recipe_code: str | None
    shift: str
    qty_kg: float


class FeedingPlanOut(BaseModel):
    lines: list[PlanLineOut]
    # Latest bounded window for today's dashboard, ordered oldest→newest
    # within that window. Full history remains GET /api/feeding/records.
    records: list[FeedingRecordOut]
    records_total: int
    records_limit: int
    # Server-computed from the complete day ledger, never the bounded record
    # preview, so progress/completion remains truthful beyond 200 entries.
    dispensed_totals: list[DispensingAggregateOut]
