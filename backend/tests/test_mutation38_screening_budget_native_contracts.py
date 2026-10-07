"""Durable native call admission conserves clocks, scopes and disabled caps."""

import asyncio
import datetime as dt
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from app.db import get_sessionmaker
from app.models.screening import ScreeningCallReservation, ScreeningDailyBudget
from app.services.screening.budget import ScreeningBudgetExhausted, reserve_provider_attempt

from .conftest import owner_with_farm


async def test_native_attempt_replay_preserves_its_paid_scope_and_disabled_cap(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    instant = dt.datetime(2026, 1, 13, 12)
    key = str(uuid4())
    async with get_sessionmaker()() as db:
        try:
            original = await reserve_provider_attempt(
                db,
                farm_id=farm_id,
                timezone_name="Asia/Kolkata",
                provider="native-camera",
                cap=0,
                attempt_id=key,
                now=instant,
            )
            replay = await reserve_provider_attempt(
                db,
                farm_id=farm_id,
                timezone_name="Asia/Kolkata",
                provider="native-camera",
                cap=0,
                attempt_id=key,
                now=instant,
            )
        except (AttributeError, ValueError, RuntimeError, ScreeningBudgetExhausted) as exc:
            pytest.fail(
                f"A valid disabled-cap admission and identical paid replay must succeed: {exc}"
            )
        assert original == replay == key
        with pytest.raises(ValueError, match="another admission scope"):
            await reserve_provider_attempt(
                db,
                farm_id=farm_id,
                timezone_name="Asia/Kolkata",
                provider="other-camera",
                cap=0,
                attempt_id=key,
                now=instant,
            )
        await db.rollback()
    async with get_sessionmaker()() as db:
        row = await db.get(ScreeningCallReservation, key)
        assert row is not None and row.farm_id == farm_id
        assert row.provider == "native-camera" and row.created_at == instant
        assert row.local_date == dt.date(2026, 1, 13)
        budget = await db.get(ScreeningDailyBudget, (farm_id, row.local_date))
        assert budget is not None and budget.reserved_calls == 1


async def test_native_call_admission_has_a_valid_default_clock(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    key = str(uuid4())
    async with get_sessionmaker()() as db:
        try:
            receipt = await reserve_provider_attempt(
                db,
                farm_id=farm_id,
                timezone_name="Asia/Kolkata",
                provider="native-clock",
                cap=0,
                attempt_id=key,
            )
        except (AttributeError, ValueError, RuntimeError, ScreeningBudgetExhausted) as exc:
            pytest.fail(f"The declared native default clock must admit a real call: {exc}")
        assert receipt == key
    async with get_sessionmaker()() as db:
        row = await db.get(ScreeningCallReservation, key)
        assert row is not None and row.provider == "native-clock"
        budget = await db.get(ScreeningDailyBudget, (farm_id, row.local_date))
        assert budget is not None and budget.reserved_calls == 1


async def test_paid_admission_keeps_the_established_cross_runtime_farm_lease(
    client: httpx.AsyncClient,
) -> None:
    """A still-running ledger-aware runtime owns the existing native 4718 lease.

    This checks protocol interoperability across replicas/deployments. Two
    callers of only the same changed source would still share its new number.
    """
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    key = str(uuid4())
    instant = dt.datetime(2026, 1, 13, 12)
    async with get_sessionmaker()() as holder, get_sessionmaker()() as observer:
        await holder.execute(
            text("SELECT pg_advisory_xact_lock(4718, CAST(:farm AS integer))"), {"farm": farm_id}
        )
        pid = await holder.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(pid, int)

        async def charge() -> str:
            async with get_sessionmaker()() as db:
                return await reserve_provider_attempt(
                    db,
                    farm_id=farm_id,
                    timezone_name="Asia/Kolkata",
                    provider="replica-camera",
                    cap=10,
                    attempt_id=key,
                    now=instant,
                )

        request = asyncio.create_task(charge())
        blocked: list[int] = []
        try:
            deadline = asyncio.get_running_loop().time() + 30
            while not request.done():
                blocked = list(
                    (
                        await observer.execute(
                            text(
                                "SELECT pid FROM pg_stat_activity WHERE datname=current_database() "
                                "AND wait_event_type='Lock' AND :holder=ANY(pg_blocking_pids(pid))"
                            ),
                            {"holder": pid},
                        )
                    ).scalars()
                )
                if blocked:
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError(
                        "Admission gave neither completion nor a real lease dependency"
                    )
                await asyncio.sleep(0.02)
            await holder.rollback()
            receipt = await asyncio.wait_for(request, timeout=30)
        finally:
            await holder.rollback()
            if not request.done():
                request.cancel()
            await asyncio.gather(request, return_exceptions=True)
    assert receipt == key
    assert blocked, "Paid admission escaped the established same-farm replica lease"
    async with get_sessionmaker()() as db:
        row = await db.get(ScreeningCallReservation, key)
        assert row is not None and row.farm_id == farm_id and row.provider == "replica-camera"
        budget = await db.get(ScreeningDailyBudget, (farm_id, row.local_date))
        assert budget is not None and budget.reserved_calls == 1
