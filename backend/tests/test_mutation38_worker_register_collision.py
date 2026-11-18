"""A genuine public registration wins before worker creation's final User flush.

Source-only proposal on captured applied207b. This is a real database race;
qualification, collection, runtime and application remain separately reviewed.
"""

import asyncio
import json
from collections.abc import Callable, Coroutine, Sequence
from concurrent.futures import Future
from types import CoroutineType
from typing import Any, cast

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import security
from app.api import team as team_api
from app.db import get_sessionmaker
from app.models import FarmMembership, User

from .conftest import owner_with_farm
from .type_helpers import json_int, json_object, json_objects


async def test_worker_create_returns_controlled_duplicate_after_public_registration(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Gate one actual worker User flush, then let public registration commit.

    Every wrapper delegates the real callable with unchanged arguments/result.
    Local observation waits are bounded. Finally releases the gate and drains
    real HTTP requests, shielded hash children and native Futures without
    cancelling them. That drain requires a separately reviewed external budget.
    """
    owner = await owner_with_farm(client, email="worker-register-owner@farm.in")
    roster = await client.get("/api/team", headers=owner)
    assert roster.status_code == 200, "A real owner must read the available team roles"
    roles = json_objects(json_object(roster.json()).get("roles"))
    role_ids = [json_int(role.get("id")) for role in roles if role.get("code") == "CLEANER"]
    assert len(role_ids) == 1, "The public farm roster must expose its CLEANER role"
    role_id = role_ids[0]
    email = "worker-register-collision@farm.in"
    worker_password = "worker-collision-password123"
    register_password = "independent-register-password123"
    async with get_sessionmaker()() as db:
        email_indexes = list(
            (
                await db.execute(
                    text(
                        "SELECT index_class.relname FROM pg_index AS definition "
                        "JOIN pg_class AS index_class "
                        "ON index_class.oid = definition.indexrelid "
                        "JOIN pg_attribute AS attribute "
                        "ON attribute.attrelid = definition.indrelid "
                        "AND attribute.attnum = ANY(definition.indkey) "
                        "WHERE definition.indrelid = 'users'::regclass "
                        "AND definition.indisunique AND definition.indnkeyatts = 1 "
                        "AND attribute.attname = 'email'"
                    )
                )
            ).scalars()
        )
    if len(email_indexes) != 1 or not isinstance(email_indexes[0], str):
        raise RuntimeError("The actual User email unique index must be identifiable")
    email_index = email_indexes[0]
    transport = client._transport
    if not isinstance(transport, httpx.ASGITransport):
        raise RuntimeError("Owned browsers require the actual fixture application")

    real_flush = AsyncSession.flush
    real_hash = cast(
        "Callable[[str], Coroutine[Any, Any, str]]", vars(team_api)["hash_password_async"]
    )
    if real_hash is not security.hash_password_async:
        raise RuntimeError("Team hashing must delegate the actual production async hash")
    executor = security._password_executor
    real_submit = executor.submit
    missing = object()
    original_submit_attribute = vars(executor).get("submit", missing)
    loop = asyncio.get_running_loop()
    original_loop_handler = loop.get_exception_handler()
    callback_errors: list[BaseException] = []
    native_futures: list[Future[object]] = []
    hash_coroutines: list[Coroutine[Any, Any, str]] = []
    hash_tasks: list[asyncio.Task[object]] = []
    requests: list[asyncio.Task[httpx.Response]] = []
    entered, resume = asyncio.Event(), asyncio.Event()
    worker_flushes = 0
    actual_collision: IntegrityError | None = None
    collision_sqlstate: str | None = None
    collision_constraint: str | None = None
    registration_completed_before_resume = False
    registered_id: int | None = None
    registered_hash: str | None = None
    primary: BaseException | None = None
    errors: list[BaseException] = []

    def observed_submit[ResultT, **P](
        work: Callable[P, ResultT], /, *args: P.args, **kwargs: P.kwargs
    ) -> Future[ResultT]:
        actual = real_submit(work, *args, **kwargs)
        native_futures.append(cast("Future[object]", actual))
        return actual

    def observed_team_hash(password: str) -> Coroutine[Any, Any, str]:
        async def delegate() -> str:
            task = asyncio.current_task()
            if task is None:
                raise RuntimeError("Actual team hash work must retain its owning task")
            hash_tasks.append(cast("asyncio.Task[object]", task))
            return await real_hash(password)

        coroutine = delegate()
        hash_coroutines.append(coroutine)
        return coroutine

    def observed_callback(actual_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        observed = context.get("exception")
        callback_errors.append(
            observed
            if isinstance(observed, BaseException)
            else RuntimeError("A real owned-work loop callback reported an unknown failure")
        )
        if original_loop_handler is None:
            actual_loop.default_exception_handler(context)
        else:
            original_loop_handler(actual_loop, context)

    async def observed_flush(session: AsyncSession, objects: Sequence[Any] | None = None) -> None:
        nonlocal worker_flushes, actual_collision, collision_sqlstate, collision_constraint
        candidates = [
            item
            for item in session.new
            if isinstance(item, User) and item.email == email and item.must_change_password is True
        ]
        if not candidates:
            await real_flush(session, objects)
            return
        worker_flushes += 1
        if len(candidates) != 1 or worker_flushes != 1:
            raise RuntimeError("Exactly one actual password-worker User insert must reach the gate")
        if (
            not hash_tasks
            or not native_futures
            or any(not task.done() or task.cancelled() for task in hash_tasks)
            or any(not future.done() or future.cancelled() for future in native_futures)
        ):
            raise RuntimeError("The final worker User flush must follow completed genuine hashing")
        # Production has already run the final real email SELECT and added
        # this User. No SQL/result/exception is substituted by this gate.
        entered.set()
        await asyncio.wait_for(resume.wait(), timeout=30)
        try:
            await real_flush(session, objects)
        except IntegrityError as observed:
            actual_collision = observed
            chain: BaseException | None = observed
            seen: set[int] = set()
            while chain is not None and id(chain) not in seen:
                seen.add(id(chain))
                state = getattr(chain, "sqlstate", None)
                constraint = getattr(chain, "constraint_name", None)
                if isinstance(state, str):
                    collision_sqlstate = state
                if isinstance(constraint, str):
                    collision_constraint = constraint
                chain = chain.__cause__
            raise

    async def wait_for_worker_boundary(request: asyncio.Task[httpx.Response]) -> None:
        waiter = asyncio.create_task(entered.wait())
        try:
            await asyncio.wait({waiter, request}, timeout=10, return_when=asyncio.FIRST_COMPLETED)
            if not entered.is_set():
                if request.done():
                    request.result()  # Preserve an actual application exception.
                raise RuntimeError("The real worker request did not reach its declared User flush")
        finally:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)

    async def drain_owned() -> None:
        outcomes: list[object] = list(await asyncio.gather(*requests, return_exceptions=True))
        for task in asyncio.all_tasks():
            if any(task.get_coro() is coroutine for coroutine in hash_coroutines) and all(
                task is not previous for previous in hash_tasks
            ):
                hash_tasks.append(cast("asyncio.Task[object]", task))
        outcomes.extend(await asyncio.gather(*hash_tasks, return_exceptions=True))
        outcomes.extend(
            await asyncio.gather(
                *(asyncio.wrap_future(future) for future in native_futures),
                return_exceptions=True,
            )
        )
        await asyncio.sleep(0)  # Allow the real owner-reservation callback to finish.
        errors.extend(outcome for outcome in outcomes if isinstance(outcome, BaseException))
        errors.extend(callback_errors)
        for coroutine in hash_coroutines:
            if not isinstance(coroutine, CoroutineType):
                errors.append(RuntimeError("A real observed hash coroutine has an unknown type"))
            elif coroutine.cr_frame is not None:
                coroutine.close()
                errors.append(RuntimeError("A real hash coroutine had no completed owning task"))
        if len(hash_tasks) != len(hash_coroutines):
            errors.append(RuntimeError("Every real team hash child must be retained and drained"))

    async def finish_without_cancelling_work() -> None:
        drain = asyncio.create_task(drain_owned())
        while not drain.done():
            try:
                await asyncio.shield(drain)
            except asyncio.CancelledError as observed:
                errors.append(observed)
        drain.result()

    with monkeypatch.context() as observer:
        observer.setattr(AsyncSession, "flush", observed_flush)
        observer.setattr(team_api, "hash_password_async", observed_team_hash)
        # Preserve descriptor absence: setattr(instance, inherited_method)
        # would leave an instance shadow after pytest's undo.
        observer.setitem(vars(executor), "submit", observed_submit)
        loop.set_exception_handler(observed_callback)
        try:
            async with (
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=transport.app, raise_app_exceptions=True),
                    base_url="http://test",
                    event_hooks=client.event_hooks,
                ) as creator,
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=transport.app, raise_app_exceptions=True),
                    base_url="http://test",
                    event_hooks=client.event_hooks,
                ) as registrar,
            ):
                creation = asyncio.create_task(
                    creator.post(
                        "/api/team/workers",
                        headers=owner,
                        json={
                            "email": email,
                            "name": "Uncommitted password worker",
                            "password": worker_password,
                            "role_id": role_id,
                        },
                    )
                )
                requests.append(creation)
                try:
                    await wait_for_worker_boundary(creation)
                    registration = asyncio.create_task(
                        registrar.post(
                            "/api/auth/register",
                            json={
                                "email": email,
                                "name": "Independent registered account",
                                "password": register_password,
                            },
                        )
                    )
                    requests.append(registration)
                    registered = await asyncio.wait_for(asyncio.shield(registration), timeout=20)
                    assert registered.status_code == 201, (
                        "The independent public registration must commit before worker resume"
                    )
                    registered_id = json_int(
                        json_object(json_object(registered.json()).get("user")).get("id")
                    )
                    async with get_sessionmaker()() as db:
                        winners = list(
                            (await db.execute(select(User).where(User.email == email))).scalars()
                        )
                        assert len(winners) == 1, "Public registration must commit one real User"
                        assert winners[0].id == registered_id
                        registered_hash = winners[0].password_hash
                    if creation.done():
                        creation.result()
                        raise RuntimeError("The worker resumed before the explicit gate release")
                    registration_completed_before_resume = True
                except BaseException as observed:
                    primary = observed
                finally:
                    resume.set()
                    await finish_without_cancelling_work()
        finally:
            loop.set_exception_handler(original_loop_handler)

    if primary is not None:
        errors.insert(0, primary)
    unique_errors: list[BaseException] = []
    for observed_error in errors:
        if all(observed_error is not previous for previous in unique_errors):
            unique_errors.append(observed_error)
    if unique_errors:
        if len(unique_errors) == 1:
            raise unique_errors[0]
        raise BaseExceptionGroup("Actual worker/register work or drain failed", unique_errors)
    restored = (
        AsyncSession.flush is real_flush
        and vars(team_api)["hash_password_async"] is real_hash
        and vars(executor).get("submit", missing) is original_submit_attribute
        and executor.submit == real_submit
        and loop.get_exception_handler() is original_loop_handler
    )
    if (
        not restored
        or len(requests) != 2
        or worker_flushes != 1
        or not registration_completed_before_resume
        or any(not task.done() or task.cancelled() for task in [*requests, *hash_tasks])
        or any(not future.done() or future.cancelled() for future in native_futures)
    ):
        raise RuntimeError(
            "Real request/hash/native ownership or observer restoration was incomplete"
        )
    if actual_collision is None:
        raise RuntimeError(
            "The actual worker User insert did not encounter a real uniqueness error"
        )
    if collision_sqlstate != "23505" or collision_constraint != email_index:
        raise actual_collision  # Preserve an unknown actual database error unchanged.

    created, registered = (task.result() for task in requests)
    assert registered.status_code == 201, (
        "The independent registered account must remain the winner"
    )
    created_body = json_object(created.json())
    assert created_body.get("detail") == team_api.CANT_ADD_TO_TEAM, (
        "A concurrent existing email must retain the non-enumerating team rejection"
    )
    after = await client.get("/api/team", headers=owner)
    assert after.status_code == 200, "The owner roster must remain usable after collision rollback"
    members = json_objects(json_object(after.json()).get("memberships"))
    assert not any(member.get("email") == email for member in members), (
        "The independently registered account must not become an owner-provisioned worker"
    )
    async with get_sessionmaker()() as db:
        users = list((await db.execute(select(User).where(User.email == email))).scalars())
        assert len(users) == 1, "The real uniqueness collision must leave one account"
        assert users[0].id == registered_id
        assert users[0].deleted_at is None
        assert users[0].must_change_password is False
        password_unchanged = users[0].password_hash == registered_hash
        linked = (
            await db.execute(select(FarmMembership).where(FarmMembership.user_id == users[0].id))
        ).scalar_one_or_none()
    assert password_unchanged, "Failed worker creation must preserve the registered credential"
    assert linked is None, "Failed worker creation must not silently enroll the registered account"

    observations = {
        "contract": "worker_create_public_register_unique_email_collision",
        "worker_status": created.status_code,
        "registration_status": registered.status_code,
        "actual_worker_User_flushes": worker_flushes,
        "registration_committed_before_resume": registration_completed_before_resume,
        "actual_unique_sqlstate": collision_sqlstate,
        "actual_constraint_matches_real_email_index": collision_constraint == email_index,
        "actual_team_hash_children": len(hash_tasks),
        "actual_native_futures": len(native_futures),
        "all_requests_drained": all(task.done() for task in requests),
        "all_hash_children_drained": all(task.done() for task in hash_tasks),
        "all_native_futures_drained": all(future.done() for future in native_futures),
        "observers_restored": restored,
        "registered_password_unchanged": password_unchanged,
        "registered_account_unlinked": linked is None,
    }
    with capsys.disabled():
        print("WORKER_REGISTER_COLLISION " + json.dumps(observations, sort_keys=True), flush=True)
    assert created.status_code == 400, (
        "A real email registered before worker insertion must receive the controlled team400; "
        f"actual worker status: {created.status_code}"
    )
