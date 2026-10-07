"""Actual stored round cohorts keep native admission and treatment evidence exact."""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_sessionmaker
from app.models import HealthRound, HealthRoundTarget, Task
from app.services.health_rounds import (
    ensure_round_snapshot,
    is_herd_round,
    recorded_components,
    require_round_targets,
    round_counts,
    round_is_complete,
)
from app.utils import today

from .conftest import owner_with_farm
from .test_health_extended import make_animal
from .test_health_safety import _seed_herd_round_task
from .type_helpers import Headers


async def _snapshot_pending_programme(
    db: AsyncSession, farm_id: int, title: str, category: str = "VACCINE"
) -> tuple[Task, HealthRound]:
    task = Task(
        farm_id=farm_id,
        title=title,
        due_date=today(),
        category=category,
        status="PENDING",
        auto_generated=True,
    )
    db.add(task)
    await db.flush()
    # Insertion owns the creation lock; retain the documented task mutex too.
    task = (await db.execute(select(Task).where(Task.id == task.id).with_for_update())).scalar_one()
    assert is_herd_round(task) is True
    try:
        round_ = await ensure_round_snapshot(db, task)
    except ValueError as exc:
        pytest.fail(f"A recognized pending herd programme must snapshot successfully: {exc}")
    return task, round_


@pytest.mark.parametrize(
    ("category", "title", "component"),
    [("VACCINE", "PPR round", "PPR"), ("DEWORMING", "Scheduled deworming", "Deworming")],
    ids=["vaccine", "deworming"],
)
async def test_native_pending_snapshot_retains_exact_farm_members_and_initial_provenance(
    client: httpx.AsyncClient, category: str, title: str, component: str
) -> None:
    headers = await owner_with_farm(client)
    first = await make_animal(client, headers, tag="NATIVE-SNAPSHOT-A")
    second = await make_animal(client, headers, tag="NATIVE-SNAPSHOT-B")
    other = await owner_with_farm(client, email="native-round-other@example.com")
    await make_animal(client, other, tag="NATIVE-SNAPSHOT-FOREIGN")
    async with get_sessionmaker()() as db:
        task, round_ = await _snapshot_pending_programme(
            db, int(headers["X-Farm-Id"]), title, category
        )
        assert round_.task_id == task.id and round_.farm_id == task.farm_id
        assert round_.required_components == [component]
        rows = list(
            (
                await db.execute(
                    select(HealthRoundTarget)
                    .where(HealthRoundTarget.task_id == task.id)
                    .order_by(HealthRoundTarget.animal_id)
                )
            ).scalars()
        )
        assert [row.animal_id for row in rows] == [first["id"], second["id"]]
        assert all(row.farm_id == task.farm_id for row in rows)
        assert all(row.added_at == round_.snapshot_at for row in rows)
        assert all(row.added_by_id is None and row.inclusion_reason is None for row in rows)
        assert await ensure_round_snapshot(db, task) is round_
        assert await round_counts(db, round_) == (2, 0, 0, 2)
        await db.commit()


async def test_native_snapshot_rejects_a_real_skipped_duty_without_backfilling(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, tag="NATIVE-SKIPPED-ROUND")
    task_id = await _seed_herd_round_task(headers, "PPR round", initialize=False)
    skipped = await client.post(
        f"/api/tasks/{task_id}/skip",
        headers=headers,
        json={"reason": "Programme postponed before any declared cohort"},
    )
    assert skipped.status_code == 200, skipped.text
    async with get_sessionmaker()() as db:
        task = (
            await db.execute(select(Task).where(Task.id == task_id).with_for_update())
        ).scalar_one()
        assert task.status == "SKIPPED" and task.skipped_at is not None
        with pytest.raises(ValueError, match="Only a pending herd health duty can start a round"):
            await ensure_round_snapshot(db, task)
        assert await db.get(HealthRound, task_id) is None
        assert (
            list(
                (
                    await db.execute(
                        select(HealthRoundTarget).where(HealthRoundTarget.task_id == task_id)
                    )
                ).scalars()
            )
            == []
        )


