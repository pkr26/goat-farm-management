"""Supported admission, authorization and idempotency boundary behavior."""

from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Insert

import app.services.idempotency as idempotency
from app.api._run_limits import _RunCostWindow
from app.api._shared import unique_constraint_name, visible_to
from app.db import get_sessionmaker
from app.models import Farm, FarmMembership, IdempotencyRecord, Task, User
from app.ratelimit import SlidingWindowRateLimiter
from app.utils import today, utcnow

from .conftest import owner_with_farm


def test_releasing_one_of_several_admission_slots_opens_exactly_one_slot() -> None:
    limiter = SlidingWindowRateLimiter(max_keys=2)
    for _ in range(3):
        assert limiter.try_reserve("credential", "worker", max_in_flight=3)
    assert not limiter.try_reserve("credential", "worker", max_in_flight=3)
    assert limiter.try_reserve("credential", "other", max_in_flight=3)
    assert not limiter.try_reserve("credential", "third", max_in_flight=3)

    limiter.release("credential", "worker")
    assert limiter.try_reserve("credential", "worker", max_in_flight=3)
    assert not limiter.try_reserve("credential", "worker", max_in_flight=3)

    for _ in range(3):
        limiter.release("credential", "worker")
    # Completing the last request releases the identity's cardinality slot.
    assert limiter.try_reserve("credential", "third", max_in_flight=3)
    limiter.release("credential", "worker")  # Duplicate completion is harmless.
    limiter.release("credential", "third")
    assert limiter.try_reserve("credential", "worker", max_in_flight=3)


def test_a_changed_admission_threshold_promotes_and_then_demotes_live_history() -> None:
    limiter = SlidingWindowRateLimiter(max_keys=2)
    limiter.record("login", "victim", 600, max_attempts=3)
    limiter.record("login", "cold", 600, max_attempts=3)
    assert limiter.is_blocked("login", "victim", 1, 600)
    limiter.record("login", "spray", 600, max_attempts=3)
    assert limiter.has_attempts("login", "victim")
    assert not limiter.has_attempts("login", "cold")

    assert not limiter.is_blocked("login", "victim", 3, 600)
    limiter.record("login", "protected", 600, max_attempts=1)
    limiter.record("login", "next", 600, max_attempts=3)
    assert not limiter.has_attempts("login", "victim")
    assert limiter.is_blocked("login", "protected", 1, 600)


def test_booking_a_request_never_evicts_an_already_tracked_principal_in_that_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _RunCostWindow(window_seconds=60, budget=100, max_keys=2)
    actual_eviction = window._evict_one

    def checked_eviction(*, exclude: set[tuple[str, int]]) -> bool:
        before = set(window._spend)
        retained = before & exclude
        freed = actual_eviction(exclude=exclude)
        if freed:
            after = set(window._spend)
            assert len(after) == len(before) - 1, "Successful eviction must free exactly one slot"
            assert retained <= after, "Eviction must preserve every excluded existing principal"
        return freed

    monkeypatch.setattr(window, "_evict_one", checked_eviction)
    capacity_error: RuntimeError | None = None
    try:
        window.charge("farm", 1, 1)
        window.charge("user", 2, 60)
        # The farm is the only cold candidate, but belongs to this new request.
        # Evict the unrelated protected user and retain both requested charges.
        window.charge_many((("farm", 1, 1), ("user", 3, 1)))
    except RuntimeError as exc:
        capacity_error = exc
    assert capacity_error is None, (
        f"A request with exactly the supported principal count must be admitted: {capacity_error!r}"
    )
    assert window.would_exceed_budget("farm", 1, 99)
    assert not window.would_exceed_budget("farm", 1, 98)
    assert not window.would_exceed_budget("user", 2, 100)
    assert window.would_exceed_budget("user", 3, 100)


async def _team(client: httpx.AsyncClient) -> tuple[int, int, int, int]:
    owner = await owner_with_farm(client, email="visibility-boundary-owner@farm.in")
    role = await client.post(
        "/api/team/roles",
        headers=owner,
        json={"name": "Continuity operators", "permissions": ["tasks.view", "tasks.complete"]},
    )
    assert role.status_code == 201, role.text
    ids = []
    for label in ("assignee", "peer"):
        worker = await client.post(
            "/api/team/workers",
            headers={**owner, "Idempotency-Key": f"visibility-{label}"},
            json={
                "name": label,
                "email": f"visibility-{label}@farm.in",
                "role_id": role.json()["id"],
                "password": "worker-password123",
            },
        )
        assert worker.status_code == 201, worker.text
        ids.append(worker.json()["id"])
    return int(owner["X-Farm-Id"]), role.json()["id"], ids[0], ids[1]


