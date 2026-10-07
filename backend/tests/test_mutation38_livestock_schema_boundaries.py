"""Animal registration and weighing preserve supported wire boundaries."""

import httpx
import pytest
from sqlalchemy import func, select

from app.db import get_sessionmaker
from app.models import Animal, IdempotencyRecord, WeightRecord

from .conftest import owner_with_farm


def _historical_payload(tag: str, reason: str) -> dict[str, object]:
    return {
        "tag_number": tag,
        "sex": "F",
        "source": "BORN",
        "current_bucket": "FEMALE_KIDS",
        "historical_import_reason": reason,
    }


@pytest.mark.parametrize("tag", ["T", "🐐"])
async def test_historical_animal_accepts_a_single_character_tag(
    client: httpx.AsyncClient, tag: str
) -> None:
    owner = await owner_with_farm(client)
    response = await client.post(
        "/api/animals", json=_historical_payload(tag, "Paper herd register"), headers=owner
    )
    assert response.status_code == 201, response.text
    assert response.json()["tag_number"] == tag
    profile = await client.get(f"/api/animals/{response.json()['id']}", headers=owner)
    assert profile.status_code == 200, profile.text
    assert profile.json()["animal"]["tag_number"] == tag


@pytest.mark.parametrize("reason_length", [1, 255])
async def test_historical_import_reason_accepts_and_audits_its_exact_bounds(
    client: httpx.AsyncClient, reason_length: int
) -> None:
    owner = await owner_with_farm(client)
    reason = "R" * reason_length
    response = await client.post(
        "/api/animals", json=_historical_payload("REASON-BOUNDARY", reason), headers=owner
    )
    assert response.status_code == 201, response.text
    profile = await client.get(f"/api/animals/{response.json()['id']}", headers=owner)
    assert profile.status_code == 200, profile.text
    moves = profile.json()["moves"]
    assert len(moves) == 1
    assert moves[0]["reason"].startswith("Historical import: " + reason[:1])


async def test_too_long_historical_import_reason_is_rejected_before_creating_an_animal(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    response = await client.post(
        "/api/animals", json=_historical_payload("REASON-OVER", "R" * 256), headers=owner
    )
    assert response.status_code == 422, response.text
    assert any(
        error["loc"] == ["body", "historical_import_reason"] and error["type"] == "too_long"
        for error in response.json()["detail"]
    ), response.text
    async with get_sessionmaker()() as db:
        assert (
            await db.execute(
                select(func.count(Animal.id)).where(Animal.farm_id == int(owner["X-Farm-Id"]))
            )
        ).scalar_one() == 0


async def test_empty_historical_reason_reports_the_offending_field(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    response = await client.post(
        "/api/animals", json=_historical_payload("REASON-EMPTY", ""), headers=owner
    )
    assert response.status_code == 422, response.text
    # The client maps body-field locations onto the corresponding form input.
    # A model-root issue would leave this reason field without its inline error.
    assert any(
        error["loc"] == ["body", "historical_import_reason"] for error in response.json()["detail"]
    ), response.text
    async with get_sessionmaker()() as db:
        assert (
            await db.execute(
                select(func.count(Animal.id)).where(Animal.farm_id == int(owner["X-Farm-Id"]))
            )
        ).scalar_one() == 0


async def test_weight_notes_accept_the_full_255_character_boundary(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await client.post(
        "/api/animals",
        json=_historical_payload("NOTE-MAX", "Paper herd register"),
        headers=owner,
    )
    assert animal.status_code == 201, animal.text
    notes = "n" * 255
    response = await client.post(
        f"/api/animals/{animal.json()['id']}/weight",
        json={"weight_kg": 2.8, "notes": notes},
        headers=owner,
    )
    assert response.status_code == 201, response.text
    assert response.json()["notes"] == notes
    async with get_sessionmaker()() as db:
        persisted = (await db.execute(select(WeightRecord))).scalar_one()
        assert persisted.notes == notes


async def test_weight_notes_reject_256_characters_before_the_database_write(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await client.post(
        "/api/animals",
        json=_historical_payload("NOTE-OVER", "Paper herd register"),
        headers=owner,
    )
    assert animal.status_code == 201, animal.text
    response = await client.post(
        f"/api/animals/{animal.json()['id']}/weight",
        json={"weight_kg": 2.8, "notes": "n" * 256},
        headers=owner,
    )
    assert response.status_code == 422, response.text
    assert any(
        error["loc"] == ["body", "notes"] and error["type"] == "too_long"
        for error in response.json()["detail"]
    ), response.text
    async with get_sessionmaker()() as db:
        assert (await db.execute(select(func.count(WeightRecord.id)))).scalar_one() == 0


async def test_retained_older_animal_response_replays_missing_optional_facts_conservatively(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    headers = owner | {"Idempotency-Key": "retained-animal-response"}
    payload = _historical_payload("RETAINED-KID", "Paper herd register")
    original = await client.post("/api/animals", json=payload, headers=headers)
    assert original.status_code == 201, original.text
    assert original.json()["is_breeding_ready"] is False
    assert original.json()["is_currently_pregnant"] is False
    assert original.json()["days_in_current_bucket"] == 0

    # The replay contract accepts retained pre-deploy bodies that still fit
    # the current schema. Restore this actual committed response in the older
    # shape: all required facts remain, while only optional computed facts
    # are absent. The committed claim's scope, hash, status and dates stay valid.
    async with get_sessionmaker()() as db:
        record = (
            await db.execute(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.farm_id == farm_id,
                    IdempotencyRecord.operation == "POST /api/animals",
                )
            )
        ).scalar_one()
        assert record.completed_at is not None
        assert record.response_status == 201
        assert record.response_body is not None
        retained = dict(record.response_body)
        assert retained.pop("is_breeding_ready") is False
        assert retained.pop("is_currently_pregnant") is False
        assert retained.pop("days_in_current_bucket") == 0
        record.response_body = retained
        await db.commit()

    replay = await client.post("/api/animals", json=payload, headers=headers)
    assert replay.status_code == 201, replay.text
    assert replay.headers.get("Idempotency-Replayed") == "true"
    assert replay.json()["id"] == original.json()["id"]
    assert replay.json()["is_breeding_ready"] is False
    assert replay.json()["is_currently_pregnant"] is False
    assert replay.json()["days_in_current_bucket"] == 0
    async with get_sessionmaker()() as db:
        assert (
            await db.execute(select(func.count(Animal.id)).where(Animal.farm_id == farm_id))
        ).scalar_one() == 1
