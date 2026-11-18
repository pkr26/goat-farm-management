"""Unchanged pre-tablet public MFA issuance retains normal browser persistence.

Archived source runs with the current installed interpreter/libraries, without
lifespan. This exercises a shared-schema/key compatibility case, not an upgrade.
"""

import asyncio
import base64
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

from app import security
from app.core.config import Settings, get_settings
from app.ratelimit import auth_limiter

from .legacy_mfa_public_issuer import totp_code

FIXTURE_DIR = Path(__file__).with_name("fixtures")
ARCHIVE_PATH = FIXTURE_DIR / "legacy_mfa_pre_tablet_b285645b.tar.gz"
MANIFEST_PATH = FIXTURE_DIR / "legacy_mfa_pre_tablet_b285645b.json"
ISSUER_SCRIPT = Path(__file__).with_name("legacy_mfa_public_issuer.py")
ARCHIVE_SHA = "989f3a2a3c4fddf2074c760d1acbec750b7347d7736d1f3b544890b8cf0d7bc4"
COMMIT = "b285645b2a93709aa7294fc4698ee0f25839b13e"


@dataclass(frozen=True)
class LegacyEnvironment:
    backend: Path
    manifest: dict[str, str]
    environment: dict[str, str] = field(repr=False)
    proof_path: Path
    configuration: dict[str, object]


def _save_proof(path: Path, proof: dict[str, Any]) -> None:
    path.write_text(json.dumps(proof, indent=2) + "\n")


def _save_issuer_stderr(path: Path, data: bytes) -> None:
    path.write_bytes(data)


@pytest.fixture
def legacy_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[LegacyEnvironment]:
    blob = ARCHIVE_PATH.read_bytes()
    if hashlib.sha256(blob).hexdigest() != ARCHIVE_SHA:
        raise RuntimeError("The archived public application fixture changed")
    metadata = json.loads(MANIFEST_PATH.read_text())
    if metadata["commit"] != COMMIT or metadata["archive_sha256"] != ARCHIVE_SHA:
        raise RuntimeError("The archived provenance does not match the exact commit")
    expected: dict[str, str] = metadata["files_sha256"]
    modes: dict[str, int] = metadata["modes"]
    legacy_root = tmp_path / "legacy"
    seen: set[str] = set()
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
        for member in archive.getmembers():
            if member.isdir():
                continue
            if not member.isfile() or member.name not in expected or member.name in seen:
                raise RuntimeError("The archive contains an unexpected or duplicate entry")
            source = archive.extractfile(member)
            if source is None:
                raise RuntimeError("The archived source entry is unreadable")
            data = source.read()
            if hashlib.sha256(data).hexdigest() != expected[member.name]:
                raise RuntimeError("The archived source does not match its manifest")
            destination = legacy_root / member.name
            if legacy_root not in destination.parents:
                raise RuntimeError("The archived entry escapes its owned directory")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            destination.chmod(modes[member.name])
            seen.add(member.name)
    if seen != set(expected) or len(seen) != 126:
        raise RuntimeError("The complete canonical archive was not materialized")
    manifest = {
        name: digest for name, digest in expected.items() if name.startswith("backend/app/")
    }
    if len(manifest) != 124:
        raise RuntimeError("The canonical app manifest is incomplete")

    key_dir = tmp_path / "owned-keys"
    key_dir.mkdir()
    request.addfinalizer(lambda: shutil.rmtree(key_dir))
    private_path, public_path = key_dir / "private.pem", key_dir / "public.pem"
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    private_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    private_path.chmod(0o600)
    public_path.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    stable_key = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    settings = get_settings()
    database = urlsplit(settings.database_url).path.removeprefix("/")
    if not (database.endswith("_test") or "_test_" in database):
        raise RuntimeError("The legacy issuer requires the existing owned test database")
    profile = {
        "environment": "development",
        "jwt_private_key_path": private_path,
        "jwt_public_key_path": public_path,
        "jwt_previous_public_key_paths": [],
        "totp_encryption_key": SecretStr(stable_key),
        "totp_encryption_key_file": None,
        "totp_encryption_previous_keys": [],
        "cookie_secure": False,
        "refresh_cookie_name": "goatfarm_refresh",
        "auth_rate_limit_enabled": True,
        "auth_rate_limit_max_attempts": 8,
        "auth_rate_limit_window_seconds": 3600,
        "min_password_length": 12,
    }
    validated = Settings.model_validate(settings.model_dump() | profile)
    for name in profile:
        monkeypatch.setattr(settings, name, getattr(validated, name))
    child_tmp = tmp_path / "child-tmp"
    child_tmp.mkdir()
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "LANG": "en_US.UTF-8",
        "TZ": "UTC",
        "PYTHONPATH": str(legacy_root / "backend"),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "TMPDIR": str(child_tmp),
        "GOATFARM_ENVIRONMENT": "development",
        "GOATFARM_DATABASE_URL": settings.database_url,
        "GOATFARM_DB_SSLMODE": settings.db_sslmode,
        "GOATFARM_JWT_PRIVATE_KEY_PATH": str(private_path),
        "GOATFARM_JWT_PUBLIC_KEY_PATH": str(public_path),
        "GOATFARM_JWT_ALGORITHM": settings.jwt_algorithm,
        "GOATFARM_JWT_ISSUER": settings.jwt_issuer,
        "GOATFARM_JWT_AUDIENCE": settings.jwt_audience,
        "GOATFARM_TOTP_ENCRYPTION_KEY": stable_key,
        "GOATFARM_COOKIE_SECURE": "false",
        "GOATFARM_REFRESH_COOKIE_NAME": settings.refresh_cookie_name,
        "GOATFARM_AUTH_RATE_LIMIT_ENABLED": "true",
        "GOATFARM_AUTH_RATE_LIMIT_MAX_ATTEMPTS": "8",
        "GOATFARM_AUTH_RATE_LIMIT_WINDOW_SECONDS": "3600",
        "GOATFARM_MIN_PASSWORD_LENGTH": "12",
    }
    auth_limiter.clear()
    try:
        yield LegacyEnvironment(
            backend=legacy_root / "backend",
            manifest=manifest,
            environment=environment,
            proof_path=tmp_path / "issuer-consumer-proof.json",
            configuration={
                "database_url_sha256": hashlib.sha256(settings.database_url.encode()).hexdigest(),
                "public_key_sha256": hashlib.sha256(public_path.read_bytes()).hexdigest(),
                "stable_totp_key_sha256": hashlib.sha256(stable_key.encode()).hexdigest(),
                "jwt_algorithm": settings.jwt_algorithm,
                "jwt_issuer": settings.jwt_issuer,
                "jwt_audience": settings.jwt_audience,
                "cookie_name": settings.refresh_cookie_name,
                "cookie_secure": settings.cookie_secure,
            },
        )
    finally:
        auth_limiter.clear()


