"""Mutation-campaign gap tests: task-generated bucket moves keep their actor.

The 2026-09-30 campaign showed every `created_by_id=user.id if user else None`
on the auto-generated lifecycle moves (quarantine release, gestation day-100
step-up, pre-delivery) could silently flip to None: the moves themselves were
asserted, WHO performed them never was. Each test below completes the duty as
the owner and pins the resulting BucketMove rows' attribution.
"""

from datetime import date, timedelta
from typing import Any

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import BucketMove
from app.utils import today

from .conftest import owner_with_farm
from .test_tasks_extended import (
    complete_duty,
    get_tabs,
    make_pregnancy,
)


async def _latest_move(animal_id: int) -> BucketMove:
    async with get_sessionmaker()() as db:
        return (
            await db.execute(
                select(BucketMove)
                .where(BucketMove.animal_id == animal_id)
                .order_by(BucketMove.id.desc())
                .limit(1)
            )
        ).scalar_one()


async def _complete_lifecycle_move(
    client: httpx.AsyncClient, owner: dict[str, Any], *, due_date: date
) -> tuple[int, int]:
    tabs = await get_tabs(client, owner)
    duty = next(
        t
        for t in tabs["today"] + tabs["overdue"] + tabs["upcoming"]
        if t["category"] == "BUCKET_MOVE" and t["due_date"] == due_date.isoformat()
    )
    resp = await complete_duty(client, owner, duty["id"])
    assert resp.status_code == 200, resp.text
    return duty["id"], int(resp.json()["completed_by_id"])


async def test_day100_step_up_move_attributes_the_completing_user(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="attr-day100@farm.in")
    # Bred 120 days ago → EKD in ~30 days → the day-100 move (EKD - 50) is overdue.
    doe, br = await make_pregnancy(client, owner, today() - timedelta(days=120))
    ekd = date.fromisoformat(br["expected_kidding_date"])
    _duty_id, completed_by = await _complete_lifecycle_move(
        client, owner, due_date=ekd - timedelta(days=50)
    )
    move = await _latest_move(doe["id"])
    assert move.to_bucket == "PREGNANCY_LATE"
    assert move.reason == "Gestation day 100 (ration step-up)"
    assert move.created_by_id == completed_by, "step-up move must be attributed to the actor"


async def test_pre_delivery_move_attributes_the_completing_user(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="attr-delivery@farm.in")
    # Bred 135 days ago → EKD in ~15 days → the pre-delivery move is due today.
    doe, br = await make_pregnancy(client, owner, today() - timedelta(days=135))
    ekd = date.fromisoformat(br["expected_kidding_date"])
    _duty_id, completed_by = await _complete_lifecycle_move(
        client, owner, due_date=ekd - timedelta(days=15)
    )
    move = await _latest_move(doe["id"])
    assert move.to_bucket == "DELIVERY"
    assert move.reason == "~2 weeks before due date"
    assert move.created_by_id == completed_by, "pre-delivery move must be attributed to the actor"


async def test_quarantine_release_moves_attribute_the_completing_user(
    client: httpx.AsyncClient,
) -> None:
    from .test_health_extended import (
        _backdated_batch_with_tasks,
        complete_quarantine_prerequisites,
    )

    owner = await owner_with_farm(client, email="attr-quarantine@farm.in")
    detail = await _backdated_batch_with_tasks(client, owner, days=50, count=3)
    await complete_quarantine_prerequisites(client, owner, detail)
    release = next(t for t in detail["tasks"] if t["category"] == "BUCKET_MOVE")
    resp = await complete_duty(client, owner, release["id"])
    assert resp.status_code == 200, resp.text
    completed_by = int(resp.json()["completed_by_id"])

    async with get_sessionmaker()() as db:
        moves = (
            (
                await db.execute(
                    select(BucketMove)
                    .where(BucketMove.reason == "45-day quarantine complete")
                    .order_by(BucketMove.id)
                )
            )
            .scalars()
            .all()
        )
    assert moves, "release must move the batch animals"
    assert {m.to_bucket for m in moves} == {"FOUNDATION"}
    assert {m.created_by_id for m in moves} == {completed_by}, (
        "release moves must be attributed to the actor, never unattributed"
    )
