"""Finance: transaction list with filters, add transaction, monthly P&L."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import false, func, select
from sqlalchemy.orm import selectinload

from ..deps import CurrentFarm, CurrentUser, DbSession, require_perm
from ..models import (
    Animal,
    BreedingOutcome,
    BreedingRecord,
    BucketMove,
    FeedInventory,
    HealthEvent,
    PurchaseBatch,
    Transaction,
    TransactionType,
)
from ..schemas.common import MAX_INT32_ID, MAX_PAGE_OFFSET
from ..schemas.finance import (
    FinanceOut,
    PnlRowOut,
    TransactionCategoryStr,
    TransactionCorrectionIn,
    TransactionIn,
    TransactionOut,
    TransactionTypeStr,
)
from ..services import (
    IdempotencyKey,
    execute_idempotent,
    monthly_pnl,
    require_animal_event_chronology,
    require_farm_not_future,
    require_purchase_before_recorded_facts,
    require_status_after_recorded_facts,
)
from ..utils import add_months, money, utcnow

router = APIRouter(prefix="/api/finance", tags=["finance"])

FinanceView = Annotated[set[str], Depends(require_perm("finance.view"))]
FinanceManage = Annotated[set[str], Depends(require_perm("finance.manage"))]

_MAX_FEED_UNIT_PRICE = Decimal("1000000000.00")


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


async def _locked_source_animal(db: DbSession, farm: CurrentFarm, animal_id: int) -> Animal:
    animal = await db.get(Animal, animal_id, with_for_update=True) if animal_id > 0 else None
    if animal is None or animal.farm_id != farm.id:
        raise HTTPException(
            status_code=409,
            detail="The animal this transaction was booked from is no longer on this farm",
        )
    return animal


def _corrected_feed_unit_price(txn: Transaction, amount: Decimal) -> Decimal | None:
    if txn.feed_quantity_kg is None or txn.feed_unit_price_per_kg is None:
        return None
    if amount == txn.amount:
        # Total cost was rounded to paise when the restock was booked; dividing
        # it back by quantity can lose the actual unit price (0.333 kg @ ₹1.00
        # stores a ₹0.33 total, whose quotient rounds to ₹0.99).
        return txn.feed_unit_price_per_kg
    unit_price = money(amount / txn.feed_quantity_kg)
    if amount > 0 and unit_price < Decimal("0.01"):
        raise HTTPException(
            status_code=422,
            detail="A positive corrected feed purchase must be at least ₹0.01 per kg",
        )
    if unit_price > _MAX_FEED_UNIT_PRICE:
        raise HTTPException(
            status_code=422,
            detail="Corrected feed purchase implies a price above ₹1,000,000,000.00 per kg",
        )
    return unit_price


def _latest_active_feed_purchase_stmt(farm_id: int, *criteria: Any) -> Any:
    """Newest active FEED_PURCHASE under the (date, source_id) ordering contract.

    Every consumer of "the latest purchase" must share this exact ordering:
    replacement transaction ids change, but corrections inherit their source
    pair, so date plus source_id stays deterministic even when two restocks
    share the same business date.
    """
    return (
        select(Transaction)
        .where(
            Transaction.farm_id == farm_id,
            Transaction.source_type == "FEED_PURCHASE",
            Transaction.voided_at.is_(None),
            *criteria,
        )
        .order_by(Transaction.date.desc(), Transaction.source_id.desc())
        .limit(1)
    )


async def _reconcile_feed_purchase(
    db: DbSession,
    farm: CurrentFarm,
    txn: Transaction,
    payload: TransactionCorrectionIn,
) -> tuple[int | None, str | None]:
    """Reprice the stock item's latest active, durably identified purchase.

    ``source_id`` is the stable chain id: replacement transaction ids change,
    but every audited correction inherits its source pair. Date plus that id
    therefore provides a deterministic latest-purchase ordering even when two
    restocks share the same business date.
    """
    amount = money(payload.amount)
    if (
        txn.feed_inventory_id is None
        and txn.feed_quantity_kg is None
        and txn.feed_unit_price_per_kg is None
    ):
        # Rows recorded before durable inventory/quantity provenance existed
        # never fed FeedInventory.last_purchase_price_per_kg, so correcting
        # them is a pure ledger edit with nothing to reconcile. Refusing
        # amount/date corrections here would permanently freeze every
        # pre-provenance purchase the moment the deployment upgrades.
        return await _resolve_related_animal(db, farm, txn.related_animal_id)
    if txn.source_id is None:
        raise HTTPException(status_code=409, detail="This feed purchase has no stable source id")
    if amount == txn.amount and payload.date == txn.date:
        # A notes-only correction cannot move the displayed last-purchase
        # price; skip the inventory row lock and both ordering probes.
        return await _resolve_related_animal(db, farm, txn.related_animal_id)

    inventory = (
        await db.execute(
            select(FeedInventory)
            .where(
                FeedInventory.id == txn.feed_inventory_id,
                FeedInventory.farm_id == farm.id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if inventory is None:
        raise HTTPException(
            status_code=409,
            detail="The feed inventory item this purchase updated no longer exists",
        )

    other_latest = (
        await db.execute(
            _latest_active_feed_purchase_stmt(
                farm.id,
                Transaction.feed_inventory_id == inventory.id,
                Transaction.id != txn.id,
            )
        )
    ).scalar_one_or_none()

    corrected_unit_price = _corrected_feed_unit_price(txn, amount)
    # The all-NULL early return plus ck_transactions_feed_purchase_provenance
    # (columns are all-or-none) guarantee complete pricing on this path.
    assert corrected_unit_price is not None

    other_key = (
        (other_latest.date, other_latest.source_id)
        if other_latest is not None and other_latest.source_id is not None
        else None
    )
    old_key = (txn.date, txn.source_id)
    corrected_key = (payload.date, txn.source_id)
    old_is_latest = other_key is None or old_key >= other_key
    corrected_is_latest = other_key is None or corrected_key >= other_key
    if not (old_is_latest or corrected_is_latest):
        # Correcting an older chain cannot change the inventory's displayed
        # last-purchase price. Its replacement still retains its own unit price.
        return await _resolve_related_animal(db, farm, txn.related_animal_id)

    if corrected_is_latest:
        latest_key = corrected_key
        latest_unit_price = corrected_unit_price
    else:
        if other_latest is None or other_key is None or other_latest.feed_unit_price_per_kg is None:
            raise HTTPException(
                status_code=409,
                detail="The latest feed purchase has incomplete inventory provenance",
            )
        latest_key = other_key
        latest_unit_price = other_latest.feed_unit_price_per_kg

    # A pre-provenance row cannot be associated with a particular inventory
    # item. If it is at least as recent as our chosen structured winner, we
    # cannot truthfully assert which purchase should supply this item's price.
    legacy_latest = (
        await db.execute(
            _latest_active_feed_purchase_stmt(
                farm.id,
                Transaction.feed_inventory_id.is_(None),
                Transaction.feed_quantity_kg.is_(None),
                Transaction.feed_unit_price_per_kg.is_(None),
            )
        )
    ).scalar_one_or_none()
    if (
        legacy_latest is not None
        and legacy_latest.source_id is not None
        and latest_key <= (legacy_latest.date, legacy_latest.source_id)
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "A legacy feed purchase may be the latest purchase for this inventory; "
                "its unit price cannot be reconciled safely"
            ),
        )
    inventory.last_purchase_price_per_kg = latest_unit_price
    return await _resolve_related_animal(db, farm, txn.related_animal_id)


async def _reconcile_source_record(
    db: DbSession,
    farm: CurrentFarm,
    txn: Transaction,
    payload: TransactionCorrectionIn,
) -> tuple[int | None, str | None] | None:
    """Keep a system-generated row and the record that produced it in step.

    A source-linked transaction is the ledger's copy of a domain event and the
    replacement inherits its ``source_type``/``source_id``, so it must stay the
    same kind of event — re-booking a sale as an expense left the animal
    profile advertising a sale price that existed nowhere in the ledger — and a
    corrected amount must reach the denormalized copy the domain record renders
    as authoritative money. Nothing else can rewrite those fields afterwards (a
    sold animal cannot change status again), so a correction that skipped them
    diverged permanently.
    """
    if txn.source_type is None or txn.source_id is None:
        return None
    if payload.type != txn.type or payload.category != txn.category:
        raise HTTPException(
            status_code=422,
            detail=(
                f"A {txn.source_type} transaction must stay {txn.type}/{txn.category}; "
                "correct its amount, date or notes instead"
            ),
        )
    amount = money(payload.amount)
    canonical_related: tuple[int | None, str | None]
    if txn.source_type == "ANIMAL_SALE":
        animal = await _locked_source_animal(db, farm, txn.source_id)
        auto_aborted = (
            await db.execute(
                select(BreedingRecord.id)
                .where(
                    BreedingRecord.farm_id == farm.id,
                    BreedingRecord.doe_id == animal.id,
                    BreedingRecord.outcome == BreedingOutcome.ABORTED.value,
                    BreedingRecord.loss_cause == "ANIMAL_STATUS_CHANGE",
                )
                .order_by(BreedingRecord.id)
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if auto_aborted is not None and payload.date != animal.status_date:
            # Pregnancy-loss attribution is an immutable audit fact at the DB
            # layer. Letting only the sale date move would split the one status
            # event into contradictory dates, so date correction is unsafe.
            raise HTTPException(
                status_code=409,
                detail=(
                    "This sale date also anchors an immutable pregnancy auto-abort; "
                    "book a compensating entry instead"
                ),
            )
        try:
            require_animal_event_chronology(animal, payload.date, "Sale date")
            await require_status_after_recorded_facts(db, animal, payload.date)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        withdrawal = (
            await db.execute(
                select(func.max(HealthEvent.withdrawal_until)).where(
                    HealthEvent.farm_id == farm.id,
                    HealthEvent.animal_id == animal.id,
                    HealthEvent.withdrawal_until >= payload.date,
                )
            )
        ).scalar_one_or_none()
        if withdrawal is not None:
            raise HTTPException(
                status_code=409,
                detail=f"Sale/cull is blocked by medicine withdrawal through {withdrawal}",
            )
        animal.sale_price = amount
        animal.status_date = payload.date
        canonical_related = (animal.id, animal.tag_number)
    elif txn.source_type == "ANIMAL_PURCHASE":
        animal = await _locked_source_animal(db, farm, txn.source_id)
        if animal.effective_dob is not None and payload.date < animal.effective_dob:
            raise HTTPException(
                status_code=422,
                detail=f"Purchase date cannot predate {animal.tag_number}'s recorded birth date",
            )
        if animal.status_date is not None and payload.date > animal.status_date:
            raise HTTPException(
                status_code=422,
                detail=f"Purchase date cannot follow {animal.tag_number}'s terminal status date",
            )
        try:
            await require_purchase_before_recorded_facts(db, animal, payload.date)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        initial_move = (
            await db.execute(
                select(BucketMove)
                .where(BucketMove.animal_id == animal.id, BucketMove.from_bucket.is_(None))
                .order_by(BucketMove.id)
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if initial_move is not None:
            initial_move.effective_date = payload.date
        animal.purchase_price = amount
        animal.purchase_date = payload.date
        canonical_related = (animal.id, animal.tag_number)
    elif txn.source_type == "PURCHASE_BATCH":
        batch = (
            await db.execute(
                select(PurchaseBatch)
                .where(PurchaseBatch.id == txn.source_id, PurchaseBatch.farm_id == farm.id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if batch is None:
            raise HTTPException(
                status_code=409,
                detail="The purchase batch this transaction was booked from no longer exists",
            )
        allocated_count = (
            await db.execute(
                select(func.count(Animal.id)).where(
                    Animal.farm_id == farm.id,
                    Animal.purchase_batch_id == batch.id,
                )
            )
        ).scalar_one()
        if allocated_count:
            if amount != txn.amount or payload.date != txn.date:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "A PURCHASE_BATCH amount and date are allocated across its animals; "
                        "book a compensating entry instead"
                    ),
                )
        else:
            # No animal ever received a per-head allocation or purchase date,
            # so this ledger-only batch can be corrected without fan-out.
            batch.total_price = amount
            batch.date = payload.date
        canonical_related = (txn.related_animal_id, None)
    elif txn.source_type == "FEED_PURCHASE":
        canonical_related = await _reconcile_feed_purchase(db, farm, txn, payload)
    else:
        # HEALTH_EVENT and any future shared sources may represent a bounded
        # submission fan-out. Notes are ledger narrative, but changing money or
        # date without a source-specific reconciler would create two versions
        # of the same event.
        if amount != txn.amount or payload.date != txn.date:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"A {txn.source_type} amount and date are shared by the records it was "
                    "booked from; book a compensating entry instead"
                ),
            )
        canonical_related = await _resolve_related_animal(db, farm, txn.related_animal_id)

    canonical_id, canonical_tag = canonical_related
    if payload.related_animal_id not in (None, canonical_id):
        raise HTTPException(
            status_code=422,
            detail="A source-linked transaction cannot be moved to a different animal",
        )
    return canonical_id, canonical_tag


@router.get("")
async def list_transactions(
    db: DbSession,
    farm: CurrentFarm,
    perms: FinanceView,
    month: str | None = None,
    # Typed against the ledger's own vocabulary: an untyped str was bound
    # straight into the SQL comparison, so "?type=%00" reached the driver and
    # answered 500 instead of a validation error.
    type: TransactionTypeStr | None = None,
    category: TransactionCategoryStr | None = None,
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
        source_related = await _reconcile_source_record(db, farm, txn, payload)

        if source_related is None:
            animal_pk, animal_tag = await _resolve_related_animal(
                db, farm, payload.related_animal_id
            )
        else:
            animal_pk, animal_tag = source_related

        txn.voided_at = utcnow()
        txn.voided_by_id = user.id
        txn.void_reason = payload.reason.strip()
        # Flush the void first so the active-source partial unique index permits
        # the corrected replacement for a system-generated source.
        await db.flush()
        replacement_feed_unit_price = txn.feed_unit_price_per_kg
        if txn.source_type == "FEED_PURCHASE":
            replacement_feed_unit_price = _corrected_feed_unit_price(txn, money(payload.amount))
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
            feed_inventory_id=txn.feed_inventory_id,
            feed_quantity_kg=txn.feed_quantity_kg,
            feed_unit_price_per_kg=replacement_feed_unit_price,
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