async def test_pre_tablet_public_login_challenge_still_creates_a_persistent_browser_session(
    client: httpx.AsyncClient, legacy_environment: LegacyEnvironment
) -> None:
    case = legacy_environment
    child: asyncio.subprocess.Process | None = None
    communication: asyncio.Task[tuple[bytes, bytes]] | None = None
    try:
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            str(ISSUER_SCRIPT),
            cwd=case.backend,
            env=case.environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        communication = asyncio.create_task(
            child.communicate(
                json.dumps(
                    {
                        "email": "legacy-public-mfa@farm.in",
                        "password": "ownerpass123",
                        "archive_backend": str(case.backend),
                        "manifest": case.manifest,
                    }
                ).encode()
            )
        )
        output, error = await asyncio.wait_for(asyncio.shield(communication), timeout=120)
        await asyncio.to_thread(
            _save_issuer_stderr, case.proof_path.with_name("issuer-stderr.log"), error
        )
        if child.returncode != 0:
            raise RuntimeError(
                f"Archived public setup failed; stderr SHA256={hashlib.sha256(error).hexdigest()}"
            )
        issued: dict[str, Any] = json.loads(output)
        token, secret, user_id = issued["mfa_token"], issued["secret"], issued["user_id"]
        if (
            not isinstance(token, str)
            or not token
            or not isinstance(secret, str)
            or not secret
            or type(user_id) is not int
            or user_id <= 0
        ):
            raise RuntimeError("The archived public result has the wrong shape")
        if json.loads(issued["login_response_body"])["mfa_token"] != token:
            raise RuntimeError("The forwarded token is not the unchanged public response value")
        if (
            hashlib.sha256(token.encode()).hexdigest() != issued["mfa_token_sha256"]
            or hashlib.sha256(issued["login_response_body"].encode()).hexdigest()
            != issued["login_body_sha256"]
        ):
            raise RuntimeError("The raw issuer response/token hashes do not match")
        body, expired = security._decode_payload_result(token, "mfa")
        now = time.time()
        if (
            body is None
            or expired
            or "tablet_setup" in body
            or body.get("sub") != str(user_id)
            or not 0 <= now - int(body["iat"]) < 120
            or int(body["exp"]) - now <= 120
        ):
            raise RuntimeError("The actual archived challenge is not live and compatible")
        transport = client._transport
        if not isinstance(transport, httpx.ASGITransport):
            raise RuntimeError("The actual current application transport is required")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=transport.app, raise_app_exceptions=True),
            base_url=client.base_url,
            event_hooks=client.event_hooks,
        ) as browser:
            cookie_name = get_settings().refresh_cookie_name
            if browser.cookies.get(cookie_name) is not None:
                raise RuntimeError("The current browser unexpectedly started authenticated")
            consumed = await browser.post(
                "/api/auth/totp/challenge",
                json={"mfa_token": token, "code": totp_code(secret, int(time.time()) // 30)},
            )
            if consumed.status_code != 200:
                raise RuntimeError(f"Genuine current redemption failed: {consumed.status_code}")
            persistent_cookie = browser.cookies.get(cookie_name)
            refresh_status: int | None = None
            if persistent_cookie:
                refreshed = await browser.post(
                    "/api/auth/refresh",
                    headers={"Origin": str(browser.base_url).rstrip("/")},
                )
                refresh_status = refreshed.status_code
                if refresh_status != 200:
                    raise RuntimeError("The actual persistent cookie did not refresh successfully")
        # Only non-secret provenance survives in the retained proof.
        proof = {
            "archive_commit": COMMIT,
            "archive_sha256": ARCHIVE_SHA,
            "issuer_stdout_sha256": hashlib.sha256(output).hexdigest(),
            "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "login_body_sha256": issued["login_body_sha256"],
            "public_requests": issued["public_requests"],
            "loaded_modules": issued["loaded_modules"],
            "dependencies": issued["dependencies"],
            "runtime": issued["runtime"],
            "wait": issued["wait"],
            "cleanup": issued["cleanup"],
            "current_consumer_status": consumed.status_code,
            "persistent_cookie_observed": bool(persistent_cookie),
            "public_refresh_status": refresh_status,
            "genuine_token_age_seconds": now - int(body["iat"]),
            "contains_tablet_setup": "tablet_setup" in body,
            "shared_configuration": case.configuration,
            "issuer_stderr_sha256": hashlib.sha256(error).hexdigest(),
            "verified_claims": {
                "kind": body["kind"],
                "sub": body["sub"],
                "ver": body["ver"],
                "iat": int(body["iat"]),
                "exp": int(body["exp"]),
                "nonce_sha256": hashlib.sha256(str(body["jti"]).encode()).hexdigest(),
            },
        }
        await asyncio.to_thread(_save_proof, case.proof_path, proof)
    except BaseException as exc:
        raise RuntimeError("The archived/current public compatibility fixture failed") from exc
    finally:
        cleanup_error: BaseException | None = None
        if child is not None and child.returncode is None:
            try:
                child.kill()
            except ProcessLookupError:
                pass
            except BaseException as exc:
                cleanup_error = exc
        if communication is not None:
            try:
                await asyncio.wait_for(asyncio.shield(communication), timeout=10)
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
                if not communication.done():
                    communication.cancel()
        # Reap even when pipe draining was interrupted; do not raise until
        # both separately owned resources have had their bounded cleanup.
        if child is not None:
            try:
                await asyncio.wait_for(child.wait(), timeout=10)
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
        if communication is not None and not communication.done():
            communication.cancel()
            try:
                await asyncio.wait([communication], timeout=2)
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
        if communication is not None and communication.done():
            try:
                communication.result()
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
        if communication is not None and not communication.done():
            raise RuntimeError(
                "The owned issuer communication task did not finish"
            ) from cleanup_error
        if cleanup_error is not None:
            raise RuntimeError("The owned archived issuer cleanup failed") from cleanup_error

    assert persistent_cookie, "A genuine pre-tablet MFA login must retain its refresh cookie"
