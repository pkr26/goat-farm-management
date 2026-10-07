"""Response schemas accept committed ORM rows and match their public wire facts."""

from typing import Literal

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import get_sessionmaker
from app.models import Animal, BucketMove
from app.schemas.animals import AnimalOut, BucketMoveOut

from .conftest import owner_with_farm


@pytest.mark.parametrize("schema_kind", ["animal", "bucket_move"])
async def test_output_schema_accepts_real_rows_and_matches_the_public_profile(
    client: httpx.AsyncClient, schema_kind: Literal["animal", "bucket_move"]
) -> None:
    owner = await owner_with_farm(client)
    creation = await client.post(
        "/api/animals",
        json={
            "tag_number": "ATTRIBUTE-CONTRACT",
            "sex": "F",
            "source": "BORN",
            "current_bucket": "FEMALE_KIDS",
            "historical_import_reason": "Paper herd register",
        },
        headers=owner,
    )
    assert creation.status_code == 201, creation.text
    animal_id = creation.json()["id"]
    profile = await client.get(f"/api/animals/{animal_id}", headers=owner)
    assert profile.status_code == 200, profile.text
    converted: AnimalOut | BucketMoveOut
    async with get_sessionmaker()() as db:
        if schema_kind == "animal":
            animal = (
                await db.execute(
                    select(Animal)
                    .where(Animal.id == animal_id)
                    .options(
                        selectinload(Animal.weight_records),
                        selectinload(Animal.breedings_as_doe),
                        selectinload(Animal.bucket_moves),
                    )
                )
            ).scalar_one()
            try:
                converted = AnimalOut.model_validate(animal)
            except ValidationError as error:
                pytest.fail(f"AnimalOut must accept a fully loaded committed animal: {error}")
            expected = profile.json()["animal"]
        else:
            move = (
                await db.execute(select(BucketMove).where(BucketMove.animal_id == animal_id))
            ).scalar_one()
            try:
                converted = BucketMoveOut.model_validate(move)
            except ValidationError as error:
                pytest.fail(f"BucketMoveOut must accept a committed bucket move: {error}")
            assert len(profile.json()["moves"]) == 1
            expected = profile.json()["moves"][0]
        assert converted.model_dump(mode="json") == expected
