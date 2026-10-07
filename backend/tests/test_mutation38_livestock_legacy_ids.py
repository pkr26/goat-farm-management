"""Reserved identity rejection also applies to constraint-valid restored rows."""

import httpx

from app.db import get_sessionmaker
from app.models import Animal

from .conftest import owner_with_farm


async def test_reserved_zero_animal_id_stays_not_found_for_a_valid_legacy_row(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    # Backups and legacy restores can retain an explicit zero INTEGER key:
    # PostgreSQL permits it, though current API creation allocates positive IDs.
    # All ordinary farm, lifecycle, source and status constraints still apply.
    async with get_sessionmaker()() as db:
        db.add(
            Animal(
                id=0,
                farm_id=int(headers["X-Farm-Id"]),
                tag_number="LEGACY-RESERVED-ZERO",
                sex="F",
                source="PURCHASED",
                current_bucket="FOUNDATION",
            )
        )
        await db.commit()
    # Confirm this is a persisted legacy row the register can serialize;
    # the profile path still promises not-found for reserved/non-positive IDs.
    register = await client.get("/api/animals", headers=headers)
    assert register.status_code == 200, register.text
    assert register.json()["total"] == 1
    assert register.json()["animals"][0]["id"] == 0
    profile = await client.get("/api/animals/0", headers=headers)
    assert profile.status_code == 404, profile.text
    assert profile.json()["detail"] == "Animal not found"
