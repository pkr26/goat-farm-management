"""Neonatal mortality preserves litter history and reports actual dam replanning."""

import asyncio
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import animals as animals_api
from app.db import get_sessionmaker
from app.models import Animal, BucketMove, Farm, KidEntry, Task
from app.services.kidding import replan_dam_after_last_kid_death
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_bugs import _doe_with_two_retained_recovery_litters
from .test_breeding_extended import (
    all_tasks,
    backdate_latest_bucket_move,
    bred_doe,
    confirm,
    get_animal,
    iso,
    kid_on_ekd,
    make_breeding,
    make_buck,
    make_doe,
    move_to,
    place_health_hold,
    pregnant_doe,
)


async def _dead(
    client: httpx.AsyncClient, owner: dict[str, str], animal_id: int, when: date
) -> None:
    response = await client.post(
        f"/api/animals/{animal_id}/status",
        headers=owner,
        json={"new_status": "DEAD", "date": iso(when), "mortality_reported_at": iso(when)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "DEAD"


async def _replan_committed_death(farm_id: int, child_id: int, expected: bool) -> None:
    # Exercise the documented boolean interface with genuine already
    # committed mortality facts and the required child row lock. No status,
    # birth outcome, clinical event or return value is fabricated.
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        child = (
            await db.execute(
                select(Animal)
                .where(Animal.id == child_id, Animal.farm_id == farm_id)
                .with_for_update()
            )
        ).scalar_one()
        assert farm is not None and child.status == "DEAD" and child.status_date is not None
        try:
            result = await replan_dam_after_last_kid_death(db, farm, child, child.status_date)
            await db.commit()
        except MultipleResultsFound as exc:
            pytest.fail(
                f"Valid mortality facts must tolerate multiple matching history rows: {exc}"
            )
        assert result is expected


async def test_adult_death_outside_the_birth_cohort_reports_no_dam_replan(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    buck = await make_buck(client, owner, "ADULT-REPLAN-RESULT")
    await _dead(client, owner, buck["id"], today())
    await _replan_committed_death(int(owner["X-Farm-Id"]), buck["id"], False)


async def test_a_dam_without_her_own_birth_entry_has_no_parent_plan_to_rewrite(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe, _buck, breeding = await pregnant_doe(
        client, owner, "IMPORTED-DAM-RESULT", gestation_days=170
    )
    kidding = await kid_on_ekd(client, owner, breeding, kids=[{"sex": "M"}])
    await _dead(client, owner, doe["id"], today())
    await _replan_committed_death(int(owner["X-Farm-Id"]), doe["id"], False)
    async with get_sessionmaker()() as db:
        own_birth = (
            await db.execute(select(KidEntry).where(KidEntry.animal_id == doe["id"]))
        ).scalar_one_or_none()
        child = await db.get(Animal, kidding["kids"][0]["animal_id"])
        assert own_birth is None and child is not None
        assert child.status == "ACTIVE" and child.current_bucket == "MALE_KIDS"


async def test_latest_litter_mortality_sets_recovery_and_reuses_a_retained_pending_plan(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    _other_dam, _other_buck, other_breeding = await pregnant_doe(
        client, owner, "OTHER-MORTALITY", gestation_days=170
    )
    other = await kid_on_ekd(client, owner, other_breeding, kids=[{"sex": "M"}])
    await _dead(client, owner, other["kids"][0]["animal_id"], today() - timedelta(days=1))
    doe, _buck, breeding = await pregnant_doe(client, owner, "LATEST-MORTALITY", gestation_days=170)
    kidding = await kid_on_ekd(client, owner, breeding, kids=[{"sex": "M"}, {"sex": "F"}])
    late_id, final_id = [entry["animal_id"] for entry in kidding["kids"]]
    late_date = today() - timedelta(days=5)
    earlier_date = today() - timedelta(days=7)
    await _dead(client, owner, late_id, late_date)
    await _replan_committed_death(farm_id, late_id, False)
    assert (await get_animal(client, owner, doe["id"]))["current_bucket"] == "RECOVERY"
    await _dead(client, owner, final_id, earlier_date)
    expected_due = late_date + timedelta(days=14)
    tasks = await all_tasks(client, owner)
    recovery = [
        task
        for task in tasks
        if task["category"] == "BUCKET_MOVE"
        and task["animal_id"] == doe["id"]
        and task["breeding_record_id"] == breeding["id"]
        and task["status"] == "PENDING"
    ]
    assert len(recovery) == 1 and recovery[0]["due_date"] == iso(expected_due)
    existing_id = int(recovery[0]["id"])
    # A retained pending calendar may be stale while its target and birth
    # facts remain valid. Re-running the exported repair must reuse its ID.
    async with get_sessionmaker()() as db:
        existing = await db.get(Task, existing_id)
        assert existing is not None
        existing.due_date = today() + timedelta(days=50)
        await db.commit()
    await _replan_committed_death(farm_id, final_id, True)
    async with get_sessionmaker()() as db:
        repaired = list(
            (
                await db.execute(
                    select(Task).where(
                        Task.farm_id == farm_id,
                        Task.animal_id == doe["id"],
                        Task.breeding_record_id == breeding["id"],
                        Task.category == "BUCKET_MOVE",
                        Task.status == "PENDING",
                    )
                )
            ).scalars()
        )
        assert len(repaired) == 1 and repaired[0].id == existing_id
        assert repaired[0].due_date == expected_due
        entries = list(
            (
                await db.execute(
                    select(KidEntry)
                    .where(KidEntry.kidding_record_id == kidding["id"])
                    .order_by(KidEntry.id)
                )
            ).scalars()
        )
        assert [(entry.status, entry.mortality_reported_at) for entry in entries] == [
            ("DIED", late_date),
            ("DIED", earlier_date),
        ]


async def test_newer_litter_death_sweeps_its_duty_after_an_older_unrelated_duty(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe, older, newer, old_child, new_child = await _doe_with_two_retained_recovery_litters(
        client, owner
    )
    await _dead(client, owner, new_child["id"], today())
    await _replan_committed_death(int(owner["X-Farm-Id"]), new_child["id"], False)
    tasks = await all_tasks(client, owner)
    by_breeding = {
        task["breeding_record_id"]: task for task in tasks if task["category"] == "WEANING"
    }
    assert by_breeding[older["id"]]["status"] == "PENDING"
    assert by_breeding[newer["id"]]["status"] == "SKIPPED"
    assert (await get_animal(client, owner, old_child["id"]))["current_bucket"] == "RECOVERY"
    assert (await get_animal(client, owner, doe["id"]))["current_bucket"] == "RECOVERY"
    assert not any(
        task["category"] == "BUCKET_MOVE"
        and task["status"] == "PENDING"
        and task["breeding_record_id"] == newer["id"]
        for task in tasks
    )


async def test_two_other_dependent_kids_keep_the_dam_and_their_own_weaning_plan(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe, buck, first = await bred_doe(
        client, owner, "TWO-DEPENDENTS", today() - timedelta(days=340)
    )
    first = await confirm(client, owner, first["id"], kid_count=1)
    first_kidding = await kid_on_ekd(client, owner, first, kids=[{"sex": "M"}])
    await move_to(client, owner, doe["id"], "RESTING", history_override=True)
    await backdate_latest_bucket_move(doe["id"], today() - timedelta(days=186))
    second = await make_breeding(
        client, owner, doe["id"], buck["id"], breeding_date=iso(today() - timedelta(days=176))
    )
    second = await confirm(client, owner, second["id"], kid_count=2)
    second_kidding = await kid_on_ekd(client, owner, second, kids=[{"sex": "M"}, {"sex": "F"}])
    older_id = first_kidding["kids"][0]["animal_id"]
    await _dead(client, owner, older_id, today())
    await _replan_committed_death(int(owner["X-Farm-Id"]), older_id, False)
    assert (await get_animal(client, owner, doe["id"]))["current_bucket"] == "RECOVERY"
    tasks = await all_tasks(client, owner)
    assert (
        next(
            task
            for task in tasks
            if task["category"] == "WEANING" and task["breeding_record_id"] == second["id"]
        )["status"]
        == "PENDING"
    )
    assert not any(
        task["category"] == "BUCKET_MOVE"
        and task["status"] == "PENDING"
        and task["breeding_record_id"] == first["id"]
        for task in tasks
    )
    for kid in second_kidding["kids"]:
        assert (await get_animal(client, owner, kid["animal_id"]))["status"] == "ACTIVE"


async def test_prelink_weaning_fallback_matches_the_litter_date_and_keeps_sweeping(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    doe, _buck, breeding = await pregnant_doe(client, owner, "PRELINK-SWEEP", gestation_days=170)
    # Restore an older pre-link duty before the current delivery creates its
    # own later row. Its old due date must not halt the sweep or be cancelled.
    async with get_sessionmaker()() as db:
        unrelated = Task(
            farm_id=farm_id,
            animal_id=doe["id"],
            category="WEANING",
            title="Retained older pre-link weaning",
            due_date=today() - timedelta(days=200),
            auto_generated=True,
            status="PENDING",
            title_args={},
        )
        db.add(unrelated)
        await db.commit()
        unrelated_id = unrelated.id
    kidding = await kid_on_ekd(client, owner, breeding, kids=[{"sex": "F"}])
    matching_due = date.fromisoformat(kidding["date"]) + timedelta(days=60)
    async with get_sessionmaker()() as db:
        matching = Task(
            farm_id=farm_id,
            animal_id=doe["id"],
            category="WEANING",
            title="Retained current pre-link weaning",
            due_date=matching_due,
            auto_generated=True,
            status="PENDING",
            title_args={},
        )
        db.add(matching)
        await db.commit()
        matching_id = matching.id
    await _dead(client, owner, kidding["kids"][0]["animal_id"], today())
    tasks = {task["id"]: task for task in await all_tasks(client, owner)}
    assert tasks[unrelated_id]["status"] == "PENDING"
    assert tasks[matching_id]["status"] == "SKIPPED"
    linked = next(
        task
        for task in tasks.values()
        if task["category"] == "WEANING" and task["breeding_record_id"] == breeding["id"]
    )
    assert linked["status"] == "SKIPPED"


async def test_kid_death_returns_a_retry_conflict_before_a_real_dam_retirement_commits(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)
    doe, _buck, breeding = await pregnant_doe(
        client, owner, "FAST-DAM-CONFLICT", gestation_days=170
    )
    kidding = await kid_on_ekd(client, owner, breeding, kids=[{"sex": "M"}])
    child_id = kidding["kids"][0]["animal_id"]
    dam_locked = asyncio.Event()
    release_dam = asyncio.Event()

    async def pause_real_dam_replan(
        db: AsyncSession, farm: Farm, child: Animal, death_date: date
    ) -> bool:
        if child.id == doe["id"]:
            dam_locked.set()
            await release_dam.wait()
        return await replan_dam_after_last_kid_death(db, farm, child, death_date)

    monkeypatch.setattr(animals_api, "replan_dam_after_last_kid_death", pause_real_dam_replan)
    dam_request = asyncio.create_task(
        client.post(f"/api/animals/{doe['id']}/status", headers=owner, json={"new_status": "DEAD"})
    )
    try:
        await asyncio.wait_for(dam_locked.wait(), timeout=10)
        try:
            conflict = await asyncio.wait_for(
                client.post(
                    f"/api/animals/{child_id}/status", headers=owner, json={"new_status": "DEAD"}
                ),
                timeout=5,
            )
        except TimeoutError:
            pytest.fail(
                "A kid death must return its retry conflict before the dam owner releases the lock"
            )
        assert conflict.status_code == 409, conflict.text
        assert "retry" in conflict.json()["detail"].lower()
        async with get_sessionmaker()() as db:
            child = await db.get(Animal, child_id)
            entry = (
                await db.execute(select(KidEntry).where(KidEntry.animal_id == child_id))
            ).scalar_one()
            assert child is not None and child.status == "ACTIVE"
            assert entry.status == "ALIVE" and entry.mortality_reported_at is None
    finally:
        release_dam.set()
        if not dam_request.done():
            try:
                await asyncio.wait_for(dam_request, timeout=15)
            except TimeoutError:
                dam_request.cancel()
        await asyncio.gather(dam_request, return_exceptions=True)
    response = await dam_request
    assert response.status_code == 200, response.text


async def test_two_real_recovery_exits_preserve_an_adults_original_birth_outcome(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    granddam = await make_doe(client, owner, "TWICE-WEANED-GRANDDAM", age_days=1500)
    sire = await make_buck(
        client,
        owner,
        "TWICE-WEANED-SIRE",
        date_of_birth=iso(today() - timedelta(days=1500)),
        weight_date=iso(today() - timedelta(days=1500)),
    )
    birth = await make_breeding(
        client,
        owner,
        granddam["id"],
        sire["id"],
        breeding_date=iso(today() - timedelta(days=900)),
    )
    birth = await confirm(client, owner, birth["id"], kid_count=1)
    birth_record = await kid_on_ekd(client, owner, birth, kids=[{"sex": "F"}])
    adult_id = birth_record["kids"][0]["animal_id"]

    async def complete_actual_weaning(dam_id: int) -> None:
        task = next(
            item
            for item in await all_tasks(client, owner)
            if item["animal_id"] == dam_id
            and item["category"] == "WEANING"
            and item["status"] == "PENDING"
        )
        response = await client.post(f"/api/tasks/{task['id']}/complete", headers=owner)
        assert response.status_code == 200, response.text

    await complete_actual_weaning(granddam["id"])
    weighed = await client.post(
        f"/api/animals/{adult_id}/weight",
        headers=owner,
        json={"weight_kg": 26.0, "date": iso(today() - timedelta(days=270))},
    )
    assert weighed.status_code == 201, weighed.text
    unrelated_sire = await make_buck(
        client,
        owner,
        "TWICE-WEANED-OUTSIDER",
        date_of_birth=iso(today() - timedelta(days=1500)),
        weight_date=iso(today() - timedelta(days=1500)),
    )
    own_breeding = await make_breeding(
        client,
        owner,
        adult_id,
        unrelated_sire["id"],
        breeding_date=iso(today() - timedelta(days=260)),
    )
    own_breeding = await confirm(client, owner, own_breeding["id"], kid_count=1)
    await kid_on_ekd(client, owner, own_breeding, kids=[{"sex": "M"}])
    await complete_actual_weaning(adult_id)
    # An accepted owner history correction can return an already weaned
    # adult to RECOVERY; it cannot erase either genuine prior cohort exit.
    await move_to(client, owner, adult_id, "RECOVERY", history_override=True)
    async with get_sessionmaker()() as db:
        exits = list(
            (
                await db.execute(
                    select(BucketMove.id).where(
                        BucketMove.animal_id == adult_id, BucketMove.from_bucket == "RECOVERY"
                    )
                )
            ).scalars()
        )
        assert len(exits) == 2
    await _dead(client, owner, adult_id, today())
    await _replan_committed_death(int(owner["X-Farm-Id"]), adult_id, False)
    async with get_sessionmaker()() as db:
        entry = (
            await db.execute(select(KidEntry).where(KidEntry.animal_id == adult_id))
        ).scalar_one()
        assert entry.status == "ALIVE" and entry.mortality_reported_at is None


async def test_dead_dam_with_a_held_dependent_child_gets_no_recovery_plan(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe, _buck, breeding = await pregnant_doe(client, owner, "HELD-ORPHAN", gestation_days=170)
    kidding = await kid_on_ekd(client, owner, breeding, kids=[{"sex": "M"}])
    child_id = kidding["kids"][0]["animal_id"]
    await place_health_hold(client, owner, child_id)
    await _dead(client, owner, doe["id"], today())
    child = await get_animal(client, owner, child_id)
    assert child["status"] == "ACTIVE" and child["current_bucket"] == "RECOVERY"
    await _dead(client, owner, child_id, today())
    await _replan_committed_death(int(owner["X-Farm-Id"]), child_id, False)
    tasks = await all_tasks(client, owner)
    assert not any(
        task["animal_id"] == doe["id"]
        and task["status"] == "PENDING"
        and task["category"] == "BUCKET_MOVE"
        for task in tasks
    )


async def test_unrelated_weaning_history_does_not_hide_a_real_dependent_litter(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    noise_dam, _buck, noise_breeding = await pregnant_doe(
        client, owner, "OTHER-COHORT-HISTORY", gestation_days=260
    )
    await kid_on_ekd(client, owner, noise_breeding, kids=[{"sex": "M"}])
    noise_task = next(
        task
        for task in await all_tasks(client, owner)
        if task["animal_id"] == noise_dam["id"]
        and task["category"] == "WEANING"
        and task["status"] == "PENDING"
    )
    completed = await client.post(f"/api/tasks/{noise_task['id']}/complete", headers=owner)
    assert completed.status_code == 200, completed.text
    doe, _older, newer, old_child, new_child = await _doe_with_two_retained_recovery_litters(
        client, owner
    )
    await _dead(client, owner, new_child["id"], today())
    await _replan_committed_death(int(owner["X-Farm-Id"]), new_child["id"], False)
    assert (await get_animal(client, owner, old_child["id"]))["current_bucket"] == "RECOVERY"
    assert (await get_animal(client, owner, doe["id"]))["current_bucket"] == "RECOVERY"
    assert not any(
        task["animal_id"] == doe["id"]
        and task["breeding_record_id"] == newer["id"]
        and task["category"] == "BUCKET_MOVE"
        and task["status"] == "PENDING"
        for task in await all_tasks(client, owner)
    )


async def test_a_genuinely_weaned_child_restored_to_recovery_is_not_a_dependency(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe, buck, first = await bred_doe(
        client, owner, "RESTORED-WEANED-CHILD", today() - timedelta(days=340)
    )
    first = await confirm(client, owner, first["id"], kid_count=1)
    first_kidding = await kid_on_ekd(client, owner, first, kids=[{"sex": "M"}])
    old_child_id = first_kidding["kids"][0]["animal_id"]
    old_weaning = next(
        task
        for task in await all_tasks(client, owner)
        if task["breeding_record_id"] == first["id"] and task["category"] == "WEANING"
    )
    response = await client.post(f"/api/tasks/{old_weaning['id']}/complete", headers=owner)
    assert response.status_code == 200, response.text
    await move_to(client, owner, old_child_id, "RECOVERY", history_override=True)
    await move_to(client, owner, doe["id"], "RESTING", history_override=True)
    await backdate_latest_bucket_move(doe["id"], today() - timedelta(days=186))
    second = await make_breeding(
        client, owner, doe["id"], buck["id"], breeding_date=iso(today() - timedelta(days=176))
    )
    second = await confirm(client, owner, second["id"], kid_count=1)
    second_kidding = await kid_on_ekd(client, owner, second, kids=[{"sex": "F"}])
    await _dead(client, owner, second_kidding["kids"][0]["animal_id"], today())
    await _replan_committed_death(
        int(owner["X-Farm-Id"]), second_kidding["kids"][0]["animal_id"], True
    )
    assert (await get_animal(client, owner, old_child_id))["status"] == "ACTIVE"
    assert any(
        task["animal_id"] == doe["id"]
        and task["breeding_record_id"] == second["id"]
        and task["category"] == "BUCKET_MOVE"
        and task["status"] == "PENDING"
        for task in await all_tasks(client, owner)
    )
