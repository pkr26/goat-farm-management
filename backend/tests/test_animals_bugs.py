"""REGRESSION SUITE — the app bugs documented here are FIXED.

Each test asserts the SPEC/documented behavior and FAILED against the old
app (int32-overflow ids crashed with an asyncpg DataError → 500; over-long
notes/reasons crashed the INSERT → 500). Path/body ids above the int4 PK
ceiling now resolve to the documented 404, and schemas are capped at the
column widths → 422. Do not weaken these assertions.
"""

import httpx
import pytest

from .conftest import owner_with_farm


async def _make_animal(client: httpx.AsyncClient, headers: dict, tag: str = "A-001") -> dict:
    resp = await client.post(
        "/api/animals",
        json={"tag_number": tag, "sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# FIXED — regression test
# GET /api/animals/{animal_id} with an id >= 2**31 crashes with an unhandled
# asyncpg DataError ("value out of int32 range") instead of a documented 404.
# Cause: Animal.id is a PG INTEGER (int32) but the path param is a plain
# unbounded `int` (schemas/common.py defines BoundedId with le=2**62 for
# exactly this, but the animals routes don't use it) and _get_animal passes the
# raw value to db.get(). Any out-of-int32 id 500s the request (via httpx it
# propagates as an exception). Expected: 404 "Animal not found" (or 422).
async def test_profile_huge_id_returns_404_not_500(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.get(f"/api/animals/{2**31}", headers=owner)
    assert resp.status_code == 404


# FIXED — regression test
# Same root cause as above on the mutating routes: POST
# /api/animals/{id}/move (and /weight, /status) call _get_animal with the
# unbounded path int, so an id >= 2**31 raises an unhandled asyncpg DataError
# (500) instead of 404.
async def test_move_huge_id_returns_404_not_500(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post(
        f"/api/animals/{2**31}/move", json={"to_bucket": "BREEDING"}, headers=owner
    )
    assert resp.status_code == 404


# FIXED — regression test
# POST /api/animals/{id}/weight with notes longer than 255 chars crashes with
# an unhandled asyncpg DataError: WeightIn.notes has no max_length while the
# WeightRecord.notes column is String(255). Expected: 422 (schema bound) or the
# note stored (Text column) — never a 500.
async def test_weight_notes_over_255_chars_not_500(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await _make_animal(client, owner)
    resp = await client.post(
        f"/api/animals/{animal['id']}/weight",
        json={"weight_kg": 20, "notes": "n" * 300},
        headers=owner,
    )
    assert resp.status_code in (201, 422)


# FIXED — regression test
# POST /api/animals/{id}/move with a reason longer than 255 chars crashes with
# an unhandled asyncpg DataError: MoveIn.reason has no max_length while the
# BucketMove.reason column is String(255). Expected: 200/422, never a 500.
async def test_move_reason_over_255_chars_not_500(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await _make_animal(client, owner)
    resp = await client.post(
        f"/api/animals/{animal['id']}/move",
        json={"to_bucket": "BREEDING", "reason": "r" * 300},
        headers=owner,
    )
    assert resp.status_code in (200, 422)


# FIXED — regression test
# POST /api/animals/{id}/status with notes longer than 255 chars crashes with
# an unhandled asyncpg DataError: StatusChangeIn.notes has no max_length while
# Animal.status_notes is String(255). Expected: 200/422, never a 500.
async def test_status_notes_over_255_chars_not_500(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await _make_animal(client, owner)
    resp = await client.post(
        f"/api/animals/{animal['id']}/status",
        json={"new_status": "DEAD", "notes": "n" * 300},
        headers=owner,
    )
    assert resp.status_code in (200, 422)


# FIXED — regression test (AUDIT 2-8)
# create_animal's auto-tag retry branch was dead code: after the collision
# rollback every ORM object is expired, so the retry's `farm.id` access was a
# forbidden sync refresh on the AsyncSession (MissingGreenlet → 500) instead
# of the intended retry with a fresh generated tag. farm.id is now captured
# before the loop.
async def test_auto_tag_retry_after_collision_succeeds(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client)
    await _make_animal(client, owner, tag="G-DUP01")
    tags = iter(["G-DUP01", "G-NEW01"])  # first generated tag collides, retry wins

    async def fake_generate_unique_tag(db: object, farm_id: int, attempts: int = 10) -> str:
        return next(tags)

    monkeypatch.setattr("app.api.animals.generate_unique_tag", fake_generate_unique_tag)
    resp = await client.post(
        "/api/animals",
        json={"sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text  # never the MissingGreenlet 500
    assert resp.json()["tag_number"] == "G-NEW01"
