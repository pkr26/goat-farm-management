"""Session revocation, membership privacy, and MFA credential transitions."""

from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, update
from starlette.requests import Request

from app.api.auth import totp_confirm, totp_disable, totp_recovery_regenerate
from app.core.config import get_settings
from app.db import get_sessionmaker
from app.deps import deactivate_deleted_user_memberships
from app.models import Farm, FarmMembership, RefreshSession, TotpRecoveryCode, User
from app.models.notifications import NotificationRecipient
from app.schemas.auth import TotpCodeIn, TotpDisableIn, TotpRecoveryRegenerateIn
from app.security import (
    SessionScope,
    decode_access_claims,
    decode_refresh_claims,
    encrypt_totp_secret,
    generate_totp_secret_b32,
    issue_token,
)
from app.seed import seed_new_farm
from app.utils import today, utcnow

from .conftest import OWNER_PW, login_and_rotate, owner_with_farm
from .test_auth_extended import set_refresh_cookie
from .test_totp import _current_code, stable_totp_key  # noqa: F401 - imported fixture
from .test_worker_pin_auth import _make_pin_worker, _worker_login
from .type_helpers import JsonObject, json_object


async def _password_worker(
    client: httpx.AsyncClient, owner: dict[str, str], email: str
) -> JsonObject:
    team = (await client.get("/api/team", headers=owner)).json()
    role_id = next(r["id"] for r in team["roles"] if r["code"] == "CLEANER")
    response = await client.post(
        "/api/team/workers",
        headers=owner,
        json={
            "email": email,
            "name": "Future owner",
            "password": "workerpass123",
            "role_id": role_id,
        },
    )
    assert response.status_code == 201, response.text
    return json_object(response.json())


async def test_pin_scope_survives_refresh_and_never_gains_other_farm_or_identity_authority(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="scoped-owner@farm.in")
    membership_id, farm_id = await _make_pin_worker(client, owner, email="scoped-worker@farm.in")
    login = await _worker_login(client, farm_id, membership_id, "4321")
    assert login.status_code == 200, login.text
    user_id = login.json()["user"]["id"]
    async with get_sessionmaker()() as db:
        other_farm = Farm(name="Worker's separately owned farm", owner_id=user_id)
        db.add(other_farm)
        await db.flush()
        await seed_new_farm(db, other_farm)
        other_id = other_farm.id
        await db.commit()
    task = await client.post(
        "/api/tasks",
        headers=owner,
        json={
            "title": "Authorized tablet duty",
            "category": "CLEANING",
            "due_date": today().isoformat(),
            "assigned_user_id": user_id,
        },
    )
    assert task.status_code == 201, task.text
    for access in [
        login.json()["access_token"],
        (await client.post("/api/auth/refresh")).json()["access_token"],
    ]:
        claims = decode_access_claims(access)
        assert claims is not None
        assert claims.scope == SessionScope("PIN", farm_id, membership_id)
        assert claims.family_id
        headers = {"Authorization": f"Bearer {access}", "X-Farm-Id": str(farm_id)}
        farms = await client.get("/api/auth/farms", headers=headers)
        assert [row["id"] for row in farms.json()] == [farm_id]
        assert (await client.get("/api/auth/permissions", headers=headers)).json()[
            "is_owner"
        ] is False
        other = await client.get("/api/tasks", headers={**headers, "X-Farm-Id": str(other_id)})
        assert other.status_code == 404
        for path in ["/api/owner/overview", "/api/auth/account/export"]:
            assert (await client.get(path, headers=headers)).status_code == 403
        for path, payload in [
            ("/api/auth/farms", {"name": "Forbidden ownership"}),
            (
                "/api/auth/change-password",
                {"current_password": "anything", "new_password": "replacement123"},
            ),
            ("/api/auth/totp/confirm", {"code": "123456"}),
        ]:
            blocked = await client.post(path, headers=headers, json=payload)
            assert blocked.status_code == 403, blocked.text
        completed = await client.post(
            f"/api/tasks/{task.json()['id']}/complete",
            headers={**headers, "Idempotency-Key": "scoped-duty-completion"},
        )
        assert completed.status_code == 200, completed.text


