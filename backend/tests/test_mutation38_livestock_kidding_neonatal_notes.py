"""Persist neonatal mortality narrative only on a genuinely died live-born kid."""

from datetime import timedelta

import httpx
import pytest

from app.db import get_sessionmaker
from app.models import Animal
from app.utils import today

from .conftest import owner_with_farm
from .test_kidding_husbandry import confirmed_pregnancy, post_kidding


@pytest.mark.parametrize("status", ["ALIVE", "DIED"])
async def test_public_birth_keeps_neonatal_mortality_notes_coherent_with_child_status(
    client: httpx.AsyncClient, status: str
) -> None:
    owner = await owner_with_farm(client)
    doe, buck, breeding = await confirmed_pregnancy(
        client, owner, tag="NEONATAL-NARRATIVE", bred_days_ago=150, kid_count=1
    )
    reference_date = today()
    birth_date = reference_date - timedelta(days=2)
    response = await post_kidding(
        client,
        owner,
        int(breeding["id"]),
        birth_date.isoformat(),
        kids=[
            {
                "sex": "F",
                "status": status,
                "birth_weight": 2.6,
                "mortality_reported_at": reference_date.isoformat() if status == "DIED" else None,
            }
        ],
    )
    assert response.status_code == 201, response.text
    entry = response.json()["kids"][0]
    assert entry["status"] == status and entry["animal_id"] is not None
    async with get_sessionmaker()() as db:
        child = await db.get(Animal, entry["animal_id"])
        assert child is not None
        assert child.status == ("DEAD" if status == "DIED" else "ACTIVE")
        assert child.status_notes == ("Neonatal mortality recorded" if status == "DIED" else None)
        assert child.status_date == (reference_date if status == "DIED" else None)
        assert child.mortality_reported_at == (reference_date if status == "DIED" else None)
        assert child.date_of_birth == birth_date
        assert child.dam_id == doe["id"] and child.sire_id == buck["id"]
