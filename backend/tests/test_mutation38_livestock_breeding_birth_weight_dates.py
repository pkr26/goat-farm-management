"""The SQL weight fact includes a recorded birth mass on its birth date."""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import get_sessionmaker
from app.models import Animal
from app.services.breeding import breeding_weights_as_of
from app.utils import today

from .conftest import owner_with_farm


@pytest.mark.parametrize(
    ("offset", "expected"),
    [(-1, None), (0, 2.6), (1, 2.6)],
    ids=["before-birth", "on-birth", "after-birth"],
)
async def test_native_sql_weight_fact_preserves_literal_birth_date_availability(
    client: httpx.AsyncClient, offset: int, expected: float | None
) -> None:
    owner = await owner_with_farm(client)
    birth_date = today() - timedelta(days=90)
    created = await client.post(
        "/api/animals",
        headers=owner,
        json={
            "tag_number": "RECORDED-BIRTH-MASS",
            "sex": "F",
            "source": "BORN",
            "current_bucket": "FEMALE_KIDS",
            "date_of_birth": birth_date.isoformat(),
            "birth_weight": 2.6,
            "historical_import_reason": "Legacy birth mass without a separate weighing log",
        },
    )
    assert created.status_code == 201, created.text
    animal_id = int(created.json()["id"])
    reference_date = birth_date + timedelta(days=offset)
    async with get_sessionmaker()() as db:
        animal = (
            await db.execute(
                select(Animal)
                .where(Animal.id == animal_id)
                .options(selectinload(Animal.weight_records))
            )
        ).scalar_one()
        assert animal.birth_weight == 2.6
        assert animal.effective_dob == birth_date
        assert not animal.weight_records
        assert animal.latest_weight_kg_on(reference_date) == expected
        try:
            sql_facts = await breeding_weights_as_of(db, [animal_id], reference_date)
        except Exception as error:
            pytest.fail(f"A valid dated birth-weight fact must remain readable: {error!r}")
    # This is the native scalar-fact interface. A newborn's2.6kg naturally
    # fails adult breeding thresholds, which mask this date fence in pickers.
    assert sql_facts == {animal_id: expected}
