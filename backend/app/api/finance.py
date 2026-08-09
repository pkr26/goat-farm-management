"""Finance: transaction list with filters, add transaction, monthly P&L."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import false, func, select
from sqlalchemy.orm import selectinload

from ..deps import CurrentFarm, CurrentUser, DbSession, require_perm
from ..models import Animal, Transaction, TransactionType
from ..schemas.common import MAX_INT32_ID, MAX_PAGE_OFFSET
from ..schemas.finance import (
    FinanceOut,
    PnlRowOut,
    TransactionCorrectionIn,
    TransactionIn,
    TransactionOut,
)
from ..services import IdempotencyKey, execute_idempotent, monthly_pnl, require_farm_not_future
from ..utils import add_months, money, utcnow

router = APIRouter(prefix="/api/finance", tags=["finance"])

FinanceView = Annotated[set[str], Depends(require_perm("finance.view"))]
FinanceManage = Annotated[set[str], Depends(require_perm("finance.manage"))]


def _transaction_out(txn: Transaction) -> TransactionOut:
    """ORM → schema; `related_animal` must already be loaded (selectinload)."""
    out = TransactionOut.model_validate(txn)
    out.animal_tag = txn.related_animal.tag_number if txn.related_animal else None
    return out


async def _resolve_related_animal(
    db: DbSession, farm: CurrentFarm, animal_id: int | None
) -> tuple[int | None, str | None]:
    """Resolve an explicitly supplied ledger link or reject it.

    Silently converting a stale/foreign id to NULL acknowledges a transaction
    different from the one the operator reviewed. Unknown and cross-farm ids
    deliberately share one response so this check is not an enumeration
    oracle.
    """
    if animal_id is None:
        return None, None
    linked = await db.get(Animal, animal_id) if animal_id <= MAX_INT32_ID else None
    if linked is None or linked.farm_id != farm.id:
        raise HTTPException(status_code=400, detail="Related animal is not on this farm")
    return linked.id, linked.tag_number


@router.get("")
async def list_transactions(
    db: DbSession,
    farm: CurrentFarm,
    perms: FinanceView,
    month: str | None = None,
    type: str | None = None,
    category: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
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
            try:
                following_month = add_months(first, 1)
            except ValueError:
                # ``9999-12`` is parseable but has no representable following
                # month. Treat it like every other unusable month filter,
                # never as an uncaught date-overflow 500.
                query = query.where(false())
            else:
                query = query.where(
                    Transaction.date >= first,
                    Transaction.date < following_month,
                )
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
    response: Response,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: FinanceManage,
    idempotency_key: IdempotencyKey = None,
) -> TransactionOut:
    """Record an income/expense with an optional verified farm-animal link."""

    async def mutate() -> TransactionOut:
        try:
            require_farm_not_future(payload.date, farm, "transaction date")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        animal_pk, animal_tag = await _resolve_related_animal(db, farm, payload.related_animal_id)
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
        await db.flush()
        out = TransactionOut.model_validate(txn)
        out.animal_tag = animal_tag
        return out

    return await execute_idempotent(
        db,
        http_response=response,
        key=idempotency_key,
        farm_id=farm.id,
        actor_id=user.id,
        operation="POST /api/finance/new",
        payload=payload,
        path_identity={},
        success_status=201,
        response_type=TransactionOut,
        mutate=mutate,
    )


@router.post("/transactions/{transaction_id}/correct", status_code=201)
async def correct_transaction(
    transaction_id: int,
    payload: TransactionCorrectionIn,
    response: Response,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: FinanceManage,
    idempotency_key: IdempotencyKey = None,
) -> TransactionOut:
    """Void one ledger row and create its audited replacement atomically."""

    async def mutate() -> TransactionOut:
        try:
            require_farm_not_future(payload.date, farm, "replacement transaction date")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if not 1 <= transaction_id <= MAX_INT32_ID:
            txn = None
        else:
            txn = (
                await db.execute(
                    select(Transaction)
                    .where(Transaction.id == transaction_id, Transaction.farm_id == farm.id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
        if txn is None or txn.farm_id != farm.id:
            raise HTTPException(status_code=404, detail="Transaction not found")
        if txn.voided_at is not None:
            raise HTTPException(status_code=409, detail="Transaction has already been corrected")

        animal_pk, animal_tag = await _resolve_related_animal(db, farm, payload.related_animal_id)

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
        await db.flush()
        out = TransactionOut.model_validate(replacement)
        out.animal_tag = animal_tag
        return out

    return await execute_idempotent(
        db,
        http_response=response,
        key=idempotency_key,
        farm_id=farm.id,
        actor_id=user.id,
        operation="POST /api/finance/transactions/{transaction_id}/correct",
        payload=payload,
        path_identity={"transaction_id": transaction_id},
        success_status=201,
        response_type=TransactionOut,
        mutate=mutate,
    )
