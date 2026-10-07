"""Native random-tag allocation has a ten-candidate default collision budget."""

import secrets

import httpx
import pytest

from app.db import get_sessionmaker
from app.services import animals as animal_services

from .conftest import owner_with_farm


@pytest.mark.parametrize(
    "occupied_attempts", [9, 10], ids=["tenth-candidate-free", "ten-collisions"]
)
async def test_native_tag_allocator_accepts_tenth_candidate_and_stops_after_ten_collisions(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, occupied_attempts: int
) -> None:
    owner = await owner_with_farm(client)
    response = await client.post(
        "/api/animals",
        headers=owner,
        json={
            "tag_number": "G-22222",
            "sex": "F",
            "source": "BORN",
            "current_bucket": "FOUNDATION",
            "historical_import_reason": "Existing herd migration",
        },
    )
    assert response.status_code == 201, response.text
    # Control only entropy. Every collision still reaches the real PostgreSQL
    # namespace probe against this ordinary API-created animal.
    characters = iter("2" * (occupied_attempts * 5) + "33333")

    def candidate_character(alphabet: str) -> str:
        character = next(characters)
        assert character in alphabet
        return character

    monkeypatch.setattr(secrets, "choice", candidate_character)
    async with get_sessionmaker()() as db:
        if occupied_attempts == 10:
            with pytest.raises(RuntimeError, match="could not generate a unique tag"):
                await animal_services.generate_unique_tag(db, int(owner["X-Farm-Id"]))
        else:
            try:
                tag = await animal_services.generate_unique_tag(db, int(owner["X-Farm-Id"]))
            except Exception as error:
                pytest.fail(f"the tenth unused native candidate must succeed: {error!r}")
            assert tag == "G-33333"
