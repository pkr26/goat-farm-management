"""Owners can repair or remove custom grants retained across dairy retirement.

Before896a620, the native role editor accepted ``milk.view`` on GOAT farms:
permissions were a global catalog. The bd201c1cdc1b upgrade removes dairy farms
but retains GOAT farms and their custom roles without rewriting grant arrays.
The c4f6a8b0d2e5 JSONB upgrade preserves valid arrays too. This initial row is
that supported retained shape, with every current CHECK and FK still enforced.
"""

import json

import httpx
import pytest

from app.db import get_sessionmaker
from app.models import Role

from .conftest import owner_with_farm


@pytest.mark.parametrize("operation", ["repair", "remove"])
async def test_owner_can_repair_or_remove_a_retained_goat_role_with_retired_grants(
    client: httpx.AsyncClient, operation: str
) -> None:
    owner = await owner_with_farm(client, email="retained-custom-role-owner@farm.in")
    async with get_sessionmaker()() as restored:
        legacy = Role(
            farm_id=int(owner["X-Farm-Id"]),
            code=None,
            name="Retained farm operator",
            permissions='["milk.view"]',
        )
        restored.add(legacy)
        await restored.commit()
        role_id, previous_revision = legacy.id, legacy.revision
    roster = await client.get("/api/team", headers=owner)
    assert roster.status_code == 200, roster.text
    published = [row for row in roster.json()["roles"] if row["id"] == role_id]
    assert len(published) == 1 and published[0]["permissions"] == []

    endpoint = f"/api/team/roles/{role_id}"
    if operation == "repair":
        saved = await client.put(
            endpoint,
            headers=owner,
            json={
                "name": "Current farm operator",
                "permissions": ["tasks.view"],
                "expected_revision": previous_revision,
            },
        )
        assert saved.status_code == 200, (
            "A retired grant must not prevent its farm owner replacing it with current grants: "
            f"{saved.status_code} {saved.text}"
        )
        assert saved.json()["permissions"] == ["tasks.view"]
        assert saved.json()["revision"] > previous_revision
    else:
        removed = await client.delete(endpoint, headers=owner)
        assert removed.status_code == 204, (
            "A retired grant must not prevent its farm owner retiring an unused custom role: "
            f"{removed.status_code} {removed.text}"
        )
    async with get_sessionmaker()() as observer:
        stored = await observer.get(Role, role_id)
        assert stored is not None, "Retirement keeps the role's historical FK anchor"
        if operation == "repair":
            assert stored.name == "Current farm operator" and stored.deleted_at is None
            assert set(json.loads(stored.permissions)) == {"tasks.view"}
        else:
            assert stored.deleted_at is not None
            assert set(json.loads(stored.permissions)) == {"milk.view"}
