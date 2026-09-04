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
from .common import (
    _finite as _finite_validator,
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

# Fat bounds mirror the parlour milk-record bound (a sale cannot carry a fat
# reading the parlour itself would reject). Price ceilings are generous but
# finite: no real Telangana procurement rate approaches them, and they bound
# fabricated revenue from a mistyped rate. All four are strict finite floats
# (JSON numbers only — no string coercion, no NaN/Inf), matching the parlour
# side. The DB provenance CHECK is intentionally looser (fat 0-12, prices up
# to 1e9); these schema bounds are the enforced contract.
MilkFatPctFloat = Annotated[float, Field(strict=True, ge=3, le=12), _finite_validator]
MilkPricePerLitreFloat = Annotated[float, Field(strict=True, ge=0, le=500), _finite_validator]
MilkPricePerKgFatFloat = Annotated[float, Field(strict=True, ge=0, le=5_000), _finite_validator]
# The DB CHECK prices provenance in numeric(12,3) litres between 0.001 and
# 1e6: a positive-but-sub-milli value would pass a gt=0 schema bound and then
# die as an unhandled CheckViolation at flush (HTTP 500), so the schema floor
# is the storage floor.
MilkLitresFloat = Annotated[float, Field(strict=True, ge=0.001, le=1_000_000), _finite_validator]

# Categories the ledger only ever writes itself (animal exits and purchase
# batches carry their own audited provenance); a manual row in these
# categories would be indistinguishable from system-generated revenue.
SYSTEM_ONLY_CATEGORIES = frozenset({"ANIMAL_SALE", "ANIMAL_PURCHASE"})


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
    UNIT CONVENTION: the litre figure is treated as a kilogram (no ~1.03
    density conversion) — the simulation engine shares this convention, so
    internal reconciliations agree while plant statements paid on weighed
    kg carry a systematic ~3% volume-vs-mass gap.
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
    *,
    allow_absent_provenance: bool = False,
) -> None:
    """Shared provenance coherence for booking and correcting milk income.

    Milk income is never free prose: it must carry litres and one complete
    pricing basis (flat ₹/litre or the fat-based pair — never half of it), and
    when a basis is complete the amount must reproduce the priced total within
    one paisa so the ledger can never disagree with the milk it says was sold.
    """
    milk_fields = (
        txn.milk_litres,
        txn.milk_unit_price_per_litre,
        txn.milk_fat_pct,
        txn.milk_price_per_kg_fat,
    )
    is_milk_income = txn.category == "MILK" and txn.type == "INCOME"
    if all(field is None for field in milk_fields):
        if is_milk_income and not allow_absent_provenance:
            raise ValueError(
                "Milk income requires provenance: milk_litres plus a price "
                "(milk_unit_price_per_litre, or milk_fat_pct + milk_price_per_kg_fat)"
            )
        return
    if not is_milk_income:
        raise ValueError(
            "Milk litres / unit price are valid only on INCOME transactions in the MILK category"
        )
    if txn.milk_litres is None:
        raise ValueError("Milk provenance requires litres")
    if (txn.milk_fat_pct is None) != (txn.milk_price_per_kg_fat is None):
        raise ValueError(
            "Fat-based milk provenance requires both milk_fat_pct and milk_price_per_kg_fat"
        )
    if txn.milk_unit_price_per_litre is None and txn.milk_fat_pct is None:
        raise ValueError(
            "Milk provenance requires a price: milk_unit_price_per_litre or milk_price_per_kg_fat"
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
    # of fat).
    milk_litres: MilkLitresFloat | None = None
    milk_unit_price_per_litre: MilkPricePerLitreFloat | None = None
    milk_fat_pct: MilkFatPctFloat | None = None
    milk_price_per_kg_fat: MilkPricePerKgFatFloat | None = None

    @model_validator(mode="after")
    def _manual_row_policy(self) -> "TransactionIn":
        if self.category in SYSTEM_ONLY_CATEGORIES:
            raise ValueError(
                f"{self.category} rows are generated by the animal purchase/sale "
                "workflow and cannot be created manually"
            )
        _validate_milk_provenance(self)
        return self


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: date
    type: TransactionTypeStr
    category: TransactionCategoryStr
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
    milk_litres: MilkLitresFloat | None = None
    milk_unit_price_per_litre: MilkPricePerLitreFloat | None = None
    milk_fat_pct: MilkFatPctFloat | None = None
    milk_price_per_kg_fat: MilkPricePerKgFatFloat | None = None
    reason: PostgresText = Field(min_length=3, max_length=255)

    @model_validator(mode="after")
    def _milk_provenance_coherent(self) -> "TransactionCorrectionIn":
        # Corrections may keep a system-generated category (they replace an
        # existing audited row). A replacement may omit milk provenance only
        # when the row it replaces never carried any: pre-provenance legacy
        # MILK-income rows must stay correctable (amount/notes/date) instead
        # of being frozen. The API layer enforces that the original actually
        # lacked provenance — a replacement that DROPS the provenance of a
        # row that had it is rejected there.
        _validate_milk_provenance(self, allow_absent_provenance=True)
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
