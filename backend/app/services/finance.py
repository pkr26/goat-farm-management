"""Finance."""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    Animal,
    AnimalStatus,
    Farm,
    FeedInventory,
    InsurancePolicy,
    InsurancePremium,
    TaskCategory,
    Transaction,
    TransactionType,
    WeightRecord,
)
from ..models.finance import (
    INSURANCE_STATUS_ACTIVE,
    INSURANCE_STATUS_CLAIMED,
    INSURANCE_STATUS_LAPSED,
)
from ..utils import add_months, money, today, utcnow
from ._common import _add_task

# A renewal duty lands a month ahead of the policy's renewal date: late
# enough not to nag, early enough to arrange the insurer's paperwork.
INSURANCE_RENEWAL_LEAD_DAYS = 30

# Insurance premiums are immutable register payments rather than mutable
# Transaction rows.  Finance summaries use this explicit category so the
# aggregate is explainable without pretending the payment is a manual ledger
# transaction.
INSURANCE_PREMIUM_CATEGORY = "INSURANCE"

# BIZ-3 (2026-09-16): one renewal books one non-prorated premium row, so the
# covered span needs a bound — five years, renewed successively for longer.
MAX_RENEWAL_SPAN_DAYS = 5 * 366

LIFETIME_PNL_FEED_NOTE = (
    "Feed costs are tracked at farm level only and are not part of this animal's figures."
)


async def monthly_pnl(db: AsyncSession, farm: Farm, n_months: int = 12) -> list[dict[str, Any]]:
    """Income vs expense for the last ``n_months`` calendar months,
    including zero-activity months, most recent first.

    Ledger rows and immutable premium payments are each aggregated in SQL;
    no transaction-by-transaction scan is loaded into Python. Output shape
    and rounding are unchanged: row totals rounded to 2dp, category sums raw.
    Voided ledger rows remain in the audit trail but never affect totals."""
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

    # Premiums are payment facts outside ``transactions``.  Their accounting
    # date is when the farm recorded the payment, not the coverage start:
    # renewal may be paid early for a future period.  Keep the same category
    # shape as ledger rows so consumers see the expense's provenance rather
    # than a silent adjustment to the monthly total.
    premium_month_col = func.to_char(InsurancePremium.recorded_on, "YYYY-MM")
    premium_result = await db.execute(
        select(premium_month_col, func.sum(InsurancePremium.premium))
        .where(
            InsurancePremium.farm_id == farm.id,
            InsurancePremium.recorded_on >= first_month,
            InsurancePremium.recorded_on < after_last_month,
        )
        .group_by(premium_month_col)
    )
    for month, total in premium_result.all():
        row = months.setdefault(
            month,
            {
                "month": month,
                "income": Decimal("0.00"),
                "expense": Decimal("0.00"),
                "categories": {},
            },
        )
        row["expense"] += total
        category = row["categories"].setdefault(
            INSURANCE_PREMIUM_CATEGORY,
            {"income": Decimal("0.00"), "expense": Decimal("0.00")},
        )
        category["expense"] += total

    rows = sorted(months.values(), key=lambda r: r["month"], reverse=True)
    for row in rows:
        row["income"] = round(row["income"], 2)
        row["expense"] = round(row["expense"], 2)
        row["net"] = round(row["income"] - row["expense"], 2)
    return rows


async def _book_premium(
    db: AsyncSession,
    farm_id: int,
    policy: InsurancePolicy,
    *,
    premium: Decimal,
    covered_from: date,
    covered_until: date,
    recorded_on: date,
    recorded_by_id: int | None,
) -> None:
    """Append one premium payment to the policy's audit history.

    Degenerate zero-length periods (renewal on the start/horizon date itself)
    book nothing: there is no covered span to charge for.
    """
    if covered_until <= covered_from:
        return
    db.add(
        InsurancePremium(
            farm_id=farm_id,
            policy_id=policy.id,
            premium=money(premium),
            covered_from=covered_from,
            covered_until=covered_until,
            recorded_on=recorded_on,
            recorded_by_id=recorded_by_id,
        )
    )


