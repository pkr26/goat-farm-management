"""SQL evidence oracle isolated from duplicate-key side effects."""

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import get_sessionmaker

from .conftest import owner_with_farm
from .test_breeding_extended import make_animal
from .test_health_safety import _seed_herd_round_task


@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
async def test_one_round_coverage_row_cannot_be_changed_or_deleted(
    client: httpx.AsyncClient, operation: str
) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, "ROUND-IMMUTABLE")
    task_id = await _seed_herd_round_task(headers, "ET + HS pre-monsoon round (2026) — all animals")
    recorded = await client.post(
        "/api/health/events",
        json={
            "scope": "bucket",
            "bucket": "FOUNDATION",
            "expected_animal_ids": [animal["id"]],
            "type": "VACCINE",
            "disease_target": "ET",
            "schedule_template_name": "Enterotoxaemia (ET)",
            "task_id": task_id,
        },
        headers=headers,
    )
    assert recorded.status_code == 201, recorded.text
    # One target, one component: no duplicate primary key can mask the
    # append-only trigger when this row's recorded disease is changed.
    statement = (
        "UPDATE health_round_coverage SET component='PPR' "
        if operation == "UPDATE"
        else "DELETE FROM health_round_coverage "
    )
    async with get_sessionmaker()() as db:
        with pytest.raises(DBAPIError, match="Health-round evidence is append-only"):
            await db.execute(
                text(
                    statement + "WHERE task_id=:task_id AND animal_id=:animal_id "
                    "AND component='Enterotoxaemia (ET)'"
                ),
                {"task_id": task_id, "animal_id": animal["id"]},
            )
        await db.rollback()
    progress = await client.get(f"/api/health/rounds/{task_id}", headers=headers)
    assert progress.status_code == 200, progress.text
    body = progress.json()
    assert body["task_status"] == "PENDING"
    assert body["remaining_units"] == 1
    assert body["targets"][0]["covered_components"] == ["Enterotoxaemia (ET)"]


@pytest.mark.parametrize("operation", ["REPLACE_MEMBER", "DELETE_MEMBER", "CHANGE_PROGRAMME"])
async def test_declared_round_cohort_and_programme_cannot_be_rewritten_by_raw_sql(
    client: httpx.AsyncClient, operation: str
) -> None:
    headers = await owner_with_farm(client)
    original = await make_animal(client, headers, "COHORT-DECLARED")
    task_id = await _seed_herd_round_task(headers, "ET + HS pre-monsoon round (2026) — all animals")
    arrival = await make_animal(client, headers, "COHORT-NEW-ARRIVAL")
    # No treatment or exclusion references exist yet. Replacing/deleting
    # exactly this one member cannot be rejected by a dependent-row FK or
    # duplicate key, so the declaration's own immutability must enforce it.
    if operation == "REPLACE_MEMBER":
        statement = (
            "UPDATE health_round_targets SET animal_id=:arrival_id "
            "WHERE task_id=:task_id AND animal_id=:animal_id"
        )
    elif operation == "DELETE_MEMBER":
        statement = (
            "DELETE FROM health_round_targets WHERE task_id=:task_id AND animal_id=:animal_id"
        )
    else:
        statement = (
            "UPDATE health_rounds SET required_components='[\"PPR\"]'::jsonb WHERE task_id=:task_id"
        )
    async with get_sessionmaker()() as db:
        with pytest.raises(DBAPIError, match="Health-round evidence is append-only"):
            await db.execute(
                text(statement),
                {"task_id": task_id, "animal_id": original["id"], "arrival_id": arrival["id"]},
            )
        await db.rollback()
    progress = await client.get(f"/api/health/rounds/{task_id}", headers=headers)
    assert progress.status_code == 200, progress.text
    body = progress.json()
    assert body["total_targets"] == 1
    assert [target["animal_id"] for target in body["targets"]] == [original["id"]]
    assert body["available_additions"] == 1
    assert body["required_components"] == [
        "Enterotoxaemia (ET)",
        "Haemorrhagic Septicaemia (HS)",
    ]
    assert body["remaining_units"] == 2
