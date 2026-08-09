"""Account-deletion tombstone and lifecycle-race regression tests.

Deletion removes every reusable credential by atomically tombstoning ``User``.
Membership anchors are retained and deactivated later in finite batches. Those
rows are intentional: immutable farm history and personal-task foreign keys
keep a stable pseudonymous actor/role without allowing authentication.
"""

import asyncio
import re

import httpx
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.db import get_engine, get_sessionmaker
from app.deps import deactivate_deleted_user_memberships
from app.main import create_app
from app.models import Farm, FarmMembership, RefreshSession, Role, Task, User
from app.security import verify_password
from app.utils import today, utcnow

from .conftest import owner_with_farm

WORKER_PASSWORD = "workerpass123"
REFRESH_COOKIE = get_settings().refresh_cookie_name


async def create_worker(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    *,
    email: str,
    role_code: str = "CLEANER_MANAGER",
    name: str = "Departing Worker",
) -> tuple[dict, dict]:
    team = await client.get("/api/team", headers=owner)
    assert team.status_code == 200, team.text
    role = next(row for row in team.json()["roles"] if row["code"] == role_code)
    created = await client.post(
        "/api/team/workers",
        json={
            "email": email,
            "name": name,
            "password": WORKER_PASSWORD,
            "role_id": role["id"],
        },
        headers=owner,
    )
    assert created.status_code == 201, created.text
    return created.json(), role


async def login_worker(
    client: httpx.AsyncClient, owner: dict[str, str], email: str
) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login",
        json={"email": email, "password": WORKER_PASSWORD},
    )
    assert response.status_code == 200, response.text
    return {
        "Authorization": f"Bearer {response.json()['access_token']}",
        "X-Farm-Id": owner["X-Farm-Id"],
    }


async def create_task(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    title: str,
    *,
    assigned_user_id: int | None = None,
    category: str = "OTHER",
    recur_days: int | None = None,
) -> dict:
    response = await client.post(
        "/api/tasks",
        json={
            "title": title,
            "due_date": today().isoformat(),
            "category": category,
            "assigned_user_id": assigned_user_id,
            "recur_days": recur_days,
        },
        headers=owner,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def wait_for_blocked_sessions(minimum: int, timeout_seconds: float = 10.0) -> None:
    """Wait until real database lock contention reaches the expected stage."""
    for _ in range(int(timeout_seconds / 0.01)):
        async with get_sessionmaker()() as db:
            blocked = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                    )
                )
            ).scalar_one()
        if blocked >= minimum:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"expected at least {minimum} lock-blocked database sessions")


def second_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()),
        base_url="http://test",
    )


async def test_deleted_membership_cleanup_is_exactly_batched() -> None:
    async with get_sessionmaker()() as db:
        owner = User(
            email="cleanup-owner@farm.in",
            password_hash="argon2-placeholder",
        )
        db.add(owner)
        await db.flush()
        farm = Farm(name="Cleanup Farm", owner_id=owner.id, timezone="Asia/Kolkata")
        db.add(farm)
        await db.flush()
        role = Role(
            farm_id=farm.id,
            code=None,
            name="Cleanup Role",
            permissions="[]",
        )
        db.add(role)
        await db.flush()
        deleted_users = [
            User(
                email=f"deleted-{index}-{index:032x}@deleted.invalid",
                name=None,
                password_hash="argon2-placeholder",
                deleted_at=utcnow(),
            )
            for index in range(1, 8)
        ]
        db.add_all(deleted_users)
        await db.flush()
        db.add_all(
            [
                FarmMembership(
                    farm_id=farm.id,
                    user_id=deleted_user.id,
                    role_id=role.id,
                    is_active=True,
                )
                for deleted_user in deleted_users
            ]
        )
        await db.commit()

    async with get_sessionmaker()() as db:
        await db.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(
            row[0]
            for row in (
                await db.execute(
                    text(
                        """
                        EXPLAIN (COSTS OFF)
                        SELECT membership.id
                        FROM farm_memberships AS membership
                        JOIN (
                            SELECT deleted_user.id
                            FROM users AS deleted_user
                            WHERE deleted_user.deleted_at IS NOT NULL
                              AND EXISTS (
                                  SELECT 1
                                  FROM farm_memberships AS probe
                                  WHERE probe.user_id = deleted_user.id
                                    AND probe.is_active IS TRUE
                              )
                            ORDER BY deleted_user.id
                            LIMIT 3
                        ) AS deleted_users
                          ON deleted_users.id = membership.user_id
                        WHERE membership.is_active IS TRUE
                        ORDER BY membership.user_id, membership.id
                        LIMIT 3
                        FOR UPDATE OF membership SKIP LOCKED
                        """
                    )
                )
            ).all()
        )
        assert "ix_users_deleted_id" in plan, plan
        assert "ix_farm_memberships_active_user_id_id" in plan, plan

        assert await deactivate_deleted_user_memberships(db, batch_size=3) == 3
        await db.commit()
    async with get_sessionmaker()() as db:
        remaining = (
            await db.execute(
                select(func.count())
                .select_from(FarmMembership)
                .join(User, User.id == FarmMembership.user_id)
                .where(FarmMembership.is_active.is_(True), User.deleted_at.is_not(None))
            )
        ).scalar_one()
        assert remaining == 4
        assert await deactivate_deleted_user_memberships(db, batch_size=3) == 3
        await db.commit()
    async with get_sessionmaker()() as db:
        remaining = (
            await db.execute(
                select(func.count())
                .select_from(FarmMembership)
                .join(User, User.id == FarmMembership.user_id)
                .where(FarmMembership.is_active.is_(True), User.deleted_at.is_not(None))
            )
        ).scalar_one()
        assert remaining == 1


