"""Finance."""

from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Farm, Transaction, TransactionType
from ..utils import add_months, today


async def monthly_pnl(db: AsyncSession, farm: Farm, n_months: int = 12) -> list[dict[str, Any]]:
    """Income vs expense for the last ``n_months`` calendar months,
    including zero-activity months, most recent first.

    Aggregated in SQL (GROUP BY month/type/category) — the row-at-a-time
    Python sum loaded every transaction into memory. Output shape and
    rounding are unchanged: row totals rounded to 2dp, category sums raw.
    Voided rows remain in the audit trail but never affect financial totals."""
    current_month = today(farm.timezone).replace(day=1)
    first_month = add_months(current_month, -(n_months - 1))
    after_last_month = add_months(current_month, 1)
    month_col = func.to_char(Transaction.date, "YYYY-MM")
    result = await db.execute(
        select(month_col, Transaction.type, Transaction.category, func.sum(Transaction.amount))
        .where(
            Transaction.farm_id == farm.id,
            Transaction.date >= first_month,
            Transaction.date < after_last_month,
            Transaction.voided_at.is_(None),
        )
        .group_by(month_col, Transaction.type, Transaction.category)
    )
    # Pre-seed every calendar month. This prevents an old transaction-bearing
    # month from being presented as part of "last 12 months" merely because
    # recent months had no activity.
    months: dict[str, dict[str, Any]] = {
        month.strftime("%Y-%m"): {
            "month": month.strftime("%Y-%m"),
            "income": Decimal("0.00"),
            "expense": Decimal("0.00"),
            "categories": {},
        }
        for month in (add_months(current_month, -offset) for offset in range(n_months))
    }
    for month, txn_type, category, total in result.all():
        row = months.setdefault(
            month,
            {
                "month": month,
                "income": Decimal("0.00"),
                "expense": Decimal("0.00"),
                "categories": {},
            },
        )
        kind = "income" if txn_type == TransactionType.INCOME.value else "expense"
        row[kind] += total
        cat = row["categories"].setdefault(
            category, {"income": Decimal("0.00"), "expense": Decimal("0.00")}
        )
        cat[kind] += total

    rows = sorted(months.values(), key=lambda r: r["month"], reverse=True)
    for row in rows:
        row["income"] = round(row["income"], 2)
        row["expense"] = round(row["expense"], 2)
        row["net"] = round(row["income"] - row["expense"], 2)
    return rows
