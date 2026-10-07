"""A worker's ordinary scoped count includes its own real completed review duty."""

import httpx

from .conftest import owner_with_farm, provisioned_worker_login
from .test_tasks_extended import WORKER_PW, add_worker, make_custom_role, make_duty


async def test_worker_without_verification_permission_counts_its_completed_review_duty(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="worker-review-count-owner@farm.in")
    working_role = await make_custom_role(
        client, owner, "Inspection cleaners", ["tasks.view", "tasks.complete"]
    )
    await add_worker(client, owner, working_role, "worker-review-count@farm.in")
    headers, _ = await provisioned_worker_login(client, "worker-review-count@farm.in", WORKER_PW)
    worker = headers | {"X-Farm-Id": owner["X-Farm-Id"]}
    duty = await make_duty(
        client,
        owner,
        title="Actually finished assigned cleaning",
        category="CLEANING",
        assigned_role_id=working_role,
    )
    completed = await client.post(f"/api/tasks/{duty['id']}/complete", headers=worker)
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "DONE"
    response = await client.get("/api/tasks", headers=worker)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["awaiting_total"] == 1
    assert [row["id"] for row in body["awaiting"]] == [duty["id"]]
    assert body["completed_total"] == 0 and body["completed"] == []
    for name in ["today", "overdue", "upcoming"]:
        assert body[name + "_total"] == 0 and body[name] == []
