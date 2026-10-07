"""A retained valid policy link identifies the same animal across insurance responses.

Zero is reserved at animal resource URLs, but a constraint-valid restored zero
animal key can still be an existing FK target of a positive policy. The policy
register/history already expose that real link; renewal and claim must retain it.
"""

import json
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest

from app.db import get_sessionmaker
from app.models import Animal, InsurancePolicy
from app.utils import today

from .conftest import owner_with_farm


@pytest.mark.parametrize("link", ["retained-zero", "ordinary-positive", "herd-wide"])
async def test_linked_tag_is_consistent_in_register_history_renewal_and_claim(
    client: httpx.AsyncClient,
    link: str,
) -> None:
    owner = await owner_with_farm(client)
    reference = today()
    expected_tag = None if link == "herd-wide" else f"INSURANCE-{link}"
    async with get_sessionmaker()() as db:
        animal_id = None
        if expected_tag is not None:
            animal = Animal(
                farm_id=int(owner["X-Farm-Id"]),
                tag_number=expected_tag,
                sex="F",
                source="PURCHASED",
                current_bucket="FOUNDATION",
                status="ACTIVE",
            )
            if link == "retained-zero":
                animal.id = 0
            db.add(animal)
            await db.flush()
            animal_id = animal.id
        policy = InsurancePolicy(
            farm_id=int(owner["X-Farm-Id"]),
            animal_id=animal_id,
            policy_number=f"CONSISTENT-LINK-{link}",
            insurer="Retained livestock insurance",
            sum_insured=Decimal("1000.00"),
            premium=Decimal("10.00"),
            start_date=reference,
            renewal_date=reference + timedelta(days=30),
            status="active",
        )
        db.add(policy)
        await db.commit()
        policy_id = policy.id
        assert policy_id > 0

    register = await client.get("/api/finance/insurance", headers=owner)
    history_before = await client.get(f"/api/finance/insurance/{policy_id}/history", headers=owner)
    renewal = await client.post(
        f"/api/finance/insurance/{policy_id}/renew",
        headers=owner,
        json={"renewal_date": (reference + timedelta(days=60)).isoformat()},
    )
    claim = await client.post(
        f"/api/finance/insurance/{policy_id}/claim",
        headers=owner,
        json={"claim_date": reference.isoformat()},
    )
    history_after = await client.get(f"/api/finance/insurance/{policy_id}/history", headers=owner)
    for response in (register, history_before, renewal, claim, history_after):
        assert response.status_code == 200, response.text
    wire_policies = {
        "register": next(row for row in register.json()["policies"] if row["id"] == policy_id),
        "history_before": history_before.json()["policy"],
        "renewal": renewal.json(),
        "claim": claim.json(),
        "history_after": history_after.json()["policy"],
    }
    print(
        "INSURANCE_LINK_OBSERVATION="
        + json.dumps(
            {
                "scope": link,
                "responses": {
                    endpoint: {"animal_id": row["animal_id"], "animal_tag": row["animal_tag"]}
                    for endpoint, row in wire_policies.items()
                },
            },
            sort_keys=True,
        )
    )
    assert renewal.json()["renewal_date"] == (reference + timedelta(days=60)).isoformat()
    assert claim.json()["status"] == "claimed"
    async with get_sessionmaker()() as db:
        retained_policy = await db.get(InsurancePolicy, policy_id)
        assert retained_policy is not None and retained_policy.animal_id == animal_id
        assert retained_policy.status == "claimed" and retained_policy.claimed_by_id is not None
        if animal_id is not None:
            retained_animal = await db.get(Animal, animal_id)
            assert retained_animal is not None and retained_animal.tag_number == expected_tag
    for endpoint, row in wire_policies.items():
        assert row["animal_id"] == animal_id, endpoint
        assert row["animal_tag"] == expected_tag, endpoint