async def test_account_delete_request_never_queries_membership_history(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="constant-delete-owner@farm.in")
    membership, _role = await create_worker(
        client,
        owner,
        email="constant-delete-worker@farm.in",
        role_code="CLEANER",
    )
    worker = await login_worker(client, owner, "constant-delete-worker@farm.in")
    statements: list[str] = []

    def capture_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement.lower())

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        deleted = await client.request(
            "DELETE",
            "/api/auth/account",
            json={"current_password": WORKER_PASSWORD},
            headers=worker,
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)
    assert deleted.status_code == 204, deleted.text
    assert statements
    assert all("farm_memberships" not in statement for statement in statements)

    async with get_sessionmaker()() as db:
        retained = await db.get(FarmMembership, membership["id"])
        assert retained is not None and retained.is_active is True


async def test_delete_tombstones_identity_and_preserves_task_attribution(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(
        client,
        email="tombstone-owner@farm.in",
        farm_name="Tombstone Farm",
    )
    original_email = "departing-manager@farm.in"
    membership, role = await create_worker(client, owner, email=original_email)
    worker_id = membership["user_id"]
    membership_id = membership["id"]
    worker = await login_worker(client, owner, original_email)
    stolen_refresh = client.cookies.get(REFRESH_COOKIE)
    assert stolen_refresh

    completed = await create_task(
        client,
        owner,
        "Historically completed",
        assigned_user_id=worker_id,
    )
    response = await client.post(f"/api/tasks/{completed['id']}/complete", headers=worker)
    assert response.status_code == 200, response.text
    assert response.json()["completed_by_id"] == worker_id

    skipped = await create_task(
        client,
        owner,
        "Historically skipped",
        assigned_user_id=worker_id,
    )
    response = await client.post(
        f"/api/tasks/{skipped['id']}/skip",
        json={"reason": "Recorded before departure"},
        headers=worker,
    )
    assert response.status_code == 200, response.text
    assert response.json()["skipped_by_id"] == worker_id

    verified = await create_task(client, owner, "Historically verified", category="CLEANING")
    response = await client.post(f"/api/tasks/{verified['id']}/complete", headers=owner)
    assert response.status_code == 200, response.text
    response = await client.post(f"/api/tasks/{verified['id']}/verify", headers=worker)
    assert response.status_code == 200, response.text
    assert response.json()["verified_by_id"] == worker_id

    pending = await create_task(
        client,
        owner,
        "Pending personal assignment",
        assigned_user_id=worker_id,
    )

    async with get_sessionmaker()() as db:
        original = await db.get(User, worker_id)
        assert original is not None
        original_password_hash = original.password_hash
        assert original.email == original_email
        assert original.name == "Departing Worker"

    deleted = await client.request(
        "DELETE",
        "/api/auth/account",
        json={"current_password": WORKER_PASSWORD},
        headers=worker,
    )
    assert deleted.status_code == 204, deleted.text
    assert client.cookies.get(REFRESH_COOKIE) is None

    # Every previously issued credential is dead, including a copied refresh
    # token that is no longer present in the deleting browser's cookie jar.
    assert (await client.get("/api/auth/me", headers=worker)).status_code == 401
    login = await client.post(
        "/api/auth/login",
        json={"email": original_email, "password": WORKER_PASSWORD},
    )
    assert login.status_code == 401
    client.cookies.clear()
    client.cookies.set(REFRESH_COOKIE, stolen_refresh, domain="test.local", path="/")
    refresh = await client.post("/api/auth/refresh")
    assert refresh.status_code == 401

    async with get_sessionmaker()() as db:
        tombstone = await db.get(User, worker_id)
        assert tombstone is not None
        assert tombstone.deleted_at is not None
        assert tombstone.name is None
        assert tombstone.email != original_email
        assert re.fullmatch(
            rf"deleted-{worker_id}-[0-9a-f]{{32}}@deleted\.invalid",
            tombstone.email,
        )
        assert tombstone.password_hash != original_password_hash
        valid_old_password, _ = verify_password(WORKER_PASSWORD, tombstone.password_hash)
        assert valid_old_password is False
        assert tombstone.display_name == "Deleted account"

        retained_membership = await db.get(FarmMembership, membership_id)
        assert retained_membership is not None
        assert retained_membership.is_active is True
        assert retained_membership.role_id == role["id"]
        refresh_sessions = list(
            (
                await db.execute(select(RefreshSession).where(RefreshSession.user_id == worker_id))
            ).scalars()
        )
        assert refresh_sessions == []

        tasks = {
            task.id: task
            for task in (
                await db.execute(
                    select(Task)
                    .where(
                        Task.id.in_([completed["id"], skipped["id"], verified["id"], pending["id"]])
                    )
                    .options(
                        selectinload(Task.completed_by),
                        selectinload(Task.skipped_by),
                        selectinload(Task.verified_by),
                    )
                )
            ).scalars()
        }
        completed_row = tasks[completed["id"]]
        assert completed_row.completed_by_id == worker_id
        assert completed_row.completed_by is tombstone
        assert completed_row.completed_by is not None
        assert completed_row.completed_by.display_name == "Deleted account"
        assert completed_row.assigned_user_id == worker_id
        assert completed_row.assigned_role_id == role["id"]

        assert tasks[skipped["id"]].skipped_by_id == worker_id
        assert tasks[skipped["id"]].skipped_by is tombstone
        assert tasks[skipped["id"]].assigned_user_id == worker_id
        assert tasks[skipped["id"]].assigned_role_id == role["id"]

        assert tasks[verified["id"]].verified_by_id == worker_id
        assert tasks[verified["id"]].verified_by is tombstone

        pending_row = tasks[pending["id"]]
        assert pending_row.status == "PENDING"
        assert pending_row.assigned_user_id == worker_id
        assert pending_row.assigned_role_id == role["id"]
        assert await deactivate_deleted_user_memberships(db, batch_size=10) == 1
        await db.commit()
        await db.refresh(retained_membership)
        assert retained_membership.is_active is False

    # The scrubbed address releases the original identifier for a genuinely
    # new account; historical actor rows must continue pointing at the old id.
    re_registered = await client.post(
        "/api/auth/register",
        json={
            "email": original_email,
            "password": "brandnewpass123",
            "name": "New Account Holder",
        },
    )
    assert re_registered.status_code == 201, re_registered.text
    new_user_id = re_registered.json()["user"]["id"]
    assert new_user_id != worker_id
    async with get_sessionmaker()() as db:
        historical_task = await db.get(Task, completed["id"])
        old_identity = await db.get(User, worker_id)
        new_identity = await db.get(User, new_user_id)
        assert historical_task is not None
        assert historical_task.completed_by_id == worker_id
        assert old_identity is not None and old_identity.deleted_at is not None
        assert new_identity is not None and new_identity.deleted_at is None
        assert new_identity.email == original_email


async def test_manual_assignment_queued_behind_deletion_cannot_recreate_live_reference(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="assignment-race-owner@farm.in")
    original_email = "assignment-racer@farm.in"
    membership, _role = await create_worker(
        client,
        owner,
        email=original_email,
        role_code="CLEANER",
    )
    worker_id = membership["user_id"]
    worker = await login_worker(client, owner, original_email)

    holder = get_sessionmaker()()
    # Account deletion and new personal assignment now serialize directly on
    # the target User. No request enumerates or locks all Membership rows.
    await holder.execute(select(User).where(User.id == worker_id).with_for_update())
    deletion = asyncio.create_task(
        client.request(
            "DELETE",
            "/api/auth/account",
            json={"current_password": WORKER_PASSWORD},
            headers=worker,
        )
    )
    assignment: asyncio.Task[httpx.Response] | None = None
    try:
        await wait_for_blocked_sessions(1)
        async with second_client() as other:
            assignment = asyncio.create_task(
                other.post(
                    "/api/tasks",
                    json={
                        "title": "Must not dangle",
                        "due_date": today().isoformat(),
                        "assigned_user_id": worker_id,
                    },
                    headers=owner,
                )
            )
            await wait_for_blocked_sessions(2)
            await holder.commit()
            deleted, assigned = await asyncio.gather(deletion, assignment)
    finally:
        await holder.rollback()
        await holder.close()
        for request in (deletion, assignment):
            if request is not None and not request.done():
                request.cancel()

    assert deleted.status_code == 204, deleted.text
    assert assigned.status_code == 400, assigned.text
    assert assigned.json()["detail"] == "Assigned worker is not an active member of this farm"
    async with get_sessionmaker()() as db:
        retained_membership = await db.get(FarmMembership, membership["id"])
        assert retained_membership is not None
        assert retained_membership.is_active is True
        dangling = list(
            (await db.execute(select(Task).where(Task.assigned_user_id == worker_id))).scalars()
        )
        assert dangling == []
        assert await deactivate_deleted_user_memberships(db, batch_size=10) == 1
        await db.commit()
        await db.refresh(retained_membership)
        assert retained_membership.is_active is False


async def test_recurring_completion_racing_deletion_retains_inactive_assignment_anchor(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="recurrence-race-owner@farm.in")
    original_email = "recurrence-racer@farm.in"
    membership, role = await create_worker(
        client,
        owner,
        email=original_email,
        role_code="CLEANER",
    )
    worker_id = membership["user_id"]
    worker = await login_worker(client, owner, original_email)
    original = await create_task(
        client,
        owner,
        "Recurring race",
        assigned_user_id=worker_id,
        recur_days=1,
    )

    holder = get_sessionmaker()()
    await holder.execute(select(Task).where(Task.id == original["id"]).with_for_update())
    async with second_client() as other:
        completion = asyncio.create_task(
            other.post(f"/api/tasks/{original['id']}/complete", headers=worker)
        )
        deletion: asyncio.Task[httpx.Response] | None = None
        try:
            await wait_for_blocked_sessions(1)
            deletion = asyncio.create_task(
                client.request(
                    "DELETE",
                    "/api/auth/account",
                    json={"current_password": WORKER_PASSWORD},
                    headers=worker,
                )
            )
            await wait_for_blocked_sessions(2)
            await holder.commit()
            completed, deleted = await asyncio.gather(completion, deletion)
        finally:
            await holder.rollback()
            await holder.close()
            for request in (completion, deletion):
                if request is not None and not request.done():
                    request.cancel()

    assert completed.status_code == 200, completed.text
    assert deleted.status_code == 204, deleted.text
    async with get_sessionmaker()() as db:
        series = list(
            (
                await db.execute(
                    select(Task)
                    .where(Task.recurring_series_id == original["recurring_series_id"])
                    .order_by(Task.id)
                )
            ).scalars()
        )
        assert len(series) == 2
        completed_row, successor = series
        assert completed_row.id == original["id"]
        assert completed_row.status == "DONE"
        assert completed_row.completed_by_id == worker_id
        assert completed_row.assigned_user_id == worker_id
        assert completed_row.assigned_role_id == role["id"]
        assert successor.status == "PENDING"
        assert successor.assigned_user_id == worker_id
        assert successor.assigned_role_id == role["id"]
        retained_membership = await db.get(FarmMembership, membership["id"])
        assert retained_membership is not None
        assert retained_membership.is_active is True
        assert await deactivate_deleted_user_memberships(db, batch_size=10) == 1
        await db.commit()
        await db.refresh(retained_membership)
        assert retained_membership.is_active is False