async def test_locked_task_visibility_follows_real_assignee_availability(
    client: httpx.AsyncClient,
) -> None:
    from sqlalchemy.exc import MultipleResultsFound

    farm_id, role_id, assignee_id, peer_id = await _team(client)
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assignee = await db.get(FarmMembership, assignee_id)
        peer = await db.get(FarmMembership, peer_id)
        assert farm is not None and assignee is not None and peer is not None
        viewer = await db.get(User, peer.user_id)
        assert viewer is not None
        task = Task(
            farm_id=farm_id,
            title="Named worker's pending duty",
            due_date=today(),
            category="OTHER",
            status="PENDING",
            auto_generated=False,
            assigned_user_id=assignee.user_id,
            assigned_role_id=role_id,
        )
        db.add(task)
        await db.commit()

        async def assert_visibility(expected: bool, availability: str) -> None:
            result: bool | MultipleResultsFound
            try:
                result = await visible_to(db, task, viewer, farm, peer, lock_assignee=True)
            except MultipleResultsFound as exc:
                result = exc
            assert result is expected, (
                f"A real {availability} assignee must produce boolean visibility {expected}; "
                "its unique identity must not turn the availability query into multiple rows"
            )

        await assert_visibility(False, "active")
        await db.execute(
            update(FarmMembership).where(FarmMembership.id == assignee_id).values(is_active=False)
        )
        await db.commit()
        await assert_visibility(True, "inactive")


async def test_database_rejects_pending_named_tasks_without_their_continuity_role(
    client: httpx.AsyncClient,
) -> None:
    farm_id, _role_id, assignee_id, _peer_id = await _team(client)
    async with get_sessionmaker()() as db:
        assignee = await db.get(FarmMembership, assignee_id)
        assert assignee is not None
        db.add(
            Task(
                farm_id=farm_id,
                title="Invalid roleless personal assignment",
                due_date=today(),
                category="OTHER",
                status="PENDING",
                auto_generated=False,
                assigned_user_id=assignee.user_id,
                assigned_role_id=None,
            )
        )
        with pytest.raises(IntegrityError) as blocked:
            await db.flush()
        assert unique_constraint_name(blocked.value) == "ck_tasks_user_assignment_has_role"
        await db.rollback()


async def _pinned_visibility_case(
    db: AsyncSession, farm_id: int, task_id: int, peer_id: int
) -> tuple[Task, User, Farm, FarmMembership]:
    farm = await db.get(Farm, farm_id)
    task = await db.get(Task, task_id)
    peer = await db.get(FarmMembership, peer_id)
    assert farm is not None and task is not None and peer is not None
    viewer = await db.get(User, peer.user_id)
    assert viewer is not None
    return task, viewer, farm, peer


async def _pending_named_task(farm_id: int, role_id: int, assignee_id: int) -> int:
    async with get_sessionmaker()() as db:
        assignee = await db.get(FarmMembership, assignee_id)
        assert assignee is not None
        task = Task(
            farm_id=farm_id,
            title="Concurrent continuity decision",
            due_date=today(),
            category="OTHER",
            status="PENDING",
            auto_generated=False,
            assigned_user_id=assignee.user_id,
            assigned_role_id=role_id,
        )
        db.add(task)
        await db.commit()
        return task.id


async def test_independent_pinned_visibility_readers_share_the_availability_locks(
    client: httpx.AsyncClient,
) -> None:
    farm_id, role_id, assignee_id, peer_id = await _team(client)
    task_id = await _pending_named_task(farm_id, role_id, assignee_id)
    async with get_sessionmaker()() as first, get_sessionmaker()() as second:
        first_case = await _pinned_visibility_case(first, farm_id, task_id, peer_id)
        second_case = await _pinned_visibility_case(second, farm_id, task_id, peer_id)
        assert not await visible_to(first, *first_case, lock_assignee=True)
        await second.execute(text("SET LOCAL lock_timeout = '500ms'"))
        blocked = None
        result = None
        try:
            result = await visible_to(second, *second_case, lock_assignee=True)
        except DBAPIError as exc:
            blocked = exc
        assert blocked is None, "independent availability readers must hold compatible shared locks"
        assert result is False


