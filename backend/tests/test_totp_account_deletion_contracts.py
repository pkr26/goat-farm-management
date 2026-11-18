"""In-flight public TOTP requests recheck a genuinely removed own account."""

import asyncio
import base64
import hmac
import time
from collections.abc import Iterator
from contextlib import suppress
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

from .test_totp import stable_totp_key  # noqa: F401

PASSWORD = "ownerpass123"
TOTP_STEP_SECONDS = 30
type Seam = Literal[
    "confirm_first_snapshot",
    "confirm_final_reload",
    "regenerate_first_snapshot",
    "regenerate_final_reload",
]


@pytest.fixture(autouse=True)
def _enabled_policy(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[None]:
    request.getfixturevalue("stable_totp_key")
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


def _json_object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise RuntimeError("Public setup returned an unexpected JSON object")
    return cast(dict[str, object], value)


def _required_string(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeError("Public setup omitted a required string")
    return value


def _current_code(secret: str) -> tuple[str, int]:
    step = int(time.time() // TOTP_STEP_SECONDS)
    digest = hmac.new(base64.b32decode(secret), step.to_bytes(8, "big"), "sha1").digest()
    offset = digest[-1] & 0x0F
    truncated = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFF_FFFF
    return f"{truncated % 1_000_000:06d}", step


async def _wait_for_later_step(activation_step: int) -> None:
    async with asyncio.timeout(45):
        remaining = (activation_step + 1) * TOTP_STEP_SECONDS - time.time()
        if remaining > 0:
            await asyncio.sleep(remaining + 0.05)
        if int(time.time() // TOTP_STEP_SECONDS) <= activation_step:
            raise RuntimeError("The real TOTP clock did not advance to a later step")


async def _request(
    browser: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, str] | None = None,
    timeout_seconds: float = 30,
) -> httpx.Response:
    async with asyncio.timeout(timeout_seconds):
        return await browser.request(method, path, headers=headers, json=payload)


def _require_recovery_response(response: httpx.Response) -> None:
    if response.status_code != 200:
        raise RuntimeError(f"Public factor control failed: {response.status_code}")
    codes = _json_object(response.json()).get("codes")
    if not isinstance(codes, list) or not codes or not all(isinstance(code, str) for code in codes):
        raise RuntimeError("Public factor control did not return its actual recovery codes")


async def _public_factor(
    browser: httpx.AsyncClient, seam: Seam, email: str
) -> tuple[dict[str, str], int, str]:
    registered = await _request(
        browser, "POST", "/api/auth/register", payload={"email": email, "password": PASSWORD}
    )
    if registered.status_code != 201:
        raise RuntimeError(f"Public registration failed: {registered.status_code}")
    body = _json_object(registered.json())
    user_id = _json_object(body.get("user")).get("id")
    if type(user_id) is not int or user_id <= 0:
        raise RuntimeError("Public registration returned an invalid user identity")
    headers = {"Authorization": f"Bearer {_required_string(body.get('access_token'))}"}
    farms = await _request(browser, "GET", "/api/auth/farms", headers=headers)
    if farms.status_code != 200 or farms.json() != []:
        raise RuntimeError("The removable public account unexpectedly owns or lists a farm")
    enrolled = await _request(
        browser,
        "POST",
        "/api/auth/totp/enroll",
        headers=headers,
        payload={"current_password": PASSWORD},
    )
    if enrolled.status_code != 200:
        raise RuntimeError(f"Public factor enrollment failed: {enrolled.status_code}")
    secret = _required_string(_json_object(enrolled.json()).get("secret"))
    if seam.startswith("regenerate_"):
        code, activation_step = _current_code(secret)
        activated = await _request(
            browser, "POST", "/api/auth/totp/confirm", headers=headers, payload={"code": code}
        )
        _require_recovery_response(activated)
        # Activation consumed its actual step. Wait for the real next step;
        # no future code, fake clock or stored replay-state change is needed.
        await _wait_for_later_step(activation_step)
    return headers, user_id, secret


def _operation(seam: Seam, secret: str) -> tuple[str, dict[str, str]]:
    code, _step = _current_code(secret)
    if seam.startswith("confirm_"):
        return "/api/auth/totp/confirm", {"code": code}
    return "/api/auth/totp/recovery/regenerate", {"current_password": PASSWORD, "code": code}


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


@pytest.mark.parametrize(
    "seam",
    [
        "confirm_first_snapshot",
        "confirm_final_reload",
        "regenerate_first_snapshot",
        "regenerate_final_reload",
    ],
)
async def test_public_account_removal_wins_before_totp_revalidation(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    seam: Seam,
) -> None:
    reached_seam = asyncio.Event()
    resume_endpoint = asyncio.Event()
    gate_claimed = False
    gate_returned = False
    gate_sites: list[Seam] = []
    server_task: object | None = None
    server_request: Request | None = None
    real_current_user = deps.current_user
    real_prepare = auth_api._prepare_totp_recovery_codes
    real_password = auth_api._confirm_current_password
    responses: list[httpx.Response] = []
    fixture_error: BaseException | None = None

    try:
        async with _browser(client) as requester, _browser(client) as remover:
            # Establish the normal public200 contract with a distinct real
            # account before installing any scheduling wrapper.
            async with _browser(client) as control:
                control_headers, _, control_secret = await _public_factor(
                    control, seam, f"totp-removal-control-{seam}@farm.in"
                )
                control_path, control_payload = _operation(seam, control_secret)
                control_response = await _request(
                    control,
                    "POST",
                    control_path,
                    headers=control_headers,
                    payload=control_payload,
                )
                _require_recovery_response(control_response)

            headers, user_id, secret = await _public_factor(
                requester, seam, f"totp-removal-race-{seam}@farm.in"
            )
            path, payload = _operation(seam, secret)
            request_marker = f"totp-account-removal-{seam}"
            transport = client._transport
            if not isinstance(transport, httpx.ASGITransport) or not isinstance(
                transport.app, FastAPI
            ):
                raise RuntimeError("This contract requires the actual FastAPI application")
            app = transport.app
            if real_current_user in app.dependency_overrides:
                raise RuntimeError("Authentication already has a dependency override")

            async def pause_owned_seam() -> None:
                nonlocal gate_claimed, gate_returned
                if gate_claimed:
                    return
                gate_claimed = True
                gate_sites.append(seam)
                reached_seam.set()
                await resume_endpoint.wait()
                gate_returned = True

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
                    and request.headers.get("X-Request-ID") == request_marker
                    and actual_user.id == user_id
                ):
                    actual_task = asyncio.current_task()
                    if actual_task is None or server_task is not None:
                        raise RuntimeError("The owned request did not have one real server task")
                    # Middleware can dispatch this server task separately from
                    # the outer HTTPX task. Bind only the actual request task.
                    server_task = actual_task
                    server_request = request
                    if seam == "confirm_first_snapshot":
                        await pause_owned_seam()
                return actual_user

            async def prepare_then_pause() -> tuple[list[str], list[str]]:
                prepared = await real_prepare()
                if asyncio.current_task() is server_task:
                    await pause_owned_seam()
                return prepared

            async def confirm_password_then_pause(
                db: AsyncSession,
                request: Request,
                payload_password: str,
                user: User,
                *,
                scope: str,
            ) -> None:
                actual_user_id = user.id
                await real_password(db, request, payload_password, user, scope=scope)
                # The real helper's finally has released identity admission.
                # Its rollback expired user: use only the earlier scalar now.
                if (
                    asyncio.current_task() is server_task
                    and request is server_request
                    and actual_user_id == user_id
                ):
                    await pause_owned_seam()

            with monkeypatch.context() as schedule_patch:
                schedule_patch.setitem(
                    app.dependency_overrides, real_current_user, identify_after_real_authentication
                )
                if seam in {"confirm_final_reload", "regenerate_final_reload"}:
                    schedule_patch.setattr(
                        auth_api, "_prepare_totp_recovery_codes", prepare_then_pause
                    )
                elif seam == "regenerate_first_snapshot":
                    schedule_patch.setattr(
                        auth_api, "_confirm_current_password", confirm_password_then_pause
                    )
                pending_request = asyncio.create_task(
                    _request(
                        requester,
                        "POST",
                        path,
                        headers=headers | {"X-Request-ID": request_marker},
                        payload=payload,
                        timeout_seconds=90,
                    )
                )
                ready = asyncio.create_task(reached_seam.wait())
                owned_requests = [pending_request]
                try:
                    completed, _ = await asyncio.wait(
                        [pending_request, ready], timeout=20, return_when=asyncio.FIRST_COMPLETED
                    )
                    if not completed or not reached_seam.is_set() or pending_request.done():
                        raise RuntimeError("The owned TOTP request did not reach its genuine seam")
                    removal = asyncio.create_task(
                        _request(
                            remover,
                            "DELETE",
                            "/api/auth/account",
                            headers=headers,
                            payload={"current_password": PASSWORD},
                        )
                    )
                    owned_requests.append(removal)
                    done, _ = await asyncio.wait([removal], timeout=30)
                    if not done:
                        raise RuntimeError(
                            "Own-account deletion did not finish before gate release"
                        )
                    if removal.result().status_code != 204:
                        raise RuntimeError("The genuine own-account deletion did not commit204")
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
    except BaseException as exc:
        raise RuntimeError("The owned TOTP client or setup context failed") from exc

    # Every gate, actor, client and scheduling override has finished before
    # the sole business oracle. Unknown actor/setup failures stay errors.
    if fixture_error is not None:
        raise RuntimeError(
            "The public TOTP deletion schedule was not established"
        ) from fixture_error
    if len(responses) != 2 or responses[1].status_code != 204:
        raise RuntimeError("The independently completed public deletion was not observed")
    if not gate_claimed or not gate_returned or gate_sites != [seam] or server_request is None:
        raise RuntimeError("The scheduler did not complete exactly one owned genuine seam")
    assert responses[0].status_code == 401
