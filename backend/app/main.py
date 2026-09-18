"""FastAPI app factory. Dev: uvicorn app.main:app --reload (from backend/)."""

import asyncio
import logging
import math
import os
import re
import time
import unicodedata
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from . import metrics, ratelimit
from .api import (
    animals,
    auth,
    breeding,
    buckets,
    dashboard,
    feeding,
    finance,
    health,
    kidding,
    ops_simulation,
    planner,
    purchases,
    screening,
    simulation,
    tasks,
    team,
)
from .core.config import get_settings
from .db import get_engine, get_sessionmaker
from .deps import deactivate_deleted_user_memberships, purge_expired_refresh_sessions
from .schemas.common import RequestValidationErrorOut
from .schemas.ops import HealthStatusOut, ReadinessStatusOut, ReadinessUnavailableOut
from .security import PasswordWorkCapacityError, prime_dummy_password_hash, validate_jwt_keypair
from .seed import repair_legacy_data_batch, seed_startup
from .services.animals import skip_inactive_animal_tasks_batch
from .services.cadence import ensure_cadence_farm_batch
from .services.idempotency import (
    IDEMPOTENCY_KEY_PATTERN,
    MAX_IDEMPOTENCY_KEY_LENGTH,
    purge_expired_idempotency_records,
)

logger = logging.getLogger("goatfarm")

CORS_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]
CORS_HEADERS = [
    "Authorization",
    "Content-Type",
    "Idempotency-Key",
    "X-Farm-Id",
    "X-Request-ID",
]
CORS_EXPOSE_HEADERS = ["Idempotency-Replayed", "X-Request-ID", "Retry-After"]

# Canonical verbs for the bounded metrics method label (RT-M-3); anything
# else collapses to a single "OTHER" series instead of per-spelling series.
_KNOWN_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})

# Mnemonic spellings for the control characters most likely to appear in a
# percent-decoded path; every other control char becomes a \uXXXX escape in
# _log_safe_path (RT-M-4).
_LOG_CONTROL_ESCAPES = {"\r": "\\r", "\n": "\\n", "\t": "\\t"}

# Request ID of the in-flight request, bound into every log record by
# _RequestIdFilter so a user report can be correlated with server logs.
_request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


class RequestBodyLimitMiddleware:
    """Bound request bodies both with and without a Content-Length header.

    The receive wrapper counts streamed/chunked bodies before the framework
    concatenates or JSON-decodes them, preventing an unauthenticated client
    from turning an otherwise bounded schema field into process-memory DoS.
    """

    def __init__(self, app: ASGIApp, max_bytes: int, max_target_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.max_target_bytes = max_target_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        raw_path = scope.get("raw_path") or str(scope.get("path", "")).encode("utf-8")
        query_string = scope.get("query_string", b"")
        if len(raw_path) + (1 if query_string else 0) + len(query_string) > self.max_target_bytes:
            response = JSONResponse(
                status_code=414,
                content={"detail": "Request target is too long"},
            )
            await response(scope, receive, send)
            return

        headers = {name.lower(): value for name, value in scope.get("headers", [])}
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                content_length = int(raw_length)
            except ValueError:
                response = JSONResponse(
                    status_code=400, content={"detail": "Invalid Content-Length"}
                )
                await response(scope, receive, send)
                return
            if content_length < 0:
                response = JSONResponse(
                    status_code=400, content={"detail": "Invalid Content-Length"}
                )
                await response(scope, receive, send)
                return
            if content_length > self.max_bytes:
                response = JSONResponse(
                    status_code=413,
                    content={"detail": "Request body is too large"},
                )
                await response(scope, receive, send)
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # Raised while the request is parsed inside FastAPI's
                    # exception boundary, producing the normal JSON 413.
                    raise HTTPException(status_code=413, detail="Request body is too large")
            return message

        await self.app(scope, limited_receive, send)


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_var.get()
        return True


def _configure_logging() -> None:
    """INFO-level root logging with the request ID in every line. No-op when
    a harness (uvicorn's log config, pytest) already installed handlers —
    except the filter, which is harmless on any handler."""
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s",
        )
    root.setLevel(logging.INFO)
    for handler in root.handlers:
        if not any(isinstance(f, _RequestIdFilter) for f in handler.filters):
            handler.addFilter(_RequestIdFilter())