async def test_pin_refresh_and_access_require_live_membership(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="revoke-pin-owner@farm.in")
    membership_id, farm_id = await _make_pin_worker(
        client, owner, email="revoke-pin-worker@farm.in"
    )
    login = await _worker_login(client, farm_id, membership_id, "4321")
    access = login.json()["access_token"]
    async with get_sessionmaker()() as db:
        await db.execute(
            update(FarmMembership).where(FarmMembership.id == membership_id).values(is_active=False)
        )
        await db.commit()
    assert (
        await client.get("/api/auth/me", headers={"Authorization": f"Bearer {access}"})
    ).status_code == 401
    assert (await client.post("/api/auth/refresh")).status_code == 401


async def test_pin_logout_revokes_only_its_family_and_preserves_password_owner_session(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="family-owner@farm.in")
    worker = await _password_worker(client, owner, "family-worker@farm.in")
    password_headers = await login_and_rotate(client, "family-worker@farm.in", "workerpass123")
    password_cookie = client.cookies.get(get_settings().refresh_cookie_name)
    assert password_cookie is not None
    reset = await client.post(
        f"/api/team/workers/{worker['id']}/reset-pin", headers=owner, json={"pin": "4321"}
    )
    assert reset.status_code == 200, reset.text
    # The response uses the acting owner when advertising reset eligibility.
    assert reset.json()["can_reset_password"] is True
    password_login = await client.post(
        "/api/auth/login", json={"email": "family-worker@farm.in", "password": "workerpass123!r1"}
    )
    assert password_login.status_code == 200
    password_headers = {"Authorization": f"Bearer {password_login.json()['access_token']}"}
    password_cookie = client.cookies.get(get_settings().refresh_cookie_name)
    assert password_cookie is not None
    pin_login = await _worker_login(client, int(owner["X-Farm-Id"]), worker["id"], "4321")
    pin_token = pin_login.json()["access_token"]
    assert (
        await client.post("/api/auth/logout", headers={"Authorization": f"Bearer {pin_token}"})
    ).status_code == 204
    assert (
        await client.get("/api/auth/me", headers={"Authorization": f"Bearer {pin_token}"})
    ).status_code == 401
    assert (await client.get("/api/auth/me", headers=password_headers)).status_code == 200
    set_refresh_cookie(client, password_cookie)
    assert (await client.post("/api/auth/refresh")).status_code == 200
    assert (await client.get("/api/team", headers=owner)).status_code == 200


async def test_unmarked_legacy_access_and_refresh_fail_closed_then_password_reauthentication_works(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="cutover-owner@farm.in")
    current = decode_access_claims(owner["Authorization"].removeprefix("Bearer "))
    assert current is not None
    old_access = issue_token(
        current.user_id, "access", 600, extra_claims={"ver": current.token_version}
    )
    assert decode_access_claims(old_access) is None
    assert (
        await client.get("/api/auth/me", headers={"Authorization": f"Bearer {old_access}"})
    ).status_code == 401
    jti, family = uuid4().hex, uuid4().hex
    old_refresh = issue_token(
        current.user_id, "refresh", 600, jti=jti, extra_claims={"fid": family}
    )
    async with get_sessionmaker()() as db:
        row = RefreshSession(
            user_id=current.user_id,
            jti=jti,
            family_id=family,
            expires_at=utcnow() + timedelta(minutes=10),
        )
        db.add(row)
        await db.flush()
        await db.execute(
            update(RefreshSession).where(RefreshSession.id == row.id).values(session_origin=None)
        )
        await db.commit()
    assert decode_refresh_claims(old_refresh) is None
    set_refresh_cookie(client, old_refresh)
    assert (await client.post("/api/auth/refresh")).status_code == 401
    reauth = await client.post(
        "/api/auth/login", json={"email": "cutover-owner@farm.in", "password": OWNER_PW}
    )
    assert reauth.status_code == 200
    assert (
        await client.get(
            "/api/team",
            headers={**owner, "Authorization": f"Bearer {reauth.json()['access_token']}"},
        )
    ).status_code == 200
    assert (await client.post("/api/auth/refresh")).status_code == 200


