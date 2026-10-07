"""Move narratives fit their full stored limit and reject the next character."""

from datetime import timedelta

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Animal, BucketMove
from app.utils import today

from .conftest import owner_with_farm
from .test_animals_extended import get_profile, iso, make_animal


async def test_move_reason_accepts_and_preserves_all_255_characters(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(
        client,
        owner,
        tag="MOVE-REASON-MAX",
        date_of_birth=iso(today() - timedelta(days=500)),
        weight_kg=26,
        weight_date=iso(today()),
    )
    reason = "r" * 255
    response = await client.post(
        f"/api/animals/{animal['id']}/move",
        json={"to_bucket": "BREEDING", "reason": reason},
        headers=owner,
    )
    assert response.status_code == 200, response.text
    assert response.json()["current_bucket"] == "BREEDING"
    profile = await get_profile(client, owner, animal["id"])
    assert len(profile["moves"]) == 2
    assert profile["moves"][0]["reason"] == reason
    async with get_sessionmaker()() as db:
        move = (
            await db.execute(
                select(BucketMove).where(
                    BucketMove.animal_id == animal["id"], BucketMove.to_bucket == "BREEDING"
                )
            )
        ).scalar_one()
        assert move.reason == reason


async def test_move_reason_rejects_256_characters_before_changing_history(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(
        client,
        owner,
        tag="MOVE-REASON-OVER",
        date_of_birth=iso(today() - timedelta(days=500)),
        weight_kg=26,
        weight_date=iso(today()),
    )
    response = await client.post(
        f"/api/animals/{animal['id']}/move",
        json={"to_bucket": "BREEDING", "reason": "r" * 256},
        headers=owner,
    )
    assert response.status_code == 422, response.text
    assert any(
        error["loc"] == ["body", "reason"] and error["type"] == "too_long"
        for error in response.json()["detail"]
    ), response.text
    async with get_sessionmaker()() as db:
        persisted = await db.get(Animal, animal["id"])
        assert persisted is not None and persisted.current_bucket == "FOUNDATION"
        moves = (
            (await db.execute(select(BucketMove).where(BucketMove.animal_id == animal["id"])))
            .scalars()
            .all()
        )
        assert len(moves) == 1
        assert moves[0].from_bucket is None and moves[0].to_bucket == "FOUNDATION"
