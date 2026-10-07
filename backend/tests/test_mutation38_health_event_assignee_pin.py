"""A peer's linked health evidence pins the inactive named assignee until commit."""

import asyncio

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import health as health_api
from app.db import get_sessionmaker
from app.models import Farm, FarmMembership, HealthEvent, HealthRound, HealthRoundCoverage, Task
from app.services.health_rounds import require_round_targets
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import make_animal
from .test_tasks_extended import worker_headers


async def _blocked_by(pid: int) -> bool:
    async with get_sessionmaker()() as observer:
        return bool(
            await observer.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                    "WHERE datname=current_database() AND :holder=ANY(pg_blocking_pids(pid)))"
                ),
                {"holder": pid},
            )
        )


async def test_linked_health_evidence_pins_the_named_assignee_during_peer_fallback(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    peer, peer_id = await worker_headers(client, owner, "MANAGER", "event-peer@farm.in")
    _assignee_headers, assignee_id = await worker_headers(
        client, owner, "MANAGER", "event-assignee@farm.in"
    )
    animal = await make_animal(client, owner, "EVENT-FALLBACK-ANIMAL")
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        membership = (
            await db.execute(
                select(FarmMembership).where(
                    FarmMembership.farm_id == farm_id, FarmMembership.user_id == assignee_id
                )
            )
        ).scalar_one()
        assert membership.is_active is True
        membership_id = membership.id
        duty = Task(
            farm_id=farm_id,
            title="Vaccinate PPR",
            category="VACCINE",
            auto_generated=True,
            due_date=today(farm.timezone),
            status="PENDING",
            title_args={},
            assigned_user_id=assignee_id,
            assigned_role_id=membership.role_id,
            created_by_id=farm.owner_id,
        )
        db.add(duty)
        await db.commit()
        task_id = duty.id
    initialized = await client.post(f"/api/health/rounds/{task_id}/start", headers=owner)
    assert initialized.status_code == 200, initialized.text
    deactivated = await client.put(
        f"/api/team/workers/{membership_id}/status", headers=owner, json={"is_active": False}
    )
    assert deactivated.status_code == 200, deactivated.text
    authorized = asyncio.Event()
    release = asyncio.Event()
    event_pids: list[int] = []

    async def pause_after_real_target_review(
        db: AsyncSession, round_: HealthRound, animal_ids: list[int], components: list[str]
    ) -> None:
        await require_round_targets(db, round_, animal_ids, components)
        if round_.task_id == task_id:
            pid = await db.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(pid, int)
            event_pids.append(pid)
            authorized.set()
            await release.wait()

    monkeypatch.setattr(health_api, "require_round_targets", pause_after_real_target_review)
    evidence = asyncio.create_task(
        client.post(
            "/api/health/events",
            headers=peer,
            json={
                "scope": "bucket",
                "bucket": "FOUNDATION",
                "expected_animal_ids": [animal["id"]],
                "type": "VACCINE",
                "disease_target": "PPR",
                "task_id": task_id,
            },
        )
    )
    activate: asyncio.Task[httpx.Response] | None = None
    try:
        await asyncio.wait_for(authorized.wait(), timeout=10)
        activate = asyncio.create_task(
            client.put(
                f"/api/team/workers/{membership_id}/status", headers=owner, json={"is_active": True}
            )
        )
        for _ in range(1000):
            if await _blocked_by(event_pids[0]):
                break
            if activate.done():
                result = await activate
                assert result.status_code == 200, result.text
                raise AssertionError(
                    "A named assignee reactivated before the peer's health evidence committed"
                )
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("The actual activation never reached the pinned assignee")
        release.set()
        response = await asyncio.wait_for(evidence, timeout=15)
        activated = await asyncio.wait_for(activate, timeout=15)
    finally:
        release.set()
        for request in (evidence, activate):
            if request is not None and not request.done():
                request.cancel()
        await asyncio.gather(
            *(request for request in (evidence, activate) if request is not None),
            return_exceptions=True,
        )
    assert response.status_code == 201, response.text
    assert activated.status_code == 200, activated.text
    assert len(response.json()) == 1 and response.json()[0]["animal_id"] == animal["id"]
    async with get_sessionmaker()() as db:
        stored_membership = await db.get(FarmMembership, membership_id)
        stored_duty = await db.get(Task, task_id)
        event = await db.get(HealthEvent, response.json()[0]["id"])
        coverage = await db.get(HealthRoundCoverage, (task_id, animal["id"], "PPR"))
        assert stored_membership is not None and stored_membership.is_active is True
        assert stored_duty is not None and stored_duty.status == "DONE"
        assert stored_duty.completed_by_id == peer_id and stored_duty.completed_at is not None
        assert stored_duty.assigned_user_id == assignee_id
        assert event is not None and event.created_by_id == peer_id
        assert coverage is not None and coverage.health_event_id == event.id