async def test_roster_pagination_reaches_workers_beyond_first_hundred(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="pages-owner@farm.in")
    member_id, farm_id = await _make_pin_worker(client, owner, email="pages-template@farm.in")
    async with get_sessionmaker()() as db:
        template = await db.get(FarmMembership, member_id)
        assert template is not None
        for i in range(104):
            account = User(email=f"pages-{i}@farm.in", name=f"Worker {i}", password_hash="unused")
            db.add(account)
            await db.flush()
            db.add(
                FarmMembership(
                    user_id=account.id,
                    farm_id=farm_id,
                    role_id=template.role_id,
                    pin_hash=template.pin_hash,
                )
            )
        await db.commit()
    page1 = (await client.get("/api/auth/worker-roster", params={"farm_id": farm_id})).json()
    assert len(page1["items"]) == 100
    page2 = (
        await client.get(
            "/api/auth/worker-roster",
            params={"farm_id": farm_id, "after_membership_id": page1["next_after_membership_id"]},
        )
    ).json()
    assert len(page2["items"]) == 5
    assert page2["next_after_membership_id"] is None
    ids = [entry["membership_id"] for entry in page1["items"] + page2["items"]]
    assert len(set(ids)) == 105
    assert ids == sorted(ids)


@pytest.mark.usefixtures("stable_totp_key")
@pytest.mark.parametrize("operation", ["confirm", "disable", "regenerate"])
async def test_every_mfa_final_lock_rejects_inflight_revoked_generation(
    client: httpx.AsyncClient,
    operation: str,
) -> None:
    registered = await client.post(
        "/api/auth/register", json={"email": f"mfa-race-{operation}@farm.in", "password": OWNER_PW}
    )
    user_id = registered.json()["user"]["id"]
    secret = generate_totp_secret_b32()
    code, _ = _current_code(secret)
    encrypted = encrypt_totp_secret(secret)
    async with get_sessionmaker()() as db:
        await db.execute(
            update(User)
            .where(User.id == user_id)
            .values(
                totp_secret_enc=encrypted,
                totp_state="PENDING" if operation == "confirm" else "ACTIVE",
                totp_last_step=None,
            )
        )
        db.add(TotpRecoveryCode(user_id=user_id, code_hash="existing-recovery-hash"))
        await db.commit()
    async with get_sessionmaker()() as request_db:
        authenticated = await request_db.get(User, user_id)
        assert authenticated is not None
        # This principal was approved by CurrentUser before revocation won in
        # another transaction. The ORM snapshot must not update the comparison.
        async with get_sessionmaker()() as revoker:
            await revoker.execute(
                update(User).where(User.id == user_id).values(token_version=User.token_version + 1)
            )
            await revoker.commit()
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/auth/totp",
                "headers": [],
                "client": ("127.0.0.1", 1),
            }
        )
        with pytest.raises(HTTPException) as rejected:
            if operation == "confirm":
                await totp_confirm(TotpCodeIn(code=code), request, request_db, authenticated)
            elif operation == "disable":
                await totp_disable(
                    TotpDisableIn(current_password=OWNER_PW, code=code),
                    request,
                    request_db,
                    authenticated,
                )
            else:
                await totp_recovery_regenerate(
                    TotpRecoveryRegenerateIn(current_password=OWNER_PW, code=code),
                    request,
                    request_db,
                    authenticated,
                )
        assert rejected.value.status_code == 401
        await request_db.rollback()
    async with get_sessionmaker()() as db:
        account = await db.get(User, user_id)
        assert account is not None and account.totp_secret_enc == encrypted
        assert account.totp_state == ("PENDING" if operation == "confirm" else "ACTIVE")
        recovery = list(
            (
                await db.execute(
                    select(TotpRecoveryCode.code_hash).where(TotpRecoveryCode.user_id == user_id)
                )
            ).scalars()
        )
        assert recovery == ["existing-recovery-hash"]


