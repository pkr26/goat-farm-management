"""Durable farm-local-day call admission; reservations survive image rollback.

Charges are conservative: a crash after reservation can consume a unit without
an HTTP request. Existing logical run rows cannot prove old retry counts, so
first use reserves their two-attempt upper bound. Rollout must stop old workers;
any pre-cutover in-flight work is held through its local-day boundary by the
migration, rather than guessed as zero spend.
"""

from __future__ import annotations

import datetime as dt
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, literal, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from ...models.screening import ScreeningCallReservation, ScreeningDailyBudget, ScreeningRun
from ...utils import DEFAULT_BUSINESS_TIMEZONE, utcnow

BUDGET_LOCK_NAMESPACE = 4718
# The f6 migration holds in-flight legacy work through its local-day boundary.
# This is a cutover marker, not a spend counter to increment when cap=0.
CUTOVER_HOLD_CALLS = 2_147_483_647


class ScreeningBudgetExhausted(Exception):
    """No outbound request is allowed; this is admission, not a provider failure."""


async def reserve_provider_attempt(
    parent: AsyncSession,
    *,
    farm_id: int,
    timezone_name: str,
    provider: str,
    cap: int,
    attempt_id: str | None = None,
    now: dt.datetime | None = None,
) -> str:
    """Commit an idempotent charge before external I/O on an independent session."""
    instant = now or utcnow()
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo(DEFAULT_BUSINESS_TIMEZONE)
    local_date = instant.replace(tzinfo=dt.UTC).astimezone(zone).date()
    midnight = dt.datetime.combine(local_date, dt.time.min, tzinfo=zone)
    day_start = midnight.astimezone(dt.UTC).replace(tzinfo=None)
    day_end = dt.datetime.combine(local_date + dt.timedelta(days=1), dt.time.min, tzinfo=zone)
    day_end = day_end.astimezone(dt.UTC).replace(tzinfo=None)
    receipt_id = attempt_id or str(uuid4())
    bind = parent.bind
    if isinstance(bind, AsyncConnection):
        bind = bind.engine
    if bind is None:
        raise RuntimeError("Screening call admission needs a bound database session")
    async with AsyncSession(bind=bind, expire_on_commit=False) as charge_db, charge_db.begin():
        await charge_db.execute(
            select(func.pg_advisory_xact_lock(literal(BUDGET_LOCK_NAMESPACE), literal(farm_id)))
        )
        previous = await charge_db.get(ScreeningCallReservation, receipt_id)
        if previous is not None:
            if (previous.farm_id, previous.local_date, previous.provider) != (
                farm_id,
                local_date,
                provider,
            ):
                raise ValueError("Provider attempt identity belongs to another admission scope")
            return receipt_id
        budget = await charge_db.get(ScreeningDailyBudget, (farm_id, local_date))
        if budget is None:
            prior_runs = int(
                (
                    await charge_db.execute(
                        select(func.count())
                        .select_from(ScreeningRun)
                        .where(
                            ScreeningRun.farm_id == farm_id,
                            ScreeningRun.created_at >= day_start,
                            ScreeningRun.created_at < day_end,
                        )
                    )
                ).scalar_one()
            )
            budget = ScreeningDailyBudget(
                farm_id=farm_id, local_date=local_date, reserved_calls=2 * prior_runs
            )
            charge_db.add(budget)
            await charge_db.flush()
        if budget.reserved_calls == CUTOVER_HOLD_CALLS:
            raise ScreeningBudgetExhausted("Legacy screening work is held until the next local day")
        if cap > 0 and budget.reserved_calls >= cap:
            raise ScreeningBudgetExhausted("Daily screening call budget is exhausted")
        budget.reserved_calls += 1
        charge_db.add(
            ScreeningCallReservation(
                attempt_id=receipt_id,
                farm_id=farm_id,
                local_date=local_date,
                provider=provider,
                created_at=instant,
            )
        )
    return receipt_id
