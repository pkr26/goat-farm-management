"""In-flight own-account requests recheck the live account before writing."""

import asyncio
from collections.abc import Iterator
from typing import Any, Literal

import httpx
import pytest
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db import get_sessionmaker
from app.models import Farm, User
from app.ratelimit import auth_limiter

PASSWORD = "ownerpass123"


@pytest.fixture(autouse=True)
def _enabled_policy(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    settings = get_settings()
    policy = {
        "auth_rate_limit_enabled": True,
        "auth_rate_limit_max_attempts": 8,
        "auth_rate_limit_window_seconds": 3600,
        "min_password_length": 12,
    }
    validated = Settings.model_validate(settings.model_dump() | policy)
    for key in policy:
        monkeypatch.setattr(settings, key, getattr(validated, key))
    auth_limiter.clear()
    try:
        yield
    finally:
        auth_limiter.clear()


def _browser(client: httpx.AsyncClient) -> httpx.AsyncClient:
    transport = client._transport
    if not isinstance(transport, httpx.ASGITransport):
        raise RuntimeError("This contract requires the actual application transport")
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=transport.app, raise_app_exceptions=True),
        base_url="http://test",
        event_hooks=client.event_hooks,
    )


async def _drain_owned_requests(
    tasks: list[asyncio.Task[httpx.Response]],
) -> list[httpx.Response]:
    wait_error: BaseException | None = None
    cancel_error: BaseException | None = None
    exceeded_drain = False
    try:
        _, pending = await asyncio.wait(tasks, timeout=30)
        exceeded_drain = bool(pending)
    except BaseException as exc:
        wait_error = exc
    finally:
        # Cancellation of the test must still cancel and retrieve every
        # owned actor. Inspect all tasks instead of trusting an interrupted
        # wait's return value, and retrieve completed outcomes synchronously.
        unfinished = [task for task in tasks if not task.done()]
        for task in unfinished:
            task.cancel()
        if unfinished:
            try:
                await asyncio.wait(unfinished, timeout=10)
            except BaseException as exc:
                cancel_error = exc
        outcomes: list[httpx.Response | BaseException] = []
        for task in tasks:
            if task.done():
                try:
                    outcomes.append(task.result())
                except BaseException as exc:
                    outcomes.append(exc)
    if any(not task.done() for task in tasks):
        raise RuntimeError("Owned requests did not finish cancellation") from (
            cancel_error or wait_error
        )
    if wait_error is not None or cancel_error is not None:
        raise RuntimeError("Owned request cleanup was interrupted") from (
            cancel_error or wait_error
        )
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise RuntimeError("An owned application request raised") from outcome
    if exceeded_drain:
        raise TimeoutError("Owned requests exceeded the cleanup deadline")
    return [outcome for outcome in outcomes if isinstance(outcome, httpx.Response)]


@pytest.mark.parametrize("operation", ["create_farm", "totp_enroll"])
async def test_account_removal_wins_before_an_authenticated_request_locks_the_user(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    operation: Literal["create_farm", "totp_enroll"],
) -> None:
    reached_user_lock = asyncio.Event()
    resume_user_lock = asyncio.Event()
    gate_claimed = False
    intercepted_session_ids: list[int] = []
    original_execute = AsyncSession.execute
    responses: list[httpx.Response] = []
    fixture_error: BaseException | None = None

    try:
        async with _browser(client) as requester, _browser(client) as remover:
            registered = await requester.post(
                "/api/auth/register",
                json={"email": f"account-lifecycle-{operation}@farm.in", "password": PASSWORD},
            )
            if registered.status_code != 201:
                raise RuntimeError(f"Account setup failed: {registered.status_code}")
            body = registered.json()
            user_id = int(body["user"]["id"])
            headers = {"Authorization": f"Bearer {body['access_token']}"}
            async with get_sessionmaker()() as db:
                owned = await db.scalar(
                    select(func.count()).select_from(Farm).where(Farm.owner_id == user_id)
                )
                if owned:
                    raise RuntimeError("The removable account unexpectedly owns a farm")

            async def pause_first_live_user_lock(
                session: AsyncSession, statement: Any, *args: Any, **kwargs: Any
            ) -> Any:
                nonlocal gate_claimed
                if (
                    not gate_claimed
                    and isinstance(statement, Select)
                    and statement._for_update_arg is not None
                    and "populate_existing" in statement.get_execution_options()
                    and len(statement.column_descriptions) == 1
                    and statement.column_descriptions[0].get("entity") is User
                    and user_id in statement.compile().params.values()
                ):
                    # The real query exists, but has not acquired its User lock.
                    # Only this first request waits; removal proceeds normally.
                    gate_claimed = True
                    intercepted_session_ids.append(id(session))
                    reached_user_lock.set()
                    await resume_user_lock.wait()
                return await original_execute(session, statement, *args, **kwargs)

            monkeypatch.setattr(AsyncSession, "execute", pause_first_live_user_lock)
            path = "/api/auth/farms" if operation == "create_farm" else "/api/auth/totp/enroll"
            payload = (
                {"name": "Lifecycle Farm", "timezone": "UTC"}
                if operation == "create_farm"
                else {"current_password": PASSWORD}
            )
            request_headers = headers | {"Idempotency-Key": f"lifecycle-{operation}-own-account"}
            pending_request = asyncio.create_task(
                requester.post(path, headers=request_headers, json=payload)
            )
            ready = asyncio.create_task(reached_user_lock.wait())
            owned_requests = [pending_request]
            try:
                completed, _ = await asyncio.wait(
                    [pending_request, ready], timeout=15, return_when=asyncio.FIRST_COMPLETED
                )
                if not completed or not reached_user_lock.is_set():
                    raise RuntimeError("The pending endpoint did not reach its live-user lock")
                removal = asyncio.create_task(
                    remover.request(
                        "DELETE",
                        "/api/auth/account",
                        headers=headers,
                        json={"current_password": PASSWORD},
                    )
                )
                owned_requests.append(removal)
                done, _ = await asyncio.wait([removal], timeout=30)
                if not done:
                    raise TimeoutError("Own-account removal did not finish before gate release")
            except BaseException as exc:
                fixture_error = exc
            finally:
                resume_user_lock.set()
                if not ready.done():
                    ready.cancel()
                try:
                    await asyncio.gather(ready, return_exceptions=True)
                except BaseException as exc:
                    if fixture_error is None:
                        fixture_error = exc
                finally:
                    responses = await _drain_owned_requests(owned_requests)

    except BaseException as exc:
        raise RuntimeError("The owned client context failed") from exc

    # Fixture and actor faults stay nonassertion errors. The intended oracle
    # is the endpoint response after real removal and complete actor cleanup.
    if fixture_error is not None:
        raise RuntimeError("The account-removal schedule was not established") from fixture_error
    if len(responses) != 2 or responses[1].status_code != 204:
        raise RuntimeError("The separate own-account removal did not commit successfully")
    if not gate_claimed or len(intercepted_session_ids) != 1:
        raise RuntimeError("The scheduler did not observe exactly one pending locking reload")
    assert responses[0].status_code == 401, responses[0].text
    async with get_sessionmaker()() as db:
        account = await db.get(User, user_id)
        assert account is not None and account.deleted_at is not None
        assert account.totp_state is None and account.totp_secret_enc is None
        assert (
            await db.scalar(select(func.count()).select_from(Farm).where(Farm.owner_id == user_id))
            == 0
        )
