"""Native candidate queries honor an explicit historical date and their current-date default."""

from datetime import date, timedelta

import httpx
import pytest

from app.db import get_sessionmaker
from app.models import Farm
from app.services.breeding import breeding_candidate_counts, breeding_candidate_page
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import make_animal


@pytest.mark.parametrize("query", ["page", "counts"])
async def test_native_candidate_queries_keep_historical_weight_separate_from_current_default(
    client: httpx.AsyncClient, query: str
) -> None:
    owner = await owner_with_farm(client)
    service_date = today()
    earlier = service_date - timedelta(days=60)
    doe = await make_animal(
        client,
        owner,
        "HISTORICAL-CANDIDATE",
        date_of_birth=date(2024, 1, 1).isoformat(),
        weight_kg=20.0,
        weight_date=date(2024, 1, 1).isoformat(),
    )
    weighing = await client.post(
        f"/api/animals/{doe['id']}/weight",
        headers=owner,
        json={"weight_kg": 26.0, "date": (service_date - timedelta(days=1)).isoformat()},
    )
    assert weighing.status_code == 201, weighing.text
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        assert farm is not None
        if query == "page":
            try:
                historical_rows, historical_total = await breeding_candidate_page(
                    db, farm, "doe", q=None, limit=10, offset=0, reference_date=earlier
                )
            except Exception as error:
                pytest.fail(f"valid historical candidate date must query successfully: {error!r}")
            assert historical_total == 0
            assert historical_rows == []
            try:
                current_rows, current_total = await breeding_candidate_page(
                    db, farm, "doe", q=None, limit=10, offset=0
                )
            except Exception as error:
                pytest.fail(f"native omitted date must use the farm's current date: {error!r}")
            assert current_total == 1
            assert [(animal.id, weight) for animal, weight in current_rows] == [(doe["id"], 26.0)]
        else:
            try:
                historical_counts = await breeding_candidate_counts(
                    db, farm, reference_date=earlier
                )
            except Exception as error:
                pytest.fail(f"valid historical count date must query successfully: {error!r}")
            assert historical_counts == (0, 0)
            try:
                current_counts = await breeding_candidate_counts(db, farm)
            except Exception as error:
                pytest.fail(
                    f"native omitted count date must use the farm's current date: {error!r}"
                )
            assert current_counts == (1, 0)