async def _throttle_summary_loop(interval_seconds: int = 300) -> None:
    """DET-3/DET-4 (2026-09-16): periodically log per-scope 429 totals.

    The limiter's per-request "throttled" lines exist, but a slow distributed
    campaign below per-request alert thresholds is only visible once
    aggregated; in-memory state also dies with the process. One summary line
    per interval keeps the signal durable and greppable."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            summary = ratelimit.drain_throttle_rejections()
            if summary:
                logger.info(
                    "auth rate-limit 429 summary (last %ss): %s",
                    interval_seconds,
                    ", ".join(f"{scope}={count}" for scope, count in sorted(summary.items())),
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("throttle summary loop iteration failed")


async def _refresh_session_cleanup_loop(
    interval_seconds: int,
    batch_size: int,
    max_batches: int,
) -> None:
    """Bound refresh-session retention for a process that runs indefinitely."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            removed_total = 0
            for _batch in range(max_batches):
                async with get_sessionmaker()() as db:
                    removed = await purge_expired_refresh_sessions(
                        db,
                        batch_size=batch_size,
                    )
                    await db.commit()
                metrics.record_refresh_session_purge_batch(removed)
                removed_total += removed
                if removed < batch_size:
                    break
            if removed_total:
                logger.info("purged %d expired refresh_sessions rows", removed_total)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A transient database outage must not kill the maintenance loop;
            # readiness reports the outage and the next interval retries.
            logger.exception("periodic refresh-session cleanup failed")


async def _idempotency_cleanup_loop(
    interval_seconds: int,
    batch_size: int,
    max_batches: int,
) -> None:
    """Purge finite expiry batches outside all user request transactions."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            removed_total = 0
            for _batch in range(max_batches):
                async with get_sessionmaker()() as db:
                    removed = await purge_expired_idempotency_records(db, batch_size=batch_size)
                    await db.commit()
                metrics.record_maintenance_batch("idempotency_purge", removed)
                removed_total += removed
                if removed < batch_size:
                    break
            if removed_total:
                logger.info("purged %d expired idempotency records", removed_total)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("periodic idempotency cleanup failed")


async def _legacy_data_repair_loop(
    interval_seconds: int,
    farm_batch_size: int,
    task_batch_size: int,
    max_batches: int,
) -> None:
    """Repair legacy tenants after readiness, in finite concurrent-safe units."""
    while True:
        try:
            repaired_farms = 0
            repaired_tasks = 0
            for _batch in range(max_batches):
                async with get_sessionmaker()() as db:
                    farms, tasks_count = await repair_legacy_data_batch(
                        db,
                        farm_batch_size=farm_batch_size,
                        task_batch_size=task_batch_size,
                    )
                    await db.commit()
                repaired_farms += farms
                repaired_tasks += tasks_count
                metrics.record_maintenance_batch("legacy_repair", farms + tasks_count)
                if farms < farm_batch_size and tasks_count < task_batch_size:
                    break
            if repaired_farms or repaired_tasks:
                logger.info(
                    "legacy repair processed farms=%d tasks=%d",
                    repaired_farms,
                    repaired_tasks,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("periodic legacy-data repair failed")
        await asyncio.sleep(interval_seconds)


async def _inactive_animal_task_cleanup_loop(
    interval_seconds: int,
    batch_size: int,
    max_batches: int,
) -> None:
    """Converge hidden duties after animal lifecycle exits in finite units."""
    while True:
        try:
            removed_total = 0
            for _batch in range(max_batches):
                async with get_sessionmaker()() as db:
                    removed = await skip_inactive_animal_tasks_batch(
                        db,
                        batch_size=batch_size,
                    )
                    await db.commit()
                metrics.record_maintenance_batch("inactive_animal_tasks", removed)
                removed_total += removed
                if removed < batch_size:
                    break
            if removed_total:
                logger.info(
                    "skipped %d pending tasks linked to inactive animals",
                    removed_total,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("periodic inactive-animal task cleanup failed")
        await asyncio.sleep(interval_seconds)


async def _deleted_membership_cleanup_loop(
    interval_seconds: int,
    batch_size: int,
    max_batches: int,
) -> None:
    """Converge retained memberships after authoritative User tombstones."""
    while True:
        try:
            deactivated_total = 0
            for _batch in range(max_batches):
                async with get_sessionmaker()() as db:
                    deactivated = await deactivate_deleted_user_memberships(
                        db,
                        batch_size=batch_size,
                    )
                    await db.commit()
                metrics.record_maintenance_batch("deleted_memberships", deactivated)
                deactivated_total += deactivated
                if deactivated < batch_size:
                    break
            if deactivated_total:
                logger.info(
                    "deactivated %d memberships retained for deleted users",
                    deactivated_total,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("periodic deleted-membership cleanup failed")
        await asyncio.sleep(interval_seconds)


async def _cadence_materialization_loop(
    interval_seconds: int,
    farm_batch_size: int,
    max_batches: int,
) -> None:
    """Materialize recurring husbandry duties for every farm, in keyset pages.

    The task board GET is read-only; this short-interval sweep is what brings
    each farm's cadence calendar onto the board. Pages are finite and keyset
    (id > cursor), and the per-farm advisory lock inside the cadence service
    serializes any overlap between two sweeps of a rolling deploy.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            processed_total = 0
            after_farm_id = 0
            for _batch in range(max_batches):
                async with get_sessionmaker()() as db:
                    processed, after_farm_id = await ensure_cadence_farm_batch(
                        db,
                        batch_size=farm_batch_size,
                        after_farm_id=after_farm_id,
                    )
                    await db.commit()
                metrics.record_maintenance_batch("cadence_materialization", processed)
                processed_total += processed
                if processed < farm_batch_size:
                    break
            if processed_total:
                logger.info("cadence sweep materialized duties for %d farms", processed_total)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("periodic cadence materialization failed")


