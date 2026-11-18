"""Factor disable observes enrollment committed after genuine authentication."""

import asyncio
from collections.abc import Iterator
from typing import Annotated

import httpx
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import func, select

from app import deps
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


async def test_factor_enrollment_wins_before_authenticated_disable_reloads_the_user(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation = "totp_disable"
    authenticated = asyncio.Event()
    resume_endpoint = asyncio.Event()
    gate_claimed = False
    authenticated_user_ids: list[int] = []
    real_current_user = deps.current_user
    responses: list[httpx.Response] = []
    fixture_error: BaseException | None = None

    try:
        async with _browser(client) as requester, _browser(client) as enroller:
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

            transport = client._transport
            if not isinstance(transport, httpx.ASGITransport) or not isinstance(
                transport.app, FastAPI
            ):
                raise RuntimeError("This contract requires the actual FastAPI application")
            app = transport.app
            if real_current_user in app.dependency_overrides:
                raise RuntimeError("Authentication already has a dependency override")
            path = "/api/auth/totp/disable"

            async def pause_after_real_authentication(
                request: Request,
                db: deps.DbSession,
                credentials: Annotated[
                    HTTPAuthorizationCredentials | None, Depends(deps._bearer_scheme)
                ],
            ) -> User:
                nonlocal gate_claimed
                # Delegate all credential, database, generation and family checks
                # to the unchanged dependency. Only delay its successful return.
                actual_user = await real_current_user(request, db, credentials)
                if not gate_claimed and request.url.path == path and actual_user.id == user_id:
                    gate_claimed = True
                    authenticated_user_ids.append(actual_user.id)
                    authenticated.set()
                    await resume_endpoint.wait()
                return actual_user

            monkeypatch.setitem(
                app.dependency_overrides, real_current_user, pause_after_real_authentication
            )
            payload = {"current_password": PASSWORD, "code": "123456"}
            request_headers = headers | {"Idempotency-Key": f"lifecycle-{operation}-own-account"}
            pending_request = asyncio.create_task(
                requester.request(
                    "POST",
                    path,
                    headers=request_headers,
                    json=payload,
                )
            )
            ready = asyncio.create_task(authenticated.wait())
            owned_requests = [pending_request]
            try:
                completed, _ = await asyncio.wait(
                    [pending_request, ready], timeout=15, return_when=asyncio.FIRST_COMPLETED
                )
                if not completed or not authenticated.is_set():
                    raise RuntimeError(
                        "The pending endpoint did not complete genuine authentication"
                    )
                enrollment = asyncio.create_task(
                    enroller.request(
                        "POST",
                        "/api/auth/totp/enroll",
                        headers=headers,
                        json={"current_password": PASSWORD},
                    )
                )
                owned_requests.append(enrollment)
                done, _ = await asyncio.wait([enrollment], timeout=30)
                if not done:
                    raise TimeoutError("Own-account enrollment did not finish before gate release")
            except BaseException as exc:
                fixture_error = exc
            finally:
                resume_endpoint.set()
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
    # is the endpoint response after real enrollment and complete actor cleanup.
    if fixture_error is not None:
        raise RuntimeError("The factor-enrollment schedule was not established") from fixture_error
    if len(responses) != 2 or responses[1].status_code != 200:
        raise RuntimeError("The separate own-account enrollment did not commit successfully")
    if not gate_claimed or authenticated_user_ids != [user_id]:
        raise RuntimeError("The scheduler did not delay exactly one authenticated request")
    assert responses[0].status_code == 204, responses[0].text
    async with get_sessionmaker()() as db:
        account = await db.get(User, user_id)
        assert account is not None and account.deleted_at is None
        assert account.totp_state is None and account.totp_secret_enc is None
        assert (
            await db.scalar(select(func.count()).select_from(Farm).where(Farm.owner_id == user_id))
            == 0
        )
