"""Availability regressions for bounded off-loop password work."""

from __future__ import annotations

import asyncio
import logging
import threading

import httpx
import pytest
from sqlalchemy.pool import QueuePool

import app.api.auth as auth_api
import app.api.team as team_api
import app.security as security
from app.core.config import get_settings
from app.db import get_engine
from app.ratelimit import auth_limiter

from .conftest import OWNER_PW, owner_with_farm, register
from .type_helpers import checked_out_connections


async def test_password_work_is_off_loop_and_has_no_waiting_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    started = [threading.Event() for _ in range(security._password_worker_count)]
    call_index = 0
    call_lock = threading.Lock()

    def stalled_hash(password: str) -> str:
        nonlocal call_index
        with call_lock:
            index = call_index
            call_index += 1
        started[index].set()
        assert release.wait(timeout=5)
        return f"hashed:{password}"

    monkeypatch.setattr(security, "hash_password", stalled_hash)
    jobs = [
        asyncio.create_task(security.hash_password_async(f"password-{index}"))
        for index in range(security._password_worker_count)
    ]
    try:
        for signal in started:
            assert await asyncio.to_thread(signal.wait, 2)

        # The event loop remains responsive while every native worker is busy.
        ticked = False

        async def event_loop_tick() -> None:
            nonlocal ticked
            await asyncio.sleep(0)
            ticked = True

        await asyncio.wait_for(event_loop_tick(), timeout=0.1)
        assert ticked

        # Capacity is workers, not workers plus an attacker-controlled queue.
        with pytest.raises(security.PasswordWorkCapacityError):
            await security.hash_password_async("must-fail-fast")
    finally:
        release.set()
        await asyncio.gather(*jobs)


