"""The animal identity guard accepts both endpoints of PostgreSQL INTEGER."""

import httpx
import pytest

from app.db import get_sessionmaker
from app.models import Animal

from .conftest import owner_with_farm


@pytest.mark.parametrize("animal_id", [1, 2_147_483_647])
async def test_real_stored_animals_at_positive_int32_endpoints_remain_readable(
    client: httpx.AsyncClient, animal_id: int
) -> None:
    headers = await owner_with_farm(client)
    # These are legal positive INTEGER keys. Seed them explicitly because the
    # normal sequence does not reach its ceiling during an ordinary test run.
    async with get_sessionmaker()() as db:
        db.add(
            Animal(
                id=animal_id,
                farm_id=int(headers["X-Farm-Id"]),
                tag_number=f"VALID-ID-{animal_id}",
                sex="F",
                source="PURCHASED",
                current_bucket="FOUNDATION",
            )
        )
        await db.commit()
    profile = await client.get(f"/api/animals/{animal_id}", headers=headers)
    assert profile.status_code == 200, profile.text
    assert profile.json()["animal"]["id"] == animal_id
    assert profile.json()["animal"]["tag_number"] == f"VALID-ID-{animal_id}"
