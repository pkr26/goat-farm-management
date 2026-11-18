"""Worker creation must retain its chosen live Role through membership commit."""

import asyncio
import json
import sys
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from types import CodeType, CoroutineType
from typing import Any, cast

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import security
from app.api import team as team_api
from app.db import get_sessionmaker
from app.models import Farm, Role, User

from .conftest import owner_with_farm
from .type_helpers import json_int, json_object, json_objects


async def test_worker_creation_pins_its_live_role_until_membership_commit(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Delegate real operations and gate only the final post-hash Role read.

    Gate and observation waits have local deadlines. Cleanup releases the gate
    and drains all actual requests, hash children, and native Futures without
    cancellation. That drain has no local deadline; any future executable
    profile must supply an external phase budget. No runtime is approved here.
    """
    owner = await owner_with_farm(client, email="create-role-pin-owner@farm.in")
    me = await client.get("/api/auth/me", headers=owner)
    assert me.status_code == 200, "The real owner identity must be readable"
    owner_id = json_int(json_object(me.json()).get("id"))
    farm_id = int(owner["X-Farm-Id"])
    created_role = await client.post(
        "/api/team/roles",
        headers=owner,
        json={"name": "Creation destination", "permissions": ["tasks.view"]},
    )
    assert created_role.status_code == 201, "A valid custom Role must be publicly creatable"
    destination = json_int(json_object(created_role.json()).get("id"))
    worker_email = "create-role-pin-worker@farm.in"
    transport = client._transport
    if not isinstance(transport, httpx.ASGITransport):
        raise RuntimeError("The owned clients require the actual fixture application")

    real_role = team_api._get_role
    real_delete_code = team_api.delete_role.__code__
    mutate_codes = [
        value
        for value in team_api._create_worker_after_idempotency_gate.__code__.co_consts
        if isinstance(value, CodeType) and value.co_name == "mutate"
    ]
    if len(mutate_codes) != 1:
        raise RuntimeError("The real create-worker mutation must have one identifiable code body")
    final_mutate_code = mutate_codes[0]
    real_async_hash = cast(
        "Callable[[str], Coroutine[Any, Any, str]]", vars(team_api)["hash_password_async"]
    )
    if real_async_hash is not security.hash_password_async:
        raise RuntimeError("Team hashing must use the actual production async hash")
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
    entered, resume, deleting = asyncio.Event(), asyncio.Event(), asyncio.Event()
    creator_pid: int | None = None
    deleter_pid: int | None = None
    final_reads = 0
    delete_reads = 0
    schedule: str | None = None
    primary: BaseException | None = None
    errors: list[BaseException] = []

    def observed_submit[ResultT, **P](
        work: Callable[P, ResultT], /, *args: P.args, **kwargs: P.kwargs
    ) -> Future[ResultT]:
        actual = real_submit(work, *args, **kwargs)
        native_futures.append(cast("Future[object]", actual))
        return actual

    def observed_async_hash(password: str) -> Coroutine[Any, Any, str]:
        async def delegate() -> str:
            actual_task = asyncio.current_task()
            if actual_task is None:
                raise RuntimeError("A real production hash must have its actual owning task")
            hash_tasks.append(cast("asyncio.Task[object]", actual_task))
            return await real_async_hash(password)

        actual_coroutine = delegate()
        hash_coroutines.append(actual_coroutine)
        return actual_coroutine

    def observed_callback(actual_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        error = context.get("exception")
        callback_errors.append(
            error
            if isinstance(error, BaseException)
            else RuntimeError("An owned-work loop callback reported an unexpected failure")
        )
        if original_loop_handler is None:
            actual_loop.default_exception_handler(context)
        else:
            original_loop_handler(actual_loop, context)

    async def observed_role(
        db: AsyncSession,
        farm: Farm,
        target_id: int,
        *,
        for_update: bool = False,
        no_key_update: bool = False,
        for_share: bool = False,
    ) -> Role:
        nonlocal creator_pid, deleter_pid, final_reads, delete_reads
        frame = sys._getframe(1)
        caller_code = frame.f_code
        actor = frame.f_locals.get("user")
        final_creator = (
            caller_code is final_mutate_code
            and frame.f_locals.get("email") == worker_email
            and isinstance(actor, User)
            and actor.id == owner_id
            and farm.id == farm_id
            and target_id == destination
        )
        owned_deletion = (
            caller_code is real_delete_code
            and isinstance(actor, User)
            and actor.id == owner_id
            and farm.id == farm_id
            and target_id == destination
        )
        del frame
        if owned_deletion:
            delete_reads += 1
            deleter_pid = await db.scalar(text("SELECT pg_backend_pid()"))
            if not isinstance(deleter_pid, int):
                raise RuntimeError("The actual role DELETE backend must be observable")
            deleting.set()
        actual = await real_role(
            db,
            farm,
            target_id,
            for_update=for_update,
            no_key_update=no_key_update,
            for_share=for_share,
        )
        if final_creator:
            final_reads += 1
            if (
                final_reads != 1
                or not hash_tasks
                or not native_futures
                or any(not task.done() or task.cancelled() for task in hash_tasks)
                or any(not future.done() or future.cancelled() for future in native_futures)
            ):
                raise RuntimeError("The final Role observation must follow completed real hashing")
            creator_pid = await db.scalar(text("SELECT pg_backend_pid()"))
            if not isinstance(creator_pid, int):
                raise RuntimeError("The actual worker-create backend must be observable")
            entered.set()
            await asyncio.wait_for(resume.wait(), timeout=30)
        return actual

    async def wait_for_boundary(
        event: asyncio.Event, request: asyncio.Task[httpx.Response]
    ) -> None:
        waiter = asyncio.create_task(event.wait())
        try:
            await asyncio.wait({waiter, request}, timeout=10, return_when=asyncio.FIRST_COMPLETED)
            if not event.is_set():
                if request.done():
                    request.result()  # Preserve an actual request exception, if present.
                raise RuntimeError("The actual owned request did not reach its declared boundary")
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
        await asyncio.sleep(0)  # Let the real owner-reservation done callback finish.
        errors.extend(outcome for outcome in outcomes if isinstance(outcome, BaseException))
        errors.extend(callback_errors)
        for coroutine in hash_coroutines:
            if not isinstance(coroutine, CoroutineType):
                errors.append(RuntimeError("An observed real hash coroutine had an unknown type"))
            elif coroutine.cr_frame is not None:
                coroutine.close()
                errors.append(RuntimeError("An actual hash coroutine had no completed owner"))
        if len(hash_tasks) != len(hash_coroutines):
            errors.append(RuntimeError("Every actual hash child must be retained and drained"))

    with monkeypatch.context() as observer:
        observer.setattr(team_api, "_get_role", observed_role)
        observer.setattr(team_api, "hash_password_async", observed_async_hash)
        observer.setitem(vars(executor), "submit", observed_submit)
        loop.set_exception_handler(observed_callback)
        try:
            async with (
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=transport.app), base_url="http://test"
                ) as creator,
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=transport.app), base_url="http://test"
                ) as remover,
            ):
                creation = asyncio.create_task(
                    creator.post(
                        "/api/team/workers",
                        headers=owner | {"Idempotency-Key": "worker-final-role-pin"},
                        json={
                            "email": worker_email,
                            "password": "workerpass123",
                            "role_id": destination,
                        },
                    )
                )
                requests.append(creation)
                try:
                    await wait_for_boundary(entered, creation)
                    deletion = asyncio.create_task(
                        remover.delete(f"/api/team/roles/{destination}", headers=owner)
                    )
                    requests.append(deletion)
                    await wait_for_boundary(deleting, deletion)
                    if creator_pid is None or deleter_pid is None or creator_pid == deleter_pid:
                        raise RuntimeError("The two actual requests must own distinct DB backends")
                    async with asyncio.timeout(10):
                        while not deletion.done():
                            async with get_sessionmaker()() as probe:
                                role_wait = await probe.scalar(
                                    text(
                                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                        "WHERE datname = current_database() AND pid = :deleter "
                                        "AND :creator = ANY(pg_blocking_pids(pid)) "
                                        "AND wait_event_type = 'Lock' "
                                        "AND query LIKE '%roles%' AND query LIKE '%FOR UPDATE%')"
                                    ),
                                    {"creator": creator_pid, "deleter": deleter_pid},
                                )
                            if role_wait is True:
                                schedule = "owned_role_update_wait"
                                break
                            await asyncio.sleep(0.01)
                        if schedule is None:
                            deletion.result()  # Unknown application exceptions remain errors.
                            schedule = "actual_delete_completed_before_creator_resume"
                except BaseException as error:
                    primary = error
                finally:
                    resume.set()
                    await drain_owned()
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
        raise BaseExceptionGroup("Actual worker-create/delete work or drain failed", unique_errors)
    restored = (
        team_api._get_role is real_role
        and vars(team_api)["hash_password_async"] is real_async_hash
        and vars(executor).get("submit", missing) is original_submit_attribute
        and executor.submit == real_submit
        and loop.get_exception_handler() is original_loop_handler
    )
    if (
        not restored
        or len(requests) != 2
        or final_reads != 1
        or delete_reads != 1
        or schedule is None
        or any(not task.done() or task.cancelled() for task in [*requests, *hash_tasks])
        or any(not future.done() or future.cancelled() for future in native_futures)
    ):
        raise RuntimeError("Actual ownership, observation, drain, or restoration was incomplete")
    created, deleted = (task.result() for task in requests)
    roster = await client.get("/api/team", headers=owner)
    assert roster.status_code == 200, "The real owner must read the completed roster"
    body = json_object(roster.json())
    members = json_objects(body.get("memberships"))
    roles = json_objects(body.get("roles"))
    owned = [member for member in members if member.get("email") == worker_email]
    linked = len(owned) == 1 and owned[0].get("role_id") == destination
    role_visible = any(role.get("id") == destination for role in roles)
    observations = {
        "contract": "worker_creation_retains_live_role_through_membership_commit",
        "creator_status": created.status_code,
        "delete_status": deleted.status_code,
        "creator_backend": creator_pid,
        "deleter_backend": deleter_pid,
        "schedule": schedule,
        "final_role_reads": final_reads,
        "delete_role_reads": delete_reads,
        "actual_hash_children": len(hash_tasks),
        "actual_native_futures": len(native_futures),
        "all_requests_drained": all(task.done() for task in requests),
        "all_hash_children_drained": all(task.done() for task in hash_tasks),
        "all_native_futures_drained": all(future.done() for future in native_futures),
        "observers_restored": restored,
        "public_membership_links_role": linked,
        "public_role_visible": role_visible,
    }
    with capsys.disabled():
        print("WORKER_CREATE_ROLE_PIN " + json.dumps(observations, sort_keys=True), flush=True)
    assert created.status_code == 201, "A valid worker must commit with its selected live Role"
    assert deleted.status_code == 400, "A Role assigned by the concurrent create must stay in use"
    assert deleted.json()["detail"] == "Role still has workers assigned — reassign them first."
    assert linked, "The public roster must retain the created worker's selected Role"
    assert role_visible, "The selected Role must remain live after the in-use deletion refusal"
