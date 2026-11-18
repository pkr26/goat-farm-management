"""Public SQL-backed readiness includes the stated age and measured-weight floors."""

from datetime import date
from decimal import Decimal
from typing import Literal

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Farm, WeightRecord
from app.models.species import GOAT_PROFILE
from app.utils import add_months, today

from .conftest import owner_with_farm


async def _registered_and_weighed_doe(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    *,
    tag: str,
    birth_date: date,
    weighing_date: date,
    weight: float,
) -> int:
    registered = await client.post(
        "/api/animals",
        headers=owner,
        json={
            "tag_number": tag,
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "date_of_birth": birth_date.isoformat(),
            "purchase_date": weighing_date.isoformat(),
            "historical_import_reason": "Existing herd intake with a documented birth date",
        },
    )
    assert registered.status_code == 201, registered.text
    animal_id = int(registered.json()["id"])
    weighed = await client.post(
        f"/api/animals/{animal_id}/weight",
        headers=owner,
        json={"date": weighing_date.isoformat(), "weight_kg": weight},
    )
    assert weighed.status_code == 201, weighed.text
    async with get_sessionmaker()() as db:
        record = (
            await db.execute(select(WeightRecord).where(WeightRecord.animal_id == animal_id))
        ).scalar_one()
        assert record.date == weighing_date
        assert record.weight_kg == weight
    return animal_id


@pytest.mark.parametrize(
    ("boundary", "at_floor"),
    [("age", True), ("age", False), ("weight", True), ("weight", False)],
    ids=["age-at-floor", "age-below-floor", "weight-at-floor", "weight-below-floor"],
)
async def test_public_readiness_includes_the_floor_and_excludes_the_unit_below(
    client: httpx.AsyncClient, boundary: Literal["age", "weight"], at_floor: bool
) -> None:
    owner = await owner_with_farm(client, email=f"readiness-{boundary}@farm.in")
    permissions = await client.get("/api/auth/permissions", headers=owner)
    assert permissions.status_code == 200, permissions.text
    assert "breeding.view" in permissions.json()["permissions"]
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        assert farm is not None
        reference = today(farm.timezone)
    minimum_age = GOAT_PROFILE.min_breeding_age_months
    minimum_weight = GOAT_PROFILE.min_breeding_weight_kg
    if boundary == "age":
        # The API exposes age in whole calendar months. One whole-month unit
        # below avoids making the negative depend on a midnight birthday.
        age = minimum_age if at_floor else minimum_age - 1
        weight = minimum_weight + 1.0
    else:
        # The scale records hundredths of a kilogram; use its smallest stored
        # step rather than a rounded or biologically impossible weight.
        below_weight = float(Decimal(str(minimum_weight)) - Decimal("0.01"))
        age = minimum_age + 1
        weight = minimum_weight if at_floor else below_weight
    animal_id = await _registered_and_weighed_doe(
        client,
        owner,
        tag=f"READY-{boundary}",
        birth_date=add_months(reference, -age),
        weighing_date=reference,
        weight=weight,
    )
    profile = await client.get(f"/api/animals/{animal_id}", headers=owner)
    assert profile.status_code == 200, profile.text
    animal = profile.json()["animal"]
    assert animal["sex"] == "F"
    assert animal["status"] == "ACTIVE"
    assert animal["current_bucket"] == "FOUNDATION"
    assert animal["movement_restricted"] is False
    assert animal["suspected_scheduled_disease"] is False
    assert animal["is_currently_pregnant"] is False
    assert animal["age_months"] == age
    assert animal["latest_weight_kg"] == weight
    assert animal["is_breeding_ready"] is at_floor

    listed = await client.get("/api/animals", headers=owner)
    assert listed.status_code == 200, listed.text
    rows = {row["id"]: row for row in listed.json()["animals"]}
    assert set(rows) == {animal_id}
    assert rows[animal_id]["age_months"] == age
    assert rows[animal_id]["latest_weight_kg"] == weight
    assert rows[animal_id]["is_breeding_ready"] is at_floor
