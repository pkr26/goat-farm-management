"""Real birth mass is available only from birth in the dated SQL-facts interface.

Current HTTP views use the farm's current date. Historical assertions below
exercise the helper's explicit reference-date parameter, not an HTTP as-of API.
"""

from datetime import timedelta
from typing import Literal

import httpx
import pytest
from sqlalchemy import func, select

from app.api._shared import animal_computed_facts
from app.db import get_sessionmaker
from app.models import Animal, Farm, WeightRecord
from app.utils import today

from .conftest import owner_with_farm


@pytest.mark.parametrize(
    "birth_date_field",
    ["date_of_birth", "estimated_dob"],
    ids=["recorded-birth", "estimated-birth"],
)
async def test_sql_birthweight_fallback_respects_a_real_recorded_or_estimated_birth(
    client: httpx.AsyncClient,
    birth_date_field: Literal["date_of_birth", "estimated_dob"],
) -> None:
    owner = await owner_with_farm(client, email=f"birthmass-{birth_date_field}@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        timezone = farm.timezone
    birth_date = today(timezone) - timedelta(days=60)
    created = await client.post(
        "/api/animals",
        headers=owner,
        json={
            "tag_number": "DATED-BIRTH-MASS",
            "sex": "F",
            "source": "BORN",
            "current_bucket": "FEMALE_KIDS",
            birth_date_field: birth_date.isoformat(),
            "birth_weight": 2.6,
            "historical_import_reason": "Recorded birth mass without an entry weighing",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["latest_weight_kg"] == 2.6
    animal_id = int(created.json()["id"])

    async with get_sessionmaker()() as db:
        animal = await db.get(Animal, animal_id)
        assert animal is not None
        assert getattr(animal, birth_date_field) == birth_date
        other_date_field = (
            "estimated_dob" if birth_date_field == "date_of_birth" else "date_of_birth"
        )
        assert getattr(animal, other_date_field) is None
        assert animal.birth_weight == 2.6
        weighing_count = await db.scalar(
            select(func.count())
            .select_from(WeightRecord)
            .where(WeightRecord.animal_id == animal_id)
        )
        assert weighing_count == 0, "The fallback must be tested without a separate weighing record"

        for reference in (birth_date, birth_date + timedelta(days=1)):
            facts = await animal_computed_facts(db, [animal], reference, timezone)
            assert facts[animal_id].latest_weight_kg == 2.6, (
                "The genuine birth mass must be available on and after its recorded birth"
            )
        before_birth = await animal_computed_facts(
            db, [animal], birth_date - timedelta(days=1), timezone
        )
        assert before_birth[animal_id].latest_weight_kg is None, (
            "A historical reference before birth cannot contain the animal's birth mass"
        )
