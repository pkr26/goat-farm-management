"""A standing team quota is a cheap failure before genuine native Argon work."""

import asyncio
import json
import threading
from collections.abc import Callable, Coroutine, Iterator
from concurrent.futures import Future
from contextlib import contextmanager
from types import CoroutineType
from typing import Any, cast

import httpx
import pytest

from app import security
from app.api import team as team_api
from app.core.config import get_settings

from .conftest import owner_with_farm
from .type_helpers import json_int, json_object, json_objects


async def test_full_team_rejects_a_valid_worker_before_real_argon_work(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Observe real jobs and drain them before restoring the configuration.

    Each observed hash delegates the original function unchanged, then adds one
    diagnostic verification in that same native job. Native drain has no local
    timeout; a future separately approved external phase budget must bound it.
    """
    transport = client._transport
    if not isinstance(transport, httpx.ASGITransport):
        raise RuntimeError("The owned browser requires the actual fixture ASGI app")
    original_settings = get_settings().model_dump()
    executor = security._password_executor
    original_slots = security._password_slots
    real_submit = executor.submit
    real_hash = security.hash_password
    real_verify = security.verify_password
    team_symbols = vars(team_api)
    real_async_hash = cast(
        "Callable[[str], Coroutine[Any, Any, str]]", team_symbols["hash_password_async"]
    )
    real_native_work = security._run_password_work
    if real_async_hash is not security.hash_password_async:
        raise RuntimeError("Team hashing must use the actual production async hash")
    missing = object()
    original_submit_attribute = vars(executor).get("submit", missing)
    loop = asyncio.get_running_loop()
    original_loop_handler = loop.get_exception_handler()
    callback_errors: list[BaseException] = []
    loop_thread = threading.get_ident()
    lock = threading.Lock()
    requests: list[asyncio.Task[object]] = []
    hash_coroutines: list[Coroutine[Any, Any, str]] = []
    entered_hash_tasks: list[asyncio.Task[object]] = []
    drained_hash_tasks: list[asyncio.Task[object]] = []
    native_futures: list[Future[object]] = []
    hash_threads: list[int] = []
    verifications: list[bool] = []
    setup_hash_jobs: int | None = None
    response: httpx.Response | None = None
    primary_error: BaseException | None = None

    @contextmanager
    def observe_callbacks() -> Iterator[None]:
        def observed_handler(
            actual_loop: asyncio.AbstractEventLoop, context: dict[str, Any]
        ) -> None:
            error = context.get("exception")
            callback_errors.append(
                error
                if isinstance(error, BaseException)
                else RuntimeError(
                    "The event loop reported an unexpected owned-work callback failure"
                )
            )
            # Keep the actual loop diagnostic; observations never swallow it.
            try:
                if original_loop_handler is None:
                    actual_loop.default_exception_handler(context)
                else:
                    original_loop_handler(actual_loop, context)
            except BaseException as handler_error:
                callback_errors.append(handler_error)
                raise

        loop.set_exception_handler(observed_handler)
        try:
            yield
        finally:
            loop.set_exception_handler(original_loop_handler)

    async def owned[ResultT](operation: Coroutine[Any, Any, ResultT]) -> ResultT:
        task = asyncio.create_task(operation)
        requests.append(cast("asyncio.Task[object]", task))
        return await asyncio.shield(task)

    def observe_submit[ResultT, **P](
        work: Callable[P, ResultT], /, *args: P.args, **kwargs: P.kwargs
    ) -> Future[ResultT]:
        actual = real_submit(work, *args, **kwargs)
        with lock:
            native_futures.append(cast("Future[object]", actual))
        return actual

    def observe_hash(password: str) -> str:
        with lock:
            hash_threads.append(threading.get_ident())
        actual = real_hash(password)
        # Verification adds declared diagnostic work, never a replacement hash.
        verified, needs_rehash = real_verify(password, actual)
        with lock:
            verifications.append(verified and not needs_rehash)
        return actual

    def observe_async_hash(password: str) -> Coroutine[Any, Any, str]:
        async def delegate() -> str:
            actual_task = asyncio.current_task()
            if actual_task is None:
                raise RuntimeError("Production hash work must have its actual asyncio task")
            entered_hash_tasks.append(cast("asyncio.Task[object]", actual_task))
            return await real_async_hash(password)

        actual_coroutine = delegate()
        hash_coroutines.append(actual_coroutine)
        return actual_coroutine

    async def finish_owned(workflow_failure: BaseException | None) -> None:
        request_outcomes = await asyncio.gather(*requests, return_exceptions=True)
        # Retain entered tasks even after DONE. Identity-matching pending tasks
        # also catches a production child created but not yet scheduled on HTTP
        # cancellation; the observer creates no replacement Task or Future.
        tasks = list(entered_hash_tasks)
        for task in asyncio.all_tasks():
            if any(task.get_coro() is coroutine for coroutine in hash_coroutines) and all(
                task is not previous for previous in tasks
            ):
                tasks.append(cast("asyncio.Task[object]", task))
        hash_outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        drained_hash_tasks.extend(tasks)
        with lock:
            actual_futures = list(native_futures)
        native_outcomes = await asyncio.gather(
            *(asyncio.wrap_future(future) for future in actual_futures), return_exceptions=True
        )
        # Allow the real team's owner-reservation done callback to run too.
        await asyncio.sleep(0)
        errors: list[BaseException] = list(callback_errors) + [
            outcome
            for outcome in [*request_outcomes, *hash_outcomes, *native_outcomes]
            if isinstance(outcome, BaseException)
        ]
        errors = [error for error in errors if error is not workflow_failure]
        for coroutine in hash_coroutines:
            if not isinstance(coroutine, CoroutineType):
                errors.append(RuntimeError("A produced hash coroutine had an unexpected type"))
            elif coroutine.cr_frame is not None:
                coroutine.close()
                errors.append(RuntimeError("A produced hash coroutine had no completed owner"))
        if len(tasks) != len(hash_coroutines):
            errors.append(RuntimeError("Every real team hash child must be retained and drained"))
        if any(not future.done() or future.cancelled() for future in actual_futures):
            errors.append(RuntimeError("An actual production native Future was not completed"))
        if errors:
            raise BaseExceptionGroup("Owned public or genuine native work failed", errors)

    async def drain_owned(workflow_failure: BaseException | None) -> None:
        cleanup = asyncio.create_task(finish_owned(workflow_failure))
        interrupted: asyncio.CancelledError | None = None
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError as error:
                if cleanup.cancelled():
                    raise RuntimeError("The owned native drain itself was cancelled") from error
                interrupted = error
        cleanup.result()
        if interrupted is not None:
            raise interrupted

    try:
        with monkeypatch.context() as configuration:
            configuration.setenv("GOATFARM_MAX_TEAM_MEMBERS_PER_FARM", "1")
            get_settings.cache_clear()
            settings = get_settings()
            configured = settings.model_dump()
            if settings.max_team_members_per_farm != 1 or any(
                configured[key] != value
                for key, value in original_settings.items()
                if key != "max_team_members_per_farm"
            ):
                raise RuntimeError("Only the validated deployment team limit may change")
            with monkeypatch.context() as observation, observe_callbacks():
                # Restoring the exact instance dictionary also preserves whether
                # submit originally came from the real executor class descriptor.
                observation.setitem(vars(executor), "submit", observe_submit)
                observation.setattr(security, "hash_password", observe_hash)
                observation.setattr(team_api, "hash_password_async", observe_async_hash)
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=transport.app, raise_app_exceptions=True),
                    base_url=client.base_url,
                    event_hooks={"request": list(client.event_hooks["request"])},
                ) as browser:
                    workflow_error: BaseException | None = None
                    try:
                        owner = await owned(
                            owner_with_farm(browser, email="quota-argon-owner@farm.in")
                        )
                        page = await owned(browser.get("/api/team", headers=owner))
                        assert page.status_code == 200, "The public team must be readable"
                        roles = json_objects(json_object(page.json()).get("roles"))
                        cleaners = [
                            json_int(role.get("id"))
                            for role in roles
                            if role.get("code") == "CLEANER"
                        ]
                        assert len(cleaners) == 1, "The owned farm must expose its cleaner role"
                        role_id = cleaners[0]
                        assert role_id > 0, "The actual owned role must have a positive id"
                        first = await owned(
                            browser.post(
                                "/api/team/workers",
                                headers=owner | {"Idempotency-Key": "real-argon-quota-fill"},
                                json={
                                    "email": "quota-argon-first@farm.in",
                                    "password": "workerpass123",
                                    "role_id": role_id,
                                },
                            )
                        )
                        assert first.status_code == 201, "A valid public worker must fill quota"
                        worker_id = json_int(json_object(first.json()).get("id"))
                        page = await owned(browser.get("/api/team", headers=owner))
                        assert page.status_code == 200, "The public full roster must be readable"
                        members = json_objects(json_object(page.json()).get("memberships"))
                        assert len(members) == 1, (
                            "The actual public roster must fill the one-seat quota"
                        )
                        assert json_int(members[0].get("id")) == worker_id, (
                            "The public roster must contain the genuine worker just created"
                        )
                        with lock:
                            setup_hash_jobs = len(hash_threads)
                            setup_futures = list(native_futures)
                        if setup_hash_jobs < 1 or any(
                            not future.done() or future.cancelled() for future in setup_futures
                        ):
                            raise RuntimeError("Real setup hashing must finish before the probe")
                        response = await owned(
                            browser.post(
                                "/api/team/workers",
                                headers=owner | {"Idempotency-Key": "full-quota-real-argon-probe"},
                                json={
                                    "email": "quota-argon-fresh@farm.in",
                                    "password": "workerpass123",
                                    "role_id": role_id,
                                },
                            )
                        )
                        assert response.status_code == 409, "A full team must reject a valid worker"
                        body = json_object(response.json())
                        assert body == {
                            "detail": "This farm has reached its team-member limit.",
                            "code": "STANDING_QUOTA_CONFLICT",
                        }, "A full team must report its standing-quota conflict"
                    except BaseException as error:
                        workflow_error = error
                        raise
                    finally:
                        try:
                            await drain_owned(workflow_error)
                        except BaseException as cleanup_error:
                            if workflow_error is not None:
                                raise BaseExceptionGroup(
                                    "Public quota workflow and real native drain failed",
                                    [workflow_error, cleanup_error],
                                ) from None
                            raise
            if (
                security.hash_password is not real_hash
                or security.verify_password is not real_verify
                or team_symbols["hash_password_async"] is not real_async_hash
                or security._run_password_work is not real_native_work
                or security._password_executor is not executor
                or security._password_slots is not original_slots
                or vars(executor).get("submit", missing) is not original_submit_attribute
                or executor.submit != real_submit
                or loop.get_exception_handler() is not original_loop_handler
            ):
                raise RuntimeError("The original production hash callables were not restored")
    except BaseException as error:
        primary_error = error
        raise
    finally:
        try:
            get_settings.cache_clear()
            if get_settings().model_dump() != original_settings:
                raise RuntimeError("The original validated configuration was not restored")
        except BaseException as cleanup_error:
            if primary_error is not None:
                raise BaseExceptionGroup(
                    "Public native workflow and configuration restoration failed",
                    [primary_error, cleanup_error],
                ) from None
            raise

    if response is None or setup_hash_jobs is None:
        raise RuntimeError("The actual public quota observation was not completed")
    with lock:
        total_hash_jobs = len(hash_threads)
        actual_threads = list(hash_threads)
        actual_verifications = list(verifications)
        actual_futures = list(native_futures)
    quota_hash_jobs = total_hash_jobs - setup_hash_jobs
    if (
        quota_hash_jobs < 0
        or len(actual_futures) != total_hash_jobs
        or len(actual_verifications) != total_hash_jobs
        or any(not task.done() or task.cancelled() for task in [*requests, *drained_hash_tasks])
        or any(not future.done() or future.cancelled() for future in actual_futures)
    ):
        raise RuntimeError("The real Argon, native completion, or ownership observations differed")
    assert all(actual_verifications), "Every actual generated password hash must verify"
    assert all(thread != loop_thread for thread in actual_threads), (
        "Actual Argon password work must stay off the event-loop thread"
    )
    observations = {
        "contract": "full_team_preflight_before_real_argon",
        "actual_quota_hash_jobs": quota_hash_jobs,
        "actual_setup_hash_jobs": setup_hash_jobs,
        "actual_total_native_futures": len(actual_futures),
        "actual_native_thread_count": len(set(actual_threads)),
        "all_hashes_off_event_loop": all(thread != loop_thread for thread in actual_threads),
        "all_actual_hashes_verified": all(actual_verifications),
        "all_actual_native_futures_completed": all(future.done() for future in actual_futures),
        "all_owned_requests_and_hash_children_completed": all(
            task.done() for task in [*requests, *drained_hash_tasks]
        ),
        "all_observed_loop_callbacks_clean": not callback_errors,
        "actual_status": response.status_code,
    }
    with capsys.disabled():
        print("TEAM_QUOTA_REAL_ARGON " + json.dumps(observations, sort_keys=True), flush=True)
    assert quota_hash_jobs == 0, "A full team must be rejected before real Argon work is admitted"