def _enforce_production_private_key_mode() -> None:
    """RT-A-3: refuse to boot when the production private key is shared.

    A world-readable mounted private key boots cleanly through
    ``validate_jwt_keypair`` but hands the RS256 signing capability to every
    local account on the host. ``os.stat`` follows the K8s-secret symlink
    deliberately kept legal for key reads; the pinned-fd in-read replacement
    check in security.py covers swap-in races. No-op outside production.
    """
    if get_settings().environment != "production":
        return
    private_key_path = Path(get_settings().jwt_private_key_path)
    key_mode = os.stat(private_key_path).st_mode
    if key_mode & 0o077:
        raise RuntimeError(
            f"Refusing to boot: JWT private key {private_key_path} is "
            f"group/other accessible (mode {oct(key_mode & 0o777)}) — "
            "chmod 600 it (mount secrets with restricted permissions)"
        )


def _metrics_method(request: Request) -> str:
    """Canonical verb for the metrics label (RT-M-3).

    The raw method token is attacker-chosen; free-range values give the
    label unbounded cardinality (one Prometheus time series per spelling).
    Map anything outside the standard verbs to a single OTHER bucket.
    """
    return request.method if request.method in _KNOWN_HTTP_METHODS else "OTHER"


def _log_safe_path(path: str) -> str:
    """Single-line, control-free path for request logs (RT-M-4).

    The logged path is percent-decoded upstream, so %0A/%0D inject
    newlines into the log line. Escape every control character (C0, DEL,
    C1) plus the Unicode line/paragraph separators so one request cannot
    forge subsequent log records or smuggle terminal escapes into log
    viewers. \r/\n/\t keep mnemonic spellings for grep-friendliness.
    """
    return "".join(
        _LOG_CONTROL_ESCAPES.get(char, f"\\u{ord(char):04x}")
        if unicodedata.category(char) == "Cc" or char in "\u2028\u2029"
        else char
        for char in path
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    # Schema is owned by Alembic (alembic upgrade head). Before readiness we
    # seed only fixed-size global reference data; tenant role/inventory/task
    # repair is the finite post-readiness worker below.
    logger.info("startup (environment=%s)", get_settings().environment)
    # Single-process guard: the auth rate limiter, simulation CPU budget and
    # worker-idempotency gates are in-memory per-process controls that the
    # provided Dockerfile's `--workers 1` makes correct. Any other launcher
    # (bare uvicorn --workers N, replicas) silently multiplies every budget
    # — surface that loudly instead of mis-deploying quietly. Detecting the
    # actual worker count is not reliably possible from inside the process.
    workers_env = os.environ.get("UVICORN_WORKERS", "1")
    concurrency_env = os.environ.get("WEB_CONCURRENCY", "1")
    if workers_env != "1" or concurrency_env != "1":
        logger.warning(
            "UVICORN_WORKERS/WEB_CONCURRENCY indicates a multi-process deployment: "
            "auth throttles, the simulation CPU budget and worker idempotency gates "
            "are per-process and will be multiplied. The provided Dockerfile runs "
            "--workers 1 by design."
        )
    # Fail before accepting traffic when active/previous production key files
    # are missing, malformed, weak, duplicate, unreadable, or when the active
    # pair does not match. Development may generate its active pair here.
    validate_jwt_keypair()
    _enforce_production_private_key_mode()
    # INFRA-1 (2026-09-16): with no trusted proxy configured, every client
    # shares the socket peer's identity in the per-IP auth ledgers. Behind a
    # direct edge/terminator that is the edge itself — one attacker can then
    # 429 all registrations deployment-wide. The shipped compose wires the
    # edge IP; a bare deployment (or one that adds an OUTER TLS terminator
    # without extending GOATFARM_TRUSTED_PROXY_HOSTS) must hear about it.
    settings = get_settings()
    if settings.environment == "production" and not settings.trusted_proxy_hosts:
        logger.warning(
            "GOATFARM_TRUSTED_PROXY_HOSTS is empty in production: X-Forwarded-For "
            "is ignored and every client shares one rate-limit identity. If any "
            "proxy, load balancer, or TLS terminator fronts this process, add "
            "its IP to GOATFARM_TRUSTED_PROXY_HOSTS or the per-IP auth ledgers "
            "become deployment-wide (one attacker locks out all signups)."
        )
    # Warm the timing-equalization dummy hash so the first unknown-email
    # login pays no cold-start cost.
    prime_dummy_password_hash()
    try:
        async with get_sessionmaker()() as db:
            await seed_startup(db)
            # One finite boot batch bounds readiness latency; the periodic
            # worker continues any remaining expiry cleanup after readiness.
            removed = await purge_expired_refresh_sessions(
                db,
                batch_size=get_settings().refresh_session_cleanup_batch_size,
            )
            if removed:
                logger.info("purged %d expired refresh_sessions rows", removed)
            await db.commit()
    except Exception:
        logger.critical("startup seeding failed; refusing to serve", exc_info=True)
        raise
    refresh_cleanup_task = asyncio.create_task(
        _refresh_session_cleanup_loop(
            get_settings().refresh_session_cleanup_interval_seconds,
            get_settings().refresh_session_cleanup_batch_size,
            get_settings().refresh_session_cleanup_max_batches,
        ),
        name="refresh-session-cleanup",
    )
    idempotency_cleanup_task = asyncio.create_task(
        _idempotency_cleanup_loop(
            get_settings().idempotency_cleanup_interval_seconds,
            get_settings().idempotency_cleanup_batch_size,
            get_settings().idempotency_cleanup_max_batches,
        ),
        name="idempotency-cleanup",
    )
    legacy_repair_task = asyncio.create_task(
        _legacy_data_repair_loop(
            get_settings().legacy_repair_interval_seconds,
            get_settings().legacy_repair_farm_batch_size,
            get_settings().legacy_repair_task_batch_size,
            get_settings().legacy_repair_max_batches,
        ),
        name="legacy-data-repair",
    )
    inactive_animal_task_cleanup_task = asyncio.create_task(
        _inactive_animal_task_cleanup_loop(
            get_settings().inactive_animal_task_cleanup_interval_seconds,
            get_settings().inactive_animal_task_cleanup_batch_size,
            get_settings().inactive_animal_task_cleanup_max_batches,
        ),
        name="inactive-animal-task-cleanup",
    )
    deleted_membership_cleanup_task = asyncio.create_task(
        _deleted_membership_cleanup_loop(
            get_settings().deleted_membership_cleanup_interval_seconds,
            get_settings().deleted_membership_cleanup_batch_size,
            get_settings().deleted_membership_cleanup_max_batches,
        ),
        name="deleted-membership-cleanup",
    )
    cadence_materialization_task = asyncio.create_task(
        _cadence_materialization_loop(
            get_settings().cadence_materialization_interval_seconds,
            get_settings().cadence_materialization_farm_batch_size,
            get_settings().cadence_materialization_max_batches,
        ),
        name="cadence-materialization",
    )
    throttle_summary_task = asyncio.create_task(_throttle_summary_loop(), name="throttle-summary")
    try:
        yield
    finally:
        refresh_cleanup_task.cancel()
        idempotency_cleanup_task.cancel()
        throttle_summary_task.cancel()
        legacy_repair_task.cancel()
        inactive_animal_task_cleanup_task.cancel()
        deleted_membership_cleanup_task.cancel()
        cadence_materialization_task.cancel()
        for cleanup_task in (
            refresh_cleanup_task,
            idempotency_cleanup_task,
            legacy_repair_task,
            inactive_animal_task_cleanup_task,
            deleted_membership_cleanup_task,
            cadence_materialization_task,
        ):
            with suppress(asyncio.CancelledError):
                await cleanup_task
        logger.info("shutdown: disposing database engine")
        await get_engine().dispose()


def _json_safe(value: Any) -> Any:
    """Make a validation-error payload JSON-safe. Starlette's JSONResponse
    dumps with allow_nan=False, so a rejected non-finite float (raw JSON
    `NaN`/`Infinity` hitting a FiniteFloat validator) would otherwise crash
    the 422 response itself and surface as a 500."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)  # "nan" / "inf" / "-inf"
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


async def request_validation_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Return a stable 422 without reflecting rejected request data.

    Pydantic's raw errors include an ``input`` member.  Echoing it can place a
    mistyped password, clinical note, or other sensitive value into browser
    tooling and intermediary diagnostics.  Clients only need the location,
    machine type, and message; those fields are also safe from non-finite JSON
    values that motivated the original sanitizer.
    """
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    public_errors = [
        {key: error[key] for key in ("type", "loc", "msg") if key in error} for error in errors
    ]
    return JSONResponse(
        status_code=422,
        content={"detail": _json_safe(jsonable_encoder(public_errors))},
    )


def _apply_baseline_response_headers(request: Request, response: Response, request_id: str) -> None:
    """Correlation + baseline browser protections for every response.

    API deployments are sometimes exposed directly rather than solely through
    the hardened Next.js server, so these are set at this boundary too.
    """
    response.headers["X-Request-ID"] = request_id
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.path.startswith("/api/"):
        # Auth and farm payloads contain private data and bearer-adjacent
        # state. Shared browsers/proxies must not retain API responses.
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log the traceback (with request ID) but return an opaque 500 — no
    internals leak to the client.

    Starlette routes the ``Exception`` key to ServerErrorMiddleware, the
    OUTERMOST wrapper, so this runs above request_id_middleware: its `finally`
    has already reset the contextvar (the traceback would be logged as `[-]`)
    and its header pass never sees this response. Recover the id the middleware
    stashed on the request and stamp the same headers here instead. The
    exception is still re-raised by ServerErrorMiddleware afterwards, so the
    ASGI server keeps its own error signal.
    """
    request_id = getattr(request.state, "request_id", None) or _request_id_var.get()
    token = _request_id_var.set(request_id)
    try:
        # The percent-decoded path carries the same %0A/%0D log-forgery
        # surface as the access log (RT-M-4) — sanitize here too, or a 500 on
        # a crafted path can forge subsequent log records from this sink.
        logger.error(
            "unhandled error on %s %s",
            request.method,
            _log_safe_path(request.url.path),
            exc_info=exc,
        )
    finally:
        _request_id_var.reset(token)
    response = JSONResponse(status_code=500, content={"detail": "Internal server error"})
    _apply_baseline_response_headers(request, response, request_id)
    # ``Exception`` is rendered by Starlette's outer ServerErrorMiddleware,
    # above every user-installed middleware.  CORSMiddleware therefore never
    # sees this response; reproduce its exact-origin simple-response headers so
    # an allowed SPA can read the structured 500 and correlation id.
    origin = request.headers.get("Origin")
    allowed_origins = get_settings().cors_origins
    if origin is not None and ("*" in allowed_origins or origin in allowed_origins):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Expose-Headers"] = ", ".join(CORS_EXPOSE_HEADERS)
        response.headers.add_vary_header("Origin")
    return response


async def password_capacity_handler(request: Request, exc: Exception) -> JSONResponse:
    """Keep Argon2 bursts from queuing memory-hard work or blocking the loop."""
    assert isinstance(exc, PasswordWorkCapacityError)
    return JSONResponse(
        status_code=429,
        content={"detail": "Password service is busy — please retry shortly."},
        headers={"Retry-After": "1"},
    )


async def healthz() -> HealthStatusOut:
    """Liveness: the process is up. Unauthenticated, no dependencies."""
    return HealthStatusOut(status="ok")


async def readyz() -> ReadinessStatusOut | JSONResponse:
    """Readiness: the DB pool can serve a query (SELECT 1)."""
    try:
        async with get_sessionmaker()() as db:
            await db.execute(text("SELECT 1"))
    except Exception:
        logger.exception("readiness probe failed")
        return JSONResponse(
            status_code=503,
            content=ReadinessUnavailableOut(status="unavailable").model_dump(mode="json"),
        )
    return ReadinessStatusOut(status="ready")


# Mutations whose Idempotency-Key the server REQUIRES (see
# services.idempotency.required_idempotency_key). FastAPI infers the header's
# OpenAPI "required" flag from the dependency parameter's None default, so it
# publishes optional even where a keyless request is a guaranteed 422 — the
# generated contract must tell clients the truth.
_REQUIRED_IDEMPOTENCY_HEADER_ROUTES = (
    ("/api/finance/new", "post"),
    ("/api/feeding/dispense", "post"),
    ("/api/feeding/mix", "post"),
    ("/api/feeding/inventory/{item_id}/add", "post"),
    ("/api/purchases/new", "post"),
    ("/api/auth/farms", "post"),
)


def _publish_required_idempotency_headers(app: FastAPI) -> None:
    default_openapi = app.openapi
    request_validation_schema = RequestValidationErrorOut.model_json_schema(
        ref_template="#/components/schemas/{model}"
    )
    request_validation_definitions = request_validation_schema.pop("$defs", {})

    def openapi_with_required_headers() -> dict[str, Any]:
        schema = default_openapi()
        # Router-wide 422 docs intentionally use a oneOf for domain
        # ErrorOuts and the sanitised request-validation list. FastAPI only
        # registers components it discovers through response_model/model;
        # adding a model there would merge a sibling $ref into the oneOf and
        # incorrectly make domain-error strings invalid. Register this exact
        # custom handler shape explicitly instead.
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        for name, definition in request_validation_definitions.items():
            components.setdefault(name, definition)
        components.setdefault("RequestValidationErrorOut", request_validation_schema)
        for path, method in _REQUIRED_IDEMPOTENCY_HEADER_ROUTES:
            for parameter in schema["paths"][path][method]["parameters"]:
                if parameter.get("name") == "Idempotency-Key" and parameter.get("in") == "header":
                    parameter["required"] = True
                    # FastAPI derives the parameter schema from the Optional
                    # dependency default, leaving ``anyOf: [string, null]``
                    # even though a missing key is a 422 on these routes.
                    # Drop the null arm (keeping the validated bounds) so
                    # generated clients type the header as the mandatory
                    # string it is.
                    parameter["schema"] = {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_IDEMPOTENCY_KEY_LENGTH,
                        "pattern": IDEMPOTENCY_KEY_PATTERN,
                    }
        return schema

    app.openapi = openapi_with_required_headers  # type: ignore[method-assign]


async def metrics_endpoint() -> Response:
    """Prometheus text exposition for internal scrapers (never under /api)."""
    return Response(content=metrics.render(), media_type=metrics.CONTENT_TYPE_LATEST)


def create_app() -> FastAPI:
    _configure_logging()
    settings = get_settings()
    # Fail fast (and loudly, with the single-replica explanation) if the
    # configured limiter backend does not exist. Settings validation rejects
    # unknown values first; this keeps the seam honest for direct Settings()
    # construction.
    ratelimit.build_limiter_backend()
    is_production = settings.environment == "production"
    # Interactive docs and the raw schema are dev conveniences; in production
    # they disclose the full API surface, so they are not served at all.
    app = FastAPI(
        title="Goat Farm Management API",
        version="2.0.0",
        lifespan=lifespan,
        docs_url=None if is_production else "/docs",
        redoc_url=None if is_production else "/redoc",
        openapi_url=None if is_production else "/openapi.json",
    )
    _publish_required_idempotency_headers(app)
    app.add_exception_handler(RequestValidationError, request_validation_handler)
    app.add_exception_handler(PasswordWorkCapacityError, password_capacity_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_bytes=settings.max_request_body_bytes,
        max_target_bytes=settings.max_request_target_bytes,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        # Enumerated (not "*") so the credentialed preflight surface matches
        # exactly what the API and SPA use.
        allow_methods=CORS_METHODS,
        allow_headers=CORS_HEADERS,
        expose_headers=CORS_EXPOSE_HEADERS,
    )
    trusted = [h.strip() for h in settings.trusted_proxy_hosts.split(",") if h.strip()]
    if trusted:
        # Only honor X-Forwarded-For when it arrives from a configured proxy;
        # with the default (trust nothing) a client can spoof the header but
        # it is ignored, so it can't steer the auth rate limiter.
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=trusted)

    def _route_template(request: Request) -> str:
        """Route *template* for metrics/labels (bounded), not the raw path."""
        route = request.scope.get("route")
        path = getattr(route, "path", None)
        return path if isinstance(path, str) and path else "unmatched"

    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Honor a well-formed inbound ID (proxy/load-balancer correlation);
        # anything else gets a fresh UUID so client input can't forge logs.
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _REQUEST_ID_RE.fullmatch(incoming) else uuid.uuid4().hex
        token = _request_id_var.set(request_id)
        # Also on the request: an unhandled error is rendered by
        # ServerErrorMiddleware, outside this middleware and after the
        # contextvar below is reset.
        request.state.request_id = request_id
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            # Read-only hook: the idempotency service (out of rewrite scope
            # here) marks every replayed response with this header, so
            # counting it at the middleware covers every idempotent mutation
            # without touching the service or its callers.
            if response.headers.get("Idempotency-Replayed") == "true":
                metrics.record_idempotency_replay()
            _apply_baseline_response_headers(request, response, request_id)
            return response
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            metrics.observe_http_request(
                _metrics_method(request),
                _route_template(request),
                status_code,
                duration_ms / 1000,
            )
            logger.info(
                "request method=%s path=%s status=%d duration_ms=%.2f",
                request.method,
                _log_safe_path(request.url.path),
                status_code,
                duration_ms,
            )
            _request_id_var.reset(token)

    app.get(
        "/healthz",
        response_model=HealthStatusOut,
        include_in_schema=True,
    )(healthz)
    app.get(
        "/readyz",
        response_model=ReadinessStatusOut,
        responses={
            503: {
                "model": ReadinessUnavailableOut,
                "description": "Database connection unavailable",
            }
        },
        include_in_schema=True,
    )(readyz)
    if settings.metrics_enabled:
        # Deliberately NOT under /api: the compose edge routes /api/ to the
        # backend and everything else to the frontend, so this endpoint is
        # unreachable from the public internet in the shipped topology and
        # can stay unauthenticated for internal scrapers. It is likewise kept
        # out of the OpenAPI contract (include_in_schema=False) because it is
        # an operational interface, not part of the client API. When
        # GOATFARM_METRICS_ENABLED=false the route is not registered at all
        # (404) and app.metrics collectors take no observations.
        app.get("/metrics", include_in_schema=False)(metrics_endpoint)
    app.include_router(auth.router)
    app.include_router(animals.router)
    app.include_router(buckets.router)
    app.include_router(breeding.router)
    app.include_router(kidding.router)
    app.include_router(health.router)
    app.include_router(tasks.router)
    app.include_router(feeding.router)
    app.include_router(finance.router)
    app.include_router(purchases.router)
    app.include_router(dashboard.router)
    app.include_router(team.router)
    app.include_router(simulation.router)
    app.include_router(planner.router)
    app.include_router(ops_simulation.router)
    app.include_router(screening.router)
    return app


app = create_app()
