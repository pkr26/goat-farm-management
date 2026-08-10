"""Pydantic schemas for the finance module."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .common import (
    BoundedId,
    MoneyFloat,
    NonNegativeMoneyFloat,
    PastOrTodayDate,
    PostgresText,
    QuantityKgFloat,
    StrictInputModel,
)

TransactionTypeStr = Literal["INCOME", "EXPENSE"]
# Mirrors models.TransactionCategory exactly (v1 validated against the enum).
TransactionCategoryStr = Literal[
    "ANIMAL_SALE",
    "ANIMAL_PURCHASE",
    "FEED",
    "MEDICINE",
    "VET",
    "LABOUR",
    "EQUIPMENT",
    "MILK",
    "MANURE",
    "OTHER",
]


class TransactionIn(StrictInputModel):
    date: PastOrTodayDate
    type: TransactionTypeStr
    category: TransactionCategoryStr
    amount: MoneyFloat
    # transactions.notes String(255)
    notes: PostgresText | None = Field(default=None, max_length=255)
    related_animal_id: BoundedId | None = None  # API verifies same-farm existence


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: date
    type: str
    category: str
    amount: float
    notes: str | None
    related_animal_id: int | None
    animal_tag: str | None = None
    created_at: datetime
    source_type: str | None
    source_id: int | None
    correction_of_id: int | None
    voided_at: datetime | None
    voided_by_id: int | None
    void_reason: str | None


class TransactionCorrectionIn(StrictInputModel):
    """Audited replacement for an existing ledger row.

    Zero is allowed here so an erroneous system-generated amount can be
    neutralized without deleting its provenance.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    date: PastOrTodayDate
    type: TransactionTypeStr
    category: TransactionCategoryStr
    amount: NonNegativeMoneyFloat
    notes: PostgresText | None = Field(default=None, max_length=255)
    related_animal_id: BoundedId | None = None
    feed_quantity_kg: QuantityKgFloat | None = None
    reason: PostgresText = Field(min_length=3, max_length=255)


class PnlRowOut(BaseModel):
    month: str  # YYYY-MM
    income: float
    expense: float
    net: float
    categories: dict[str, dict[str, float]]


class FinanceOut(BaseModel):
    transactions: list[TransactionOut]
    transactions_total: int
    limit: int
    offset: int
    total_income: float
    total_expense: float
    pnl: list[PnlRowOut]
