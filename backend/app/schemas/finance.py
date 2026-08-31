"""Pydantic schemas for the finance module."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..utils import money
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

# A paise-rounded total may differ from the provenance product by one paisa of
# rounding; anything beyond that is a mistyped amount or price, not rounding.
_MILK_AMOUNT_TOLERANCE = Decimal("0.01")

MilkFatPctFloat = Annotated[float, Field(ge=0, le=12)]
MilkPricePerKgFatFloat = Annotated[float, Field(ge=0, le=1_000_000_000)]


def _expected_milk_amount(
    litres: float,
    unit_price_per_litre: float | None,
    fat_pct: float | None,
    price_per_kg_fat: float | None,
) -> Decimal | None:
    """Paise-exact amount implied by complete milk provenance, else None.

    Fat-based procurement wins whenever its pair is complete — the dairy
    plant pays for fat solids, so litres x fat%/100 x ₹/kg-fat is the price
    of record; a flat ₹/litre only prices the sale when no fat pair is given.
    Converting through ``str`` keeps binary-float noise out of the ledger.
    """
    if fat_pct is not None and price_per_kg_fat is not None:
        return money(
            Decimal(str(litres)) * Decimal(str(fat_pct)) / 100 * Decimal(str(price_per_kg_fat))
        )
    if unit_price_per_litre is not None:
        return money(Decimal(str(litres)) * Decimal(str(unit_price_per_litre)))
    return None


def _validate_milk_provenance(
    txn: "TransactionIn | TransactionCorrectionIn",
) -> None:
    """Shared provenance coherence for booking and correcting milk income.

    Provenance is all-or-nothing: any milk field requires the MILK/INCOME
    context, litres, and one complete pricing basis (flat ₹/litre or the
    fat-based pair — never half of it). When a basis is complete the amount
    is not free prose: it must reproduce the priced total within one paisa
    so the ledger can never disagree with the milk it says was sold.
    """
    milk_fields = (
        txn.milk_litres,
        txn.milk_unit_price_per_litre,
        txn.milk_fat_pct,
        txn.milk_price_per_kg_fat,
    )
    if all(field is None for field in milk_fields):
        return
    if txn.category != "MILK" or txn.type != "INCOME":
        raise ValueError(
            "Milk litres / unit price are valid only on INCOME transactions "
            "in the MILK category"
        )
    if txn.milk_litres is None:
        raise ValueError("Milk provenance requires litres")
    if (txn.milk_fat_pct is None) != (txn.milk_price_per_kg_fat is None):
        raise ValueError(
            "Fat-based milk provenance requires both milk_fat_pct and milk_price_per_kg_fat"
        )
    if txn.milk_unit_price_per_litre is None and txn.milk_fat_pct is None:
        raise ValueError(
            "Milk provenance requires a price: milk_unit_price_per_litre or "
            "milk_price_per_kg_fat"
        )
    expected = _expected_milk_amount(
        txn.milk_litres,
        txn.milk_unit_price_per_litre,
        txn.milk_fat_pct,
        txn.milk_price_per_kg_fat,
    )
    booked = money(txn.amount)
    if expected is not None and abs(booked - expected) > _MILK_AMOUNT_TOLERANCE:
        raise ValueError(
            f"Milk provenance prices to ₹{expected}, but ₹{booked} was booked; "
            "correct the amount or the provenance"
        )


class TransactionIn(StrictInputModel):
    date: PastOrTodayDate
    type: TransactionTypeStr
    category: TransactionCategoryStr
    amount: MoneyFloat
    # transactions.notes String(255)
    notes: PostgresText | None = Field(default=None, max_length=255)
    related_animal_id: BoundedId | None = None  # API verifies same-farm existence
    # Optional milk-sale provenance (valid only on INCOME/MILK rows): flat
    # ₹/litre, or fat-based procurement (fat % of the shipment plus ₹ per kg
    # of fat). The DB CHECK mirrors these bounds.
    milk_litres: Annotated[float, Field(gt=0, le=1_000_000)] | None = None
    milk_unit_price_per_litre: Annotated[float, Field(ge=0, le=1_000_000_000)] | None = None
    milk_fat_pct: MilkFatPctFloat | None = None
    milk_price_per_kg_fat: MilkPricePerKgFatFloat | None = None

    @model_validator(mode="after")
    def _milk_provenance_coherent(self) -> "TransactionIn":
        _validate_milk_provenance(self)
        return self


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
    milk_litres: float | None
    milk_unit_price_per_litre: float | None
    milk_fat_pct: float | None
    milk_price_per_kg_fat: float | None


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
    milk_litres: Annotated[float, Field(gt=0, le=1_000_000)] | None = None
    milk_unit_price_per_litre: Annotated[float, Field(ge=0, le=1_000_000_000)] | None = None
    milk_fat_pct: MilkFatPctFloat | None = None
    milk_price_per_kg_fat: MilkPricePerKgFatFloat | None = None
    reason: PostgresText = Field(min_length=3, max_length=255)

    @model_validator(mode="after")
    def _milk_provenance_coherent(self) -> "TransactionCorrectionIn":
        _validate_milk_provenance(self)
        return self


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
