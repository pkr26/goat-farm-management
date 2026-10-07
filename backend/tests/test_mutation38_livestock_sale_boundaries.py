"""Public sale admission, operator advice and derived ledger-cap boundaries."""

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Animal, Transaction
from app.utils import today

from .conftest import owner_with_farm
from .test_animals_status_husbandry import change_status, make_animal


def _calendar_months_before(value: date, months: int) -> date:
    year, zero_based_month = divmod(value.year * 12 + value.month - 1 - months, 12)
    month = zero_based_month + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


@pytest.mark.parametrize("age_months", [7, 8, 9])
async def test_public_male_sale_opens_at_eight_calendar_months_and_explains_the_literal_gate(
    client: httpx.AsyncClient, age_months: int
) -> None:
    owner = await owner_with_farm(client)
    observation_date = today()
    male = await make_animal(
        client,
        owner,
        f"SALE-AGE-{age_months}",
        sex="M",
        date_of_birth=_calendar_months_before(observation_date, age_months).isoformat(),
    )
    response = await change_status(
        client, owner, int(male["id"]), "SOLD", date=observation_date.isoformat(), sale_price=5000
    )
    assert response.status_code == (422 if age_months == 7 else 200), response.text
    if age_months == 7:
        assert response.json()["detail"] == (
            "SALE-AGE-7 is 7 months old — the meat-sale window opens at 8 months and "
            "24 kg (record a cull instead if the animal must leave the herd now)"
        )
    async with get_sessionmaker()() as db:
        stored = await db.get(Animal, male["id"])
        assert stored is not None
        assert stored.status == ("ACTIVE" if age_months == 7 else "SOLD")


@pytest.mark.parametrize("weight", [23.99, 24.0, 24.01])
async def test_public_male_sale_weight_advisory_is_strictly_below_twenty_four_kg(
    client: httpx.AsyncClient, weight: float
) -> None:
    owner = await owner_with_farm(client)
    male = await make_animal(
        client,
        owner,
        "SALE-WEIGHT-BOUNDARY",
        sex="M",
        date_of_birth=(today() - timedelta(days=300)).isoformat(),
    )
    response = await change_status(
        client, owner, int(male["id"]), "SOLD", sale_price=5000, sale_weight_kg=weight
    )
    assert response.status_code == 200, response.text
    async with get_sessionmaker()() as db:
        stored = await db.get(Animal, male["id"])
        assert stored is not None
        assert stored.status_notes == (
            "sold below the 24–28 kg market window" if weight < 24 else None
        )


async def test_public_sale_composes_operator_note_and_weight_advice_at_literal_255_characters(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    male = await make_animal(
        client,
        owner,
        "SALE-NOTE-BOUNDARY",
        sex="M",
        date_of_birth=(today() - timedelta(days=300)).isoformat(),
    )
    operator_note = "N" * 240
    response = await change_status(
        client,
        owner,
        int(male["id"]),
        "SOLD",
        sale_price=5000,
        sale_weight_kg=20.0,
        notes=operator_note,
    )
    assert response.status_code == 200, response.text
    expected = (operator_note + " — sold below the 24–28 kg market window")[:255]
    async with get_sessionmaker()() as db:
        stored = await db.get(Animal, male["id"])
        assert stored is not None and stored.status_notes == expected
        assert len(stored.status_notes) == 255


@pytest.mark.parametrize(
    ("rate", "expected_price", "accepted"),
    [
        (19999999.99, "999999999.50", True),
        (20000000.0, "1000000000.00", True),
        (20000000.01, "1000000000.50", False),
    ],
    ids=["half-rupee-below-cap", "on-cap", "half-rupee-above-cap"],
)
async def test_public_derived_sale_books_exact_paise_through_the_inclusive_billion_rupee_cap(
    client: httpx.AsyncClient, rate: float, expected_price: str, accepted: bool
) -> None:
    owner = await owner_with_farm(client)
    male = await make_animal(
        client,
        owner,
        "SALE-PRICE-BOUNDARY",
        sex="M",
        date_of_birth=(today() - timedelta(days=300)).isoformat(),
    )
    response = await change_status(
        client, owner, int(male["id"]), "SOLD", sale_weight_kg=50.0, sale_price_per_kg=rate
    )
    assert response.status_code == (200 if accepted else 422), response.text
    async with get_sessionmaker()() as db:
        stored = await db.get(Animal, male["id"])
        ledger = list(
            (
                await db.execute(
                    select(Transaction).where(
                        Transaction.farm_id == int(owner["X-Farm-Id"]),
                        Transaction.related_animal_id == male["id"],
                        Transaction.source_type == "ANIMAL_SALE",
                    )
                )
            ).scalars()
        )
        assert stored is not None
        if accepted:
            assert stored.status == "SOLD" and stored.sale_price == Decimal(expected_price)
            assert len(ledger) == 1 and ledger[0].amount == Decimal(expected_price)
            assert response.json()["sale_price"] == float(expected_price)
        else:
            assert "ledger cap" in response.json()["detail"]
            assert stored.status == "ACTIVE" and stored.sale_price is None
            assert not ledger
