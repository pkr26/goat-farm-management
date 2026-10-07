"""Real policy lookup and cached-read guards retain current committed insurance state."""

import httpx
import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.db import get_sessionmaker
from app.models import Animal, Farm, InsurancePolicy
from app.services.finance import _lock_new_policy_animal_active, lock_insurance_policy
from app.utils import today

from .conftest import owner_with_farm
from .test_finance_extended import make_animal
from .test_finance_insurance import add_policy


@pytest.mark.parametrize("coverage", ["animal", "herd"])
async def test_native_policy_lookup_locks_and_returns_the_actual_same_farm_policy(
    client: httpx.AsyncClient, coverage: str
) -> None:
    headers = await owner_with_farm(client, email="native-policy-lookup@farm.in")
    animal_id: int | None = None
    if coverage == "animal":
        animal_id = int((await make_animal(client, headers, tag="NATIVE-COVERED"))["id"])
    response = await add_policy(client, headers, policy_number="NATIVE-LOOKUP", animal_id=animal_id)
    assert response.status_code == 201, response.text
    policy_id = int(response.json()["id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(headers["X-Farm-Id"]))
        assert farm is not None
        try:
            policy = await lock_insurance_policy(db, farm, policy_id)
        except (ValueError, SQLAlchemyError) as exc:
            pytest.fail(f"The stored same-farm policy must lock and return successfully: {exc}")
        assert policy is not None
        assert (policy.id, policy.farm_id, policy.animal_id) == (policy_id, farm.id, animal_id)
        assert policy.policy_number == "NATIVE-LOOKUP" and policy.status == "active"


async def test_native_policy_lock_refreshes_cached_cover_after_an_actual_public_sale(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="native-policy-cached-sale@farm.in")
    animal_id = int((await make_animal(client, headers, tag="CACHED-COVER"))["id"])
    response = await add_policy(client, headers, policy_number="NATIVE-CACHED", animal_id=animal_id)
    assert response.status_code == 201, response.text
    policy_id = int(response.json()["id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(headers["X-Farm-Id"]))
        cached = await db.get(InsurancePolicy, policy_id)
        animal = await db.get(Animal, animal_id)
        assert farm is not None and cached is not None and animal is not None
        assert cached.status == "active" and animal.status == "ACTIVE"
        sold = await client.post(
            f"/api/animals/{animal_id}/status",
            headers=headers,
            json={"new_status": "SOLD", "date": today().isoformat(), "sale_price": 5000},
        )
        assert sold.status_code == 200, sold.text
        async with get_sessionmaker()() as observer:
            actual = await observer.get(InsurancePolicy, policy_id)
            exited = await observer.get(Animal, animal_id)
            assert actual is not None and exited is not None
            assert actual.status == "lapsed" and exited.status == "SOLD"
        # A real previously read native session has retained the old identity
        # objects; the documented lock helper promises to refresh both rows.
        assert cached.status == "active" and animal.status == "ACTIVE"
        locked = await lock_insurance_policy(db, farm, policy_id)
        assert locked is cached
        assert (locked.status, animal.status) == ("lapsed", "SOLD")


async def test_new_policy_guard_rejects_a_cached_animal_after_an_actual_public_sale(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="native-new-policy-cached-sale@farm.in")
    animal_id = int((await make_animal(client, headers, tag="CACHED-NEW-COVER"))["id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(headers["X-Farm-Id"]))
        cached = await db.get(Animal, animal_id)
        assert farm is not None and cached is not None and cached.status == "ACTIVE"
        sold = await client.post(
            f"/api/animals/{animal_id}/status",
            headers=headers,
            json={"new_status": "SOLD", "date": today().isoformat(), "sale_price": 5000},
        )
        assert sold.status_code == 200, sold.text
        async with get_sessionmaker()() as observer:
            actual = await observer.get(Animal, animal_id)
            assert actual is not None and actual.status == "SOLD"
        assert cached.status == "ACTIVE"
        with pytest.raises(ValueError, match="has left the herd"):
            await _lock_new_policy_animal_active(db, farm, animal_id)
        assert cached.status == "SOLD"
