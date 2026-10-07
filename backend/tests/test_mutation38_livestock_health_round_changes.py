"""Herd-round target changes preserve admission, identity and audit facts."""

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import FarmMembership, HealthRound, Task, User

from .conftest import owner_with_farm
from .test_health_extended import make_animal
from .test_health_safety import _seed_herd_round_task
from .test_scoped_picker_lookups import limited_worker, set_status


async def _start(client: httpx.AsyncClient, owner: dict[str, str], task_id: int) -> None:
    started = await client.post(f"/api/health/rounds/{task_id}/start", headers=owner)
    assert started.status_code == 200, started.text


async def _change(
    client: httpx.AsyncClient,
    actor: dict[str, str],
    task_id: int,
    operation: str,
    animal_ids: list[int],
    reason: str,
) -> httpx.Response:
    return await client.post(
        f"/api/health/rounds/{task_id}/{operation}",
        headers=actor,
        json={"animal_ids": animal_ids, "reason": reason},
    )


async def _assign_existing_role(task_id: int, actor_email: str) -> None:
    async with get_sessionmaker()() as db:
        role_id = (
            await db.execute(
                select(FarmMembership.role_id)
                .join(User, User.id == FarmMembership.user_id)
                .where(User.email == actor_email)
            )
        ).scalar_one()
        task = await db.get(Task, task_id)
        assert task is not None
        # Native duty assignment changes operational responsibility only;
        # the real worker/role exist and every composite FK stays enabled.
        task.assigned_role_id = role_id
        await db.commit()