async def _spawn_renewal_task(db: AsyncSession, farm_id: int, policy: InsurancePolicy) -> None:
    """Queue the accountant's renewal duty for a future-dated policy.

    A renewal date that has already arrived gets no task: the 30-day lead has
    passed, and a backdated duty would only bury the register's real work.
    The duty names the policy number (herd-level rows have no animal to
    link) and carries the animal for per-animal policies.
    """
    due = policy.renewal_date - timedelta(days=INSURANCE_RENEWAL_LEAD_DAYS)
    await _add_task(
        db,
        farm_id,
        f"Insurance renewal due: policy {policy.policy_number}",
        due,
        TaskCategory.INSURANCE,
        animal_id=policy.animal_id,
        title_key="insurance_renewal",
        title_args={
            "policy_id": policy.id,
            "policy_number": policy.policy_number,
            "renewal_date": policy.renewal_date.isoformat(),
            "due_date": due.isoformat(),
        },
    )


async def _require_linked_animal_active(
    db: AsyncSession, farm: Farm, policy: InsurancePolicy, action: str
) -> None:
    """Refuse policy work whose covered animal has left the herd.

    A policy on a sold/dead/culled goat cannot move forward: renewing cover
    for stock the farm no longer holds is exactly the register entry the
    dashboard would then nag about forever.
    """
    if policy.animal_id is None:
        return
    animal = await db.get(Animal, policy.animal_id)
    if animal is None or animal.farm_id != farm.id:
        return  # the tenant FK makes this unreachable; fail permissively
    if animal.status != AnimalStatus.ACTIVE.value:
        raise ValueError(
            f"Cannot {action}: covered animal {animal.tag_number} has left the herd "
            f"({animal.status.lower()})"
        )


