"""Draft subprocess adapter for the unchanged pre-tablet public MFA issuer.

The parent supplies the complete child environment before launch, owns the
database/schema/keys, and bounds and reaps this process. Stdin is one JSON object
with email, password, archive_backend and the canonical backend/app hash manifest.
Successful stdout is one private JSON result; it contains live credentials and
must never be copied into public audit logs. All other stdout goes to stderr.

Importing this helper imports only the standard library and defines functions.
It never imports an application, reads stdin, changes configuration or starts work
until explicitly called. No historical lifespan or network listener is started.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import importlib
import importlib.metadata
import json
import os
import sys
import time
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Protocol, TypedDict, cast
from urllib.parse import parse_qs, urlsplit

if TYPE_CHECKING:
    from httpx import AsyncClient, Response
    from sqlalchemy.ext.asyncio import AsyncEngine
    from starlette.types import ASGIApp

ARCHIVE_COMMIT = "b285645b2a93709aa7294fc4698ee0f25839b13e"
APP_FILE_COUNT = 124
APP_MANIFEST_SHA256 = "671e6772e19e8140a9b498777d3b7b46d4bfeaf49ec6c101232f6fff5c0aedad"
MAX_INPUT_BYTES = 131_072
REQUEST_TIMEOUT_SECONDS = 30.0
NEXT_STEP_TIMEOUT_SECONDS = 45.0
CLEANUP_TIMEOUT_SECONDS = 10.0
TOTP_STEP_SECONDS = 30
BASE_URL = "http://test"

_REQUIRED_APP_MODULES = frozenset(
    {
        "app",
        "app.api.auth",
        "app.core.config",
        "app.db",
        "app.deps",
        "app.main",
        "app.schemas.auth",
        "app.security",
    }
)
_DEPENDENCY_MODULES = {
    "PyJWT": "jwt",
    "SQLAlchemy": "sqlalchemy",
    "alembic": "alembic",
    "anyio": "anyio",
    "argon2-cffi": "argon2",
    "asyncpg": "asyncpg",
    "boto3": "boto3",
    "cryptography": "cryptography",
    "fastapi": "fastapi",
    "greenlet": "greenlet",
    "httpx": "httpx",
    "pillow": "PIL",
    "prometheus-client": "prometheus_client",
    "pydantic": "pydantic",
    "pydantic-core": "pydantic_core",
    "pydantic-settings": "pydantic_settings",
    "python-dotenv": "dotenv",
    "starlette": "starlette",
    "uvicorn": "uvicorn",
}


class _IssuerError(RuntimeError):
    """A diagnostic whose message never includes credentials or response bodies."""


@dataclass(frozen=True, slots=True)
class IssuerInput:
    email: str
    password: str
    archive_backend: Path
    manifest: dict[str, str]


class ModuleReceipt(TypedDict):
    origin: str
    manifest_path: str
    sha256: str


class DependencyReceipt(TypedDict):
    version: str
    module: str
    loaded: bool
    module_origin: str | None


class RequestReceipt(TypedDict):
    method: str
    path: str
    status_code: int
    started_at_unix: float
    received_at_unix: float
    elapsed_seconds: float
    response_sha256: str
    response_bytes: int
    set_cookie_count: int


class WaitReceipt(TypedDict):
    activation_code_step: int
    started_at_unix: float
    completed_at_unix: float
    elapsed_seconds: float
    observed_step: int
    timeout_seconds: float


class CleanupReceipt(TypedDict):
    clients_closed: int
    engine_present: bool
    engine_disposed: bool
    per_resource_timeout_seconds: float


class RuntimeReceipt(TypedDict):
    executable: str
    python_version: str
    implementation: str
    lifespan: str
    transport: str
    base_url: str


class IssuerResult(TypedDict):
    schema: str
    archive_commit: str
    archive_backend: str
    app_manifest_sha256: str
    mfa_token: str
    mfa_token_sha256: str
    login_response_body: str
    login_body_sha256: str
    secret: str
    user_id: int
    activation_code_generated_at_unix: float
    confirmation_received_at_unix: float
    public_requests: list[RequestReceipt]
    wait: WaitReceipt
    loaded_modules: dict[str, ModuleReceipt]
    dependencies: dict[str, DependencyReceipt]
    runtime: RuntimeReceipt
    cleanup: CleanupReceipt
    result_ready_at_unix: float


class _ArchiveMain(Protocol):
    def create_app(self) -> ASGIApp: ...


class _ArchiveDatabase(Protocol):
    _engine: AsyncEngine | None


def totp_code(secret: str, step: int) -> str:
    """Return the RFC 6238 SHA-1 six-digit code for a real 30-second step.

    This pure standard-library function accepts the API's Base32 secret. The
    caller supplies the counter, normally int(time.time() // 30); no clock,
    application, database, key generation or mutable state is involved here.
    """
    if not isinstance(secret, str) or not secret:
        raise ValueError("A nonempty Base32 TOTP secret is required")
    if type(step) is not int or not 0 <= step < 2**64:
        raise ValueError("The TOTP step must be an unsigned 64-bit integer")
    try:
        key = base64.b32decode(secret, casefold=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("The TOTP secret is not valid Base32") from exc
    if not key:
        raise ValueError("The TOTP secret decoded to an empty key")
    digest = hmac.new(key, step.to_bytes(8, "big"), "sha1").digest()
    offset = digest[-1] & 0x0F
    truncated = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFF_FFFF
    return f"{truncated % 1_000_000:06d}"


def _object(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise _IssuerError(f"{context} must be a JSON object")
    return cast(dict[str, object], value)


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise _IssuerError(f"{context} must be a nonempty string")
    return value


def read_input() -> IssuerInput:
    """Read and validate the private subprocess protocol without importing app."""
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise _IssuerError("Issuer input exceeds its size limit")
    try:
        decoded: object = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise _IssuerError("Issuer input is not valid JSON") from exc
    payload = _object(decoded, "Issuer input")
    if set(payload) != {"email", "password", "archive_backend", "manifest"}:
        raise _IssuerError("Issuer input has unexpected or missing fields")
    email = _string(payload["email"], "email")
    password = _string(payload["password"], "password")
    if len(email) > 254 or len(password.encode("utf-8")) > 128:
        raise _IssuerError("Issuer credentials exceed the archived schema bounds")
    supplied_backend = Path(_string(payload["archive_backend"], "archive_backend"))
    if not supplied_backend.is_absolute():
        raise _IssuerError("archive_backend must be absolute")
    backend = supplied_backend.resolve(strict=True)
    if not backend.is_dir():
        raise _IssuerError("archive_backend is not a directory")
    raw_manifest = _object(payload["manifest"], "manifest")
    manifest: dict[str, str] = {}
    for relative, value in raw_manifest.items():
        path = PurePosixPath(relative)
        if (
            path.is_absolute()
            or path.parts[:2] != ("backend", "app")
            or ".." in path.parts
            or str(path) != relative
        ):
            raise _IssuerError("Manifest path is outside canonical backend/app")
        digest = _string(value, "Manifest hash")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise _IssuerError("Manifest contains an invalid SHA256")
        manifest[relative] = digest
    if len(manifest) != APP_FILE_COUNT or _manifest_hash(manifest) != APP_MANIFEST_SHA256:
        raise _IssuerError("Manifest does not match the complete canonical archived app")
    return IssuerInput(email, password, backend, manifest)


def _manifest_hash(manifest: dict[str, str]) -> str:
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _verify_tree(inputs: IssuerInput) -> None:
    app_root = inputs.archive_backend / "app"
    if not app_root.is_dir() or app_root.is_symlink():
        raise _IssuerError("Canonical archived app directory is missing or a symlink")
    actual: dict[str, str] = {}
    for path in app_root.rglob("*"):
        if path.is_symlink():
            raise _IssuerError("Archived app contains a symlink")
        if path.is_file():
            relative = "backend/" + path.relative_to(inputs.archive_backend).as_posix()
            actual[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != inputs.manifest:
        raise _IssuerError("Archived app file set or bytes differ from the canonical manifest")


def _verify_loaded_modules(inputs: IssuerInput) -> dict[str, ModuleReceipt]:
    receipts: dict[str, ModuleReceipt] = {}
    for name, module in tuple(sys.modules.items()):
        if name != "app" and not name.startswith("app."):
            continue
        if module is None or module.__file__ is None:
            raise _IssuerError("A canonical app module has no source-file origin")
        origin = Path(module.__file__).resolve(strict=True)
        if not origin.is_relative_to(inputs.archive_backend / "app"):
            raise _IssuerError("A canonical app module was imported outside the archive")
        relative = "backend/" + origin.relative_to(inputs.archive_backend).as_posix()
        digest = hashlib.sha256(origin.read_bytes()).hexdigest()
        if inputs.manifest.get(relative) != digest:
            raise _IssuerError("An imported app module does not match its expected file hash")
        if module.__spec__ is None or module.__spec__.origin is None:
            raise _IssuerError("An imported app module lacks a normal import specification")
        if Path(module.__spec__.origin).resolve(strict=True) != origin:
            raise _IssuerError("An app module's import specification disagrees with its file")
        package_paths: object = getattr(module, "__path__", None)
        if package_paths is not None:
            paths = cast(list[str], package_paths)
            if [Path(path).resolve(strict=True) for path in paths] != [origin.parent]:
                raise _IssuerError("An app package searches outside its canonical archive path")
        receipts[name] = {"origin": str(origin), "manifest_path": relative, "sha256": digest}
    if not _REQUIRED_APP_MODULES.issubset(receipts):
        raise _IssuerError("The complete archived issuer dependency closure was not imported")
    return dict(sorted(receipts.items()))


def _dependency_versions() -> dict[str, DependencyReceipt]:
    versions: dict[str, DependencyReceipt] = {}
    for distribution, module_name in sorted(_DEPENDENCY_MODULES.items()):
        try:
            version = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError as exc:
            raise _IssuerError("A required dependency has no installed version metadata") from exc
        module = sys.modules.get(module_name)
        origin = module.__file__ if module is not None else None
        versions[distribution] = {
            "version": version,
            "module": module_name,
            "loaded": module is not None,
            "module_origin": str(Path(origin).resolve()) if origin is not None else None,
        }
    return versions


def _check_child_environment(inputs: IssuerInput) -> None:
    if (inputs.archive_backend / ".env").exists():
        raise _IssuerError("The archived backend must not contain an environment file")
    for name in (
        "GOATFARM_DATABASE_URL",
        "GOATFARM_JWT_PRIVATE_KEY_PATH",
        "GOATFARM_JWT_PUBLIC_KEY_PATH",
        "GOATFARM_TOTP_ENCRYPTION_KEY",
    ):
        if not os.environ.get(name):
            raise _IssuerError("The parent did not supply all explicit issuer resource settings")
    if os.environ.get("GOATFARM_ENVIRONMENT") != "development":
        raise _IssuerError("This isolated adapter requires the explicit development profile")


async def _post(
    client: AsyncClient,
    inputs: IssuerInput,
    receipts: list[RequestReceipt],
    path: str,
    body: dict[str, str],
    expected_status: int,
    *,
    bearer: str | None = None,
) -> tuple[Response, dict[str, object]]:
    _verify_loaded_modules(inputs)
    started_at = time.time()
    started_monotonic = time.monotonic()
    headers = {} if bearer is None else {"Authorization": f"Bearer {bearer}"}
    async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
        response = await client.post(path, json=body, headers=headers)
    received_at = time.time()
    receipts.append(
        {
            "method": "POST",
            "path": path,
            "status_code": response.status_code,
            "started_at_unix": started_at,
            "received_at_unix": received_at,
            "elapsed_seconds": time.monotonic() - started_monotonic,
            "response_sha256": hashlib.sha256(response.content).hexdigest(),
            "response_bytes": len(response.content),
            "set_cookie_count": len(response.headers.get_list("set-cookie")),
        }
    )
    _verify_loaded_modules(inputs)
    if response.status_code != expected_status:
        raise _IssuerError(f"Archived public {path} returned HTTP {response.status_code}")
    try:
        decoded: object = response.json()
    except ValueError as exc:
        raise _IssuerError(f"Archived public {path} returned invalid JSON") from exc
    return response, _object(decoded, f"Archived public {path} response")


def _registration_credentials(body: dict[str, object], email: str) -> tuple[str, int]:
    if set(body) != {"access_token", "token_type", "user"} or body["token_type"] != "bearer":
        raise _IssuerError("Archived registration did not return the actual TokenOut shape")
    access_token = _string(body["access_token"], "Registration access token")
    user = _object(body["user"], "Registration user")
    user_id = user.get("id")
    if type(user_id) is not int or user_id <= 0:
        raise _IssuerError("Archived registration returned an invalid user identity")
    if user.get("email") != email.strip().lower() or user.get("totp_state") is not None:
        raise _IssuerError("Archived registration returned unexpected fresh-account state")
    return access_token, user_id


def _enrollment_secret(body: dict[str, object]) -> str:
    if set(body) != {"secret", "otpauth_uri"}:
        raise _IssuerError("Archived enrollment did not return the actual TotpEnrollOut shape")
    secret = _string(body["secret"], "Enrollment secret")
    uri = urlsplit(_string(body["otpauth_uri"], "Enrollment otpauth URI"))
    query = parse_qs(uri.query, strict_parsing=True)
    if (
        uri.scheme != "otpauth"
        or uri.netloc != "totp"
        or query.get("secret") != [secret]
        or query.get("algorithm") != ["SHA1"]
        or query.get("digits") != ["6"]
        or query.get("period") != ["30"]
    ):
        raise _IssuerError("Archived enrollment does not describe the expected RFC6238 factor")
    return secret


def _validate_confirmation(body: dict[str, object]) -> None:
    codes = body.get("codes")
    if (
        set(body) != {"codes"}
        or not isinstance(codes, list)
        or not codes
        or not all(isinstance(code, str) and code for code in codes)
    ):
        raise _IssuerError("Archived confirmation did not return TotpRecoveryCodesOut")


async def _wait_for_next_step(activation_code_step: int) -> WaitReceipt:
    started_at = time.time()
    started_monotonic = time.monotonic()
    async with asyncio.timeout(NEXT_STEP_TIMEOUT_SECONDS):
        while True:
            observed_at = time.time()
            observed_step = int(observed_at // TOTP_STEP_SECONDS)
            if observed_step > activation_code_step:
                return {
                    "activation_code_step": activation_code_step,
                    "started_at_unix": started_at,
                    "completed_at_unix": observed_at,
                    "elapsed_seconds": time.monotonic() - started_monotonic,
                    "observed_step": observed_step,
                    "timeout_seconds": NEXT_STEP_TIMEOUT_SECONDS,
                }
            await asyncio.sleep(0.1)


def _login_mfa_token(response: Response, body: dict[str, object]) -> str:
    if set(body) != {"mfa_token", "access_token", "token_type", "user"}:
        raise _IssuerError("Archived password login did not return the actual LoginOut shape")
    if body["access_token"] is not None or body["user"] is not None:
        raise _IssuerError("Password-only MFA login unexpectedly returned session material")
    if body["token_type"] != "bearer" or response.headers.get_list("set-cookie"):
        raise _IssuerError("Password-only MFA login unexpectedly issued cookies or token type")
    token = _string(body["mfa_token"], "Actual archived login mfa_token")
    if len(token) > 4096:
        raise _IssuerError("Archived login mfa_token exceeds the public challenge schema")
    return token


async def _cleanup(clients: list[AsyncClient]) -> CleanupReceipt:
    failures: list[str] = []
    clients_closed = 0
    for client in reversed(clients):
        try:
            async with asyncio.timeout(CLEANUP_TIMEOUT_SECONDS):
                await client.aclose()
            clients_closed += 1
        except Exception as exc:
            failures.append(type(exc).__name__)
    database = sys.modules.get("app.db")
    engine = cast(_ArchiveDatabase, database)._engine if database is not None else None
    engine_disposed = False
    if engine is not None:
        try:
            async with asyncio.timeout(CLEANUP_TIMEOUT_SECONDS):
                await engine.dispose()
            engine_disposed = True
        except Exception as exc:
            failures.append(type(exc).__name__)
    if failures:
        raise _IssuerError("Issuer resource cleanup failed: " + ", ".join(failures))
    return {
        "clients_closed": clients_closed,
        "engine_present": engine is not None,
        "engine_disposed": engine_disposed,
        "per_resource_timeout_seconds": CLEANUP_TIMEOUT_SECONDS,
    }


async def issue_legacy_mfa(inputs: IssuerInput) -> IssuerResult:
    """Drive only ordinary archived public routes; never construct a token."""
    clients: list[AsyncClient] = []
    receipts: list[RequestReceipt] = []
    try:
        if sys.version_info < (3, 13):  # noqa: UP036 -- explicit pre-import compatibility check
            raise _IssuerError("The archived backend requires Python 3.13 or newer")
        _check_child_environment(inputs)
        _verify_tree(inputs)
        if any(name == "app" or name.startswith("app.") for name in sys.modules):
            raise _IssuerError("Issuer process already contains a canonical app package")

        # This is a dedicated child. Preserve archive bytes and pin canonical
        # imports before the first archived import; no Settings cache is reset.
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(inputs.archive_backend))
        import httpx

        archived_main = cast(_ArchiveMain, importlib.import_module("app.main"))
        _verify_loaded_modules(inputs)
        application = archived_main.create_app()
        _verify_loaded_modules(inputs)

        # ASGITransport never enters lifespan; each client owns its transport.
        setup_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application, raise_app_exceptions=True),
            base_url=BASE_URL,
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=False,
            trust_env=False,
        )
        clients.append(setup_client)
        _, registration = await _post(
            setup_client,
            inputs,
            receipts,
            "/api/auth/register",
            {"email": inputs.email, "password": inputs.password},
            201,
        )
        access_token, user_id = _registration_credentials(registration, inputs.email)
        _, enrollment = await _post(
            setup_client,
            inputs,
            receipts,
            "/api/auth/totp/enroll",
            {"current_password": inputs.password},
            200,
            bearer=access_token,
        )
        secret = _enrollment_secret(enrollment)
        generated_at = time.time()
        activation_step = int(generated_at // TOTP_STEP_SECONDS)
        _, confirmation = await _post(
            setup_client,
            inputs,
            receipts,
            "/api/auth/totp/confirm",
            {"code": totp_code(secret, activation_step)},
            200,
            bearer=access_token,
        )
        _validate_confirmation(confirmation)
        confirmation_received_at = receipts[-1]["received_at_unix"]
        wait_receipt = await _wait_for_next_step(activation_step)

        # A separate ordinary browser has no registration refresh cookie and
        # sends only the genuine password. No bearer is passed to this login.
        login_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application, raise_app_exceptions=True),
            base_url=BASE_URL,
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=False,
            trust_env=False,
        )
        clients.append(login_client)
        if login_client.cookies:
            raise _IssuerError("The password-login browser did not start with an empty cookie jar")
        login_response, login_body = await _post(
            login_client,
            inputs,
            receipts,
            "/api/auth/login",
            {"email": inputs.email, "password": inputs.password},
            200,
        )
        token = _login_mfa_token(login_response, login_body)
        if login_client.cookies:
            raise _IssuerError("The password-only MFA browser unexpectedly received cookies")
        raw_login_body = login_response.content.decode("utf-8")
        _verify_tree(inputs)
        loaded_modules = _verify_loaded_modules(inputs)
        dependencies = _dependency_versions()
    except _IssuerError:
        raise
    except Exception as exc:
        # Imported application errors may contain request values. Keep the
        # unknown actor failure as infrastructure without echoing its message.
        raise _IssuerError(f"Archived public issuer failed ({type(exc).__name__})") from None
    finally:
        try:
            cleanup_receipt = await _cleanup(clients)
        except _IssuerError:
            raise
        except Exception as exc:
            raise _IssuerError(f"Issuer cleanup failed ({type(exc).__name__})") from None

    return {
        "schema": "legacy-mfa-public-issuer-result-v1",
        "archive_commit": ARCHIVE_COMMIT,
        "archive_backend": str(inputs.archive_backend),
        "app_manifest_sha256": APP_MANIFEST_SHA256,
        "mfa_token": token,
        "mfa_token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
        "login_response_body": raw_login_body,
        "login_body_sha256": hashlib.sha256(login_response.content).hexdigest(),
        "secret": secret,
        "user_id": user_id,
        "activation_code_generated_at_unix": generated_at,
        "confirmation_received_at_unix": confirmation_received_at,
        "public_requests": receipts,
        "wait": wait_receipt,
        "loaded_modules": loaded_modules,
        "dependencies": dependencies,
        "runtime": {
            "executable": sys.executable,
            "python_version": sys.version,
            "implementation": sys.implementation.name,
            "lifespan": "off",
            "transport": "httpx.ASGITransport(raise_app_exceptions=True)",
            "base_url": BASE_URL,
        },
        "cleanup": cleanup_receipt,
        "result_ready_at_unix": time.time(),
    }


def main() -> None:
    """Keep all incidental stdout separate from the sole private JSON result."""
    protocol_stdout = sys.stdout
    with redirect_stdout(sys.stderr):
        try:
            inputs = read_input()
            result = asyncio.run(issue_legacy_mfa(inputs))
        except _IssuerError:
            raise
        except Exception as exc:
            raise _IssuerError(f"Issuer protocol failed ({type(exc).__name__})") from None
    json.dump(result, protocol_stdout, separators=(",", ":"), allow_nan=False)
    protocol_stdout.write("\n")
    protocol_stdout.flush()


if __name__ == "__main__":
    main()
