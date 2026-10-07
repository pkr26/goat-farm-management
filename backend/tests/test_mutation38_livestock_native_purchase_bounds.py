"""The typed purchase service rejects domain bounds before writing its graph."""

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_sessionmaker
from app.models import Animal, Farm, PurchaseBatch, Task, Transaction
from app.services.purchases import create_purchase_batch
from app.utils import today

from .conftest import owner_with_farm


async def _purchase(db: AsyncSession, farm: Farm, field: str, value: int) -> PurchaseBatch:
    return await create_purchase_batch(
        db,
        farm,
        today(),
        "Native stock procurement",
        value if field == "count" else 1,
        None,
        None,
        None,
        "Native domain boundary",
        field == "individual_weight",
        transport_hours=value if field == "transport_hours" else None,
        individual_weights_kg=[float(value)] if field == "individual_weight" else None,
    )


@pytest.mark.parametrize(
    ("field", "value", "valid", "message"),
    [
        ("count", 0, False, "at least 1"),
        ("count", 1, True, ""),
        ("count", 1000, True, ""),
        ("transport_hours", 0, True, ""),
        ("transport_hours", 240, True, ""),
        ("transport_hours", 241, False, "between 0 and 240"),
        ("individual_weight", 0, False, "must be positive"),
    ],
    ids=[
        "zero-heads",
        "one-head",
        "max-heads",
        "zero-transit",
        "max-transit",
        "above-transit",
        "zero-arrival-weight",
    ],
)
async def test_native_purchase_domain_bounds_and_rejection_leave_no_partial_graph(
    client: httpx.AsyncClient, field: str, value: int, valid: bool, message: str
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        if valid:
            try:
                batch = await _purchase(db, farm, field, value)
                await db.commit()
            except Exception as error:
                pytest.fail(f"valid native purchase bound must complete: {error!r}")
            assert batch.count == (value if field == "count" else 1)
            if field == "transport_hours":
                assert batch.transport_hours == value
        else:
            try:
                await _purchase(db, farm, field, value)
            except ValueError as error:
                assert message in str(error)
            except Exception as error:
                pytest.fail(
                    f"native invalid bound must produce its domain ValueError before SQL: {error!r}"
                )
            else:
                pytest.fail("native invalid bound was accepted")
    async with get_sessionmaker()() as db:
        batches = (
            await db.execute(
                select(func.count())
                .select_from(PurchaseBatch)
                .where(PurchaseBatch.farm_id == farm_id)
            )
        ).scalar_one()
        animals = (
            await db.execute(
                select(func.count()).select_from(Animal).where(Animal.farm_id == farm_id)
            )
        ).scalar_one()
        ledger = (
            await db.execute(
                select(func.count()).select_from(Transaction).where(Transaction.farm_id == farm_id)
            )
        ).scalar_one()
        duties = (
            await db.execute(
                select(func.count())
                .select_from(Task)
                .where(Task.farm_id == farm_id, Task.purchase_batch_id.is_not(None))
            )
        ).scalar_one()
    assert batches == (1 if valid else 0)
    assert ledger == (1 if valid else 0)
    assert animals == 0
    assert duties == 0
