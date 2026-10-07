"""Committed profile data remains readable while a husbandry write is pending."""

from collections.abc import AsyncGenerator

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db, get_sessionmaker
from app.main import create_app
from app.models.animals import Animal

from .conftest import owner_with_farm
from .test_animals_extended import make_animal


async def test_profile_reads_committed_history_without_waiting_for_a_pending_write(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="PROFILE-READ-DURING-WRITE")
    animal_id = animal["id"]

    async def bounded_reader() -> AsyncGenerator[AsyncSession]:
        async with get_sessionmaker()() as reader:
            # Make an unintended row-lock wait a bounded HTTP failure rather
            # than allowing this correctness test to stall the campaign.
            await reader.execute(text("SET LOCAL lock_timeout = '700ms'"))
            yield reader

    app = create_app()
    app.dependency_overrides[get_db] = bounded_reader
    async with get_sessionmaker()() as writer:
        await writer.execute(select(Animal.id).where(Animal.id == animal_id).with_for_update())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as reader:
            reader.cookies.update(client.cookies)
            response = await reader.get(f"/api/animals/{animal_id}", headers=owner)
        assert response.status_code == 200, response.text
        assert response.json()["animal"]["id"] == animal_id
        assert response.json()["animal"]["tag_number"] == "PROFILE-READ-DURING-WRITE"
        await writer.rollback()
