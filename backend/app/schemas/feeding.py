"""Pydantic schemas for the feeding module."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .animals import BucketStr
from .common import PastOrTodayDate, PositiveFloat

ShiftStr = Literal["MORNING", "AFTERNOON", "NIGHT"]


class DispenseIn(BaseModel):
    bucket: BucketStr
    shift: ShiftStr
    recipe_code: str | None = None  # validated against FeedRecipe when given
    qty_kg: PositiveFloat
    date: PastOrTodayDate | None = None  # defaults to today


class MixIn(BaseModel):
    recipe_code: str = Field(min_length=1)
    batch_kg: PositiveFloat


class StockAddIn(BaseModel):
    qty_kg: PositiveFloat
    price_per_kg: PositiveFloat | None = None


class FeedSettingIn(BaseModel):
    bucket: BucketStr
    daily_kg_per_head: PositiveFloat


class FeedInventoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ingredient: str
    category: str
    unit: str
    qty_on_hand: float
    reorder_level: float | None
    last_purchase_price_per_kg: float | None


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


class FeedingPlanOut(BaseModel):
    lines: list[PlanLineOut]
    records: list[FeedingRecordOut]  # recent dispensing log
