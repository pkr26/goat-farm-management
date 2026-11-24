"""A non-positive entry weight records no weight row instead of a 500."""

import httpx
import pytest
from fastapi import Response
from sqlalchemy import func, select

from app.api.animals import create_animal
from app.db import get_sessionmaker
from app.models import Farm, User, WeightRecord
from app.schemas.animals import AnimalCreateIn

from .conftest import owner_with_farm


async def test_nonpositive_entry_weight_persists_no_weight_record(
    client: httpx.AsyncClient,
) -> None:
    """Defence in depth for the entry-weight insert.

    ``weight_kg`` is ``gt=0`` on the wire, so the HTTP surface cannot deliver
    zero — but ``AnimalCreateIn`` is neither frozen nor validate-assignment
    (``StrictInputModel`` only forbids extras), and nothing re-checks the
    lower bound between the handler's entry and the entry-weight insert. The
    insert's own ``> 0`` guard is what keeps a zero from reaching the
    ``ck_weight_records_weight_positive`` CHECK and turning a registration
    into an unhandled 500. Pin that guard directly.
    """
    owner = await owner_with_farm(client, email="zero-entry-weight@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        user = await db.get(User, farm.owner_id)
        assert user is not None
        payload = AnimalCreateIn.model_validate(
            {
                "tag_number": "ZERO-ENTRY-WEIGHT",
                "sex": "F",
                "source": "BORN",
                "current_bucket": "FOUNDATION",
                "historical_import_reason": "Reconcile the signed annual herd register",
                "weight_kg": 1.0,
            }
        )
        # Post-validation assignment: the value the wire contract forbids, fed
        # straight to the handler the way a future caller or a loosened schema
        # would deliver it.
        payload.weight_kg = 0.0
        assert payload.weight_kg == 0.0

        try:
            created = await create_animal(
                payload=payload,
                db=db,
                farm=farm,
                user=user,
                perms={"animals.create"},
                response=Response(),
                idempotency_key=None,
            )
        except Exception as error:
            pytest.fail(
                "A non-positive entry weight must record nothing, not raise "
                f"{type(error).__name__}: {error}"
            )
        await db.commit()

        weight_rows = await db.scalar(
            select(func.count())
            .select_from(WeightRecord)
            .where(WeightRecord.animal_id == created.id)
        )
    assert weight_rows == 0, f"a 0 kg entry weight persisted {weight_rows} weight record(s)"
    assert created.latest_weight_kg is None
