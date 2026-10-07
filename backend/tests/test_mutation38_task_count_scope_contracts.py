"""Duty count aggregates match real visible rows and farm-wide verification authority."""

import httpx

from .conftest import owner_with_farm, provisioned_worker_login
from .test_tasks_extended import WORKER_PW, add_worker, make_animal, make_custom_role, make_duty


async def test_one_real_linked_animal_contributes_to_its_own_pending_tab_count(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="single-linked-count@farm.in")
    animal_id = await make_animal(client, owner)
    duty = await make_duty(
        client, owner, title="The actual linked animal inspection", animal_id=animal_id
    )
    response = await client.get("/api/tasks", headers=owner)
    assert response.status_code == 200, response.text
    body = response.json()
    assert duty["id"] in [row["id"] for row in body["today"]]
    assert body["today_total"] >= 1 and body["today_total"] == len(body["today"])
    for name in ["overdue", "upcoming"]:
        assert body[name + "_total"] == len(body[name])


async def test_verifier_counts_another_roles_actual_review_duty_without_history_scope(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="verification-count-owner@farm.in")
    reviewer_role = await make_custom_role(
        client, owner, "Quality reviewer", ["tasks.view", "tasks.verify"]
    )
    working_role = await make_custom_role(
        client, owner, "Different cleaning team", ["tasks.view", "tasks.complete"]
    )
    await add_worker(client, owner, reviewer_role, "verification-count-worker@farm.in")
    headers, _ = await provisioned_worker_login(
        client, "verification-count-worker@farm.in", WORKER_PW
    )
    reviewer = headers | {"X-Farm-Id": owner["X-Farm-Id"]}
    duty = await make_duty(
        client,
        owner,
        title="Another team's actual cleaning",
        category="CLEANING",
        assigned_role_id=working_role,
    )
    completed = await client.post(f"/api/tasks/{duty['id']}/complete", headers=owner)
    assert completed.status_code == 200 and completed.json()["status"] == "DONE", completed.text
    response = await client.get("/api/tasks", headers=reviewer)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["awaiting_total"] == 1
    assert [row["id"] for row in body["awaiting"]] == [duty["id"]]
    assert (
        body["today_total"]
        == body["overdue_total"]
        == body["upcoming_total"]
        == body["completed_total"]
        == 0
    )
    assert body["today"] == body["overdue"] == body["upcoming"] == body["completed"] == []