async def test_read_only_visibility_does_not_wait_for_an_uncommitted_lifecycle_update(
    client: httpx.AsyncClient,
) -> None:
    farm_id, role_id, assignee_id, peer_id = await _team(client)
    task_id = await _pending_named_task(farm_id, role_id, assignee_id)
    async with get_sessionmaker()() as writer, get_sessionmaker()() as reader:
        reader_case = await _pinned_visibility_case(reader, farm_id, task_id, peer_id)
        await writer.execute(
            update(FarmMembership).where(FarmMembership.id == assignee_id).values(is_active=False)
        )
        await reader.execute(text("SET LOCAL lock_timeout = '500ms'"))
        blocked = None
        result = None
        try:
            # An observational read sees the last committed active assignee;
            # only an unsafe action requests the explicit availability pins.
            result = await visible_to(reader, *reader_case)
        except DBAPIError as exc:
            blocked = exc
        assert blocked is None, "observational visibility must use the committed snapshot"
        assert result is False


class SavedOutput(BaseModel):
    value: int


@pytest.mark.parametrize(
    "key", ["", "x" * 129, "tab\tkey", "non-ascii-é"], ids=["empty", "overlong", "tab", "unicode"]
)
async def test_reusable_idempotency_execution_rejects_bad_keys_before_any_mutation(
    key: str,
) -> None:
    mutated = False

    async def mutate() -> SavedOutput:
        nonlocal mutated
        mutated = True
        return SavedOutput(value=1)

    async with get_sessionmaker()() as db:
        with pytest.raises(HTTPException) as rejected:
            await idempotency.execute_idempotent(
                db,
                http_response=Response(),
                key=key,
                farm_id=1,
                actor_id=1,
                operation="boundary.save",
                payload=SavedOutput(value=1),
                path_identity={},
                success_status=201,
                response_type=SavedOutput,
                mutate=mutate,
            )
        assert rejected.value.status_code == 422
        assert "printable non-whitespace ASCII" in rejected.value.detail
        assert not mutated
        assert not db.in_transaction()


async def test_expiry_cleanup_between_conflict_and_lookup_retries_the_fresh_claim(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client, email="idempotency-boundary-owner@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        actor_id = farm.owner_id
    calls = 0

    async def mutate() -> SavedOutput:
        nonlocal calls
        calls += 1
        return SavedOutput(value=calls)

    async def invoke(db: Any, response: Response) -> SavedOutput:
        return await idempotency.execute_idempotent(
            db,
            http_response=response,
            key="expiry-cleanup-boundary",
            farm_id=farm_id,
            actor_id=actor_id,
            operation="boundary.save",
            payload=SavedOutput(value=42),
            path_identity={},
            success_status=201,
            response_type=SavedOutput,
            mutate=mutate,
        )

    async with get_sessionmaker()() as db:
        assert (await invoke(db, Response())).value == 1
        await db.execute(
            update(IdempotencyRecord)
            .where(IdempotencyRecord.operation == "boundary.save")
            .values(expires_at=utcnow() - timedelta(seconds=1))
        )
        await db.commit()

    claims = 0
    purged = 0
    async with get_sessionmaker()() as db:
        original_execute = db.execute

        async def execute(statement: Any, *args: Any, **kwargs: Any) -> Any:
            nonlocal claims, purged
            result = await original_execute(statement, *args, **kwargs)
            if isinstance(statement, Insert):
                claims += 1
                if claims == 1:
                    # Schedule the actual retention worker at the supported
                    # READ COMMITTED gap. No database result is fabricated.
                    async with get_sessionmaker()() as cleanup:
                        purged = await idempotency.purge_expired_idempotency_records(
                            cleanup, batch_size=1
                        )
                        await cleanup.commit()
            return result

        monkeypatch.setattr(db, "execute", execute)
        error = None
        output = None
        try:
            output = await invoke(db, Response())
        except HTTPException as exc:
            error = exc
        assert error is None, "a cleaned-up conflict must retry its fresh claim"
        assert output is not None and output.value == 2
        assert claims == 2 and purged == 1

    async with get_sessionmaker()() as db:
        replay_response = Response()
        assert (await invoke(db, replay_response)).value == 2
        assert replay_response.headers["Idempotency-Replayed"] == "true"
        assert calls == 2
        assert (
            await db.execute(
                select(IdempotencyRecord).where(IdempotencyRecord.operation == "boundary.save")
            )
        ).scalar_one().response_body == {"value": 2}
