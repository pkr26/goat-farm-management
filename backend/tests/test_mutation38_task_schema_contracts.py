"""Duty input boundaries and retained response defaults preserve factual task meaning."""

from datetime import date

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.db import get_sessionmaker
from app.models import IdempotencyRecord, Task
from app.schemas.tasks import TaskCreateIn, TaskRejectIn, TaskSkipIn
from app.utils import today

from .conftest import owner_with_farm


@pytest.mark.parametrize(("length", "accepted"), [(0, False), (1, True), (200, True), (201, False)])
def test_manual_duty_title_keeps_the_published_one_to_two_hundred_character_band(
    length: int, accepted: bool
) -> None:
    payload = {"title": "T" * length, "due_date": date(2026, 1, 1)}
    if not accepted:
        with pytest.raises(ValidationError):
            TaskCreateIn.model_validate(payload)
        return
    try:
        result = TaskCreateIn.model_validate(payload)
    except ValidationError as exc:
        pytest.fail(f"A supported manual duty title was rejected: {exc}")
    assert result.title == payload["title"] and result.category == "OTHER"


@pytest.mark.parametrize(
    ("due", "accepted"),
    [
        (date(1999, 12, 31), False),
        (date(2000, 1, 1), True),
        (date(2100, 12, 31), True),
        (date(2101, 1, 1), False),
    ],
    ids=["before-floor", "floor", "ceiling", "after-ceiling"],
)
def test_native_manual_duty_due_date_keeps_the_stated_inclusive_year_band(
    due: date, accepted: bool
) -> None:
    payload = {"title": "Calendar boundary inspection", "due_date": due}
    if not accepted:
        with pytest.raises(ValidationError):
            TaskCreateIn.model_validate(payload)
        return
    try:
        result = TaskCreateIn.model_validate(payload)
    except ValidationError as exc:
        pytest.fail(f"A supported duty calendar date was rejected: {exc}")
    assert result.due_date == due


@pytest.mark.parametrize("kind", ["skip", "reject"])
@pytest.mark.parametrize(
    ("text", "accepted"),
    [(" X ", True), ("   ", False), ("X" * 255, True), ("X" * 256, False)],
    ids=["trim-human-reason", "blank-reason", "max-reason", "overlong-reason"],
)
def test_duty_audit_reason_adapters_trim_and_bound_actual_human_input(
    kind: str, text: str, accepted: bool
) -> None:
    schema = TaskSkipIn if kind == "skip" else TaskRejectIn
    field = "reason" if kind == "skip" else "note"
    if not accepted:
        with pytest.raises(ValidationError):
            schema.model_validate({field: text})
        return
    try:
        result = schema.model_validate({field: text})
    except ValidationError as exc:
        pytest.fail(f"A supported human duty audit reason was rejected: {exc}")
    assert result.model_dump()[field] == text.strip()


async def test_retained_manual_duty_response_does_not_invent_a_verification_requirement(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="retained-task-response@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    headers = owner | {"Idempotency-Key": "retained-manual-task-response"}
    payload = {"title": "Actual one-off inspection", "due_date": today().isoformat()}
    original = await client.post("/api/tasks", headers=headers, json=payload)
    assert original.status_code == 201, original.text
    factual = original.json()
    assert factual["status"] == "PENDING" and factual["needs_verification"] is False
    async with get_sessionmaker()() as db:
        record = (
            await db.execute(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.farm_id == farm_id,
                    IdempotencyRecord.operation == "tasks.create",
                )
            )
        ).scalar_one()
        assert record.completed_at is not None and record.response_status == 201
        assert record.response_body is not None
        # Restore a real committed claim in the optional older wire shape,
        # retaining every required fact and the original request/scope/audit.
        retained = dict(record.response_body)
        assert retained.pop("needs_verification") is False
        record.response_body = retained
        await db.commit()
    replay = await client.post("/api/tasks", headers=headers, json=payload)
    assert replay.status_code == 201, replay.text
    assert replay.headers.get("Idempotency-Replayed") == "true"
    assert replay.json()["id"] == factual["id"]
    assert replay.json()["needs_verification"] is False
    current = await client.get(f"/api/tasks/{factual['id']}", headers=owner)
    assert current.status_code == 200, current.text
    assert current.json()["needs_verification"] is False
    async with get_sessionmaker()() as db:
        assert await db.scalar(select(func.count(Task.id)).where(Task.farm_id == farm_id)) == 1
        stored = await db.get(Task, factual["id"])
        assert stored is not None and stored.status == "PENDING"
        assert stored.completed_at is None and stored.verified_at is None
