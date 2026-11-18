"""Reachable cache-path, tablet scope, credential and pinned-identity contracts."""

from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request
from starlette.types import Scope

import app.core.config as config
from app.db import get_sessionmaker
from app.deps import (
    CurrentFarm,
    CurrentUser,
    _pin_authenticated_user,
    current_membership,
    current_user,
    deactivate_deleted_user_memberships,
)
from app.models import Farm, FarmMembership, User
from app.security import decode_access_claims, encrypt_totp_secret, generate_totp_secret_b32
from app.utils import utcnow

from .conftest import OWNER_PW, owner_with_farm
from .test_totp import stable_totp_key  # noqa: F401 - imported fixture
from .test_worker_pin_auth import _make_pin_worker, _worker_login


def test_existing_private_key_keeps_its_legacy_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = tmp_path / "backend"
    keys = backend / "keys"
    keys.mkdir(parents=True)
    private = keys / "jwt_private.pem"
    private.write_text("existing private key placeholder")
    monkeypatch.setattr(config, "BACKEND_DIR", backend)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    assert config._dev_key_dir() == keys
    assert private.read_text() == "existing private key placeholder"


def test_public_key_alone_does_not_override_the_explicit_user_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = tmp_path / "backend"
    (backend / "keys").mkdir(parents=True)
    (backend / "keys/jwt_public.pem").write_text("public material is not a private key")
    cache = tmp_path / "cache with spaces"
    monkeypatch.setattr(config, "BACKEND_DIR", backend)
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    assert config._dev_key_dir() == cache / "goatfarm" / "keys"
    assert not cache.exists()


@pytest.mark.parametrize("cache_value", [None, ""])
def test_unset_or_empty_xdg_uses_the_users_cache_without_creating_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache_value: str | None
) -> None:
    home = tmp_path / "user-home"
    monkeypatch.setattr(config, "BACKEND_DIR", tmp_path / "backend")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    if cache_value is None:
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_CACHE_HOME", cache_value)
    assert config._dev_key_dir() == home / ".cache" / "goatfarm" / "keys"
    assert not home.exists()


def scoped_request(
    token: str, path: str, *, method: str = "GET", route: APIRoute | None = None
) -> Request:
    scope: Scope = {
        "type": "http",
        "method": method,
        "path": path,
        "scheme": "http",
        "server": ("test", 80),
        "client": ("127.0.0.1", 12345),
        "headers": [(b"authorization", ("Bearer " + token).encode())],
        "query_string": b"",
    }
    if route is not None:
        scope["route"] = route
    return Request(scope)


async def test_real_tablet_credential_cannot_enter_a_new_identity_only_route(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="dependency-owner@farm.in")
    membership_id, farm_id = await _make_pin_worker(
        client, owner, email="dependency-worker@farm.in"
    )
    login = await _worker_login(client, farm_id, membership_id, "4321")
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    credential = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    def identity_resource(user: CurrentUser) -> dict[str, int]:
        return {"user_id": user.id}

    def farm_resource(farm: CurrentFarm) -> dict[str, int]:
        return {"farm_id": farm.id}

    # Construct real resolved dependency graphs without mounting or enabling
    # any production endpoint or overriding an authentication dependency.
    identity_route = APIRoute("/api/new-identity-resource", identity_resource, methods=["GET"])
    farm_route = APIRoute("/api/new-farm-resource", farm_resource, methods=["GET"])
    async with get_sessionmaker()() as db:
        bootstrap = await current_user(scoped_request(token, "/api/auth/me"), db, credential)
        assert bootstrap.id == login.json()["user"]["id"]
        allowed = await current_user(
            scoped_request(token, farm_route.path, route=farm_route), db, credential
        )
        assert allowed.id == bootstrap.id
        with pytest.raises(HTTPException) as blocked:
            await current_user(
                scoped_request(token, identity_route.path, route=identity_route), db, credential
            )
        assert blocked.value.status_code == 403
        assert blocked.value.detail == (
            "Tablet sessions cannot manage account or global resources; sign in with your password."
        )


