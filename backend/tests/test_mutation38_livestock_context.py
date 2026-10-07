"""Pregnancy classification is a result of the ultrasound workflow."""

from datetime import date

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Animal, BucketMove
from app.services.animals import require_bucket_transition

from .conftest import owner_with_farm
from .test_breeding_extended import make_doe


def test_pregnancy_classification_requires_ultrasound_context() -> None:
    doe = Animal(
        tag_number="ULTRASOUND-CONTEXT",
        sex="F",
        status="ACTIVE",
        current_bucket="BREEDING",
        date_of_birth=date(2024, 1, 1),
        movement_restricted=False,
        suspected_scheduled_disease=False,
    )
    with pytest.raises(ValueError, match="Illegal lifecycle transition"):
        require_bucket_transition(
            doe,
            "PREGNANCY_EARLY",
            context="manual",
            reference_date=date(2026, 10, 5),
            facts=(26.0, False),
        )
    require_bucket_transition(
        doe,
        "PREGNANCY_EARLY",
        context="ultrasound",
        reference_date=date(2026, 10, 5),
        facts=(26.0, True),
    )


async def test_manual_move_cannot_fabricate_pregnancy_classification(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers, "MANUAL-PREGNANCY", bucket="BREEDING")
    response = await client.post(
        f"/api/animals/{doe['id']}/move",
        headers=headers,
        json={"to_bucket": "PREGNANCY_EARLY", "reason": "Manual classification"},
    )
    assert response.status_code == 409, response.text
    async with get_sessionmaker()() as db:
        animal = await db.get(Animal, doe["id"])
        assert animal is not None
        assert animal.current_bucket == "BREEDING"
        fabricated_moves = (
            (
                await db.execute(
                    select(BucketMove).where(
                        BucketMove.animal_id == doe["id"],
                        BucketMove.to_bucket == "PREGNANCY_EARLY",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert fabricated_moves == []
