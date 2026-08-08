"""Finance: transaction list with filters, add transaction, monthly P&L."""

import math
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import false, func, select
from sqlalchemy.orm import selectinload

from ..deps import CurrentFarm, CurrentUser, DbSession, require_perm
from ..models import Animal, Transaction, TransactionType
from ..schemas.common import MAX_INT32_ID
from ..schemas.finance import FinanceOut, PnlRowOut, TransactionIn, TransactionOut
from ..services import monthly_pnl
from ..utils import add_months

router = APIRouter(prefix="/api/finance", tags=["finance"])

FinanceView = Annotated[set[str], Depends(require_perm("finance.view"))]
FinanceManage = Annotated[set[str], Depends(require_perm("finance.manage"))]


def _transaction_out(txn: Transaction) -> TransactionOut:
    """ORM → schema; `related_animal` must already be loaded (selectinload)."""
    out = TransactionOut.model_validate(txn)
    out.animal_tag = txn.related_animal.tag_number if txn.related_animal else None
    return out


@router.get("")
async def list_transactions(
    db: DbSession,
    farm: CurrentFarm,
    perms: FinanceView,
    month: str | None = None,
    type: str | None = None,
    category: str | None = None,
) -> FinanceOut:
    """Transactions (newest first, max 200) with optional month/type/category
    filters, all-time totals and the 12-month P&L."""
    query = (
        select(Transaction)
        .options(selectinload(Transaction.related_animal))
        .where(Transaction.farm_id == farm.id)
    )
    if month:
        try:
            first = datetime.strptime(month, "%Y-%m").date()
        except ValueError:  # v1's LIKE on a garbage month simply matched nothing
            query = query.where(false())
        else:
            query = query.where(Transaction.date >= first, Transaction.date < add_months(first, 1))
    if type:
        query = query.where(Transaction.type == type)
    if category:
        query = query.where(Transaction.category == category)
    result = await db.execute(
        query.order_by(Transaction.date.desc(), Transaction.id.desc()).limit(200)
    )
    # Rows poisoned before the schema bounds existed (inf/nan amounts) are
    # unserializable — never serve them.
    txns = [txn for txn in result.scalars().all() if math.isfinite(txn.amount)]

    # All-time totals aggregated in SQL; the finite filter drops
    # legacy poisoned rows (NaN fails `amount < inf` in PostgreSQL since NaN
    # sorts above +inf) instead of poisoning the sums.
    totals_result = await db.execute(
        select(Transaction.type, func.sum(Transaction.amount))
        .where(
            Transaction.farm_id == farm.id,
            Transaction.amount < math.inf,
            Transaction.amount > -math.inf,
        )
        .group_by(Transaction.type)
    )
    total_income = 0.0
    total_expense = 0.0
    for txn_type, total in totals_result.all():
        if txn_type == TransactionType.INCOME.value:
            total_income += total
        else:
            total_expense += total

    pnl = await monthly_pnl(db, farm)
    return FinanceOut(
        transactions=[_transaction_out(txn) for txn in txns],
        total_income=total_income,
        total_expense=total_expense,
        pnl=[PnlRowOut.model_validate(row) for row in pnl],
    )


@router.post("/new", status_code=201)
async def add_transaction(
    payload: TransactionIn,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: FinanceManage,
) -> TransactionOut:
    """Record an income/expense; a foreign-farm animal link is stripped, not stored."""
    # Only link animals of this farm — a cross-farm (or unknown) id is stripped.
    animal_pk = None
    animal_tag = None
    if payload.related_animal_id is not None:
        # Ids above the int4 PK ceiling cannot exist — stripped like any
        # unknown id, never an asyncpg int32 DataError (500).
        linked = (
            await db.get(Animal, payload.related_animal_id)
            if payload.related_animal_id <= MAX_INT32_ID
            else None
        )
        if linked is not None and linked.farm_id == farm.id:
            animal_pk = linked.id
            animal_tag = linked.tag_number
    txn = Transaction(
        farm_id=farm.id,
        date=payload.date,
        type=payload.type,
        category=payload.category,
        amount=payload.amount,
        related_animal_id=animal_pk,
        notes=(payload.notes or "").strip() or None,
        created_by_id=user.id,
    )
    db.add(txn)
    await db.commit()
    out = TransactionOut.model_validate(txn)
    out.animal_tag = animal_tag
    return out
