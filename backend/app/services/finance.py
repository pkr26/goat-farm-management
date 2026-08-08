"""Finance."""

import math
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Farm, Transaction, TransactionType


async def monthly_pnl(db: AsyncSession, farm: Farm, n_months: int = 12) -> list[dict[str, Any]]:
    """Income vs expense by month (and category) for the last n_months,
    most recent first.

    Aggregated in SQL (GROUP BY month/type/category) — the row-at-a-time
    Python sum loaded every transaction into memory. Output shape and
    rounding are unchanged: row totals rounded to 2dp, category sums raw.
    Rows poisoned before the schema bounds existed (inf/nan amounts) are
    excluded by the finite filter below — they can't serialize."""
    month_col = func.to_char(Transaction.date, "YYYY-MM")
    result = await db.execute(
        select(month_col, Transaction.type, Transaction.category, func.sum(Transaction.amount))
        .where(
            Transaction.farm_id == farm.id,
            # Finite filter: in PostgreSQL NaN = NaN is TRUE and NaN sorts
            # ABOVE +inf, so `amount == amount` is a no-op kept only for
            # documentation — the `< inf` bound is what actually excludes NaN
            # (and ±inf fail the two bounds).
            Transaction.amount == Transaction.amount,
            Transaction.amount < math.inf,
            Transaction.amount > -math.inf,
        )
        .group_by(month_col, Transaction.type, Transaction.category)
    )
    months: dict[str, dict[str, Any]] = {}
    for month, txn_type, category, total in result.all():
        row = months.setdefault(
            month, {"month": month, "income": 0.0, "expense": 0.0, "categories": {}}
        )
        kind = "income" if txn_type == TransactionType.INCOME.value else "expense"
        row[kind] += total
        cat = row["categories"].setdefault(category, {"income": 0.0, "expense": 0.0})
        cat[kind] += total

    rows = sorted(months.values(), key=lambda r: r["month"], reverse=True)[:n_months]
    for row in rows:
        row["income"] = round(row["income"], 2)
        row["expense"] = round(row["expense"], 2)
        row["net"] = round(row["income"] - row["expense"], 2)
    return rows