async def test_locked_identity_reloads_after_public_password_rotation(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="pinned-user@farm.in")
    token = owner["Authorization"].removeprefix("Bearer ")
    claims = decode_access_claims(token)
    assert claims is not None
    request = scoped_request(token, "/api/tasks", method="POST")
    request.state.authenticated_token_version = claims.token_version
    request.state.authenticated_scope = claims.scope
    request.state.authenticated_family_id = claims.family_id
    async with get_sessionmaker()() as db:
        user = await db.get(User, claims.user_id)
        assert user is not None
        assert (await _pin_authenticated_user(db, request, user)).id == user.id
        await db.rollback()
    async with get_sessionmaker()() as db:
        stale_user = await db.get(User, claims.user_id)
        assert stale_user is not None
        changed = await client.post(
            "/api/auth/change-password",
            headers=owner,
            json={"current_password": OWNER_PW, "new_password": "replacement-password123"},
        )
        assert changed.status_code == 200, changed.text
        with pytest.raises(HTTPException) as revoked:
            await _pin_authenticated_user(db, request, stale_user)
        assert revoked.value.status_code == 401
        assert revoked.value.detail == "Session has been revoked"
        assert revoked.value.headers == {"WWW-Authenticate": "Bearer"}


async def test_unsafe_membership_requires_the_same_farm_locked_bundle(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="locked-bundle-owner@farm.in")
    membership_id, farm_id = await _make_pin_worker(
        client, owner, email="locked-bundle-worker@farm.in"
    )
    login = await _worker_login(client, farm_id, membership_id, "4321")
    assert login.status_code == 200, login.text
    request = scoped_request(login.json()["access_token"], "/api/tasks", method="POST")
    async with get_sessionmaker()() as db:
        member = await db.get(FarmMembership, membership_id)
        farm = await db.get(Farm, farm_id)
        assert member is not None and farm is not None
        user = await db.get(User, member.user_id)
        assert user is not None
        request.state.locked_membership = member
        request.state.locked_membership_farm_id = farm_id
        assert await current_membership(request, db, user, farm) is member
        request.state.locked_membership_farm_id = farm_id + 1
        with pytest.raises(HTTPException) as unpinned:
            await current_membership(request, db, user, farm)
        assert unpinned.value.status_code == 401
        assert unpinned.value.detail == "Authorization could not be pinned"


@pytest.mark.usefixtures("stable_totp_key")
async def test_legacy_tombstone_cleanup_resets_rotation_without_scrubbing_live_users() -> None:
    cipher = encrypt_totp_secret(generate_totp_secret_b32())
    assert cipher is not None
    async with get_sessionmaker()() as db:
        deleted = User(
            email="deleted-retired-identity@deleted.invalid",
            password_hash="unusable-retained-hash",
            deleted_at=utcnow(),
            totp_secret_enc=cipher,
            totp_state="PENDING",
            totp_last_step=19,
            must_change_password=True,
        )
        live = User(
            email="live-identity@farm.in",
            password_hash="unusable-retained-hash",
            totp_secret_enc=cipher,
            totp_state="PENDING",
            totp_last_step=19,
            must_change_password=True,
        )
        db.add_all([deleted, live])
        await db.flush()
        deleted_id, live_id = deleted.id, live.id
        await db.commit()
    async with get_sessionmaker()() as db:
        assert await deactivate_deleted_user_memberships(db, batch_size=1) == 0
        await db.commit()
    async with get_sessionmaker()() as db:
        scrubbed, untouched = await db.get(User, deleted_id), await db.get(User, live_id)
        assert scrubbed is not None and untouched is not None
        assert (
            scrubbed.totp_secret_enc,
            scrubbed.totp_state,
            scrubbed.totp_last_step,
            scrubbed.must_change_password,
        ) == (None, None, None, False)
        assert (
            untouched.totp_secret_enc,
            untouched.totp_state,
            untouched.totp_last_step,
            untouched.must_change_password,
        ) == (cipher, "PENDING", 19, True)