@pytest.mark.usefixtures("stable_totp_key")
async def test_deletion_scrubs_mfa_and_bounded_restartable_cleanup_scrubs_inactive_memberships(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="privacy-owner@farm.in")
    worker = await _password_worker(client, owner, "privacy-worker@farm.in")
    headers = await login_and_rotate(client, "privacy-worker@farm.in", "workerpass123")
    async with get_sessionmaker()() as db:
        await db.execute(
            update(User)
            .where(User.id == worker["user_id"])
            .values(
                totp_secret_enc=encrypt_totp_secret(generate_totp_secret_b32()),
                totp_state="PENDING",
                totp_last_step=123,
            )
        )
        await db.execute(
            update(FarmMembership)
            .where(FarmMembership.id == worker["id"])
            .values(
                is_active=False,
                pin_hash="retained-pin-hash",
                pin_updated_at=utcnow(),
            )
        )
        db.add_all(
            [
                TotpRecoveryCode(user_id=worker["user_id"], code_hash=f"retained-{i}")
                for i in range(3)
            ]
        )
        db.add(
            NotificationRecipient(
                farm_id=int(owner["X-Farm-Id"]),
                membership_id=worker["id"],
                phone="+919876543210",
                daily_digest=True,
                verified=True,
            )
        )
        await db.commit()
    removed = await client.request(
        "DELETE",
        "/api/auth/account",
        headers=headers,
        json={"current_password": "workerpass123!r1"},
    )
    assert removed.status_code == 204, removed.text
    async with get_sessionmaker()() as db:
        tombstone = await db.get(User, worker["user_id"])
        assert tombstone is not None
        assert (tombstone.totp_secret_enc, tombstone.totp_state, tombstone.totp_last_step) == (
            None,
            None,
            None,
        )
        await deactivate_deleted_user_memberships(db, batch_size=1)
        await db.commit()
        assert (
            await db.execute(select(func.count()).select_from(TotpRecoveryCode))
        ).scalar_one() == 2
    # Separate transactions model restart. Completed memberships must not hide
    # recovery/recipient cleanup still needed for the same tombstone.
    for _ in range(3):
        async with get_sessionmaker()() as db:
            await deactivate_deleted_user_memberships(db, batch_size=1)
            await db.commit()
    async with get_sessionmaker()() as db:
        membership = await db.get(FarmMembership, worker["id"])
        assert (
            membership is not None
            and membership.pin_hash is None
            and membership.pin_updated_at is None
        )
        assert membership.is_active is False
        recipient = (await db.execute(select(NotificationRecipient))).scalar_one()
        assert (
            recipient.phone == "deleted"
            and recipient.daily_digest is False
            and recipient.verified is False
        )
        assert (
            await db.execute(select(func.count()).select_from(TotpRecoveryCode))
        ).scalar_one() == 0


async def test_owner_transfer_makes_deletion_feasible_and_retains_operational_farm(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="departing-owner@farm.in")
    worker = await _password_worker(client, owner, "successor-owner@farm.in")
    before_rotation = await client.post(
        f"/api/auth/farms/{owner['X-Farm-Id']}/transfer-ownership",
        headers=owner,
        json={"membership_id": worker["id"], "current_password": OWNER_PW},
    )
    assert before_rotation.status_code == 409
    await login_and_rotate(client, "successor-owner@farm.in", "workerpass123")
    transferred = await client.post(
        f"/api/auth/farms/{owner['X-Farm-Id']}/transfer-ownership",
        headers=owner,
        json={"membership_id": worker["id"], "current_password": OWNER_PW},
    )
    assert transferred.status_code == 200, transferred.text
    assert (await client.get("/api/team", headers=owner)).status_code == 404
    deletion = await client.request(
        "DELETE", "/api/auth/account", headers=owner, json={"current_password": OWNER_PW}
    )
    assert deletion.status_code == 204, deletion.text
    successor_login = await client.post(
        "/api/auth/login", json={"email": "successor-owner@farm.in", "password": "workerpass123!r1"}
    )
    successor_headers = {
        "Authorization": f"Bearer {successor_login.json()['access_token']}",
        "X-Farm-Id": owner["X-Farm-Id"],
    }
    assert (await client.get("/api/team", headers=successor_headers)).status_code == 200
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        assert farm is not None and farm.owner_id == worker["user_id"]


async def test_transient_manager_setup_and_exact_cancellation_preserve_newer_session_cookie(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="transient-owner@farm.in")
    original_cookie = client.cookies.get(get_settings().refresh_cookie_name)
    setup = await client.post(
        "/api/auth/login",
        json={
            "email": "transient-owner@farm.in",
            "password": OWNER_PW,
            "tablet_setup": True,
        },
    )
    assert setup.status_code == 200, setup.text
    assert "set-cookie" not in setup.headers
    assert client.cookies.get(get_settings().refresh_cookie_name) == original_cookie
    setup_headers = {"Authorization": f"Bearer {setup.json()['access_token']}"}
    assert (await client.get("/api/auth/farms", headers=setup_headers)).status_code == 200
    newer = await client.post(
        "/api/auth/login", json={"email": "transient-owner@farm.in", "password": OWNER_PW}
    )
    newer_cookie = client.cookies.get(get_settings().refresh_cookie_name)
    canceled = await client.post("/api/auth/logout-session", headers=setup_headers)
    assert canceled.status_code == 204, canceled.text
    assert "set-cookie" not in canceled.headers
    assert client.cookies.get(get_settings().refresh_cookie_name) == newer_cookie
    assert (await client.get("/api/auth/me", headers=setup_headers)).status_code == 401
    newer_headers = {"Authorization": f"Bearer {newer.json()['access_token']}"}
    assert (await client.get("/api/auth/me", headers=newer_headers)).status_code == 200
    assert (await client.get("/api/team", headers=owner)).status_code == 200
    assert (await client.post("/api/auth/refresh")).status_code == 200


