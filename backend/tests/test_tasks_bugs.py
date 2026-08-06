"""REGRESSION SUITE — the tasks/duties app bug documented here is FIXED.

The test encodes the SPEC/contract expectation and FAILED against the old
app (an int32-overflow path id crashed with an asyncpg DataError → 500).
Path ids above the int4 PK ceiling now resolve to 404. Do not weaken it.
"""

import httpx

from .conftest import owner_with_farm


# FIXED — regression test
# Endpoint: POST /api/tasks/{task_id}/complete (and skip/verify/reject — same
# `_get_task` lookup path).
# Repro: a task id larger than int32 (e.g. 10**20) used to reach SQLAlchemy
# unbounded, and asyncpg raised DataError "value out of int32 range"
# (tasks.id is INTEGER/int4) → unhandled 500. `_get_task` now treats ids
# above the int4 PK ceiling as not found → 404 "Task not found".
async def test_huge_task_id_rejected_not_500(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post(f"/api/tasks/{10**20}/complete", headers=owner)
    assert resp.status_code in (404, 422)
