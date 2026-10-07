"""Public terminal histories and a real farm clock bound the mortality memo."""

from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal
from typing import Self

import httpx
import pytest

from app import utils
from app.db import get_sessionmaker
from app.models import Farm
from app.services.finance import mortality_memo
from app.utils import add_months, today

from .conftest import owner_with_farm
from .test_finance_extended import change_status, make_animal


async def test_mortality_window_retains_first_day_and_excludes_next_month_facts(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client, email="native-mortality-window@farm.in")
    current = add_months(today().replace(day=1), -1)
    first = add_months(current, -1)
    after = add_months(current, 1)
    birth = (first - timedelta(days=800)).isoformat()
    for tag, effective, weight in (
        ("WINDOW-FIRST", first, 1.0),
        ("WINDOW-OLDER", first - timedelta(days=1), 2.0),
        ("WINDOW-LATER", after, 3.0),
    ):
        animal = await make_animal(
            client,
            owner,
            tag=tag,
            date_of_birth=birth,
            weight_kg=weight,
            weight_date=effective.isoformat(),
        )
        await change_status(
            client,
            owner,
            animal["id"],
            "DEAD",
            date=effective.isoformat(),
            mortality_cause_code="PNEUMONIA",
        )
    for tag, effective, price in (
        ("SALE-FIRST", first, 100.0),
        ("SALE-OLDER", first - timedelta(days=1), 900.0),
        ("SALE-LATER", after, 700.0),
    ):
        animal = await make_animal(client, owner, tag=tag, sex="M", date_of_birth=birth)
        await change_status(
            client,
            owner,
            animal["id"],
            "SOLD",
            date=effective.isoformat(),
            sale_price=price,
            sale_weight_kg=1.0,
        )

    fixed = datetime(current.year, current.month, 15, 0, tzinfo=UTC)

    class ObserverDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            assert tz is not None
            return cls.fromtimestamp(fixed.timestamp(), tz)

    # Only the external clock is pinned after setup. Every dated death, weight
    # and sale is a real committed public fact no later than actual today.
    # Reading at the previous observer month exercises the native half-open
    # window without rewriting history or inventing future HTTP submissions.
    monkeypatch.setattr(utils, "datetime", ObserverDatetime)
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        assert farm is not None
        memo = await mortality_memo(db, farm, n_months=2)
    assert memo["window_months"] == 2
    assert memo["head_count"] == 1
    assert memo["estimated_loss"] == Decimal("100.00")
    assert "last recorded weight" in memo["basis"]


async def test_one_rupee_realized_sale_still_values_a_positive_fractional_weight(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="native-mortality-one-rupee@farm.in")
    birth = (today() - timedelta(days=800)).isoformat()
    dead = await make_animal(
        client,
        owner,
        tag="SMALL-DEAD",
        date_of_birth=birth,
        weight_kg=2.0,
        weight_date=today().isoformat(),
    )
    sold = await make_animal(client, owner, tag="SMALL-SOLD", sex="M", date_of_birth=birth)
    await change_status(client, owner, dead["id"], "DEAD", mortality_cause_code="PNEUMONIA")
    await change_status(client, owner, sold["id"], "SOLD", sale_price=1.0, sale_weight_kg=0.5)
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        assert farm is not None
        memo = await mortality_memo(db, farm)
    assert memo["head_count"] == 1
    assert memo["estimated_loss"] == Decimal("4.00")
