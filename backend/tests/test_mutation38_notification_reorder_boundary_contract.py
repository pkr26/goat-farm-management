"""Supported native purchases at the reorder level are adequately stocked."""

from decimal import Decimal

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import FeedInventory, NotificationLog
from app.services.notifications import feed_reorder_daily

from .conftest import owner_with_farm
from .test_feeding_extended import add_stock, get_inventory
from .test_mutation38_notification_native_cycle_contracts import _always_open_settings
from .test_notifications import RecordingProvider, _farm, _membership_id, _recipient
from .type_helpers import json_int


async def test_supported_purchase_to_exact_reorder_level_has_no_below_level_alert(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="native-reorder-exact-level@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    for item in await get_inventory(client, owner):
        if item["reorder_level"] is None:
            continue
        current = Decimal(str(item["qty_on_hand"]))
        level = Decimal(str(item["reorder_level"]))
        if current < level:
            bought = await add_stock(client, owner, json_int(item["id"]), float(level - current))
            assert bought.status_code == 200, bought.text
    provider = RecordingProvider()
    async with get_sessionmaker()() as worker:
        await _recipient(worker, farm_id, membership_id)
        inventory = list(
            (
                await worker.execute(select(FeedInventory).where(FeedInventory.farm_id == farm_id))
            ).scalars()
        )
        assert any(row.reorder_level is not None for row in inventory)
        assert all(
            row.reorder_level is None or row.qty_on_hand >= row.reorder_level for row in inventory
        )
        assert any(row.qty_on_hand == row.reorder_level for row in inventory)
        sent = await feed_reorder_daily(
            worker, _always_open_settings(), provider, await _farm(worker, farm_id)
        )
    assert sent == 0, "the documented under-level alert excludes stock exactly at its reorder level"
    assert not provider.sent
    async with get_sessionmaker()() as observer:
        assert not list((await observer.execute(select(NotificationLog))).scalars())