async def test_password_capacity_error_maps_to_retryable_429(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def saturated_verify(password: str, stored: str) -> tuple[bool, bool, bool]:
        raise security.PasswordWorkCapacityError("busy")

    monkeypatch.setattr(auth_api, "verify_password_with_work_async", saturated_verify)
    response = await client.post(
        "/api/auth/login",
        json={"email": "nobody@example.com", "password": "not-the-password"},
    )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "1"
    # The 429 carries the machine-readable code for localized clients.
    assert response.json() == {
        "detail": "Password service is busy — please retry shortly.",
        "code": "RATE_LIMITED",
    }


async def test_same_email_burst_is_reserved_before_password_work(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def stalled_verify(password: str, stored: str) -> tuple[bool, bool, bool]:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return False, False, False

    monkeypatch.setattr(auth_api, "verify_password_with_work_async", stalled_verify)
    first = asyncio.create_task(
        client.post(
            "/api/auth/login",
            json={"email": "same-burst@farm.in", "password": "wrongpass123"},
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        second = await asyncio.wait_for(
            client.post(
                "/api/auth/login",
                json={"email": "same-burst@farm.in", "password": "wrongpass123"},
            ),
            timeout=0.5,
        )
        assert second.status_code == 429
        assert second.headers["Retry-After"] == "1"
        assert calls == 1
    finally:
        release.set()
    assert (await first).status_code == 401


async def test_current_password_verification_holds_no_database_connection(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Login/change/delete release their snapshot transaction before Argon."""
    headers = await register(client, "argon-connection@farm.in")
    cases = [
        (
            "/api/auth/login",
            {"email": "argon-connection@farm.in", "password": "wrongpass123"},
            None,
            401,
        ),
        (
            "/api/auth/change-password",
            {"current_password": "wrongpass123", "new_password": "replacement123"},
            headers,
            400,
        ),
        (
            "/api/auth/account",
            {"current_password": "wrongpass123"},
            headers,
            400,
        ),
    ]
    for path, payload, request_headers, expected_status in cases:
        started = asyncio.Event()
        release = asyncio.Event()

        async def stalled_verify(
            password: str,
            stored: str,
            started_signal: asyncio.Event = started,
            release_signal: asyncio.Event = release,
        ) -> tuple[bool, bool]:
            started_signal.set()
            await release_signal.wait()
            return False, False

        async def stalled_verify_with_work(
            password: str,
            stored: str,
            started_signal: asyncio.Event = started,
            release_signal: asyncio.Event = release,
        ) -> tuple[bool, bool, bool]:
            started_signal.set()
            await release_signal.wait()
            return False, False, False

        if path == "/api/auth/login":
            monkeypatch.setattr(
                auth_api, "verify_password_with_work_async", stalled_verify_with_work
            )
        else:
            monkeypatch.setattr(auth_api, "verify_password_async", stalled_verify)
        request = asyncio.create_task(
            client.request(
                "DELETE" if path == "/api/auth/account" else "POST",
                path,
                json=payload,
                headers=request_headers,
            )
        )
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            pool = get_engine().sync_engine.pool
            assert isinstance(pool, QueuePool)
            assert pool.checkedout() == 0
        finally:
            release.set()
        response = await request
        assert response.status_code == expected_status, response.text


async def test_replacement_hashing_holds_no_database_connection(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = await register(client, "argon-hash-connection@farm.in", OWNER_PW)
    started = asyncio.Event()
    release = asyncio.Event()

    async def accepted_verify(password: str, stored: str) -> tuple[bool, bool]:
        return True, False

    async def stalled_hash(password: str) -> str:
        started.set()
        await release.wait()
        return "replacement-hash-created-off-transaction"

    monkeypatch.setattr(auth_api, "verify_password_async", accepted_verify)
    monkeypatch.setattr(auth_api, "hash_password_async", stalled_hash)
    change = asyncio.create_task(
        client.post(
            "/api/auth/change-password",
            json={"current_password": OWNER_PW, "new_password": "replacement123"},
            headers=headers,
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        assert checked_out_connections(get_engine()) == 0
    finally:
        release.set()
    response = await change
    assert response.status_code == 200, response.text


async def test_team_password_hashing_uses_only_the_required_transaction(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client, email="team-argon-owner@farm.in")
    team = await client.get("/api/team", headers=owner)
    assert team.status_code == 200, team.text
    role_id = next(role["id"] for role in team.json()["roles"] if role["code"] == "CLEANER")
    existing = await client.post(
        "/api/team/workers",
        json={
            "email": "team-argon-existing@farm.in",
            "password": "workerpass123",
            "role_id": role_id,
        },
        headers=owner,
    )
    assert existing.status_code == 201, existing.text

    cases = [
        (
            "/api/team/workers",
            {
                "email": "team-argon-new@farm.in",
                "password": "workerpass123",
                "role_id": role_id,
            },
            201,
        ),
        (
            f"/api/team/workers/{existing.json()['id']}/reset-password",
            {"password": "replacement123"},
            200,
        ),
    ]
    for path, payload, expected_status in cases:
        started = asyncio.Event()
        release = asyncio.Event()

        async def stalled_hash(
            password: str,
            started_signal: asyncio.Event = started,
            release_signal: asyncio.Event = release,
        ) -> str:
            started_signal.set()
            await release_signal.wait()
            return "prepared-off-transaction-password-hash"

        monkeypatch.setattr(team_api, "hash_password_async", stalled_hash)
        request = asyncio.create_task(client.post(path, json=payload, headers=owner))
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            # Both creation and reset release their read-only authorization /
            # replay-preflight transaction before memory-hard password work.
            assert checked_out_connections(get_engine()) == 0
        finally:
            release.set()
        response = await request
        assert response.status_code == expected_status, response.text


async def test_one_owner_cannot_fill_every_team_password_work_slot(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_a = await owner_with_farm(
        client,
        email="argon-fairness-a@farm.in",
        farm_name="Argon Fairness A",
    )
    owner_b = await owner_with_farm(
        client,
        email="argon-fairness-b@farm.in",
        farm_name="Argon Fairness B",
    )
    team_a = (await client.get("/api/team", headers=owner_a)).json()
    team_b = (await client.get("/api/team", headers=owner_b)).json()
    role_a = next(role["id"] for role in team_a["roles"] if role["code"] == "CLEANER")
    role_b = next(role["id"] for role in team_b["roles"] if role["code"] == "CLEANER")
    target = await client.post(
        "/api/team/workers",
        json={
            "email": "argon-fairness-target@farm.in",
            "password": "initial-worker-password",
            "role_id": role_a,
        },
        headers=owner_a,
    )
    assert target.status_code == 201, target.text

    started_a = asyncio.Event()
    started_b = asyncio.Event()
    release = asyncio.Event()
    admitted_passwords: list[str] = []

    async def stalled_hash(password: str) -> str:
        admitted_passwords.append(password)
        if password == "owner-a-create-password":
            started_a.set()
        elif password == "owner-b-create-password":
            started_b.set()
        await release.wait()
        return f"prepared:{password}"

    monkeypatch.setattr(team_api, "hash_password_async", stalled_hash)
    first_a = asyncio.create_task(
        client.post(
            "/api/team/workers",
            json={
                "email": "argon-fairness-a-new@farm.in",
                "password": "owner-a-create-password",
                "role_id": role_a,
            },
            headers=owner_a,
        )
    )
    first_b: asyncio.Task[httpx.Response] | None = None
    try:
        await asyncio.wait_for(started_a.wait(), timeout=2)

        # Create and reset share one owner reservation. This request fails fast
        # before reaching the global Argon pool while A's create is in flight.
        saturated_a = await asyncio.wait_for(
            client.post(
                f"/api/team/workers/{target.json()['id']}/reset-password",
                json={"password": "owner-a-reset-password"},
                headers=owner_a,
            ),
            timeout=0.5,
        )
        assert saturated_a.status_code == 429, saturated_a.text
        assert saturated_a.headers["Retry-After"] == "1"
        assert "owner-a-reset-password" not in admitted_passwords

        # A different owner is admitted while A is still stalled, leaving the
        # remaining global capacity available to unrelated auth/team traffic.
        first_b = asyncio.create_task(
            client.post(
                "/api/team/workers",
                json={
                    "email": "argon-fairness-b-new@farm.in",
                    "password": "owner-b-create-password",
                    "role_id": role_b,
                },
                headers=owner_b,
            )
        )
        await asyncio.wait_for(started_b.wait(), timeout=2)
    finally:
        release.set()

    assert (await first_a).status_code == 201
    assert first_b is not None
    assert (await first_b).status_code == 201
    assert admitted_passwords == ["owner-a-create-password", "owner-b-create-password"]


async def test_team_create_and_reset_share_preargon_sliding_budget(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client, email="argon-budget-owner@farm.in")
    team = (await client.get("/api/team", headers=owner)).json()
    role_id = next(role["id"] for role in team["roles"] if role["code"] == "CLEANER")
    target = await client.post(
        "/api/team/workers",
        json={
            "email": "argon-budget-target@farm.in",
            "password": "initial-worker-password",
            "role_id": role_id,
        },
        headers=owner,
    )
    assert target.status_code == 201, target.text
    await register(client, "argon-budget-existing@farm.in", "existing-password")

    settings = get_settings()
    monkeypatch.setattr(settings, "auth_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "auth_rate_limit_max_attempts", 2)
    monkeypatch.setattr(settings, "auth_rate_limit_window_seconds", 300)
    auth_limiter.clear()
    hash_calls = 0

    async def counted_hash(password: str) -> str:
        nonlocal hash_calls
        hash_calls += 1
        return f"prepared:{password}"

    monkeypatch.setattr(team_api, "hash_password_async", counted_hash)
    try:
        # This reaches Argon but fails generically after hashing because the
        # global account already exists.
        existing = await client.post(
            "/api/team/workers",
            json={
                "email": "argon-budget-existing@farm.in",
                "password": "first-budget-password",
                "role_id": role_id,
            },
            headers=owner,
        )
        assert existing.status_code == 400, existing.text

        reset = await client.post(
            f"/api/team/workers/{target.json()['id']}/reset-password",
            json={"password": "second-budget-password"},
            headers=owner,
        )
        assert reset.status_code == 200, reset.text

        blocked = await client.post(
            "/api/team/workers",
            json={
                "email": "argon-budget-blocked@farm.in",
                "password": "third-budget-password",
                "role_id": role_id,
            },
            headers=owner,
        )
        assert blocked.status_code == 429, blocked.text
        assert blocked.headers["Retry-After"] == "300"
        assert blocked.json()["detail"] == team_api.TEAM_PASSWORD_WORK_LIMIT_REASON
        assert hash_calls == 2
    finally:
        auth_limiter.clear()


async def test_cancelled_team_hash_keeps_owner_reservation_until_work_finishes(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keep actual native completion pending after real hashing and verification.

    The first real job is parked after its original hash and one diagnostic
    verification, before production run_and_release returns its native slot.
    This is a declared completion-scheduling witness, not a claim that Argon is
    still computing. Native drain has no local timeout; an external phase or
    process budget must bound abnormal cleanup.
    """
    from collections.abc import Callable, Coroutine, Iterator
    from concurrent.futures import Future
    from contextlib import contextmanager
    from types import CoroutineType
    from typing import Any, cast

    transport = client._transport
    if not isinstance(transport, httpx.ASGITransport):
        raise RuntimeError("The owned browser requires the actual fixture ASGI app")
    if security._password_worker_count < 2:
        raise RuntimeError("This genuine same-owner admission witness needs two native slots")
    executor = security._password_executor
    original_slots = security._password_slots
    real_submit = executor.submit
    real_hash = security.hash_password
    real_verify = security.verify_password
    real_native_work = security._run_password_work
    team_symbols = vars(team_api)
    real_async_hash = cast(
        "Callable[[str], Coroutine[Any, Any, str]]", team_symbols["hash_password_async"]
    )
    if real_async_hash is not security.hash_password_async:
        raise RuntimeError("Team hashing must use the original production coroutine")
    missing = object()
    original_submit_attribute = vars(executor).get("submit", missing)
    original_settings = get_settings().model_dump()
    loop = asyncio.get_running_loop()
    original_loop_handler = loop.get_exception_handler()
    loop_thread = threading.get_ident()
    lock = threading.Lock()
    release = threading.Event()
    first_hash_ready = asyncio.Event()
    second_hash_entered = asyncio.Event()
    requests: list[asyncio.Task[object]] = []
    waiters: list[asyncio.Task[bool]] = []
    hash_coroutines: list[Coroutine[Any, Any, str]] = []
    entered_hash_tasks: list[asyncio.Task[object]] = []
    drained_hash_tasks: list[asyncio.Task[object]] = []
    native_futures: list[Future[object]] = []
    hash_threads: list[int] = []
    verifications: list[bool] = []
    callback_errors: list[BaseException] = []
    cleanup_cancelled_requests: list[asyncio.Task[object]] = []
    cancelled_request: asyncio.Task[httpx.Response] | None = None
    first_cancellation_expected = False
    still_reserved: httpx.Response | None = None
    retry: httpx.Response | None = None
    window_hash_calls: int | None = None
    second_entry_observed = False
    first_native_pending_after_cancel = False
    first_child_pending_after_cancel = False
    reservation_empty_before_retry = False

    @contextmanager
    def observe_callbacks() -> Iterator[None]:
        def observed_handler(
            actual_loop: asyncio.AbstractEventLoop, context: dict[str, Any]
        ) -> None:
            error = context.get("exception")
            callback_errors.append(
                error
                if isinstance(error, BaseException)
                else RuntimeError("An actual owned-work callback failed")
            )
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

    def spawn[ResultT](operation: Coroutine[Any, Any, ResultT]) -> asyncio.Task[ResultT]:
        task = asyncio.create_task(operation)
        requests.append(cast("asyncio.Task[object]", task))
        return task

    def observe_submit[ResultT, **P](
        work: Callable[P, ResultT], /, *args: P.args, **kwargs: P.kwargs
    ) -> Future[ResultT]:
        actual = real_submit(work, *args, **kwargs)
        with lock:
            native_futures.append(cast("Future[object]", actual))
        return actual

    def observe_hash(password: str) -> str:
        with lock:
            index = len(hash_threads)
            hash_threads.append(threading.get_ident())
        if index == 1:
            loop.call_soon_threadsafe(second_hash_entered.set)
        actual = real_hash(password)
        verified, needs_rehash = real_verify(password, actual)
        with lock:
            verifications.append(verified and not needs_rehash)
        if index == 0:
            loop.call_soon_threadsafe(first_hash_ready.set)
            release.wait()
        return actual

    def observe_async_hash(password: str) -> Coroutine[Any, Any, str]:
        async def delegate() -> str:
            task = asyncio.current_task()
            if task is None:
                raise RuntimeError("Actual production password work must own a Task")
            entered_hash_tasks.append(cast("asyncio.Task[object]", task))
            return await real_async_hash(password)

        coroutine = delegate()
        hash_coroutines.append(coroutine)
        return coroutine

    async def finish_owned(workflow_failure: BaseException | None) -> None:
        for waiter in waiters:
            if not waiter.done():
                waiter.cancel()
        waiter_outcomes = await asyncio.gather(*waiters, return_exceptions=True)
        request_outcomes = await asyncio.gather(*requests, return_exceptions=True)
        tasks = list(entered_hash_tasks)
        for task in asyncio.all_tasks():
            if any(task.get_coro() is coroutine for coroutine in hash_coroutines) and all(
                task is not previous for previous in tasks
            ):
                tasks.append(cast("asyncio.Task[object]", task))
        hash_outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        for task in tasks:
            if all(task is not previous for previous in drained_hash_tasks):
                drained_hash_tasks.append(task)
        with lock:
            actual_futures = list(native_futures)
        native_outcomes = await asyncio.gather(
            *(asyncio.wrap_future(future) for future in actual_futures), return_exceptions=True
        )
        # Run the real team's already registered owner-reservation callbacks.
        await asyncio.sleep(0)
        errors: list[BaseException] = list(callback_errors)
        errors.extend(
            outcome
            for outcome in waiter_outcomes
            if isinstance(outcome, BaseException)
            and not isinstance(outcome, asyncio.CancelledError)
        )
        for task, outcome in zip(requests, request_outcomes, strict=True):
            if not isinstance(outcome, BaseException):
                continue
            if isinstance(outcome, asyncio.CancelledError) and (
                (task is cancelled_request and first_cancellation_expected)
                or any(task is previous for previous in cleanup_cancelled_requests)
            ):
                continue
            errors.append(outcome)
        errors.extend(
            outcome
            for outcome in [*hash_outcomes, *native_outcomes]
            if isinstance(outcome, BaseException)
        )
        # Remove only the identical primary error already being propagated.
        errors = [error for error in errors if error is not workflow_failure]
        for coroutine in hash_coroutines:
            if not isinstance(coroutine, CoroutineType):
                errors.append(RuntimeError("An actual hash coroutine had an unexpected type"))
            elif coroutine.cr_frame is not None:
                coroutine.close()
                errors.append(RuntimeError("An actual hash coroutine lacked a completed owner"))
        if len(tasks) != len(hash_coroutines):
            errors.append(RuntimeError("Every actual production hash child must be drained"))
        if any(not future.done() or future.cancelled() for future in actual_futures):
            errors.append(RuntimeError("An actual native Future did not complete"))
        if errors:
            raise BaseExceptionGroup("Owned public/native completion drain failed", errors)

    async def drain_owned(workflow_failure: BaseException | None) -> None:
        cleanup = asyncio.create_task(finish_owned(workflow_failure))
        interrupted: asyncio.CancelledError | None = None
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError as error:
                if cleanup.cancelled():
                    raise RuntimeError("The owned completion drain itself was cancelled") from error
                interrupted = error
        cleanup.result()
        if interrupted is not None:
            raise interrupted

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=transport.app, raise_app_exceptions=True),
        base_url=client.base_url,
        event_hooks={"request": list(client.event_hooks["request"])},
    ) as browser:
        owner = await owner_with_farm(browser, email="argon-cancel-owner@farm.in")
        team_response = await browser.get("/api/team", headers=owner)
        if team_response.status_code != 200:
            raise RuntimeError("The genuine owned team setup did not return200")
        team = team_response.json()
        roles = [role["id"] for role in team["roles"] if role["code"] == "CLEANER"]
        if len(roles) != 1 or type(roles[0]) is not int or roles[0] <= 0:
            raise RuntimeError("The actual public team must expose one owned cleaner role")
        role_id = roles[0]
        with monkeypatch.context() as observation, observe_callbacks():
            observation.setitem(vars(executor), "submit", observe_submit)
            observation.setattr(security, "hash_password", observe_hash)
            observation.setattr(team_api, "hash_password_async", observe_async_hash)
            workflow_error: BaseException | None = None
            try:
                cancelled_request = spawn(
                    browser.post(
                        "/api/team/workers",
                        json={
                            "email": "argon-cancel-abandoned@farm.in",
                            "password": "cancelled-worker-password",
                            "role_id": role_id,
                        },
                        headers=owner,
                    )
                )
                first_ready = asyncio.create_task(first_hash_ready.wait())
                waiters.append(first_ready)
                done, _ = await asyncio.wait(
                    {first_ready, cancelled_request},
                    timeout=20,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if cancelled_request in done:
                    response = cancelled_request.result()
                    raise RuntimeError(
                        "The first request returned before its real native completion gate: "
                        + str(response.status_code)
                    )
                if first_ready not in done or not first_ready.result():
                    raise RuntimeError("No actual first Argon completion-gate witness arrived")
                with lock:
                    first_futures = list(native_futures)
                if len(first_futures) != 1 or first_futures[0].done():
                    raise RuntimeError("The actual first native Future must still be pending")
                first_cancellation_expected = True
                cancelled_request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await cancelled_request
                first_native_pending_after_cancel = not first_futures[0].done()
                first_child_pending_after_cancel = (
                    len(entered_hash_tasks) == 1 and not entered_hash_tasks[0].done()
                )
                if not first_native_pending_after_cancel or not first_child_pending_after_cancel:
                    raise RuntimeError("HTTP cancellation must leave actual native work unfinished")
                second_request = spawn(
                    browser.post(
                        "/api/team/workers",
                        json={
                            "email": "argon-cancel-blocked@farm.in",
                            "password": "blocked-worker-password",
                            "role_id": role_id,
                        },
                        headers=owner,
                    )
                )
                second_entry = asyncio.create_task(second_hash_entered.wait())
                waiters.append(second_entry)
                done, _ = await asyncio.wait(
                    {second_entry, second_request},
                    timeout=20,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    raise RuntimeError("No actual second response or native-entry witness arrived")
                if second_request in done:
                    response = second_request.result()
                    if response.status_code not in {201, 429}:
                        raise RuntimeError("The actual second request had an unexpected status")
                second_entry_observed = second_hash_entered.is_set()
                with lock:
                    window_hash_calls = len(hash_threads)
                if first_futures[0].done() or entered_hash_tasks[0].done():
                    raise RuntimeError(
                        "The first native completion must stay pending in the window"
                    )
                release.set()
                still_reserved = await asyncio.wait_for(asyncio.shield(second_request), timeout=20)
                if still_reserved.status_code not in {201, 429}:
                    raise RuntimeError(
                        "The actual completed second request had an unexpected status"
                    )
                await drain_owned(None)
                reservation_empty_before_retry = not auth_limiter._reservations
                retry = await asyncio.shield(
                    spawn(
                        browser.post(
                            "/api/team/workers",
                            json={
                                "email": "argon-cancel-retry@farm.in",
                                "password": "retry-worker-password",
                                "role_id": role_id,
                            },
                            headers=owner,
                        )
                    )
                )
            except BaseException as error:
                workflow_error = error
                raise
            finally:
                release.set()
                for waiter in waiters:
                    if not waiter.done():
                        waiter.cancel()
                if workflow_error is not None:
                    for task in requests:
                        if not task.done():
                            cleanup_cancelled_requests.append(task)
                            task.cancel()
                try:
                    await drain_owned(workflow_error)
                except BaseException as cleanup_error:
                    if workflow_error is not None:
                        raise BaseExceptionGroup(
                            "Public cancellation workflow and actual native drain failed",
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
            or get_settings().model_dump() != original_settings
        ):
            raise RuntimeError(
                "The actual original password configuration/callables were not restored"
            )

    if (
        still_reserved is None
        or retry is None
        or window_hash_calls is None
        or cancelled_request is None
    ):
        raise RuntimeError("The actual public cancellation workflow did not complete")
    with lock:
        total_hash_calls = len(hash_threads)
        actual_threads = list(hash_threads)
        actual_verifications = list(verifications)
        actual_futures = list(native_futures)
    if (
        len(actual_futures) != total_hash_calls
        or len(actual_verifications) != total_hash_calls
        or any(not task.done() or task.cancelled() for task in drained_hash_tasks)
        or any(not future.done() or future.cancelled() for future in actual_futures)
        or callback_errors
    ):
        raise RuntimeError("Actual hash completion/ownership/callback observations differed")
    assert all(actual_verifications), "All actual generated password hashes must verify"
    assert all(thread != loop_thread for thread in actual_threads), (
        "All actual password hashing must stay off the event-loop thread"
    )
    with capsys.disabled():
        import json

        print(
            "TEAM_HASH_CANCEL_REAL_ARGON "
            + json.dumps(
                {
                    "contract": "same_owner_reservation_until_actual_native_completion",
                    "first_HTTP_cancelled": cancelled_request.cancelled(),
                    "first_native_pending_after_cancel": first_native_pending_after_cancel,
                    "first_hash_child_pending_after_cancel": first_child_pending_after_cancel,
                    "second_actual_hash_entry_in_window": second_entry_observed,
                    "actual_window_hash_jobs": window_hash_calls,
                    "actual_total_hash_jobs": total_hash_calls,
                    "actual_native_futures": len(actual_futures),
                    "all_actual_hashes_verified": all(actual_verifications),
                    "all_actual_hashes_off_loop": all(
                        thread != loop_thread for thread in actual_threads
                    ),
                    "all_actual_native_completions_done": all(
                        future.done() for future in actual_futures
                    ),
                    "all_actual_hash_children_done": all(
                        task.done() for task in drained_hash_tasks
                    ),
                    "reservation_empty_before_retry": reservation_empty_before_retry,
                    "second_status": still_reserved.status_code,
                    "retry_status": retry.status_code,
                },
                sort_keys=True,
            ),
            flush=True,
        )
    assert still_reserved.status_code == 429, still_reserved.text
    assert window_hash_calls == 1
    assert reservation_empty_before_retry
    assert retry.status_code == 201, retry.text
    assert total_hash_calls == 2


async def test_team_password_throttle_logs_the_throttled_actor_id(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The sliding-budget 429 emits exactly one audit line naming the owner id."""
    owner = await owner_with_farm(client, email="argon-throttle-log-owner@farm.in")
    actor_id = (await client.get("/api/auth/me", headers=owner)).json()["id"]
    team = (await client.get("/api/team", headers=owner)).json()
    role_id = next(role["id"] for role in team["roles"] if role["code"] == "CLEANER")

    settings = get_settings()
    monkeypatch.setattr(settings, "auth_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "auth_rate_limit_max_attempts", 1)
    monkeypatch.setattr(settings, "auth_rate_limit_window_seconds", 300)
    auth_limiter.clear()

    async def fast_hash(password: str) -> str:
        return f"prepared:{password}"

    monkeypatch.setattr(team_api, "hash_password_async", fast_hash)
    try:
        first = await client.post(
            "/api/team/workers",
            json={
                "email": "throttle-log-a@farm.in",
                "password": "first-log-password",
                "role_id": role_id,
            },
            headers=owner,
        )
        assert first.status_code == 201, first.text

        with caplog.at_level(logging.INFO, logger="goatfarm.team"):
            blocked = await client.post(
                "/api/team/workers",
                json={
                    "email": "throttle-log-b@farm.in",
                    "password": "second-log-password",
                    "role_id": role_id,
                },
                headers=owner,
            )
        assert blocked.status_code == 429, blocked.text
        assert blocked.json()["detail"] == team_api.TEAM_PASSWORD_WORK_LIMIT_REASON

        # The refusal must be attributable to exactly one real owner id: the
        # access log carries no principal, so this is the only attribution.
        records = [record for record in caplog.records if record.name == "goatfarm.team"]
        assert [record.getMessage() for record in records] == [
            f"team password work throttled (actor_id={actor_id})"
        ]
    finally:
        auth_limiter.clear()
