"""Pydantic schemas for the feeding module."""

from datetime import date
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from ..models.feed_rules import DRY_ROUGHAGE  # single source of truth for the sentinel
from .animals import BucketStr
from .common import (
    NonNegativeMoneyFloat,
    PastOrTodayDate,
    QuantityKgFloat,
    StrictInputModel,
    _quantity_kg_precision,
)

ShiftStr = Literal["MORNING", "AFTERNOON", "NIGHT"]
# Mirrors models.IngredientCategory (green / dry / concentrate).
IngredientCategoryStr = Literal["ROUGHAGE_WET", "ROUGHAGE_DRY", "CONCENTRATE"]
# A dispensing record without a recipe cannot be reconciled with the ration
# plan and bypasses finished-feed stock deduction, so the router always
# refused it.  Saying so in the schema keeps the published contract (and every
# generated client) honest instead of advertising an optional field.
RECIPE_REQUIRED_MESSAGE = f"Recipe is required; use {DRY_ROUGHAGE} for the dry-roughage ration"


def _printable(value: str) -> str:
    # A recipe code is bound straight into a PostgreSQL comparison, and a text
    # column cannot hold a NUL byte: asyncpg raises
    # CharacterNotInRepertoireError, which no handler maps to a 4xx, so a code
    # carrying an escaped NUL answered an opaque 500. Recipe codes are short
    # identifiers — no control character is ever part of one.
    if any(char < " " or char == "\x7f" for char in value):
        raise ValueError("cannot contain control characters")
    return value


def _dispensed_recipe(value: str) -> str:
    # min_length=1 still admits "   ", which the router used to reject by hand
    # after trimming. Trim here so the rejection — and the trimmed code that is
    # stored, matched against a recipe and fingerprinted for idempotency — comes
    # from the one declared type.
    code = value.strip()
    if not code:
        raise ValueError(RECIPE_REQUIRED_MESSAGE)
    return code


RecipeCodeStr = Annotated[str, Field(min_length=1, max_length=30), AfterValidator(_printable)]
DispensedRecipeCodeStr = Annotated[RecipeCodeStr, AfterValidator(_dispensed_recipe)]


class DispenseIn(StrictInputModel):
    bucket: BucketStr
    shift: ShiftStr
    recipe_code: Annotated[
        DispensedRecipeCodeStr,
        Field(description=f"Ration dispensed; use {DRY_ROUGHAGE} for a grain-free ration"),
    ]
    qty_kg: QuantityKgFloat
    date: PastOrTodayDate | None = None  # defaults to today


class MixIn(StrictInputModel):
    recipe_code: RecipeCodeStr
    batch_kg: QuantityKgFloat


class StockAddIn(StrictInputModel):
    qty_kg: QuantityKgFloat
    # Non-negative (not positive): an explicit ₹0 restock is real data — the
    # service books a ₹0 expense and zeroes the last price.
    price_per_kg: NonNegativeMoneyFloat | None = None


class FeedSettingIn(StrictInputModel):
    bucket: BucketStr
    # Domain cap (RT-HIJ-5) on top of the shared whole-gram quantization:
    # real ration overrides sit far below 50 kg per head per day; the generic
    # 1e6 kg quantity ceiling only turned fat-fingered input into absurd plan
    # totals.
    daily_kg_per_head: Annotated[float, Field(gt=0, le=50), AfterValidator(_quantity_kg_precision)]


class FeedInventoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ingredient: str
    category: IngredientCategoryStr
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
    category: IngredientCategoryStr


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
    shift: ShiftStr
    bucket: BucketStr
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
    # Husbandry standards (wave-1G). Creep lines carry their age band
    # ("14–30 d" / "31–45 d" / "46–60 d"); per-head amounts are either scaled
    # from the bucket's mean latest weight (basis "weight", mean reported) or
    # the flat per-head default (basis "flat"). The buck BREEDING line notes
    # its mating-season supplement.
    creep_band: str | None = None
    basis: Literal["weight", "flat"] = "flat"
    mean_weight_kg: float | None = None
    note: str | None = None


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