async def test_native_empty_farm_is_not_completed_treatment_evidence(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    async with get_sessionmaker()() as db:
        _, round_ = await _snapshot_pending_programme(db, int(headers["X-Farm-Id"]), "PPR round")
        assert await round_counts(db, round_) == (0, 0, 0, 0)
        assert await round_is_complete(db, round_) is False


@pytest.mark.parametrize("target", ["", "   "], ids=["empty", "whitespace"])
async def test_native_blank_evidence_names_one_actual_required_component(
    client: httpx.AsyncClient, target: str
) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, tag="NATIVE-COMPONENT")
    async with get_sessionmaker()() as db:
        _, round_ = await _snapshot_pending_programme(
            db, int(headers["X-Farm-Id"]), "ET + HS pre-monsoon round"
        )
        try:
            components = recorded_components(round_, target, "Haemorrhagic Septicaemia (HS)")
        except ValueError as exc:
            pytest.fail(f"Blank evidence can name its actual selected HS component: {exc}")
        assert components == ["Haemorrhagic Septicaemia (HS)"]
        with pytest.raises(ValueError, match="required round component"):
            recorded_components(round_, target, "PPR")
        assert recorded_components(round_, "ET + HS", None) == round_.required_components


async def _record_round_component(
    client: httpx.AsyncClient, headers: Headers, task_id: int, component: str
) -> list[int]:
    reviewed = await client.post(
        "/api/health/events/preview",
        headers=headers,
        json={
            "scope": "bucket",
            "bucket": "FOUNDATION",
            "task_id": task_id,
            "round_component": component,
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    ids: list[int] = reviewed.json()["target_animal_ids"]
    recorded = await client.post(
        "/api/health/events",
        headers=headers,
        json={
            "scope": "bucket",
            "bucket": "FOUNDATION",
            "task_id": task_id,
            "expected_animal_ids": ids,
            "type": "VACCINE",
            "disease_target": component,
            "schedule_template_name": component,
        },
    )
    assert recorded.status_code == 201, recorded.text
    assert [event["animal_id"] for event in recorded.json()] == ids
    return ids


async def test_native_round_admission_and_coverage_use_the_exact_exclusion_cohort(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    first = await make_animal(client, headers, tag="NATIVE-COHORT-A")
    second = await make_animal(client, headers, tag="NATIVE-COHORT-B")
    third = await make_animal(client, headers, tag="NATIVE-COHORT-C")
    async with get_sessionmaker()() as db:
        first_task, _ = await _snapshot_pending_programme(
            db, int(headers["X-Farm-Id"]), "ET + HS pre-monsoon round"
        )
        second_task, _ = await _snapshot_pending_programme(
            db, int(headers["X-Farm-Id"]), "PPR round"
        )
        task_a_id, task_b_id = first_task.id, second_task.id
        await db.commit()
    for task_id, animal_id in ((task_a_id, first["id"]), (task_b_id, second["id"])):
        excluded = await client.post(
            f"/api/health/rounds/{task_id}/exclusions",
            headers=headers,
            json={"animal_ids": [animal_id], "reason": "Vet deferred this exact round membership"},
        )
        assert excluded.status_code == 200, excluded.text
    expected = [second["id"], third["id"]]
    hs = "Haemorrhagic Septicaemia (HS)"
    assert await _record_round_component(client, headers, task_a_id, hs) == expected
    async with get_sessionmaker()() as db:
        round_a = await db.get(HealthRound, task_a_id)
        assert round_a is not None
        assert await round_counts(db, round_a) == (3, 1, 0, 2)
        # Two actual HS events already cover these members. A duplicate check
        # is about existence, independent of how many prior rows are returned.
        with pytest.raises(ValueError, match="already recorded"):
            await require_round_targets(db, round_a, expected, [hs])
        assert await round_is_complete(db, round_a) is False
    assert (
        await _record_round_component(client, headers, task_a_id, "Enterotoxaemia (ET)") == expected
    )
    async with get_sessionmaker()() as db:
        round_a = await db.get(HealthRound, task_a_id)
        task_a = await db.get(Task, task_a_id)
        assert round_a is not None and task_a is not None
        assert await round_counts(db, round_a) == (3, 1, 2, 0)
        assert await round_is_complete(db, round_a) is True
        assert task_a.status == "DONE" and task_a.completed_at is not None
