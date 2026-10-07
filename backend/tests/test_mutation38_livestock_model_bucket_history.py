"""Movement identity ordering, dated elapsed days, and complete history derivation."""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import get_sessionmaker
from app.models import Animal, BucketMove, WeightRecord
from app.models.animals import _derive_history_farm_id
from app.services.animals import move_animal
from app.utils import today

from .conftest import owner_with_farm
from .test_concurrency import make_animal


async def _eligible_foundation_animal(client: httpx.AsyncClient, email: str, tag: str) -> int:
    owner = await owner_with_farm(client, email=email)
    return await make_animal(
        client,
        owner,
        tag=tag,
        date_of_birth=(today() - timedelta(days=800)).isoformat(),
        weight_kg=30.0,
        weight_date=today().isoformat(),
    )


async def test_last_move_same_audit_instant_uses_the_newer_persisted_identity(
    client: httpx.AsyncClient,
) -> None:
    animal_id = await _eligible_foundation_animal(client, "move-order@farm.in", "MOVE-ORDER")
    async with get_sessionmaker()() as db:
        animal = (
            await db.execute(
                select(Animal)
                .where(Animal.id == animal_id)
                .options(selectinload(Animal.bucket_moves), selectinload(Animal.weight_records))
            )
        ).scalar_one()
        assert len(animal.bucket_moves) == 1
        initial = animal.bucket_moves[0]
        move_animal(
            db,
            animal,
            "BREEDING",
            reference_date=today(),
            facts=(30.0, False),
        )
        pending = next(row for row in db.new if isinstance(row, BucketMove))
        # Set the audit instant at insertion. Real history imports may share
        # a timestamp; neither row's assigned sequence ID is restored or mocked.
        pending.moved_at = initial.moved_at
        await db.commit()
        newer_id = pending.id
        assert newer_id > initial.id
    async with get_sessionmaker()() as db:
        animal = (
            await db.execute(
                select(Animal)
                .where(Animal.id == animal_id)
                .options(selectinload(Animal.bucket_moves))
            )
        ).scalar_one()
        animal.bucket_moves.sort(key=lambda row: row.id)
        assert len(animal.bucket_moves) == 2
        assert animal.bucket_moves[0].moved_at == animal.bucket_moves[1].moved_at
        latest = animal.last_bucket_move
        assert latest is not None
        assert latest.id == newer_id
        assert latest.to_bucket == animal.current_bucket == "BREEDING"


async def test_last_move_prefers_an_assigned_identity_until_a_pending_move_is_flushed(
    client: httpx.AsyncClient,
) -> None:
    animal_id = await _eligible_foundation_animal(client, "move-pending@farm.in", "MOVE-PENDING")
    async with get_sessionmaker()() as db:
        animal = (
            await db.execute(
                select(Animal)
                .where(Animal.id == animal_id)
                .options(selectinload(Animal.bucket_moves), selectinload(Animal.weight_records))
            )
        ).scalar_one()
        assert len(animal.bucket_moves) == 1
        initial = animal.bucket_moves[0]
        assert initial.id == 1
        move_animal(db, animal, "BREEDING", reference_date=today(), facts=(30.0, False))
        pending = next(row for row in db.new if isinstance(row, BucketMove))
        pending.moved_at = initial.moved_at
        animal.bucket_moves.insert(0, pending)
        assert pending.id is None
        assert animal.last_bucket_move is initial
        await db.flush()
        assert pending.id is not None and pending.id > initial.id
        assert animal.last_bucket_move is pending
        await db.commit()


async def test_elapsed_bucket_days_accept_a_real_business_effective_date_and_clamp_prearrival(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="elapsed-bucket@farm.in")
    arrival = today() - timedelta(days=5)
    animal_id = await make_animal(
        client, owner, tag="ELAPSED-BUCKET", purchase_date=arrival.isoformat()
    )
    async with get_sessionmaker()() as db:
        animal = (
            await db.execute(
                select(Animal)
                .where(Animal.id == animal_id)
                .options(selectinload(Animal.bucket_moves))
            )
        ).scalar_one()
        assert animal.bucket_moves[0].effective_date == arrival
        try:
            elapsed = animal.days_in_current_bucket_on(today(), "Asia/Kolkata")
            before_arrival = animal.days_in_current_bucket_on(
                arrival - timedelta(days=1), "Asia/Kolkata"
            )
        except (TypeError, AttributeError) as exc:
            pytest.fail(f"Valid persisted business dates must produce elapsed bucket days: {exc!r}")
        assert elapsed == 5
        assert before_arrival == 0


async def test_history_derivation_continues_after_a_legitimate_unresolved_pending_identity(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="history-pending-order@farm.in")
    animal_id = await make_animal(client, owner, tag="HISTORY-PENDING-ORDER")
    async with get_sessionmaker()() as db:
        cached = await db.get(Animal, animal_id)
        assert cached is not None
    # This is a real detached, loaded animal, attached to a real unit Session.
    # The event's existing helper contract explicitly leaves missing identities
    # unresolved. Probe that pending stage without flushing any unresolved row.
    unresolved = WeightRecord(animal_id=None, farm_id=None, date=today(), weight_kg=2.5)
    linked = WeightRecord(animal_id=animal_id, farm_id=None, date=today(), weight_kg=26.0)
    with Session(autoflush=False) as session:
        session.add(cached)
        session.add_all([unresolved, linked])
        assert list(session.new) == [unresolved, linked]
        _derive_history_farm_id(session, None, None)
        assert unresolved.animal_id is None and unresolved.farm_id is None
        assert linked.farm_id == int(owner["X-Farm-Id"])
