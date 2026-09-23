"""Worker-tablet PIN authentication (ITEM 2 Phase 1, 2026-09-21 playbook).

Covers: PIN provisioning (and the must-change-password fence exemption), the
owner-only reset that revokes sessions, the throttled worker-login exchange
(success, wrong-PIN lockout, spray scope, unknown-pair parity, tombstone /
inactive / TOTP / must-change refusals), the unauthenticated roster's shape
and throttle, idempotent duty completion/skip replay, and the production
PIN-length validator.
"""

import json
from collections.abc import Iterator

import httpx
import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db import get_sessionmaker
from app.models import FarmMembership
from app.ratelimit import auth_limiter
from app.utils import today

from .conftest import OWNER_PW, owner_with_farm

OWNER_EMAIL = "pin-owner@farm.in"


async def _make_pin_worker(
    client: httpx.AsyncClient, owner: dict, *, pin: str = "4321", email: str
) -> tuple[int, int]:
    """Create a PIN-only worker (no password — exactly-one credential); returns
    (membership_id, farm_id)."""
    role = await client.post(
        "/api/team/roles",
        json={"name": f"Tablet role {email}", "permissions": ["tasks.view", "tasks.complete"]},
        headers=owner,
    )
    assert role.status_code == 201, role.text
    role_id = role.json()["id"]
    created = await client.post(
        "/api/team/workers",
        json={
            "name": "Pin Worker",
            "email": email,
            "role_id": role_id,
            "pin": pin,
        },
        headers={**owner, "Idempotency-Key": f"create-{email}"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["pin_set"] is True
    return body["id"], int(owner["X-Farm-Id"])


async def _worker_login(
    client: httpx.AsyncClient, farm_id: int, membership_id: int, pin: str
) -> httpx.Response:
    return await client.post(
        "/api/auth/worker-login",
        json={"farm_id": farm_id, "membership_id": membership_id, "pin": pin},
    )


@pytest.fixture(autouse=True)
def _clear_pin_limiters() -> Iterator[None]:
    # In-memory throttle state must not leak between tests (DB ids restart
    # every test while the limiter process survives — test_totp convention).
    auth_limiter.clear()
    yield
    auth_limiter.clear()


@pytest.fixture
def rate_limits_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The suite's conftest disables auth rate limiting globally; the throttle
    tests here opt back in (same pattern as test_totp)."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "auth_rate_limit_enabled", True)
    yield


async def test_pin_provisioning_skips_the_must_change_fence(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email=OWNER_EMAIL)
    membership_id, farm_id = await _make_pin_worker(client, owner, email="pin-worker-1@farm.in")

    # Roster exposes exactly the tap targets: membership id + display name.
    roster = await client.get("/api/auth/worker-roster", params={"farm_id": farm_id})
    assert roster.status_code == 200, roster.text
    items = roster.json()["items"]
    assert [item["membership_id"] for item in items] == [membership_id]
    assert items[0]["display_name"] == "Pin Worker"

    # The PIN session can act immediately: no must-change-password fence.
    login = await _worker_login(client, farm_id, membership_id, "4321")
    assert login.status_code == 200, login.text
    body = login.json()
    assert body["access_token"] and body["user"]["email"] == "pin-worker-1@farm.in"
    me = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.status_code == 200, me.text


async def test_password_only_worker_is_not_on_the_roster(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="pin-owner-2@farm.in")
    role = await client.post(
        "/api/team/roles",
        json={"name": "Password only role", "permissions": ["tasks.view"]},
        headers=owner,
    )
    role_id = role.json()["id"]
    created = await client.post(
        "/api/team/workers",
        json={
            "name": "Password Only",
            "email": "pin-worker-nopin@farm.in",
            "password": "worker-pass-123",
            "role_id": role_id,
        },
        headers=owner,
    )
    assert created.status_code == 201, created.text
    assert created.json()["pin_set"] is False
    # Password workers keep the must-change fence (owner-chosen credential).
    async with get_sessionmaker()() as db:
        from app.models import User

        user = (
            await db.execute(select(User).where(User.email == "pin-worker-nopin@farm.in"))
        ).scalar_one()
        assert user.must_change_password is True

    roster = await client.get(
        "/api/auth/worker-roster", params={"farm_id": int(owner["X-Farm-Id"])}
    )
    assert [i["membership_id"] for i in roster.json()["items"]] == []


async def test_worker_roster_can_be_disabled_entirely(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GOATFARM_WORKER_ROSTER_ENABLED=false closes the enumeration oracle:
    every farm id answers 404 before a roster row is read, with no existence
    signal distinguishing known from unknown farms (the flag is checked ahead
    of both the throttle and the query)."""
    from app.core.config import get_settings

    owner = await owner_with_farm(client, email="roster-off@farm.in")
    _membership_id, farm_id = await _make_pin_worker(
        client, owner, email="roster-off-worker@farm.in"
    )
    enabled = await client.get("/api/auth/worker-roster", params={"farm_id": farm_id})
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["items"]

    monkeypatch.setattr(get_settings(), "worker_roster_enabled", False)
    disabled = await client.get("/api/auth/worker-roster", params={"farm_id": farm_id})
    assert disabled.status_code == 404, disabled.text
    assert "items" not in disabled.json()
    # Unknown farms answer identically — the disabled endpoint is not an oracle.
    unknown = await client.get("/api/auth/worker-roster", params={"farm_id": farm_id + 12345})
    assert unknown.status_code == 404


async def test_worker_create_requires_exactly_one_credential(
    client: httpx.AsyncClient,
) -> None:
    """Both credentials at once would leave the owner-chosen password
    un-rotated on a PIN-usable account; neither mints an unusable worker.
    The 2026-09-22 verification caught the both-at-once case sailing
    through with the must-change fence silently skipped."""
    owner = await owner_with_farm(client, email="pin-owner-exactly@farm.in")
    role = await client.post(
        "/api/team/roles",
        json={"name": "Exactly one role", "permissions": ["tasks.view"]},
        headers=owner,
    )
    role_id = role.json()["id"]

    both = await client.post(
        "/api/team/workers",
        json={
            "name": "Both Credentials",
            "email": "pin-worker-both@farm.in",
            "password": "worker-pass-123",
            "role_id": role_id,
            "pin": "4321",
        },
        headers=owner,
    )
    assert both.status_code == 400, both.text
    assert "exactly one credential" in both.json()["detail"]

    neither = await client.post(
        "/api/team/workers",
        json={
            "name": "No Credentials",
            "email": "pin-worker-neither@farm.in",
            "role_id": role_id,
        },
        headers=owner,
    )
    assert neither.status_code == 400, neither.text
    assert "exactly one credential" in neither.json()["detail"]

    # Nothing was provisioned by the refused attempts.
    roster = await client.get(
        "/api/auth/worker-roster", params={"farm_id": int(owner["X-Farm-Id"])}
    )
    assert roster.json()["items"] == []


async def test_pin_only_worker_has_no_usable_web_password(
    client: httpx.AsyncClient,
) -> None:
    """A PIN-only worker's stored password hash is of an unguessable random
    secret: the web password flow must refuse every guess, and the
    must-change fence stays off (there is no owner-chosen password to
    rotate)."""
    owner = await owner_with_farm(client, email="pin-owner-nopw@farm.in")
    membership_id, farm_id = await _make_pin_worker(client, owner, email="pin-worker-nopw@farm.in")

    async with get_sessionmaker()() as db:
        from app.models import User

        user = (
            await db.execute(select(User).where(User.email == "pin-worker-nopw@farm.in"))
        ).scalar_one()
        assert user.must_change_password is False
        assert user.password_hash  # NOT NULL column holds the random sentinel

    # No web password exists to guess: the classic owner-chosen convention
    # and the PIN itself both fail closed at the login door.
    for guess in ("worker-pass-123", "4321", "password", "pin-worker-nopw"):
        web = await client.post(
            "/api/auth/login", json={"email": "pin-worker-nopw@farm.in", "password": guess}
        )
        assert web.status_code == 401, (guess, web.text)

    # The tablet path still works untouched.
    assert (await _worker_login(client, farm_id, membership_id, "4321")).status_code == 200


async def test_roster_never_exposes_emails_for_nameless_workers(
    client: httpx.AsyncClient,
) -> None:
    """User.display_name falls back to the email when name is NULL; the roster
    is unauthenticated, so a nameless PIN worker must surface the public
    membership id instead — never the email (2026-09-22 verification)."""
    owner = await owner_with_farm(client, email="pin-owner-nameless@farm.in")
    role = await client.post(
        "/api/team/roles",
        json={"name": "Nameless role", "permissions": ["tasks.view"]},
        headers=owner,
    )
    role_id = role.json()["id"]
    created = await client.post(
        "/api/team/workers",
        json={
            "email": "pin-worker-nameless@farm.in",
            "role_id": role_id,
            "pin": "4321",
        },
        headers=owner,
    )
    assert created.status_code == 201, created.text
    membership_id = created.json()["id"]

    roster = await client.get(
        "/api/auth/worker-roster", params={"farm_id": int(owner["X-Farm-Id"])}
    )
    assert roster.status_code == 200, roster.text
    items = roster.json()["items"]
    assert [item["membership_id"] for item in items] == [membership_id]
    assert items[0]["display_name"] == f"Worker {membership_id}"
    assert "pin-worker-nameless@farm.in" not in roster.text


async def test_wrong_pin_locks_the_identity_out_but_not_the_farm(
    client: httpx.AsyncClient,
    rate_limits_on: None,
) -> None:
    owner = await owner_with_farm(client, email="pin-owner-3@farm.in")
    membership_id, farm_id = await _make_pin_worker(client, owner, email="pin-worker-lock@farm.in")

    for _ in range(10):
        refused = await _worker_login(client, farm_id, membership_id, "0000")
        assert refused.status_code == 401
        assert refused.json()["detail"] == "Invalid PIN."

    # The identity key is exhausted: even the RIGHT pin is throttled now.
    throttled = await _worker_login(client, farm_id, membership_id, "4321")
    assert throttled.status_code == 429, throttled.text

    # A second worker on the same farm can still sign in: the lockout is per
    # (IP, farm, membership), not per farm.
    membership_b, _ = await _make_pin_worker(
        client, owner, pin="778899", email="pin-worker-lock-b@farm.in"
    )
    ok = await _worker_login(client, farm_id, membership_b, "778899")
    assert ok.status_code == 200, ok.text


async def test_spray_scope_limits_farm_wide_pin_guessing(
    client: httpx.AsyncClient,
    rate_limits_on: None,
) -> None:
    owner = await owner_with_farm(client, email="pin-owner-spray@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_a, _ = await _make_pin_worker(
        client, owner, pin="556677", email="pin-worker-spray-a@farm.in"
    )
    membership_b, _ = await _make_pin_worker(
        client, owner, pin="556677", email="pin-worker-spray-b@farm.in"
    )

    # Ten wrong guesses on A exhaust only A's identity budget.
    for _ in range(10):
        assert (await _worker_login(client, farm_id, membership_a, "0000")).status_code == 401
    assert (await _worker_login(client, farm_id, membership_a, "556677")).status_code == 429

    # B is untouched — the lockout is per (IP, farm, membership).
    assert (await _worker_login(client, farm_id, membership_b, "556677")).status_code == 200

    # Charge the (IP, farm) spray scope to its 10x ceiling: even a FRESH
    # identity with the right PIN is refused farm-wide.
    for _ in range(100):
        auth_limiter.record(
            "worker-pin-spray", f"127.0.0.1|{farm_id}", window_seconds=300, max_attempts=100
        )
    assert (await _worker_login(client, farm_id, membership_b, "556677")).status_code == 429


async def test_unknown_pairs_answer_the_same_generic_401(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="pin-owner-unknown@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    # Unknown membership on a REAL farm, and a membership on a nonexistent
    # farm: both answer the exact login-shape 401 (dummy-hash parity keeps
    # the timing indistinguishable from a known pair).
    for farm, membership in [(farm_id, 999_999_999), (999_999_999, 42)]:
        refused = await _worker_login(client, farm, membership, "4321")
        assert refused.status_code == 401
        assert refused.json()["detail"] == "Invalid PIN."


async def test_tombstoned_inactive_and_totp_accounts_cannot_pin_login(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="pin-owner-refuse@farm.in")
    farm_id = int(owner["X-Farm-Id"])

    # Inactive membership: refused with the generic 401.
    membership_id, _ = await _make_pin_worker(client, owner, email="pin-worker-inactive@farm.in")
    deactivated = await client.put(
        f"/api/team/workers/{membership_id}/status",
        json={"is_active": False},
        headers=owner,
    )
    assert deactivated.status_code == 200, deactivated.text
    assert (await _worker_login(client, farm_id, membership_id, "4321")).status_code == 401

    # Tombstoned account: the roster drops them and login answers 401.
    membership_b, _ = await _make_pin_worker(client, owner, email="pin-worker-tomb@farm.in")
    async with get_sessionmaker()() as db:
        from app.models import User

        user = (
            await db.execute(select(User).where(User.email == "pin-worker-tomb@farm.in"))
        ).scalar_one()
        # Real deletion scrubs the profile (ck_users_deleted_profile_scrubbed).
        user.name = None
        user.email = f"deleted-{user.id}@deleted.invalid"
        user.deleted_at = today()
        await db.commit()
    assert (await _worker_login(client, farm_id, membership_b, "4321")).status_code == 401
    roster = await client.get("/api/auth/worker-roster", params={"farm_id": farm_id})
    assert membership_b not in [i["membership_id"] for i in roster.json()["items"]]

    # TOTP-active account: PIN login refuses with its own 403 (the tablet is
    # not an authenticator). The account predates this farm, so attach the
    # membership + PIN directly.
    from .conftest import register as _register

    pin_headers = await _register(client, "pin-worker-totp@farm.in")
    enroll = await client.post(
        "/api/auth/totp/enroll", json={"current_password": OWNER_PW}, headers=pin_headers
    )
    assert enroll.status_code == 200
    from .test_totp import _current_code

    code, _step = _current_code(enroll.json()["secret"])
    confirm = await client.post("/api/auth/totp/confirm", json={"code": code}, headers=pin_headers)
    assert confirm.status_code == 200, confirm.text

    role = await client.post(
        "/api/team/roles",
        json={"name": "TOTP role", "permissions": ["tasks.view"]},
        headers=owner,
    )
    role_id = role.json()["id"]
    async with get_sessionmaker()() as db:
        from app.models import User

        totp_user = (
            await db.execute(select(User).where(User.email == "pin-worker-totp@farm.in"))
        ).scalar_one()
        membership = FarmMembership(
            farm_id=farm_id, user_id=totp_user.id, role_id=role_id, is_active=True
        )
        db.add(membership)
        await db.flush()
        from app.security import hash_password_async

        membership.pin_hash = await hash_password_async("4321")
        await db.commit()
        membership_id_c = membership.id

    refused = await _worker_login(client, farm_id, membership_id_c, "4321")
    assert refused.status_code == 403
    assert "two-factor" in refused.json()["detail"]


async def test_reset_pin_is_owner_only_and_revokes_sessions(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="pin-owner-reset@farm.in")
    membership_id, farm_id = await _make_pin_worker(client, owner, email="pin-worker-reset@farm.in")
    session = await _worker_login(client, farm_id, membership_id, "4321")
    assert session.status_code == 200
    old_token = session.json()["access_token"]

    # A delegated team manager (full team grants, but not the owner) may not
    # rotate PINs — PIN credentials gate an account's sessions.
    manager_role = await client.post(
        "/api/team/roles",
        json={"name": "Manager role", "permissions": ["team.manage"]},
        headers=owner,
    )
    manager_created = await client.post(
        "/api/team/workers",
        json={
            "name": "Team Manager",
            "email": "pin-manager@farm.in",
            "password": "manager-pass-123",
            "role_id": manager_role.json()["id"],
        },
        headers=owner,
    )
    assert manager_created.status_code == 201, manager_created.text
    manager_login = await client.post(
        "/api/auth/login",
        json={"email": "pin-manager@farm.in", "password": "manager-pass-123"},
    )
    manager_token = manager_login.json()["access_token"]
    manager_headers = {
        "Authorization": f"Bearer {manager_token}",
        "X-Farm-Id": str(farm_id),
    }
    forbidden = await client.post(
        f"/api/team/workers/{membership_id}/reset-pin",
        json={"pin": "998877"},
        headers=manager_headers,
    )
    assert forbidden.status_code == 403, forbidden.text

    # The owner rotates the PIN.
    reset = await client.post(
        f"/api/team/workers/{membership_id}/reset-pin",
        json={"pin": "998877"},
        headers={**owner, "Idempotency-Key": "reset-once"},
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["pin_set"] is True

    # The old session died with the rotation (token_version bump).
    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {old_token}"})
    assert me.status_code == 401

    # Old PIN no longer works; the new one does.
    assert (await _worker_login(client, farm_id, membership_id, "4321")).status_code == 401
    fresh = await _worker_login(client, farm_id, membership_id, "998877")
    assert fresh.status_code == 200, fresh.text

    # Idempotent replay returns the same response; a different body 409s.
    replay = await client.post(
        f"/api/team/workers/{membership_id}/reset-pin",
        json={"pin": "998877"},
        headers={**owner, "Idempotency-Key": "reset-once"},
    )
    assert replay.status_code == 200
    first = await client.post(
        f"/api/team/workers/{membership_id}/reset-pin",
        json={"pin": "112233"},
        headers={**owner, "Idempotency-Key": "reset-once"},
    )
    assert first.status_code == 409, first.text


async def test_roster_throttles_per_ip(client: httpx.AsyncClient, rate_limits_on: None) -> None:

    owner = await owner_with_farm(client, email="pin-owner-roster@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    for _ in range(30):
        ok = await client.get("/api/auth/worker-roster", params={"farm_id": farm_id})
        assert ok.status_code == 200
    throttled = await client.get("/api/auth/worker-roster", params={"farm_id": farm_id})
    assert throttled.status_code == 429, throttled.text


async def test_pin_length_floor_follows_the_deployment_setting(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="pin-owner-length@farm.in")
    role = await client.post(
        "/api/team/roles",
        json={"name": "Duty role", "permissions": ["tasks.view", "tasks.complete"]},
        headers=owner,
    )
    role_id = role.json()["id"]
    too_short = await client.post(
        "/api/team/workers",
        json={
            "name": "Short Pin",
            "email": "pin-worker-short@farm.in",
            "password": "worker-pass-123",
            "role_id": role_id,
            "pin": "12",
        },
        headers=owner,
    )
    assert too_short.status_code == 422, too_short.text  # schema: 4-12 digits


_PROD_OVERRIDES = {
    "environment": "production",
    "cookie_secure": True,
    "cors_origins": ["https://app.example.com"],
    "allowed_hosts": ["app.example.com"],
    "db_sslmode": "verify-full",
    "min_password_length": 12,
    "idempotency_request_hmac_secret": "x" * 40,
    "totp_encryption_key": "A" * 43,
}


def test_production_settings_raise_the_pin_floor() -> None:
    # Default tightens automatically; an explicit short floor is refused.
    auto = Settings(**_PROD_OVERRIDES)
    assert auto.worker_pin_min_length == 6
    with pytest.raises(ValueError, match="WORKER_PIN_MIN_LENGTH"):
        Settings(**_PROD_OVERRIDES, worker_pin_min_length=4)


async def test_worker_login_requires_a_json_content_type(client: httpx.AsyncClient) -> None:
    # AUTH-2 parity: /worker-login is a credential endpoint like /login, so a
    # non-JSON body is refused by the content-type guard before any PIN is
    # hashed or any identity is probed.
    owner = await owner_with_farm(client, email="pin-owner-415@farm.in")
    membership_id, farm_id = await _make_pin_worker(client, owner, email="pin-worker-415@farm.in")
    refused = await client.post(
        "/api/auth/worker-login",
        content=json.dumps({"farm_id": farm_id, "membership_id": membership_id, "pin": "4321"}),
        headers={"Content-Type": "text/plain"},
    )
    assert refused.status_code == 415, refused.text
    assert "application/json" in refused.json()["detail"]


async def test_owner_password_reset_blocks_pin_sign_in(client: httpx.AsyncClient) -> None:
    # The must-change fence the PIN provisioning path skips is re-armed by the
    # owner's own password reset: the account's password is the owner-chosen
    # one again, and a PIN session would be a session that can do nothing.
    owner = await owner_with_farm(client, email="pin-owner-must@farm.in")
    membership_id, farm_id = await _make_pin_worker(client, owner, email="pin-worker-must@farm.in")
    assert (await _worker_login(client, farm_id, membership_id, "4321")).status_code == 200

    reset = await client.post(
        f"/api/team/workers/{membership_id}/reset-password",
        json={"password": "owner-chosen-123"},
        headers=owner,
    )
    assert reset.status_code == 200, reset.text

    refused = await _worker_login(client, farm_id, membership_id, "4321")
    assert refused.status_code == 403, refused.text
    assert "must change its password" in refused.json()["detail"]


async def test_duty_completion_and_skip_are_idempotently_replayable(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="pin-owner-idem@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id, _ = await _make_pin_worker(client, owner, email="pin-worker-idem@farm.in")
    login = await _worker_login(client, farm_id, membership_id, "4321")
    worker = {
        "Authorization": f"Bearer {login.json()['access_token']}",
        "X-Farm-Id": str(farm_id),
    }

    # A personal manual duty assigned to this worker, completable via API.
    async with get_sessionmaker()() as db:
        user_id = (
            await db.execute(
                select(FarmMembership.user_id).where(FarmMembership.id == membership_id)
            )
        ).scalar_one()
    created = await client.post(
        "/api/tasks",
        json={
            "title": "Tablet duty",
            "due_date": today().isoformat(),
            "category": "OTHER",
            "assigned_user_id": user_id,
        },
        headers=owner,
    )
    assert created.status_code in (201, 200), created.text
    task_id = created.json()["id"]

    key = "offline-retry-1"
    done = await client.post(
        f"/api/tasks/{task_id}/complete",
        headers={**worker, "Idempotency-Key": key},
    )
    assert done.status_code == 200, done.text
    replay = await client.post(
        f"/api/tasks/{task_id}/complete",
        headers={**worker, "Idempotency-Key": key},
    )
    assert replay.status_code == 200, replay.text
    assert replay.headers.get("Idempotency-Replayed") == "true"
    assert replay.json() == done.json()
    # A different body under the SAME operation+key is the standard conflict.
    conflict = await client.post(
        f"/api/tasks/{task_id}/complete",
        json={},
        headers={**worker, "Idempotency-Key": "other-key"},
    )
    # (different key on an already-completed duty → the plain 400)
    assert conflict.status_code == 400, conflict.text

    # Skip replay: a second recurring duty.
    second = await client.post(
        "/api/tasks",
        json={
            "title": "Tablet duty 2",
            "due_date": today().isoformat(),
            "category": "OTHER",
            "assigned_user_id": user_id,
        },
        headers=owner,
    )
    assert second.status_code in (201, 200), second.text
    task2 = second.json()["id"]
    skipped = await client.post(
        f"/api/tasks/{task2}/skip",
        json={"reason": "not needed"},
        headers={**worker, "Idempotency-Key": "skip-1"},
    )
    assert skipped.status_code == 200, skipped.text
    skipped_replay = await client.post(
        f"/api/tasks/{task2}/skip",
        json={"reason": "not needed"},
        headers={**worker, "Idempotency-Key": "skip-1"},
    )
    assert skipped_replay.status_code == 200
    assert skipped_replay.headers.get("Idempotency-Replayed") == "true"
    assert skipped_replay.json() == skipped.json()

    # The standard conflict: the SAME key replayed with a DIFFERENT body is a
    # 409, not a silent replay of the first skip's answer.
    conflict = await client.post(
        f"/api/tasks/{task2}/skip",
        json={"reason": "a different reason entirely"},
        headers={**worker, "Idempotency-Key": "skip-1"},
    )
    assert conflict.status_code == 409, conflict.text
    assert "different request" in conflict.json()["detail"]
