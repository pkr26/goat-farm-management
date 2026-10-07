"""Actual historical sale rows honor the declared recent exit-weight window."""

from datetime import timedelta
from decimal import Decimal

import httpx
import pytest

from app.db import get_sessionmaker
from app.models import Animal, Farm, WeightRecord
from app.simulation.assumptions import SimulationAssumptions
from app.simulation.defaults import get_preset
from app.simulation.market import BAKRID_DATES_BY_YEAR
from app.utils import today

from .conftest import owner_with_farm
from .type_helpers import json_object


async def test_actual_sales_accept_thirty_day_weight_and_exclude_older_and_post_exit_weights(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        reference = today(farm.timezone)
        for index in range(6):
            animal = Animal(
                farm_id=farm_id,
                tag_number=f"EXIT-W-{index}",
                sex="M",
                source="BORN",
                current_bucket="FOUNDATION",
                date_of_birth=reference - timedelta(days=900),
                status="SOLD",
                status_date=reference - timedelta(days=1),
                sale_price=Decimal("9000"),
            )
            db.add(animal)
            await db.flush()
            db.add_all(
                [
                    WeightRecord(
                        farm_id=farm_id,
                        animal_id=animal.id,
                        date=reference
                        - timedelta(days=1)
                        - timedelta(days=30 if index < 5 else 31),
                        weight_kg=30.0 if index < 5 else 10.0,
                    ),
                    WeightRecord(
                        farm_id=farm_id,
                        animal_id=animal.id,
                        date=reference,
                        weight_kg=90.0,
                    ),
                ]
            )
        await db.commit()
    response = await client.get("/api/simulation/calibration", headers=owner)
    assert response.status_code == 200, response.text
    body = json_object(response.json())
    assumptions = SimulationAssumptions.model_validate(body["assumptions"])
    evidence = {row["path"]: row for row in body["evidence"]}
    preset = get_preset("osmanabadi")
    expected_price = 9000.0 / 30.0
    sold_on = reference - timedelta(days=1)
    if any(month == sold_on.month for month, _ in BAKRID_DATES_BY_YEAR.get(sold_on.year, ())):
        expected_price /= 1.0 + preset.sales.eid_price_uplift
    assert assumptions.sales.meat_price_per_kg == pytest.approx(expected_price)
    assert "sales.meat_price_per_kg" in evidence, (
        "The five valid exit observations must retain their required sale evidence"
    )
    sale_evidence = evidence["sales.meat_price_per_kg"]
    assert sale_evidence["calibrated_value"] == pytest.approx(expected_price)
    assert sale_evidence["sample_size"] == 5
    assert sale_evidence["confidence"] == "low"
    assert any("5 sale-price observations used" in warning for warning in body["warnings"])
    assert any("1 sale-price observations were excluded" in warning for warning in body["warnings"])
