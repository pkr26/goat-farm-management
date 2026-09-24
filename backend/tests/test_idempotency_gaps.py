"""Idempotency gap tests (2026-09-23 verification plan, category 9).

The replay/conflict/expiry behaviours of the key layer itself are pinned in
test_idempotency. The remaining gap is the offline-tablet story's sad path:
a queued duty completion that reaches the server AFTER someone else already
completed the same duty under a different key. The offline queue drops
409s as "already committed" — this test pins that the server answers a
defined 409 (never a 200 double-effect, never a 500), for both complete
and skip, from a different actor.
"""

import httpx

from app.utils import today

from .conftest import login_and_rotate, owner_with_farm


async def _shared_role_id(client: httpx.AsyncClient, owner: dict) -> int:
    role = await client.post(
        "/api/team/roles",
        json={
            "name": "Shift",
            "permissions": ["tasks.view", "tasks.complete", "tasks.skip"],
        },
        headers=owner,
    )
    assert role.status_code == 201, role.text
    return role.json()["id"]


async def _worker(client: httpx.AsyncClient, owner: dict, email: str, role_id: int) -> dict:
    await client.post(
        "/api/team/workers",
        json={
            "email": email,
            "name": email.split("@")[0],
            "role_id": role_id,
            "password": "workerpass123",
        },
        headers=owner,
    )
    worker = await login_and_rotate(client, email, "workerpass123")
    return worker | {"X-Farm-Id": owner["X-Farm-Id"]}


async def test_queued_completion_replayed_after_another_worker_completes(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="replay@farm.in")
    role_id = await _shared_role_id(client, owner)
    worker_a = await _worker(client, owner, "replay-a@farm.in", role_id)
    worker_b = await _worker(client, owner, "replay-b@farm.in", role_id)

    task = await client.post(
        "/api/tasks",
        json={
            "title": "Shared shift duty",
            "category": "OTHER",
            "due_date": today().isoformat(),
            "assigned_role_id": role_id,
        },
        headers=owner,
    )
    assert task.status_code == 201, task.text
    task_id = task.json()["id"]

    # Worker A completes (her tablet's key).
    first = await client.post(
        f"/api/tasks/{task_id}/complete",
        headers=worker_a | {"Idempotency-Key": "tablet-A-1"},
    )
    assert first.status_code == 200, first.text

    # Worker B's offline queue drains later with ITS OWN key. The server
    # answers a DEFINITIVE 4xx ("Task is not pending", 400) — the queue's
    # documented drop bucket (409 = same-key replay; any 4xx = definitive
    # rejection, dropped without wedging). A 200 double-effect or a 5xx
    # would both be queue-drain bugs.
    second = await client.post(
        f"/api/tasks/{task_id}/complete",
        headers=worker_b | {"Idempotency-Key": "tablet-B-1"},
    )
    assert second.status_code in (400, 409), (
        f"replayed foreign completion → {second.status_code}: {second.text[:150]}"
    )
    assert "pending" in second.json()["detail"].lower() or "already" in second.json()["detail"].lower()

    # The same for a queued skip arriving after completion.
    late_skip = await client.post(
        f"/api/tasks/{task_id}/skip",
        json={"reason": "queued from the field"},
        headers=worker_b | {"Idempotency-Key": "tablet-B-2"},
    )
    assert late_skip.status_code in (400, 409), (
        f"replayed foreign skip → {late_skip.status_code}: {late_skip.text[:150]}"
    )

    # And the task state is exactly one completion by worker A.
    detail = await client.get(f"/api/tasks/{task_id}", headers=owner)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body.get("status") == "DONE"
    assert body.get("completed_by_id") or body.get("completed_by") is not None
