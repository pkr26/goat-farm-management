"""Real public policy flows preserve inclusive claim windows and the five-year renewal edge."""

from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import InsurancePolicy, InsurancePremium, Task
from app.utils import today

from .conftest import owner_with_farm
from .test_finance_insurance import add_policy


@pytest.mark.parametrize("start_age", [0, 10])
async def test_real_current_day_claim_admits_both_coverage_endpoints_and_keeps_premium_facts(
    client: httpx.AsyncClient,
    start_age: int,
) -> None:
    owner = await owner_with_farm(client)
    reference = today()
    created = await add_policy(
        client,
        owner,
        policy_number="INCLUSIVE-CLAIM-ENDPOINT",
        start_date=(reference - timedelta(days=start_age)).isoformat(),
        renewal_date=reference.isoformat(),
    )
    assert created.status_code == 201, created.text
    policy_id = created.json()["id"]
    async with get_sessionmaker()() as db:
        premiums = list((await db.execute(select(InsurancePremium))).scalars())
        assert len(premiums) == (1 if start_age else 0)
        before = [
            (row.id, row.premium, row.covered_from, row.covered_until, row.recorded_by_id)
            for row in premiums
        ]
        assert not list(
            (await db.execute(select(Task).where(Task.category == "INSURANCE"))).scalars()
        )
    claimed = await client.post(
        f"/api/finance/insurance/{policy_id}/claim",
        headers=owner,
        json={"claim_date": reference.isoformat()},
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["status"] == "claimed"
    assert claimed.json()["claim_date"] == reference.isoformat()
    async with get_sessionmaker()() as db:
        policy = await db.get(InsurancePolicy, policy_id)
        assert (
            policy is not None
            and policy.claimed_at is not None
            and policy.claimed_by_id is not None
        )
        after = list((await db.execute(select(InsurancePremium))).scalars())
        assert [
            (row.id, row.premium, row.covered_from, row.covered_until, row.recorded_by_id)
            for row in after
        ] == before


async def test_real_renewal_accepts_literal1830_days_records_premium_and_replays_without_rebooking(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    reference = today()
    old_horizon = reference + timedelta(days=60)
    created = await add_policy(
        client,
        owner,
        policy_number="EXACT-FIVE-YEAR-RENEWAL",
        start_date=reference.isoformat(),
        renewal_date=old_horizon.isoformat(),
        premium=0.33,
    )
    assert created.status_code == 201, created.text
    policy_id = created.json()["id"]
    new_horizon = old_horizon + timedelta(days=1830)
    changed = await client.post(
        f"/api/finance/insurance/{policy_id}/renew",
        headers=owner,
        json={"renewal_date": new_horizon.isoformat(), "premium": 0.44},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["renewal_date"] == new_horizon.isoformat()
    assert changed.json()["premium"] == 0.44 and changed.json()["status"] == "active"
    repeated = await client.post(
        f"/api/finance/insurance/{policy_id}/renew",
        headers=owner,
        json={"renewal_date": new_horizon.isoformat(), "premium": 0.44},
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json() == changed.json()
    async with get_sessionmaker()() as db:
        premiums = list(
            (await db.execute(select(InsurancePremium).order_by(InsurancePremium.id))).scalars()
        )
        assert [row.premium for row in premiums] == [Decimal("0.33"), Decimal("0.44")]
        assert [(row.covered_from, row.covered_until) for row in premiums] == [
            (reference, old_horizon),
            (old_horizon, new_horizon),
        ]
        assert all(
            row.recorded_on == reference and row.recorded_by_id is not None for row in premiums
        )
        duties = list(
            (
                await db.execute(select(Task).where(Task.category == "INSURANCE").order_by(Task.id))
            ).scalars()
        )
        assert [row.due_date for row in duties] == [
            old_horizon - timedelta(days=30),
            new_horizon - timedelta(days=30),
        ]
    refused = await client.post(
        f"/api/finance/insurance/{policy_id}/renew",
        headers=owner,
        json={"renewal_date": (new_horizon + timedelta(days=1831)).isoformat()},
    )
    assert refused.status_code == 422, refused.text
    assert "at most five years" in refused.json()["detail"]
    async with get_sessionmaker()() as db:
        policy = await db.get(InsurancePolicy, policy_id)
        assert policy is not None and policy.renewal_date == new_horizon
        assert len(list((await db.execute(select(InsurancePremium))).scalars())) == 2
