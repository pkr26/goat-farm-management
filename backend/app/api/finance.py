"""Finance: transaction list with filters, add transaction, monthly P&L."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import false, func, select
from sqlalchemy.orm import selectinload

from ..deps import CurrentFarm, CurrentUser, DbSession, require_perm
from ..models import Animal, Transaction, TransactionType
from ..schemas.common import MAX_INT32_ID
from ..schemas.finance import (
    FinanceOut,
    PnlRowOut,
    TransactionCorrectionIn,
    TransactionIn,
    TransactionOut,
)
from ..services import monthly_pnl
from ..utils import add_months, money, utcnow

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
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FinanceOut:
    """Paginated transactions with optional filters, all-time totals and a
    true rolling 12-calendar-month P&L. ``transactions_total`` always reports
    the full filtered count so clients can expose honest pagination."""
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
    filtered_count = (
        await db.execute(select(func.count()).select_from(query.order_by(None).subquery()))
    ).scalar_one()
    result = await db.execute(
        query.order_by(Transaction.date.desc(), Transaction.id.desc()).offset(offset).limit(limit)
    )
    txns = list(result.scalars().all())

    # All-time totals exclude voided rows while the transaction list retains
    # them as an immutable audit trail.
    totals_result = await db.execute(
        select(Transaction.type, func.sum(Transaction.amount))
        .where(
            Transaction.farm_id == farm.id,
            Transaction.voided_at.is_(None),
        )
        .group_by(Transaction.type)
    )
    total_income = Decimal("0.00")
    total_expense = Decimal("0.00")
    for txn_type, total in totals_result.all():
        if txn_type == TransactionType.INCOME.value:
            total_income += total
        else:
            total_expense += total

    pnl = await monthly_pnl(db, farm)
    return FinanceOut(
        transactions=[_transaction_out(txn) for txn in txns],
        transactions_total=filtered_count,
        limit=limit,
        offset=offset,
        total_income=float(total_income),
        total_expense=float(total_expense),
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
        amount=money(payload.amount),
        related_animal_id=animal_pk,
        notes=(payload.notes or "").strip() or None,
        created_by_id=user.id,
    )
    db.add(txn)
    await db.commit()
    out = TransactionOut.model_validate(txn)
    out.animal_tag = animal_tag
    return out


@router.post("/transactions/{transaction_id}/correct", status_code=201)
async def correct_transaction(
    transaction_id: int,
    payload: TransactionCorrectionIn,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: FinanceManage,
) -> TransactionOut:
    """Void one ledger row and create its audited replacement atomically."""
    if not 1 <= transaction_id <= MAX_INT32_ID:
        txn = None
    else:
        txn = (
            await db.execute(
                select(Transaction).where(Transaction.id == transaction_id).with_for_update()
            )
        ).scalar_one_or_none()
    if txn is None or txn.farm_id != farm.id:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if txn.voided_at is not None:
        raise HTTPException(status_code=409, detail="Transaction has already been corrected")

    animal_pk = None
    animal_tag = None
    if payload.related_animal_id is not None:
        linked = (
            await db.get(Animal, payload.related_animal_id)
            if payload.related_animal_id <= MAX_INT32_ID
            else None
        )
        if linked is not None and linked.farm_id == farm.id:
            animal_pk = linked.id
            animal_tag = linked.tag_number

    txn.voided_at = utcnow()
    txn.voided_by_id = user.id
    txn.void_reason = payload.reason.strip()
    # Flush the void first so the active-source partial unique index permits
    # the corrected replacement for a system-generated source.
    await db.flush()
    replacement = Transaction(
        farm_id=farm.id,
        date=payload.date,
        type=payload.type,
        category=payload.category,
        amount=money(payload.amount),
        related_animal_id=animal_pk,
        notes=(payload.notes or "").strip() or None,
        created_by_id=user.id,
        source_type=txn.source_type,
        source_id=txn.source_id,
        correction_of_id=txn.id,
    )
    db.add(replacement)
    await db.commit()
    out = TransactionOut.model_validate(replacement)
    out.animal_tag = animal_tag
    return out
