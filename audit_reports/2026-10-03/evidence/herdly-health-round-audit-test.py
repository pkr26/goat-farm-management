"""Audit evidence: incomplete herd-round coverage is accepted and marked DONE."""
import httpx
from sqlalchemy import select
from app.db import get_sessionmaker
from app.models import HealthEvent, Task
from tests.conftest import owner_with_farm
from tests.test_health_extended import make_animal
from tests.test_health_safety import _seed_herd_round_task, preview


async def test_audit_partial_herd_round_closes_entire_duty(client: httpx.AsyncClient):
    owner = await owner_with_farm(client, email="partial-round-audit@farm.in")
    treated = await make_animal(client, owner, tag="AUDIT-TREATED", bucket="FOUNDATION")
    untouched = await make_animal(client, owner, tag="AUDIT-UNTOUCHED", bucket="FEMALE_KIDS")
    reviewed = await preview(client, owner, scope="bucket", bucket="FOUNDATION")
    assert reviewed.status_code == 200, reviewed.text
    task_id = await _seed_herd_round_task(owner, "ET + HS pre-monsoon round (2026) — all animals")
    result = await client.post("/api/health/events", headers=owner, json={
        "scope":"bucket", "bucket":"FOUNDATION",
        "expected_animal_ids":reviewed.json()["target_animal_ids"],
        "type":"VACCINE", "disease_target":"HS",
        "schedule_template_name":"Haemorrhagic Septicaemia (HS)", "task_id":task_id})
    assert result.status_code == 201, result.text
    async with get_sessionmaker()() as db:
        task = await db.get(Task, task_id)
        facts = list((await db.execute(select(HealthEvent).where(
            HealthEvent.farm_id == int(owner["X-Farm-Id"])))).scalars())
        assert task.status == "DONE"
        assert [fact.animal_id for fact in facts] == [treated["id"]]
        assert all(fact.animal_id != untouched["id"] for fact in facts)
        assert all(fact.schedule_template_name != "Enterotoxaemia (ET)" for fact in facts)
        print("AUDIT_PARTIAL_HERD_ROUND: task DONE; 1 of 2 goats recorded; HS only; no ET record")
