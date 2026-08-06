"""Sanity checks for the test harness itself (kept as a permanent smoke)."""

import httpx

from .conftest import owner_with_farm


async def test_harness_owner_with_farm(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.get("/api/buckets", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 10  # seeded bucket definitions


async def test_unauthenticated_is_401(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/animals")
    assert resp.status_code == 401
