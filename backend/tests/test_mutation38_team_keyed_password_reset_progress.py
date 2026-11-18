"""Distinct public keyed password resets preserve actor-authorization progress."""

import asyncio
import json
import re
import threading
from collections.abc import Callable, Coroutine, Iterator
from concurrent.futures import Future
from contextlib import contextmanager
from contextvars import ContextVar
from types import CoroutineType
from typing import Any, cast

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app import security
from app.api import team as team_api
from app.core.config import get_settings
from app.models import Farm, IdempotencyRecord, User

from .conftest import owner_with_farm
from .type_helpers import json_int, json_object, json_objects


async def test_distinct_keyed_password_resets_preserve_actor_progress(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Park only after each real claim, before real owner reauthorization.

    No SQL, claim, lock, hash result or exception is replaced. Each actual hash
    adds one declared diagnostic verification inside its genuine native job.
    Final request/native drain has no local deadline; a future approved external
    phase budget must bound it if genuine work does not finish.
    """
    transport = client._transport
    if not isinstance(transport, httpx.ASGITransport):
        raise RuntimeError("The owned browser requires the actual fixture app")
    original_settings = get_settings().model_dump()
    executor = security._password_executor
    original_slots = security._password_slots
    real_submit = executor.submit
    real_hash, real_verify = security.hash_password, security.verify_password
    real_native_work = security._run_password_work
    team_symbols = vars(team_api)
    real_async_hash = cast(
        "Callable[[str], Coroutine[Any, Any, str]]", team_symbols["hash_password_async"]
    )
    real_reauthorize = team_api._reauthorize_prepared_owner
    if real_async_hash is not security.hash_password_async:
        raise RuntimeError("Team hashing must use its actual async implementation")
    missing = object()
    original_submit_attribute = vars(executor).get("submit", missing)
    loop = asyncio.get_running_loop()
    original_loop_handler = loop.get_exception_handler()
    callback_errors: list[BaseException] = []
    loop_thread = threading.get_ident()
    lock = threading.Lock()
    stage: ContextVar[str] = ContextVar("owned_keyed_password_reset_stage", default="setup")
    requests: list[asyncio.Task[object]] = []
    hash_coroutines: list[Coroutine[Any, Any, str]] = []
    entered_hash_tasks: list[asyncio.Task[object]] = []
    entered_hash_stages: list[str] = []
    drained_hash_tasks: list[asyncio.Task[object]] = []
    native_futures: list[Future[object]] = []
    native_stages: list[str] = []
    hash_threads: list[int] = []
    verifications: list[bool] = []
    ready = {label: asyncio.Event() for label in ("first", "second")}
    release = asyncio.Event()
    claims: dict[str, tuple[int, int, str]] = {}
    claim_native_done: dict[str, bool] = {}
    claim_sessions: list[AsyncSession] = []
    expected_actor: int | None = None
    expected_farm: int | None = None
    first_response: httpx.Response | None = None
    second_response: httpx.Response | None = None
    logs_start = 0

    def start_owned[ResultT](
        operation: Coroutine[Any, Any, ResultT], label: str = "setup"
    ) -> asyncio.Task[ResultT]:
        async def invoke() -> ResultT:
            token = stage.set(label)
            try:
                return await operation
            finally:
                stage.reset(token)

        actual = asyncio.create_task(invoke())
        requests.append(cast("asyncio.Task[object]", actual))
        return actual

    async def owned[ResultT](operation: Coroutine[Any, Any, ResultT]) -> ResultT:
        return await asyncio.shield(start_owned(operation))

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

    def observe_submit[ResultT, **P](
        work: Callable[P, ResultT], /, *args: P.args, **kwargs: P.kwargs
    ) -> Future[ResultT]:
        actual = real_submit(work, *args, **kwargs)
        with lock:
            native_futures.append(cast("Future[object]", actual))
            native_stages.append(stage.get())
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
            entered_hash_stages.append(stage.get())
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

    async def at_actual_claim(db: AsyncSession, **kwargs: Any) -> tuple[User, Farm]:
        label = stage.get()
        if (
            label not in ready
            or expected_actor is None
            or expected_farm is None
            or kwargs.get("actor_id") != expected_actor
            or kwargs.get("farm_id") != expected_farm
            or not db.in_transaction()
        ):
            raise RuntimeError("The gate must belong to an actual owned prepared request")
        # This is a read of the request's own real, uncommitted public claim.
        # It neither creates a claim nor takes an artificial row lock.
        row = (
            await db.execute(
                select(
                    IdempotencyRecord.id,
                    IdempotencyRecord.key_digest,
                ).where(
                    IdempotencyRecord.actor_id == expected_actor,
                    IdempotencyRecord.farm_id == expected_farm,
                    IdempotencyRecord.operation == "team.workers.reset-password",
                    IdempotencyRecord.completed_at.is_(None),
                    IdempotencyRecord.response_status.is_(None),
                )
            )
        ).one_or_none()
        pid = await db.scalar(text("SELECT pg_backend_pid()"))
        if row is None:
            raise RuntimeError("The real keyed request has no visible fresh claim")
        claim_id, key_digest = row
        if type(pid) is not int or pid <= 0 or type(claim_id) is not int or claim_id <= 0:
            raise RuntimeError("The actual request backend and claim must be identifiable")
        if not isinstance(key_digest, str) or not key_digest:
            raise RuntimeError("The actual public claim must retain its real key digest")
        with lock:
            actual_native = [
                future
                for name, future in zip(native_stages, native_futures, strict=True)
                if name == label
            ]
        actual_children = [
            task
            for name, task in zip(entered_hash_stages, entered_hash_tasks, strict=True)
            if name == label
        ]
        completed = (
            bool(actual_native and actual_children)
            and all(future.done() and not future.cancelled() for future in actual_native)
            and all(task.done() and not task.cancelled() for task in actual_children)
        )
        if not completed:
            raise RuntimeError("Real password preparation must finish before claim observation")
        # _prepare_password_reset awaits the real team hash to return. Its
        # finally releases owner admission before this actual mutate entry.
        claim_sessions.append(db)
        claims.setdefault(label, (pid, claim_id, key_digest))
        claim_native_done[label] = completed
        ready[label].set()
        await release.wait()
        # Preserve every actual keyword, including omission of any default.
        return await real_reauthorize(db, **kwargs)

    async def wait_for_claim(label: str, request: asyncio.Task[httpx.Response]) -> None:
        observer = asyncio.create_task(ready[label].wait())
        try:
            done, _ = await asyncio.wait(
                {observer, request}, timeout=10, return_when=asyncio.FIRST_COMPLETED
            )
            if request in done and not ready[label].is_set():
                early = await request
                assert early.status_code == 200, "A valid public password reset must succeed"
                raise RuntimeError("A completed request supplies no two-claim concurrency proof")
            if not ready[label].is_set():
                raise TimeoutError("The actual owned request did not reach its fresh claim")
        finally:
            if not observer.done():
                observer.cancel()
            await asyncio.gather(observer, return_exceptions=True)

    try:
        with monkeypatch.context() as observation, observe_callbacks():
            observation.setitem(vars(executor), "submit", observe_submit)
            observation.setattr(security, "hash_password", observe_hash)
            observation.setattr(team_api, "hash_password_async", observe_async_hash)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=transport.app, raise_app_exceptions=False),
                base_url=client.base_url,
                event_hooks={"request": list(client.event_hooks["request"])},
            ) as browser:
                workflow_error: BaseException | None = None
                try:
                    owner = await owned(
                        owner_with_farm(browser, email="keyed-resets-owner@farm.in")
                    )
                    identity = await owned(browser.get("/api/auth/me", headers=owner))
                    assert identity.status_code == 200, "The actual public owner must be readable"
                    expected_actor = json_int(json_object(identity.json()).get("id"))
                    assert expected_actor > 0, "The real public owner must have a positive id"
                    expected_farm = int(owner["X-Farm-Id"])
                    assert expected_farm > 0, "The real public farm must have a positive id"
                    page = await owned(browser.get("/api/team", headers=owner))
                    assert page.status_code == 200, "The actual public team must be readable"
                    roles = json_objects(json_object(page.json()).get("roles"))
                    cleaners = [json_int(r.get("id")) for r in roles if r.get("code") == "CLEANER"]
                    assert len(cleaners) == 1, "The actual owned farm must expose its cleaner role"
                    memberships: list[int] = []
                    users: list[int] = []
                    for label in ("first", "second"):
                        created = await owned(
                            browser.post(
                                "/api/team/workers",
                                headers=owner | {"Idempotency-Key": f"keyed-reset-setup-{label}"},
                                json={
                                    "email": f"keyed-reset-{label}@farm.in",
                                    "password": "workerpass123",
                                    "role_id": cleaners[0],
                                },
                            )
                        )
                        assert created.status_code == 201, (
                            "A genuine public worker must be provisioned"
                        )
                        body = json_object(created.json())
                        memberships.append(json_int(body.get("id")))
                        users.append(json_int(body.get("user_id")))
                        assert body.get("can_reset_password") is True, (
                            "The new worker must be reset-eligible"
                        )
                        assert body.get("is_active") is True, "The new worker must be active"
                    assert len(set(memberships)) == 2 and all(x > 0 for x in memberships)
                    assert len(set(users)) == 2 and expected_actor not in users
                    logs_start = len(caplog.records)
                    observation.setattr(team_api, "_reauthorize_prepared_owner", at_actual_claim)
                    first = start_owned(
                        browser.post(
                            f"/api/team/workers/{memberships[0]}/reset-password",
                            headers=owner | {"Idempotency-Key": "independent-password-reset-first"},
                            json={"password": "reset-alpha-pass123"},
                        ),
                        "first",
                    )
                    await wait_for_claim("first", first)
                    second = start_owned(
                        browser.post(
                            f"/api/team/workers/{memberships[1]}/reset-password",
                            headers=owner
                            | {"Idempotency-Key": "independent-password-reset-second"},
                            json={"password": "reset-beta-pass123"},
                        ),
                        "second",
                    )
                    await wait_for_claim("second", second)
                    if (
                        len({value[0] for value in claims.values()}) != 2
                        or len({value[1] for value in claims.values()}) != 2
                        or len({value[2] for value in claims.values()}) != 2
                    ):
                        raise RuntimeError(
                            "The workflow requires two actual independent public claims"
                        )
                    release.set()
                    first_response, second_response = await asyncio.gather(
                        asyncio.shield(first), asyncio.shield(second)
                    )
                except BaseException as error:
                    workflow_error = error
                    raise
                finally:
                    release.set()
                    try:
                        await drain_owned(workflow_error)
                    except BaseException as cleanup_error:
                        if workflow_error is not None:
                            raise BaseExceptionGroup(
                                "Public keyed resets and owned native drain failed",
                                [workflow_error, cleanup_error],
                            ) from None
                        raise
        if (
            security.hash_password is not real_hash
            or security.verify_password is not real_verify
            or team_symbols["hash_password_async"] is not real_async_hash
            or team_api._reauthorize_prepared_owner is not real_reauthorize
            or security._run_password_work is not real_native_work
            or security._password_executor is not executor
            or security._password_slots is not original_slots
            or vars(executor).get("submit", missing) is not original_submit_attribute
            or executor.submit != real_submit
            or loop.get_exception_handler() is not original_loop_handler
            or get_settings().model_dump() != original_settings
        ):
            raise RuntimeError(
                "The original observers, callables or configuration were not restored"
            )
    except BaseException:
        raise

    if first_response is None or second_response is None or expected_actor is None:
        raise RuntimeError("The complete real two-claim public workflow was not observed")
    with lock:
        actual_threads = list(hash_threads)
        actual_verified = list(verifications)
        actual_futures = list(native_futures)
    if (
        len(actual_futures) != len(actual_threads)
        or len(actual_verified) != len(actual_threads)
        or any(not t.done() or t.cancelled() for t in [*requests, *drained_hash_tasks])
        or any(not f.done() or f.cancelled() for f in actual_futures)
    ):
        raise RuntimeError("The actual native/hash/task observations were incomplete")
    assert all(actual_verified), "Every genuine generated password hash must verify"
    assert all(thread != loop_thread for thread in actual_threads), (
        "Actual Argon work must be off-loop"
    )

    # This reads genuine server LogRecord exceptions only. It is supplementary
    # causal evidence; no error is manufactured or converted into a response.
    actor_deadlocks: list[DBAPIError] = []
    pid_details_match = False
    visited: set[int] = set()
    pending: list[BaseException] = []
    for record in caplog.records[logs_start:]:
        information = record.exc_info
        if information is not None and isinstance(information[1], BaseException):
            pending.append(information[1])
    while pending:
        recorded_error = pending.pop()
        if id(recorded_error) in visited:
            continue
        visited.add(id(recorded_error))
        if isinstance(recorded_error, BaseExceptionGroup):
            pending.extend(recorded_error.exceptions)
        for next_error in (
            recorded_error.__cause__,
            recorded_error.__context__,
            getattr(recorded_error, "orig", None),
        ):
            if isinstance(next_error, BaseException):
                pending.append(next_error)
        if (
            not isinstance(recorded_error, DBAPIError)
            or getattr(recorded_error.orig, "sqlstate", None) != "40P01"
        ):
            continue
        trace = recorded_error.__traceback__
        actual_actor_origin = False
        while trace is not None:
            frame = trace.tb_frame
            if (
                frame.f_code is real_reauthorize.__code__
                and frame.f_locals.get("actor_id") == expected_actor
                and frame.f_locals.get("farm_id") == expected_farm
                and any(frame.f_locals.get("db") is db for db in claim_sessions)
            ):
                actual_actor_origin = True
            trace = trace.tb_next
        if actual_actor_origin:
            actor_deadlocks.append(recorded_error)
            native = recorded_error.orig
            seen_native: set[int] = set()
            while isinstance(native, BaseException) and id(native) not in seen_native:
                seen_native.add(id(native))
                detail = getattr(native, "detail", None)
                if isinstance(detail, str) and all(
                    re.search(r"\b" + str(value[0]) + r"\b", detail) for value in claims.values()
                ):
                    pid_details_match = True
                native = native.__cause__
    statuses = sorted([first_response.status_code, second_response.status_code])
    observations = {
        "contract": "distinct_keyed_password_resets_preserve_actor_progress",
        "actual_statuses": statuses,
        "actual_initial_distinct_claims": len({value[1] for value in claims.values()}),
        "actual_distinct_request_backends": len({value[0] for value in claims.values()}),
        "all_claims_seen_after_actual_native_preparation": all(claim_native_done.values()),
        "actual_actor_origin_40p01_errors": len(actor_deadlocks),
        "actual_40p01_detail_matches_both_owned_pids": pid_details_match,
        "actual_native_futures": len(actual_futures),
        "all_native_futures_completed": all(f.done() for f in actual_futures),
        "all_owned_requests_and_hash_children_completed": all(
            t.done() for t in [*requests, *drained_hash_tasks]
        ),
        "all_actual_hashes_verified": all(actual_verified),
        "all_hashes_off_event_loop": all(thread != loop_thread for thread in actual_threads),
        "all_observed_loop_callbacks_clean": not callback_errors,
    }
    with capsys.disabled():
        print("TEAM_KEYED_RESET_PROGRESS " + json.dumps(observations, sort_keys=True), flush=True)
    assert statuses == [200, 200], "Independent valid keyed password resets must both succeed"
    for response, membership_id in zip((first_response, second_response), memberships, strict=True):
        body = json_object(response.json())
        assert json_int(body.get("id")) == membership_id
        assert body.get("can_reset_password") is True
