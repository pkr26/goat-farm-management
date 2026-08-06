"""Pydantic schemas for the finance module."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .common import BoundedId, PastOrTodayDate, PositiveFloat

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


class TransactionIn(BaseModel):
    date: PastOrTodayDate
    type: TransactionTypeStr
    category: TransactionCategoryStr
    amount: PositiveFloat
    notes: str | None = Field(default=None, max_length=255)  # transactions.notes String(255)
    related_animal_id: BoundedId | None = None  # cross-farm ids are stripped


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


class PnlRowOut(BaseModel):
    month: str  # YYYY-MM
    income: float
    expense: float
    net: float
    categories: dict[str, dict[str, float]]


class FinanceOut(BaseModel):
    transactions: list[TransactionOut]
    total_income: float
    total_expense: float
    pnl: list[PnlRowOut]
