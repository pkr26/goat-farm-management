"""Observable health lookup, retained-cohort and progress-read contracts."""

from datetime import datetime

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.health import _round_out
from app.db import get_sessionmaker
from app.models import Farm, Task
from app.schemas.health import HealthRoundOut
from app.utils import today, utcnow

from .conftest import owner_with_farm
from .test_health_extended import make_animal, make_batch
from .test_health_safety import _seed_herd_round_task


async def _retained_progress(db: AsyncSession, task: Task) -> HealthRoundOut:
    try:
        return await _round_out(db, task)
    except AttributeError as exc:
        if exc.obj is not None or exc.name not in {"required_components", "snapshot_at"}:
            raise
        pytest.fail("A retained uninitialized herd duty has a valid empty progress response")


@pytest.mark.parametrize("endpoint", ["animals", "purchase-batches"])
async def test_health_lookup_reports_fifty_by_default_and_accepts_exactly_one_hundred(
    client: httpx.AsyncClient, endpoint: str
) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, tag="LOOKUP-REGISTERED")
    batch = await make_batch(client, headers, count=1, create_animals=True)
    read = await client.get(f"/api/health/{endpoint}", headers=headers)
    assert read.status_code == 200, read.text
    assert read.json()["limit"] == 50
    assert read.json()["offset"] == 0
    if endpoint == "animals":
        assert read.json()["total"] == 2
        assert animal["id"] in {row["id"] for row in read.json()["animals"]}
    else:
        assert read.json()["batches"] == [{"id": batch["id"], "active_quarantine_animal_count": 1}]
    ceiling = await client.get(f"/api/health/{endpoint}?limit=100", headers=headers)
    assert ceiling.status_code == 200, ceiling.text
    assert ceiling.json()["limit"] == 100
    excessive = await client.get(f"/api/health/{endpoint}?limit=101", headers=headers)
    assert excessive.status_code == 422, excessive.text
    assert any(error["loc"] == ["query", "limit"] for error in excessive.json()["detail"])


async def test_retained_completed_round_has_no_invented_snapshot_or_counts(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, tag="LEGACY-AVAILABLE")
    async with get_sessionmaker()() as db:
        task = Task(
            farm_id=int(headers["X-Farm-Id"]),
            title="ET + HS pre-monsoon round (2025) — all animals",
            due_date=today(),
            category="VACCINE",
            status="DONE",
            auto_generated=True,
            completed_at=utcnow(),
        )
        db.add(task)
        await db.commit()
        task_id = task.id
        native = await _retained_progress(db, task)
        assert native.limit == 100 and native.offset == 0
        assert native.initialized is False and native.snapshot_at is None
        assert (native.total_targets, native.excluded_targets, native.covered_targets) == (0, 0, 0)
        assert native.remaining_units == 0 and native.available_additions == 1
        assert native.targets == []
        assert native.required_components == [
            "Enterotoxaemia (ET)",
            "Haemorrhagic Septicaemia (HS)",
        ]
    read = await client.get(f"/api/health/rounds/{task_id}", headers=headers)
    assert read.status_code == 200, read.text
    assert read.json()["limit"] == 100 and read.json()["offset"] == 0
    assert read.json()["task_status"] == "DONE"
    assert read.json()["snapshot_at"] is None
    assert read.json()["remaining_units"] == 0


async def test_round_progress_keeps_exclusions_with_their_exact_cohort_and_member(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    first = await make_animal(client, headers, tag="COHORT-A")
    second = await make_animal(client, headers, tag="COHORT-B")
    round_a = await _seed_herd_round_task(headers, "ET + HS pre-monsoon round (2026) — all animals")
    entrant = await make_animal(client, headers, tag="COHORT-LATE")
    round_b = await _seed_herd_round_task(headers, "PPR vaccination round — all animals")
    for task_id, animal_id, reason in (
        (round_a, first["id"], "Vet deferred the first member of the ET/HS cohort"),
        (round_b, second["id"], "Vet separately deferred the second member of the PPR cohort"),
    ):
        response = await client.post(
            f"/api/health/rounds/{task_id}/exclusions",
            headers=headers,
            json={"animal_ids": [animal_id], "reason": reason},
        )
        assert response.status_code == 200, response.text
    read = await client.get(f"/api/health/rounds/{round_a}", headers=headers)
    assert read.status_code == 200, read.text
    body = read.json()
    assert body["initialized"] is True and body["snapshot_at"] is not None
    assert isinstance(datetime.fromisoformat(body["snapshot_at"]), datetime)
    assert body["total_targets"] == 2 and body["excluded_targets"] == 1
    assert body["covered_targets"] == 0 and body["remaining_units"] == 2
    assert body["available_additions"] == 1
    assert [row["animal_id"] for row in body["targets"]] == [first["id"], second["id"]]
    assert entrant["id"] not in {row["animal_id"] for row in body["targets"]}
    excluded, included = body["targets"]
    assert excluded["exclusion_reason"] == "Vet deferred the first member of the ET/HS cohort"
    assert excluded["excluded_by_id"] is not None and excluded["excluded_at"] is not None
    assert included["exclusion_reason"] is None
    assert included["excluded_by_id"] is None and included["excluded_at"] is None
    assert excluded["covered_components"] == [] and included["covered_components"] == []
    page = await client.get(f"/api/health/rounds/{round_a}?limit=1&offset=1", headers=headers)
    assert page.status_code == 200, page.text
    assert page.json()["limit"] == 1 and page.json()["offset"] == 1
    assert [row["animal_id"] for row in page.json()["targets"]] == [second["id"]]
    assert page.json()["targets"][0]["exclusion_reason"] is None


@pytest.mark.parametrize("task_id", [0, 2_147_483_647], ids=["retained-zero", "int4-ceiling"])
async def test_round_lookup_obeys_positive_int4_boundary_for_real_retained_rows(
    client: httpx.AsyncClient, task_id: int
) -> None:
    headers = await owner_with_farm(client)
    async with get_sessionmaker()() as db:
        farm = (
            await db.execute(select(Farm).where(Farm.id == int(headers["X-Farm-Id"])))
        ).scalar_one()
        task = Task(
            id=task_id,
            farm_id=farm.id,
            title="PPR vaccination round — all animals",
            due_date=today(),
            category="VACCINE",
            status="DONE",
            auto_generated=True,
            completed_at=utcnow(),
        )
        db.add(task)
        await db.commit()
    read = await client.get(f"/api/health/rounds/{task_id}", headers=headers)
    assert read.status_code == (404 if task_id == 0 else 200), read.text
    if task_id != 0:
        assert read.json()["task_id"] == 2_147_483_647
        assert read.json()["initialized"] is False
