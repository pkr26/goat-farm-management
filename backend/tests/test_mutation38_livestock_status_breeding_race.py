"""A status writer must recheck clinical chronology after a competing service."""

import asyncio
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import breeding as breeding_api
from app.db import get_sessionmaker
from app.models import Animal, BreedingRecord, Farm, Task, Transaction
from app.services.breeding import create_breeding_record
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import make_buck, make_doe


async def test_backdated_sale_waits_for_a_new_service_and_preserves_its_clinical_graph(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client, email="status-service-race@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    doe = await make_doe(client, owner, "STATUS-SERVICE-DOE", bucket="BREEDING")
    buck = await make_buck(client, owner, "STATUS-SERVICE-BUCK")
    doe_id = int(doe["id"])
    prepared = asyncio.Event()
    permit_commit = asyncio.Event()
    service_pid = 0
    original_create = create_breeding_record

    async def pause_prepared_service(
        db: AsyncSession,
        farm: Farm,
        target_doe: Animal,
        target_buck: Animal | None,
        breeding_date: date,
        created_by_id: int | None = None,
        *,
        doe_latest_weight_kg: float | None,
        has_open_breeding: bool,
        method: str = "NATURAL",
        semen_sire_name: str | None = None,
        actor_is_owner: bool = False,
    ) -> BreedingRecord:
        nonlocal service_pid
        result = await original_create(
            db,
            farm,
            target_doe,
            target_buck,
            breeding_date,
            created_by_id,
            doe_latest_weight_kg=doe_latest_weight_kg,
            has_open_breeding=has_open_breeding,
            method=method,
            semen_sire_name=semen_sire_name,
            actor_is_owner=actor_is_owner,
        )
        if target_doe.id == doe_id:
            # The complete real service has flushed its clinical graph and
            # retains the request's canonical Animal locks until commit.
            pid = await db.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(pid, int)
            service_pid = pid
            prepared.set()
            await permit_commit.wait()
        return result

    monkeypatch.setattr(breeding_api, "create_breeding_record", pause_prepared_service)
    service = asyncio.create_task(
        client.post(
            "/api/breeding",
            json={
                "doe_id": doe_id,
                "buck_id": buck["id"],
                "breeding_date": today().isoformat(),
            },
            headers=owner | {"Idempotency-Key": "status-race-service"},
        )
    )
    sale: asyncio.Task[httpx.Response] | None = None
    try:
        try:
            await asyncio.wait_for(prepared.wait(), timeout=30)
        except TimeoutError:
            pytest.fail("The real breeding request did not prepare its clinical graph")
        sale = asyncio.create_task(
            client.post(
                f"/api/animals/{doe_id}/status",
                json={
                    "new_status": "SOLD",
                    "date": (today() - timedelta(days=1)).isoformat(),
                    "sale_price": 5000,
                },
                headers=owner | {"Idempotency-Key": "status-race-sale"},
            )
        )
        for _ in range(1000):
            async with get_sessionmaker()() as observer:
                queued = await observer.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE datname = current_database() "
                        "AND :service_pid = ANY(pg_blocking_pids(pid)))"
                    ),
                    {"service_pid": service_pid},
                )
            if queued:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("The status request never queued behind the real breeding")
        permit_commit.set()
        try:
            bred, sold = await asyncio.wait_for(asyncio.gather(service, sale), timeout=30)
        except TimeoutError:
            pytest.fail("The competing service and sale did not finish after commit was released")
    finally:
        permit_commit.set()
        requests = [request for request in (service, sale) if request is not None]
        for request in requests:
            if not request.done():
                request.cancel()
        await asyncio.gather(*requests, return_exceptions=True)

    assert bred.status_code == 201, bred.text
    assert sold.status_code == 422, sold.text
    breeding_id = int(bred.json()["id"])
    async with get_sessionmaker()() as observer:
        animal = await observer.get(Animal, doe_id)
        assert animal is not None and animal.status == "ACTIVE"
        record = await observer.get(BreedingRecord, breeding_id)
        assert record is not None and record.outcome == "PENDING"
        assert record.doe_id == doe_id and record.buck_id == buck["id"]
        duties = list(
            (
                await observer.execute(select(Task).where(Task.breeding_record_id == breeding_id))
            ).scalars()
        )
        assert {duty.category for duty in duties} == {"ULTRASOUND", "HEAT_WATCH"}
        assert all(duty.status == "PENDING" for duty in duties)
        assert not list(
            (
                await observer.execute(
                    select(Transaction.id).where(
                        Transaction.farm_id == farm_id,
                        Transaction.source_type == "ANIMAL_SALE",
                        Transaction.source_id == doe_id,
                    )
                )
            ).scalars()
        )
