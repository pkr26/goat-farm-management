"""Weaning moves only the duty's litter and checks only animals actually moving."""

from datetime import timedelta

import httpx

from app.db import get_sessionmaker
from app.models import Animal, BucketMove, Task
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import (
    all_tasks,
    backdate_latest_bucket_move,
    bred_doe,
    confirm,
    get_animal,
    kid_on_ekd,
    list_animals,
    make_breeding,
    move_to,
    pregnant_doe,
)


async def _old_and_current_litters(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> tuple[int, int, int, list[int]]:
    doe, buck, first = await bred_doe(
        client, owner, "DEPENDENCY-DAM", today() - timedelta(days=340)
    )
    first = await confirm(client, owner, first["id"], kid_count=1)
    first_birth = await kid_on_ekd(
        client, owner, first, kids=[{"tag": "OVERDUE-LITTER-KID", "sex": "M"}]
    )
    await move_to(client, owner, doe["id"], "RESTING", history_override=True)
    await backdate_latest_bucket_move(doe["id"], today() - timedelta(days=186))
    second = await make_breeding(
        client,
        owner,
        doe["id"],
        buck["id"],
        breeding_date=(today() - timedelta(days=176)).isoformat(),
    )
    second = await confirm(client, owner, second["id"], kid_count=2)
    second_birth = await kid_on_ekd(
        client,
        owner,
        second,
        kids=[
            {"tag": "CURRENT-DEPENDENT-ONE", "sex": "F"},
            {"tag": "CURRENT-DEPENDENT-TWO", "sex": "M"},
        ],
    )
    duty = next(
        task
        for task in await all_tasks(client, owner)
        if task["category"] == "WEANING" and task["breeding_record_id"] == first["id"]
    )
    return (
        int(doe["id"]),
        int(duty["id"]),
        int(first_birth["kids"][0]["animal_id"]),
        [int(kid["animal_id"]) for kid in second_birth["kids"]],
    )


async def _place_real_disease_hold(
    client: httpx.AsyncClient, owner: dict[str, str], animal_id: int
) -> None:
    response = await client.post(
        "/api/health/events",
        headers=owner,
        json={
            "animal_id": animal_id,
            "type": "TREATMENT",
            "disease_target": "FMD",
            "suspected_scheduled_disease": True,
        },
    )
    assert response.status_code == 201, response.text
    held = await get_animal(client, owner, animal_id)
    assert held["movement_restricted"] is True


async def test_last_litters_weaning_cannot_complete_while_its_moving_dam_is_held(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe, _, breeding = await pregnant_doe(client, owner, "MOVING-HELD-DAM", gestation_days=220)
    birth = await kid_on_ekd(client, owner, breeding)
    duty = next(
        task
        for task in await all_tasks(client, owner)
        if task["category"] == "WEANING" and task["breeding_record_id"] == breeding["id"]
    )
    await _place_real_disease_hold(client, owner, int(doe["id"]))
    response = await client.post(f"/api/tasks/{duty['id']}/complete", headers=owner)
    assert response.status_code == 409, response.text
    async with get_sessionmaker()() as db:
        actual = await db.get(Task, int(duty["id"]))
        assert actual is not None and actual.status == "PENDING"
    for animal_id in [doe["id"], *[kid["animal_id"] for kid in birth["kids"]]]:
        assert (await get_animal(client, owner, int(animal_id)))["current_bucket"] == "RECOVERY"


async def test_older_litter_can_wean_while_two_newer_dependents_keep_the_held_dam_in_recovery(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe_id, duty_id, older_id, newer_ids = await _old_and_current_litters(client, owner)
    await _place_real_disease_hold(client, owner, doe_id)
    response = await client.post(f"/api/tasks/{duty_id}/complete", headers=owner)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "DONE"
    assert (await get_animal(client, owner, older_id))["current_bucket"] == "MALE_KIDS"
    assert (await get_animal(client, owner, doe_id))["current_bucket"] == "RECOVERY"
    for newer_id in newer_ids:
        assert (await get_animal(client, owner, newer_id))["current_bucket"] == "RECOVERY"
    held = await get_animal(client, owner, doe_id)
    assert held["movement_restricted"] is True


async def test_retained_adult_daughters_actual_weaning_exit_proves_she_is_no_longer_dependent(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe, _, breeding = await pregnant_doe(client, owner, "RETAINED-ADULT-DAM", gestation_days=220)
    birth = await kid_on_ekd(client, owner, breeding, kids=[{"sex": "M", "status": "ALIVE"}])
    duty = next(
        task
        for task in await all_tasks(client, owner)
        if task["category"] == "WEANING" and task["breeding_record_id"] == breeding["id"]
    )
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        # Initially retained sparse historical graph. Its actual earlier
        # weaning exit is preserved; no contemporary movement is deleted or
        # relabelled. A retained dam_id alone does not make an adult dependent.
        adult = Animal(
            farm_id=farm_id,
            tag_number="RETAINED-INDEPENDENT-ADULT",
            sex="F",
            date_of_birth=today() - timedelta(days=400),
            birth_type="SINGLE",
            source="BORN",
            dam_id=int(doe["id"]),
            current_bucket="RECOVERY",
        )
        db.add(adult)
        await db.flush()
        db.add(
            BucketMove(
                farm_id=farm_id,
                animal_id=adult.id,
                from_bucket="RECOVERY",
                to_bucket="FEMALE_KIDS",
                reason="Weaned (day 60)",
                effective_date=today() - timedelta(days=340),
            )
        )
        await db.commit()
        adult_id = adult.id
    response = await client.post(f"/api/tasks/{duty['id']}/complete", headers=owner)
    assert response.status_code == 200, response.text
    assert (await get_animal(client, owner, int(doe["id"])))["current_bucket"] == "RESTING"
    assert (await get_animal(client, owner, int(birth["kids"][0]["animal_id"])))[
        "current_bucket"
    ] == "MALE_KIDS"
    assert (await get_animal(client, owner, adult_id))["current_bucket"] == "RECOVERY"
    register = await list_animals(client, owner)
    assert any(animal["id"] == adult_id and animal["dam_id"] == doe["id"] for animal in register)
