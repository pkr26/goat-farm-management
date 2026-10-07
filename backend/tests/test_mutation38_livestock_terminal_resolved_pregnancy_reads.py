"""Herd exit does not acquire independently held resolved pregnancy rows."""

import asyncio
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import text

from app.api.breeding import _get_breeding_record
from app.db import get_sessionmaker
from app.models import BreedingRecord, Farm, KiddingRecord
from app.utils import today

from .conftest import owner_with_farm
from .test_animals_status_husbandry import change_status
from .test_kidding_husbandry import confirmed_pregnancy, post_kidding


async def test_public_dam_retirement_does_not_wait_on_independently_pinned_delivered_history(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe, _buck, breeding = await confirmed_pregnancy(
        client, owner, tag="RESOLVED-PREGNANCY", bred_days_ago=170, kid_count=1
    )
    born = await post_kidding(
        client,
        owner,
        int(breeding["id"]),
        (today() - timedelta(days=20)).isoformat(),
        kids=[{"sex": "F"}],
    )
    assert born.status_code == 201, born.text
    delivery_id = int(born.json()["id"])
    retirement: asyncio.Task[httpx.Response] | None = None
    blocked_by_resolved_history = False
    async with get_sessionmaker()() as reader:
        farm = await reader.get(Farm, int(owner["X-Farm-Id"]))
        assert farm is not None
        historical = await _get_breeding_record(reader, farm, int(breeding["id"]), for_update=True)
        assert historical.kidding_record is not None and historical.kidding_record.id == delivery_id
        holder_pid = (await reader.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        try:
            retirement = asyncio.create_task(
                change_status(client, owner, int(doe["id"]), "SOLD", sale_price=5000)
            )
            for _ in range(2000):
                if retirement.done():
                    break
                async with get_sessionmaker()() as observer:
                    queued = await observer.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                            "WHERE datname = current_database() AND wait_event_type = 'Lock' "
                            "AND :holder_pid = ANY(pg_blocking_pids(pid)))"
                        ),
                        {"holder_pid": holder_pid},
                    )
                if queued:
                    blocked_by_resolved_history = True
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("Herd retirement did not complete or expose a real lock dependency")
        finally:
            # Release only this independently owned record. Keep the real
            # response/committed graph so waiting cannot masquerade as a
            # different final-state contract.
            await reader.rollback()
            if retirement is not None:
                try:
                    response = await asyncio.wait_for(retirement, timeout=30)
                except TimeoutError:
                    retirement.cancel()
                    await asyncio.gather(retirement, return_exceptions=True)
                    pytest.fail(
                        "Herd retirement did not finish after history ownership was released"
                    )
    assert response.status_code == 200, response.text
    async with get_sessionmaker()() as observer:
        retained = await observer.get(BreedingRecord, breeding["id"])
        birth = await observer.get(KiddingRecord, delivery_id)
        assert retained is not None and birth is not None
        assert retained.outcome == "CONFIRMED_PREGNANT" and retained.loss_date is None
        assert birth.breeding_record_id == retained.id
    assert not blocked_by_resolved_history, (
        "Retirement must not wait on resolved pregnancy held by the native record getter"
    )
    # Current HTTP reproductive writers serialize earlier through Animal;
    # the declared getter can independently pin a historical record. This
    # checks concrete availability/ownership, not an exact query count.