async def _lock_new_policy_animal_active(db: AsyncSession, farm: Farm, animal_id: int) -> Animal:
    """Lock a newly covered animal through policy creation.

    Creation has no policy row to serialize against yet.  Without the animal
    lock, a status-change transaction can lapse every policy it can see and
    commit the animal's exit just before this transaction commits a new
    ``active`` policy.  The lock makes the two outcomes serial: creation
    commits first and the exit then lapses it, or the exit commits first and
    creation rejects the no-longer-active animal.
    """
    animal = (
        await db.execute(
            select(Animal)
            .where(Animal.id == animal_id, Animal.farm_id == farm.id)
            # The API may have resolved this object earlier in the same
            # session. Rehydrate it under the lock so an identity-map value
            # cannot hide a just-committed status change.
            .execution_options(populate_existing=True)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if animal is None:
        raise ValueError("Cannot register insurance: covered animal is not on this farm")
    if animal.status != AnimalStatus.ACTIVE.value:
        raise ValueError(
            f"Cannot register insurance: covered animal {animal.tag_number} has left the herd "
            f"({animal.status.lower()})"
        )
    return animal


async def create_insurance_policy(
    db: AsyncSession,
    farm: Farm,
    *,
    policy_number: str,
    insurer: str,
    sum_insured: Decimal,
    premium: Decimal,
    start_date: date,
    renewal_date: date,
    animal_id: int | None,
    notes: str | None,
    created_by_id: int | None,
) -> InsurancePolicy:
    """Register a policy, book its first premium and queue the renewal duty.

    Shape validation (blank identifiers, money bounds, renewal ≥ start) lives
    in the wire schema; this is the domain backstop for callers that bypass
    it. The register is append-only — there is no edit or delete path, only
    renewal moving the horizon forward.
    """
    reference = today(farm.timezone)
    if start_date > reference:
        raise ValueError("Policy start date cannot be in the future")
    if renewal_date < start_date:
        raise ValueError("Policy renewal date cannot be before its start date")
    # BIZ-3 (2026-09-16) creation twin of the renewal-path cap: registration
    # books ONE non-prorated premium row for the whole start→renewal span, so
    # a decades-distant renewal_date would buy that cover for a single
    # premium exactly as an oversized renewal would — the register is
    # append-only, so the horizon must arrive by successive renewals here too.
    if (renewal_date - start_date).days > MAX_RENEWAL_SPAN_DAYS:
        raise ValueError(
            "A policy can cover at most five years — renew successively for longer cover"
        )
    if animal_id is not None:
        await _lock_new_policy_animal_active(db, farm, animal_id)
    policy = InsurancePolicy(
        farm_id=farm.id,
        animal_id=animal_id,
        policy_number=policy_number,
        insurer=insurer,
        sum_insured=money(sum_insured),
        premium=money(premium),
        start_date=start_date,
        renewal_date=renewal_date,
        notes=notes,
        created_by_id=created_by_id,
    )
    db.add(policy)
    await db.flush()
    await _book_premium(
        db,
        farm.id,
        policy,
        premium=policy.premium,
        covered_from=start_date,
        covered_until=renewal_date,
        recorded_on=reference,
        recorded_by_id=created_by_id,
    )
    if renewal_date > reference:
        await _spawn_renewal_task(db, farm.id, policy)
    return policy


async def renew_insurance_policy(
    db: AsyncSession,
    farm: Farm,
    policy: InsurancePolicy,
    *,
    renewal_date: date,
    premium: Decimal | None,
    recorded_by_id: int | None = None,
) -> InsurancePolicy:
    """Move a policy's renewal horizon forward (never backwards).

    Renewal re-activates the policy, books the new period's premium into the
    payment history (an omitted premium carries the previous price forward —
    and is recorded as such, so the lifetime P&L sums what was actually
    payable, not just the latest column value) and queues the next renewal
    duty — the register's equivalent of the ledger's correction flow: the row
    keeps its identity and audit trail instead of being rewritten into a new
    fact.
    """
    # A claim is the register's documented terminal event (see
    # claim_insurance_policy) — renewal "re-activates" the row, so without
    # this guard a renewal resurrected claimed cover into active and reopened
    # the single-shot claim window. Mirrors the claim path's own guard.
    if policy.status == INSURANCE_STATUS_CLAIMED:
        raise ValueError("A claimed policy cannot be renewed; register a new policy")
    await _require_linked_animal_active(db, farm, policy, "renew this policy")
    if renewal_date < policy.renewal_date:
        raise ValueError(
            "New renewal date cannot be before the current renewal date "
            f"{policy.renewal_date.isoformat()}"
        )
    effective_premium = money(premium) if premium is not None else policy.premium
    if renewal_date == policy.renewal_date:
        # A client that lost a successful response can safely replay the
        # exact renewal without minting another premium or duty.  Equal-date
        # requests are never allowed to alter price or reactivate lapsed
        # cover: those are real state changes and require a new period.
        if premium is not None and effective_premium != policy.premium:
            raise ValueError(
                "A renewal date must be later than the current renewal date to change premium"
            )
        if policy.status == INSURANCE_STATUS_LAPSED:
            raise ValueError(
                "A renewal date must be later than the current renewal date to reactivate cover"
            )
        return policy
    # BIZ-3 (2026-09-16): the register books ONE non-prorated premium row per
    # renewal — an arbitrarily distant horizon would book a decades-long span
    # for a single premium. Bound the covered span at five years; longer
    # cover is successive renewals, matching how insurers actually issue.
    if (renewal_date - policy.renewal_date).days > MAX_RENEWAL_SPAN_DAYS:
        raise ValueError(
            "A renewal can extend coverage by at most five years — renew "
            "successively for longer cover"
        )
    previous_horizon = policy.renewal_date
    recorded_on = today(farm.timezone)
    policy.renewal_date = renewal_date
    policy.premium = effective_premium
    policy.status = INSURANCE_STATUS_ACTIVE
    await db.flush()
    await _book_premium(
        db,
        farm.id,
        policy,
        premium=effective_premium,
        covered_from=previous_horizon,
        covered_until=renewal_date,
        recorded_on=recorded_on,
        recorded_by_id=recorded_by_id,
    )
    if renewal_date > recorded_on:
        await _spawn_renewal_task(db, farm.id, policy)
    return policy


async def claim_insurance_policy(
    db: AsyncSession,
    farm: Farm,
    policy: InsurancePolicy,
    *,
    claim_date: date,
    claimed_by_id: int,
) -> InsurancePolicy:
    """Record a claim against a policy: the register's terminal event.

    Claiming is a status fact, not a money movement — any payout the insurer
    actually settles is booked through the ledger like any other income, so
    this writer never fabricates a transaction. Allowed from any unclaimed
    status, and regardless of the covered animal's herd state: mortality
    cover is claimed precisely when the animal has died (and the exit
    auto-lapsed the policy) — an active-animal gate here would block the
    register's main use case.
    """
    if policy.status == INSURANCE_STATUS_CLAIMED:
        raise ValueError("This policy has already been claimed")
    if claim_date < policy.start_date:
        raise ValueError("Claim date cannot be before the policy start date")
    policy.status = INSURANCE_STATUS_CLAIMED
    policy.claim_date = claim_date
    policy.claimed_at = utcnow()
    policy.claimed_by_id = claimed_by_id
    await db.flush()
    return policy


async def lapse_policies_for_animal(db: AsyncSession, farm: Farm, animal: Animal) -> int:
    """End active cover when the covered animal leaves the herd.

    Sold/dead/culled stock must not keep an ``active`` policy nagging the
    dashboard expiry card forever. Herd-level policies (no animal link) are
    untouched, and already-lapsed/claimed rows keep their status — lapse is
    a fact about cover that was live at exit, not a retroactive rewrite.
    Returns the number of policies lapsed (0 for herd-level calls).
    """
    # The exit path holds the animal row already. Lock policy rows in a stable
    # order too: a concurrent claim locks its policy, records terminal claim
    # metadata, and commits. Without this lock the exit can have read ACTIVE
    # before that commit and then write only ``status='lapsed'`` over the
    # claimed row, violating the metadata/state constraint at commit time.
    # PostgreSQL rechecks the ACTIVE predicate after a wait, so a policy that
    # was claimed meanwhile simply falls out of this result and stays claimed.
    policies = (
        (
            await db.execute(
                select(InsurancePolicy)
                .where(
                    InsurancePolicy.farm_id == farm.id,
                    InsurancePolicy.animal_id == animal.id,
                    # "renewed" is not a real status value anywhere: renewal
                    # returns policies to ACTIVE, so this is a plain active-only
                    # lookup (a stray literal survived here from an old draft).
                    InsurancePolicy.status == INSURANCE_STATUS_ACTIVE,
                )
                .order_by(InsurancePolicy.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    for policy in policies:
        policy.status = INSURANCE_STATUS_LAPSED
    return len(policies)


async def lifetime_pnl(db: AsyncSession, farm: Farm, animal: Animal) -> dict[str, Any]:
    """Lifetime money in/out for one animal. Zero-safe: an animal with no
    recorded money facts reports genuine zeros, not None.

    Provenance, not raw category matching, decides what counts: only the
    domain events that were actually booked against this animal. Voided
    ledger rows are audit trail, not cost.
    """

    async def _sum_amount(*criteria: Any) -> Decimal:
        total = (
            await db.execute(
                select(func.sum(Transaction.amount)).where(
                    Transaction.farm_id == farm.id,
                    Transaction.voided_at.is_(None),
                    *criteria,
                )
            )
        ).scalar_one()
        return total if total is not None else Decimal("0.00")

    # The managed-purchase writer books the ledger row with exactly the
    # animal's purchase_price and corrections keep the pair in step
    # (api._reconcile_source_record), so summing both would double-count
    # every single purchase. The ledger is authoritative when it carries the
    # event; the denormalized column covers batch-allocated and historical
    # imports, whose purchase booked no per-animal ledger row.
    ledger_purchase = await _sum_amount(
        Transaction.source_type == "ANIMAL_PURCHASE",
        Transaction.source_id == animal.id,
    )
    purchase_cost = ledger_purchase if ledger_purchase else animal.purchase_price or Decimal("0.00")
    # Health submissions that covered several animals book one aggregate row
    # with no per-animal link; it stays a farm-level cost by construction.
    health_cost = await _sum_amount(
        Transaction.source_type == "HEALTH_EVENT",
        Transaction.related_animal_id == animal.id,
    )
    # Premiums actually payable across the animal's policies: summed from
    # the payment history, not the policy row — renewal overwrites
    # ``InsurancePolicy.premium`` with the CURRENT period's price, so the
    # column alone would hide every earlier payment.
    premiums_total = (
        await db.execute(
            select(func.sum(InsurancePremium.premium)).where(
                InsurancePremium.farm_id == farm.id,
                InsurancePremium.policy_id.in_(
                    select(InsurancePolicy.id).where(
                        InsurancePolicy.farm_id == farm.id,
                        InsurancePolicy.animal_id == animal.id,
                    )
                ),
            )
        )
    ).scalar_one()
    insurance_premiums = premiums_total if premiums_total is not None else Decimal("0.00")
    # A sale always books a ledger row (₹0 when unpriced), so the active
    # ANIMAL_SALE source pair is the complete sale history.
    sale_income = await _sum_amount(
        Transaction.source_type == "ANIMAL_SALE",
        Transaction.source_id == animal.id,
    )
    expenses = money(purchase_cost) + money(health_cost) + money(insurance_premiums)
    return {
        "animal_id": animal.id,
        "tag_number": animal.tag_number,
        "purchase_cost": money(purchase_cost),
        "health_cost": money(health_cost),
        "insurance_premiums": money(insurance_premiums),
        "sale_income": money(sale_income),
        "net": money(sale_income) - expenses,
        "note": LIFETIME_PNL_FEED_NOTE,
    }


async def feed_stock_value(db: AsyncSession, farm: Farm) -> Decimal:
    """The farm's feed stock at each item's last purchase price.

    A memo valuation, not a transaction: unpriced stock contributes zero
    (coalesce) rather than pretending a price exists. Aggregated in
    PostgreSQL's exact numeric, then rounded to paise like every other
    money figure."""
    total = (
        await db.execute(
            select(
                func.sum(
                    FeedInventory.qty_on_hand
                    * func.coalesce(FeedInventory.last_purchase_price_per_kg, 0)
                )
            ).where(FeedInventory.farm_id == farm.id)
        )
    ).scalar_one()
    return money(total if total is not None else Decimal("0.00"))


async def mortality_memo(db: AsyncSession, farm: Farm, n_months: int = 12) -> dict[str, Any]:
    """Ledger-neutral mortality visibility for the finance summary (backlog
    #31): deaths in the P&L window, valued at the farm's own realized ₹/kg
    when the data exists.

    A memo, deliberately not a transaction: the animal's cost is already in
    the ledger (purchase, health), and a death books no cash flow. The
    estimate multiply-sources the farm's own facts — the dead animals' last
    recorded weights × the average realized rate from weighed sales in the
    same window — and reports None rather than inventing a number when
    either side is missing (no weighed sale ⇒ no market rate the farm has
    actually achieved).
    """
    current_month = today(farm.timezone).replace(day=1)
    window_start = add_months(current_month, -(n_months - 1))
    after_last_month = add_months(current_month, 1)
    dead_ids = (
        (
            await db.execute(
                select(Animal.id).where(
                    Animal.farm_id == farm.id,
                    Animal.status == AnimalStatus.DEAD.value,
                    Animal.status_date >= window_start,
                    Animal.status_date < after_last_month,
                )
            )
        )
        .scalars()
        .all()
    )
    head_count = len(dead_ids)
    estimated_loss: Decimal | None = None
    basis = (
        "Deaths in the last "
        f"{n_months} months valued at each animal's last recorded weight "
        "× the farm's average realized ₹/kg from weighed sales in the window."
    )
    if dead_ids:
        # Latest recorded weight per dead animal (DISTINCT ON keeps one row
        # per animal, newest first).
        latest_weights = (
            select(WeightRecord.animal_id, WeightRecord.weight_kg)
            .where(
                WeightRecord.farm_id == farm.id,
                WeightRecord.animal_id.in_(dead_ids),
            )
            .distinct(WeightRecord.animal_id)
            .order_by(WeightRecord.animal_id, WeightRecord.date.desc(), WeightRecord.id.desc())
        )
        weight_sum = (
            await db.execute(select(func.sum(latest_weights.subquery().c.weight_kg)))
        ).scalar_one()
        # Volume-weighted average realized rate: total sale proceeds over
        # total weighed sale live-weight — resistant to one small odd lot
        # skewing a per-sale average.
        rate_row = (
            await db.execute(
                select(
                    func.sum(Animal.sale_price),
                    func.sum(Animal.sale_weight_kg),
                ).where(
                    Animal.farm_id == farm.id,
                    Animal.status == AnimalStatus.SOLD.value,
                    Animal.status_date >= window_start,
                    Animal.status_date < after_last_month,
                    Animal.sale_weight_kg.is_not(None),
                    Animal.sale_weight_kg > 0,
                )
            )
        ).one()
        proceeds, weighed_kg = rate_row
        if (
            weight_sum is not None
            and weight_sum != 0
            and proceeds is not None
            and proceeds != 0
            and weighed_kg is not None
            and weighed_kg != 0
        ):
            rate = proceeds / weighed_kg
            estimated_loss = money(Decimal(str(weight_sum)) * rate)
        else:
            basis += " No weighed sale in the window — the loss stays unvalued."
    return {
        "window_months": n_months,
        "head_count": head_count,
        # Decimal to the wire (MortalityMemoOut serializes it as a JSON
        # string): casting money through float re-introduced the paise drift
        # the money() quantization just removed.
        "estimated_loss": estimated_loss,
        "basis": basis,
    }
