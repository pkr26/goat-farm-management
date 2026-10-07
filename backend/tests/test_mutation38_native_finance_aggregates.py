"""Real constrained finance facts at exact calendar and provenance boundaries."""

from datetime import timedelta
from decimal import Decimal

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Animal, Farm, InsurancePremium, Transaction
from app.services.finance import lifetime_pnl, monthly_pnl
from app.utils import add_months, today, utcnow

from .conftest import owner_with_farm
from .test_finance_insurance import add_policy


async def test_native_monthly_summary_uses_exact_half_open_calendar_windows(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="native-pnl-calendar@farm.in")
    foreign = await owner_with_farm(client, email="native-pnl-foreign@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    foreign_id = int(foreign["X-Farm-Id"])
    current = today().replace(day=1)
    first = add_months(current, -1)
    after = add_months(current, 1)
    policy_response = await add_policy(
        client,
        owner,
        policy_number="NATIVE-CALENDAR-PAYMENTS",
        premium=0.0,
        start_date=(first - timedelta(days=366)).isoformat(),
        renewal_date=(after + timedelta(days=365)).isoformat(),
    )
    assert policy_response.status_code == 201, policy_response.text
    policy_id = policy_response.json()["id"]

    async with get_sessionmaker()() as db:
        # The native aggregate must bound even constraint-valid restored
        # records dated ahead of the current observer clock. The HTTP writer
        # rejects future transaction dates; this test makes no such HTTP claim.
        # All tenant keys, enum, money, period and immutable-history constraints
        # stay enabled, and genuine PostgreSQL aggregation supplies every row.
        db.add_all(
            Transaction(
                farm_id=row_farm,
                date=recorded,
                type=kind,
                category=category,
                amount=Decimal(amount),
                voided_at=utcnow() if voided else None,
                void_reason="Retained voided payment" if voided else None,
            )
            for row_farm, recorded, kind, category, amount, voided in (
                (farm_id, first, "INCOME", "OTHER", "111.01", False),
                (farm_id, first, "EXPENSE", "VET", "13.25", False),
                (farm_id, current, "INCOME", "MANURE", "222.02", False),
                (farm_id, first - timedelta(days=1), "INCOME", "OTHER", "333.03", False),
                (farm_id, after, "INCOME", "OTHER", "444.04", False),
                (farm_id, after + timedelta(days=1), "EXPENSE", "OTHER", "555.05", False),
                (farm_id, first, "EXPENSE", "OTHER", "666.06", True),
                (foreign_id, first, "INCOME", "OTHER", "777.07", False),
            )
        )
        # Direct/import premium facts are explicitly supported by the durable
        # period candidate key. Their accounting dates, not coverage dates,
        # determine which month receives the expense. These facts are inserted
        # once; no existing payment or clinical/animal history is rewritten.
        for index, (recorded, amount) in enumerate(
            ((first, "14.50"), (current, "9.25"), (after, "888.08"))
        ):
            covered_from = first - timedelta(days=300 - index * 30)
            db.add(
                InsurancePremium(
                    farm_id=farm_id,
                    policy_id=policy_id,
                    premium=Decimal(amount),
                    covered_from=covered_from,
                    covered_until=covered_from + timedelta(days=30),
                    recorded_on=recorded,
                )
            )
        await db.commit()
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        rows = await monthly_pnl(db, farm, n_months=2)

    assert [row["month"] for row in rows] == [
        current.strftime("%Y-%m"),
        first.strftime("%Y-%m"),
    ]
    current_row, first_row = rows
    assert (current_row["income"], current_row["expense"], current_row["net"]) == (
        Decimal("222.02"),
        Decimal("9.25"),
        Decimal("212.77"),
    )
    assert (first_row["income"], first_row["expense"], first_row["net"]) == (
        Decimal("111.01"),
        Decimal("27.75"),
        Decimal("83.26"),
    )
    assert first_row["categories"] == {
        "OTHER": {"income": Decimal("111.01"), "expense": Decimal("0.00")},
        "VET": {"income": Decimal("0.00"), "expense": Decimal("13.25")},
        "INSURANCE": {"income": Decimal("0.00"), "expense": Decimal("14.50")},
    }


async def test_batch_allocated_purchase_cost_is_not_another_goats_ledger(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="native-pnl-provenance@farm.in")
    batch = await client.post(
        "/api/purchases/new",
        headers=owner,
        json={
            "date": today().isoformat(),
            "count": 2,
            "total_price": 221.01,
            "create_animals": True,
            "avg_age_months": 18,
        },
    )
    assert batch.status_code == 201, batch.text
    unrelated = await client.post(
        "/api/animals",
        headers={**owner, "Idempotency-Key": "native-pnl-unrelated-purchase"},
        json={
            "tag_number": "UNRELATED-LEDGER",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "purchase_date": (today() - timedelta(days=100)).isoformat(),
            "purchase_price": 900.25,
            "historical_import_reason": "Existing herd purchase before app adoption",
        },
    )
    assert unrelated.status_code == 201, unrelated.text
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        assert farm is not None
        animals = list(
            (
                await db.execute(
                    select(Animal)
                    .where(Animal.purchase_batch_id == batch.json()["id"])
                    .order_by(Animal.id)
                )
            ).scalars()
        )
        assert len(animals) == 2
        assert [animal.purchase_price for animal in animals] == [
            Decimal("110.51"),
            Decimal("110.50"),
        ]
        for animal, expected in zip(animals, (Decimal("110.51"), Decimal("110.50")), strict=True):
            pnl = await lifetime_pnl(db, farm, animal)
            assert pnl["purchase_cost"] == expected
            assert pnl["net"] == -expected
            assert pnl["health_cost"] == pnl["insurance_premiums"] == pnl["sale_income"] == 0
