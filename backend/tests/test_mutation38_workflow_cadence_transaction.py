"""A cadence sweep that creates no duties preserves the caller's transaction."""

from datetime import date

import httpx
import pytest

from app.db import get_sessionmaker
from app.models import Farm
from app.services.cadence import ensure_cadence_tasks

from .conftest import owner_with_farm
from .test_cadence import farm_tasks, freeze_business_date, make_animal, run_ensure


async def test_noop_cadence_does_not_commit_a_callers_staged_farm_edit(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client, email="cadence-noop-transaction@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    await make_animal(client, owner, "CADENCE-NOOP")
    freeze_business_date(monkeypatch, date(2026, 9, 14))
    await run_ensure(farm_id)
    original_duties = [task.id for task in await farm_tasks(farm_id)]
    assert original_duties

    async with get_sessionmaker()() as caller:
        farm = await caller.get(Farm, farm_id)
        assert farm is not None
        committed_name = farm.name
        farm.name = "Caller edit awaiting its own commit"
        await caller.flush()
        try:
            await ensure_cadence_tasks(caller, farm)
            # A separate real connection sees committed state. The sweep
            # has no new duties, so the caller still owns this staged edit.
            async with get_sessionmaker()() as observer:
                visible = await observer.get(Farm, farm_id)
                assert visible is not None
                assert visible.name == committed_name
            assert caller.in_transaction()
        finally:
            await caller.rollback()

    async with get_sessionmaker()() as observer:
        visible = await observer.get(Farm, farm_id)
        assert visible is not None
        assert visible.name == committed_name
    assert [task.id for task in await farm_tasks(farm_id)] == original_duties
