"""Real ownership winners precede an already-confirmed losing transfer."""

import asyncio
from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass
from typing import Annotated, Literal, cast

import httpx
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app import deps
from app.api import auth as auth_api
from app.core.config import Settings, get_settings
from app.models import User
from app.ratelimit import auth_limiter

OWNER_PASSWORD = "ownerpass123"
WORKER_PASSWORD = "workerpass123"
SUCCESSOR_PASSWORD = "successor-owned-pass456"
type Outcome = Literal["ownership_lost", "actor_deleted"]


@dataclass(frozen=True, slots=True)
class TransferSetup:
    owner_headers: dict[str, str]
    owner_user_id: int
    farm_id: int
    membership_id: int
    successor_user_id: int
    successor_email: str


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


async def _request(
    browser: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, object] | None = None,
    timeout_seconds: float = 30,
) -> httpx.Response:
    async with asyncio.timeout(timeout_seconds):
        return await browser.request(method, path, headers=headers, json=payload)


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise RuntimeError("Public setup returned an unexpected JSON object")
    return cast(dict[str, object], value)


def _objects(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise RuntimeError("Public setup returned an unexpected JSON list")
    return [_object(item) for item in value]


def _identity(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise RuntimeError("Public setup returned an invalid identity")
    return value


def _bearer(response: httpx.Response, expected_status: int) -> tuple[dict[str, str], int]:
    if response.status_code != expected_status:
        raise RuntimeError(f"Public credential setup failed: {response.status_code}")
    body = _object(response.json())
    token = body.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("Public credential setup omitted the actual access token")
    return {"Authorization": f"Bearer {token}"}, _identity(_object(body.get("user")).get("id"))


async def _public_setup(
    owner: httpx.AsyncClient, successor: httpx.AsyncClient, label: str
) -> TransferSetup:
    registered = await _request(
        owner,
        "POST",
        "/api/auth/register",
        payload={"email": f"transfer-owner-{label}@farm.in", "password": OWNER_PASSWORD},
    )
    owner_headers, owner_user_id = _bearer(registered, 201)
    created = await _request(
        owner,
        "POST",
        "/api/auth/farms",
        headers=owner_headers,
        payload={"name": f"Transfer {label}", "timezone": "UTC"},
    )
    if created.status_code != 201:
        raise RuntimeError(f"Public farm creation failed: {created.status_code}")
    farm_id = _identity(_object(created.json()).get("id"))
    owner_headers |= {"X-Farm-Id": str(farm_id)}
    team = await _request(owner, "GET", "/api/team", headers=owner_headers)
    if team.status_code != 200:
        raise RuntimeError(f"Public team setup failed: {team.status_code}")
    roles = _objects(_object(team.json()).get("roles"))
    role = next((role for role in roles if role.get("code") == "CLEANER"), None)
    if role is None:
        raise RuntimeError("The ordinary password-worker role was not available")
    successor_email = f"transfer-successor-{label}@farm.in"
    provisioned = await _request(
        owner,
        "POST",
        "/api/team/workers",
        headers=owner_headers,
        payload={
            "email": successor_email,
            "name": "Future owner",
            "password": WORKER_PASSWORD,
            "role_id": _identity(role.get("id")),
        },
    )
    if provisioned.status_code != 201:
        raise RuntimeError(f"Public successor provisioning failed: {provisioned.status_code}")
    membership = _object(provisioned.json())
    membership_id = _identity(membership.get("id"))
    successor_user_id = _identity(membership.get("user_id"))
    if membership.get("is_active") is not True:
        raise RuntimeError("The publicly provisioned successor is not an active member")
    signed_in = await _request(
        successor,
        "POST",
        "/api/auth/login",
        headers={"X-No-Auto-Rotate": "1"},
        payload={"email": successor_email, "password": WORKER_PASSWORD},
    )
    initial_headers, signed_in_id = _bearer(signed_in, 200)
    if signed_in_id != successor_user_id:
        raise RuntimeError("The real successor login returned a different user")
    rotated = await _request(
        successor,
        "POST",
        "/api/auth/change-password",
        headers=initial_headers,
        payload={"current_password": WORKER_PASSWORD, "new_password": SUCCESSOR_PASSWORD},
    )
    successor_headers, rotated_id = _bearer(rotated, 200)
    me = await _request(successor, "GET", "/api/auth/me", headers=successor_headers)
    if me.status_code != 200:
        raise RuntimeError(f"Public successor rotation check failed: {me.status_code}")
    actual_successor = _object(me.json())
    if (
        rotated_id != successor_user_id
        or actual_successor.get("id") != successor_user_id
        or actual_successor.get("must_change_password") is not False
    ):
        raise RuntimeError("The actual successor did not complete password possession")
    return TransferSetup(
        owner_headers,
        owner_user_id,
        farm_id,
        membership_id,
        successor_user_id,
        successor_email,
    )


def _transfer(setup: TransferSetup) -> tuple[str, dict[str, object]]:
    return f"/api/auth/farms/{setup.farm_id}/transfer-ownership", {
        "membership_id": setup.membership_id,
        "current_password": OWNER_PASSWORD,
    }


async def _require_relinquished_owner(browser: httpx.AsyncClient, setup: TransferSetup) -> None:
    exported = await _request(
        browser, "GET", "/api/auth/account/export", headers=setup.owner_headers
    )
    if exported.status_code != 200:
        raise RuntimeError(f"Former-owner public export failed: {exported.status_code}")
    if _object(exported.json()).get("owned_farms") != []:
        raise RuntimeError("The real ownership winner did not relinquish the last owned farm")


async def _require_successor_ownership(browser: httpx.AsyncClient, setup: TransferSetup) -> None:
    # The winning route legitimately bumps S's generation. Reauthenticate S
    # through public password login instead of reusing its pre-transfer token.
    signed_in = await _request(
        browser,
        "POST",
        "/api/auth/login",
        payload={"email": setup.successor_email, "password": SUCCESSOR_PASSWORD},
    )
    headers, user_id = _bearer(signed_in, 200)
    if user_id != setup.successor_user_id:
        raise RuntimeError("Public successor reauthentication changed identity")
    exported = await _request(browser, "GET", "/api/auth/account/export", headers=headers)
    if exported.status_code != 200:
        raise RuntimeError(f"Successor public export failed: {exported.status_code}")
    owned = _objects(_object(exported.json()).get("owned_farms"))
    if not any(farm.get("id") == setup.farm_id for farm in owned):
        raise RuntimeError("The successor did not retain the actual transferred farm")


async def _finish_ready(task: asyncio.Task[bool]) -> None:
    if not task.done():
        task.cancel()
    done, _ = await asyncio.wait([task], timeout=10)
    if not done:
        raise RuntimeError("The owned readiness task did not finish cancellation")
    with suppress(asyncio.CancelledError):
        task.result()


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
        raise RuntimeError("Owned requests exceeded the cleanup deadline")
    return [outcome for outcome in outcomes if isinstance(outcome, httpx.Response)]


@pytest.mark.parametrize("outcome", ["ownership_lost", "actor_deleted"])
async def test_public_transfer_winner_precedes_the_confirmed_losing_request(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    outcome: Outcome,
) -> None:
    reached_seam = asyncio.Event()
    resume_endpoint = asyncio.Event()
    gate_claimed = False
    gate_returned = False
    server_task: object | None = None
    server_request: Request | None = None
    confirmed_user_ids: list[int] = []
    real_current_user = deps.current_user
    real_password = auth_api._confirm_current_password
    responses: list[httpx.Response] = []
    fixture_error: BaseException | None = None
    expected_status = 401 if outcome == "actor_deleted" else 404

    try:
        async with (
            _browser(client) as requester,
            _browser(client) as winner,
            _browser(client) as successor,
        ):
            # A distinct no-race transfer and public ownership read establish
            # the genuine setup before any request is delayed.
            async with _browser(client) as control_owner, _browser(client) as control_successor:
                control = await _public_setup(
                    control_owner, control_successor, f"control-{outcome}"
                )
                control_path, control_payload = _transfer(control)
                transferred = await _request(
                    control_owner,
                    "POST",
                    control_path,
                    headers=control.owner_headers,
                    payload=control_payload,
                )
                if transferred.status_code != 200:
                    raise RuntimeError(f"Public transfer control failed: {transferred.status_code}")
                await _require_relinquished_owner(control_owner, control)
                if outcome == "actor_deleted":
                    removed_control = await _request(
                        control_owner,
                        "DELETE",
                        "/api/auth/account",
                        headers=control.owner_headers,
                        payload={"current_password": OWNER_PASSWORD},
                    )
                    if removed_control.status_code != 204:
                        raise RuntimeError("The public former-owner deletion control did not204")
                await _require_successor_ownership(control_successor, control)

            setup = await _public_setup(requester, successor, f"race-{outcome}")
            path, payload = _transfer(setup)
            marker = f"owned-transfer-race-{outcome}"
            transport = client._transport
            if not isinstance(transport, httpx.ASGITransport) or not isinstance(
                transport.app, FastAPI
            ):
                raise RuntimeError("This contract requires the actual FastAPI application")
            app = transport.app
            if real_current_user in app.dependency_overrides:
                raise RuntimeError("Authentication already has a dependency override")

            async def identify_after_real_authentication(
                request: Request,
                db: deps.DbSession,
                credentials: Annotated[
                    HTTPAuthorizationCredentials | None, Depends(deps._bearer_scheme)
                ],
            ) -> User:
                nonlocal server_task, server_request
                actual_user = await real_current_user(request, db, credentials)
                if (
                    request.method == "POST"
                    and request.url.path == path
                    and request.headers.get("X-Request-ID") == marker
                    and actual_user.id == setup.owner_user_id
                ):
                    task = asyncio.current_task()
                    if task is None or server_task is not None:
                        raise RuntimeError("The losing transfer did not identify one server task")
                    server_task = task
                    server_request = request
                return actual_user

            async def confirm_password_then_pause(
                db: AsyncSession,
                request: Request,
                payload_password: str,
                user: User,
                *,
                scope: str,
            ) -> None:
                nonlocal gate_claimed, gate_returned
                actual_user_id = user.id
                await real_password(db, request, payload_password, user, scope=scope)
                # The unchanged helper already rolled back and its finally
                # released admission. Its caller has not acquired the advisory
                # lock yet; waiting at the later User query would block W.
                if (
                    not gate_claimed
                    and asyncio.current_task() is server_task
                    and request is server_request
                    and actual_user_id == setup.owner_user_id
                ):
                    gate_claimed = True
                    confirmed_user_ids.append(actual_user_id)
                    reached_seam.set()
                    await resume_endpoint.wait()
                    gate_returned = True

            with monkeypatch.context() as schedule_patch:
                schedule_patch.setitem(
                    app.dependency_overrides, real_current_user, identify_after_real_authentication
                )
                schedule_patch.setattr(
                    auth_api, "_confirm_current_password", confirm_password_then_pause
                )
                pending_request = asyncio.create_task(
                    _request(
                        requester,
                        "POST",
                        path,
                        headers=setup.owner_headers | {"X-Request-ID": marker},
                        payload=payload,
                        timeout_seconds=150,
                    )
                )
                ready = asyncio.create_task(reached_seam.wait())
                owned_requests = [pending_request]
                try:
                    completed, _ = await asyncio.wait(
                        [pending_request, ready], timeout=20, return_when=asyncio.FIRST_COMPLETED
                    )
                    if not completed or not reached_seam.is_set() or pending_request.done():
                        raise RuntimeError(
                            "The losing transfer did not finish genuine confirmation"
                        )
                    winning_request = asyncio.create_task(
                        _request(
                            winner,
                            "POST",
                            path,
                            headers=setup.owner_headers,
                            payload=payload,
                        )
                    )
                    owned_requests.append(winning_request)
                    done, _ = await asyncio.wait([winning_request], timeout=30)
                    if not done or winning_request.result().status_code != 200:
                        raise RuntimeError("The independent public ownership transfer did not200")
                    await _require_relinquished_owner(winner, setup)
                    if outcome == "actor_deleted":
                        removal = asyncio.create_task(
                            _request(
                                winner,
                                "DELETE",
                                "/api/auth/account",
                                headers=setup.owner_headers,
                                payload={"current_password": OWNER_PASSWORD},
                            )
                        )
                        owned_requests.append(removal)
                        done, _ = await asyncio.wait([removal], timeout=30)
                        if not done or removal.result().status_code != 204:
                            raise RuntimeError("The genuine former-owner deletion did not204")
                except BaseException as exc:
                    fixture_error = exc
                finally:
                    resume_endpoint.set()
                    try:
                        await _finish_ready(ready)
                    except BaseException as exc:
                        if fixture_error is None:
                            fixture_error = exc
                    try:
                        responses = await _drain_owned_requests(owned_requests)
                    except BaseException as exc:
                        if fixture_error is None:
                            fixture_error = exc
            if fixture_error is None:
                await _require_successor_ownership(successor, setup)
    except BaseException as exc:
        raise RuntimeError("The owned transfer setup or client context failed") from exc

    # All actors, clients, gates and overrides finish before the sole public
    # business assertion. Unknown fixture and cleanup faults remain errors.
    if fixture_error is not None:
        raise RuntimeError(
            "The genuine public transfer schedule was not established"
        ) from fixture_error
    expected_responses = 3 if outcome == "actor_deleted" else 2
    if len(responses) != expected_responses or responses[1].status_code != 200:
        raise RuntimeError("The independent public transfer winner was not observed")
    if outcome == "actor_deleted" and responses[2].status_code != 204:
        raise RuntimeError("The completed former-owner public deletion was not observed")
    if (
        not gate_claimed
        or not gate_returned
        or confirmed_user_ids != [setup.owner_user_id]
        or server_request is None
    ):
        raise RuntimeError("The scheduler did not finish exactly one real confirmed transfer seam")
    assert responses[0].status_code == expected_status