@pytest.mark.usefixtures("stable_totp_key")
async def test_transient_mode_survives_mfa_exchange_without_cookie_replacement(
    client: httpx.AsyncClient,
) -> None:
    from .test_totp import _enroll_and_activate

    owner = await owner_with_farm(client, email="transient-mfa-owner@farm.in")
    secret, _ = await _enroll_and_activate(client, owner)
    cookie = client.cookies.get(get_settings().refresh_cookie_name)
    setup = await client.post(
        "/api/auth/login",
        json={
            "email": "transient-mfa-owner@farm.in",
            "password": OWNER_PW,
            "tablet_setup": True,
        },
    )
    assert setup.status_code == 200 and setup.json()["mfa_token"]
    exchanged = await client.post(
        "/api/auth/totp/challenge",
        json={
            "mfa_token": setup.json()["mfa_token"],
            "code": _current_code(secret, drift=1)[0],
        },
    )
    assert exchanged.status_code == 200, exchanged.text
    assert "set-cookie" not in exchanged.headers
    assert client.cookies.get(get_settings().refresh_cookie_name) == cookie
    claims = decode_access_claims(exchanged.json()["access_token"])
    assert claims is not None and claims.family_id
    async with get_sessionmaker()() as db:
        expires = (
            await db.execute(
                select(RefreshSession.expires_at).where(
                    RefreshSession.family_id == claims.family_id
                )
            )
        ).scalar_one()
    assert expires <= utcnow() + timedelta(seconds=get_settings().access_token_ttl_seconds)


@pytest.mark.parametrize(
    "operation", ["change-password", "totp-enroll", "account-delete", "team-reset-pin"]
)
async def test_family_cancellation_winning_during_credential_work_rejects_stale_mutation(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    """Family-only cancellation must remain a final-lock authorization barrier."""
    import asyncio

    import app.api.auth as auth_api
    import app.api.team as team_api
    from app.main import create_app
    from app.security import hash_password_async as real_hash
    from app.security import verify_password_async as real_verify

    owner = await owner_with_farm(client, email=f"family-race-{operation}@farm.in")
    member_id, _ = await _make_pin_worker(
        client, owner, email=f"family-race-worker-{operation}@farm.in"
    )
    claims = decode_access_claims(owner["Authorization"].removeprefix("Bearer "))
    assert claims is not None
    entered, resume = asyncio.Event(), asyncio.Event()

    async def stalled_verify(password: str, stored: str) -> tuple[bool, bool]:
        entered.set()
        await resume.wait()
        return await real_verify(password, stored)

    async def stalled_hash(password: str) -> str:
        entered.set()
        await resume.wait()
        return await real_hash(password)

    monkeypatch.setattr(auth_api, "verify_password_async", stalled_verify)
    monkeypatch.setattr(team_api, "hash_password_async", stalled_hash)
    method, path, payload = {
        "change-password": (
            "POST",
            "/api/auth/change-password",
            {"current_password": OWNER_PW, "new_password": "must-never-land123"},
        ),
        "totp-enroll": ("POST", "/api/auth/totp/enroll", {"current_password": OWNER_PW}),
        "account-delete": ("DELETE", "/api/auth/account", {"current_password": OWNER_PW}),
        "team-reset-pin": ("POST", f"/api/team/workers/{member_id}/reset-pin", {"pin": "9876"}),
    }[operation]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as contender:
        attempt = asyncio.create_task(contender.request(method, path, headers=owner, json=payload))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            canceled = await client.post("/api/auth/logout-session", headers=owner)
            assert canceled.status_code == 204, canceled.text
        finally:
            resume.set()
        response = await asyncio.wait_for(attempt, timeout=10)
    assert response.status_code == 401, response.text
    async with get_sessionmaker()() as db:
        account = await db.get(User, claims.user_id)
        assert account is not None and account.token_version == claims.token_version
        assert account.deleted_at is None and account.totp_state is None
