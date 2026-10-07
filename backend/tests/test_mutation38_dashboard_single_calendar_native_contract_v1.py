"""One independently dated male distinguishes eight- and nine-month finish."""

from calendar import monthrange
from datetime import date

import httpx
from sqlalchemy import Date, cast, func, select

from app.db import get_sessionmaker
from app.models import Animal, Farm
from app.simulation.market import bakrid_occurrences

from .conftest import owner_with_farm


def shift_month(day: date, months: int) -> date:
    year, month = divmod(day.year * 12 + day.month - 1 + months, 12)
    month += 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


async def test_native_single_male_finishes_on_the_ninth_month_hold_window_edge(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="mutation-dashboard-single-calendar@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        day = (
            await db.execute(
                select(cast(func.timezone(Farm.timezone, func.now()), Date)).where(
                    Farm.id == farm_id
                )
            )
        ).scalar_one()
        assert isinstance(day, date)
        festival = next(
            entry.observed_on for entry in bakrid_occurrences() if entry.observed_on > day
        )
        window_start = shift_month(festival, -2)
        born = shift_month(window_start, -9)
        assert born <= day
        assert born > shift_month(day, -9)
        assert shift_month(born, 9) == window_start
        assert shift_month(born, 8) < window_start
        db.add(
            Animal(
                farm_id=farm_id,
                tag_number="SINGLE-NINTH-MONTH-HOLD",
                sex="M",
                source="BORN",
                date_of_birth=born,
                birth_type="SINGLE",
                birth_weight=2.5,
                current_bucket="MALE_KIDS",
            )
        )
        await db.commit()
    response = await client.get("/api/dashboard", headers=owner)
    assert response.status_code == 200, response.text
    assert response.json()["advisory"] == {
        "key": "bakrid_hold",
        "args": {"count": 1, "festival_date": festival.isoformat()},
    }