async def test_round_start_rejects_an_unrecognized_native_programme_as_422(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    await make_animal(client, owner, "UNDECLARED-ROUND")
    # The declared native Task interface permits a pending herd-health duty
    # whose free-text title does not identify a seeded vaccine programme.
    # Starting it must ask for programme declaration without inventing a
    # clinical target or a completed treatment.
    task_id = await _seed_herd_round_task(owner, "Programme not yet declared", initialize=False)
    response = await client.post(f"/api/health/rounds/{task_id}/start", headers=owner)
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == (
        "Declare a recognized vaccine/deworming programme before starting a round"
    )
    async with get_sessionmaker()() as db:
        assert await db.get(HealthRound, task_id) is None
        task = await db.get(Task, task_id)
        assert task is not None and task.status == "PENDING"


async def test_health_manager_cannot_change_another_workers_assigned_herd_round(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    await make_animal(client, owner, "ASSIGNED-ROUND")
    assigned = await limited_worker(
        client,
        owner,
        name="Assigned vet",
        email="assigned-round@example.test",
        permissions=["health.view", "health.manage"],
    )
    other = await limited_worker(
        client,
        owner,
        name="Other vet",
        email="other-round@example.test",
        permissions=["health.view", "health.manage"],
    )
    task_id = await _seed_herd_round_task(owner, "PPR round", initialize=False)
    async with get_sessionmaker()() as db:
        assignee_id = (
            await db.execute(select(User.id).where(User.email == "assigned-round@example.test"))
        ).scalar_one()
        task = await db.get(Task, task_id)
        assert task is not None
        task.assigned_user_id = assignee_id
        task.assigned_role_id = (
            await db.execute(
                select(FarmMembership.role_id).where(
                    FarmMembership.farm_id == int(owner["X-Farm-Id"]),
                    FarmMembership.user_id == assignee_id,
                )
            )
        ).scalar_one()
        await db.commit()
    refused = await client.post(f"/api/health/rounds/{task_id}/start", headers=other)
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"] == "This duty is not assigned to you"
    async with get_sessionmaker()() as db:
        assert await db.get(HealthRound, task_id) is None
    await _start(client, assigned, task_id)


async def test_round_target_admission_preserves_start_tenant_active_and_pending_guards(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    anchor = await make_animal(client, owner, "ROUND-ANCHOR")
    task_id = await _seed_herd_round_task(owner, "PPR round", initialize=False)
    unstarted = await _change(client, owner, task_id, "targets", [anchor["id"]], "Reviewed")
    assert unstarted.status_code == 409, unstarted.text
    assert unstarted.json()["detail"] == "Start the herd round before changing targets"
    await _start(client, owner, task_id)
    entrant = await make_animal(client, owner, "ROUND-NEW-ARRIVAL")
    admitted = await _change(client, owner, task_id, "targets", [entrant["id"]], "New arrival")
    assert admitted.status_code == 200, admitted.text
    assert admitted.json()["total_targets"] == 2
    assert admitted.json()["task_status"] == "PENDING"
    foreign = await owner_with_farm(client, email="foreign-round@example.test", farm_name="Other")
    outsider = await make_animal(client, foreign, "FOREIGN-ROUND-TARGET")
    absent = await _change(client, owner, task_id, "targets", [outsider["id"]], "Reviewed")
    assert absent.status_code == 404, absent.text
    assert absent.json()["detail"] == "Round animal not found"
    departed = await make_animal(client, owner, "DEPARTED-ROUND-ARRIVAL")
    await set_status(client, owner, departed["id"], "DEAD")
    inactive = await _change(client, owner, task_id, "targets", [departed["id"]], "Reviewed")
    assert inactive.status_code == 409, inactive.text
    assert inactive.json()["detail"] == "Only an active animal can join a round"


async def test_round_inclusion_replays_require_exact_actor_reason_and_continue_to_new_arrivals(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    await make_animal(client, owner, "INCLUSION-ANCHOR")
    task_id = await _seed_herd_round_task(owner, "PPR round", initialize=False)
    await _start(client, owner, task_id)
    first = await make_animal(client, owner, "INCLUSION-FIRST")
    second = await make_animal(client, owner, "INCLUSION-SECOND")
    added = await _change(client, owner, task_id, "targets", [first["id"]], "Arrival reviewed")
    assert added.status_code == 200, added.text
    repeated = await _change(
        client, owner, task_id, "targets", [first["id"], second["id"]], "Arrival reviewed"
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["total_targets"] == 3
    changed_reason = await _change(
        client, owner, task_id, "targets", [first["id"]], "Different review"
    )
    assert changed_reason.status_code == 409, changed_reason.text
    assert changed_reason.json()["detail"] == "Animal is already a round target"
    second_actor = await limited_worker(
        client,
        owner,
        name="Inclusion vet",
        email="inclusion-vet@example.test",
        permissions=["health.view", "health.manage"],
    )
    await _assign_existing_role(task_id, "inclusion-vet@example.test")
    changed_actor = await _change(
        client, second_actor, task_id, "targets", [first["id"]], "Arrival reviewed"
    )
    assert changed_actor.status_code == 409, changed_actor.text
    assert changed_actor.json()["detail"] == "Animal is already a round target"


async def test_round_exclusion_replays_require_exact_actor_reason_and_continue_to_remaining_targets(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animals = [await make_animal(client, owner, f"EXCLUSION-{index}") for index in range(3)]
    task_id = await _seed_herd_round_task(owner, "PPR round", initialize=False)
    await _start(client, owner, task_id)
    first = await _change(client, owner, task_id, "exclusions", [animals[0]["id"]], "Vet deferred")
    assert first.status_code == 200, first.text
    assert first.json()["excluded_targets"] == 1 and first.json()["task_status"] == "PENDING"
    replay = await _change(client, owner, task_id, "exclusions", [animals[0]["id"]], "Vet deferred")
    assert replay.status_code == 200, replay.text
    assert replay.json()["excluded_targets"] == 1
    reason_conflict = await _change(
        client, owner, task_id, "exclusions", [animals[0]["id"]], "Different vet decision"
    )
    assert reason_conflict.status_code == 409, reason_conflict.text
    assert reason_conflict.json()["detail"] == "Round exclusion is already recorded"
    second_actor = await limited_worker(
        client,
        owner,
        name="Exclusion vet",
        email="exclusion-vet@example.test",
        permissions=["health.view", "health.manage"],
    )
    await _assign_existing_role(task_id, "exclusion-vet@example.test")
    actor_conflict = await _change(
        client, second_actor, task_id, "exclusions", [animals[0]["id"]], "Vet deferred"
    )
    assert actor_conflict.status_code == 409, actor_conflict.text
    assert actor_conflict.json()["detail"] == "Round exclusion is already recorded"
    continued = await _change(
        client, owner, task_id, "exclusions", [animals[0]["id"], animals[1]["id"]], "Vet deferred"
    )
    assert continued.status_code == 200, continued.text
    assert (
        continued.json()["excluded_targets"] == 2 and continued.json()["task_status"] == "PENDING"
    )
    finished = await _change(
        client, owner, task_id, "exclusions", [animals[2]["id"]], "Vet deferred"
    )
    assert finished.status_code == 200, finished.text
    assert finished.json()["excluded_targets"] == 3 and finished.json()["task_status"] == "DONE"
