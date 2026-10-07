"""Paid-call caps follow local midnight through a UTC calendar-day change."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Farm, ScreeningCallReservation, ScreeningDailyBudget
from app.services.screening.budget import ScreeningBudgetExhausted, reserve_provider_attempt

from .conftest import owner_with_farm


@pytest.mark.parametrize(
    ("timezone_name", "local_date"),
    [("Asia/Kolkata", dt.date(2026, 10, 8)), ("America/New_York", dt.date(2026, 10, 7))],
)
async def test_utc_midnight_does_not_reset_the_local_day_paid_call_cap(
    client: httpx.AsyncClient, timezone_name: str, local_date: dt.date
) -> None:
    owner = await owner_with_farm(client, email="paid-utc-midnight@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    before = dt.datetime(2026, 10, 7, 23, 59)
    after = dt.datetime(2026, 10, 8, 0, 1)
    next_date = local_date + dt.timedelta(days=1)
    local_reset = dt.datetime.combine(next_date, dt.time.min, ZoneInfo(timezone_name))
    local_reset = local_reset.astimezone(dt.UTC).replace(tzinfo=None)

    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        farm.timezone = timezone_name
        await db.commit()
        try:
            receipt = await reserve_provider_attempt(
                db,
                farm_id=farm_id,
                timezone_name=timezone_name,
                provider="midnight",
                cap=1,
                attempt_id="before-utc-midnight",
                now=before,
            )
        except ScreeningBudgetExhausted as exc:
            pytest.fail(f"The first paid attempt in this local day must fit: {exc}")
        assert receipt == "before-utc-midnight"
        await db.rollback()
        with pytest.raises(ScreeningBudgetExhausted, match="Daily screening call budget"):
            await reserve_provider_attempt(
                db,
                farm_id=farm_id,
                timezone_name=timezone_name,
                provider="midnight",
                cap=1,
                attempt_id="after-utc-midnight",
                now=after,
            )
        budget = await db.get(ScreeningDailyBudget, (farm_id, local_date))
        assert budget is not None and budget.reserved_calls == 1
        try:
            await reserve_provider_attempt(
                db,
                farm_id=farm_id,
                timezone_name=timezone_name,
                provider="midnight",
                cap=1,
                attempt_id="next-local-day",
                now=local_reset,
            )
        except ScreeningBudgetExhausted as exc:
            pytest.fail(f"The next local midnight must release a new day's allowance: {exc}")
        budgets = (
            (
                await db.execute(
                    select(ScreeningDailyBudget).order_by(ScreeningDailyBudget.local_date)
                )
            )
            .scalars()
            .all()
        )
        assert [(row.local_date, row.reserved_calls) for row in budgets] == [
            (local_date, 1),
            (next_date, 1),
        ]
        reservations = (await db.execute(select(ScreeningCallReservation))).scalars().all()
        assert {row.attempt_id for row in reservations} == {"before-utc-midnight", "next-local-day"}
