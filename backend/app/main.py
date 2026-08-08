"""FastAPI app factory. Dev: uvicorn app.main:app --reload (from backend/)."""

import logging
import math
import re
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import date, datetime
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.middleware.base import RequestResponseEndpoint
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

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
    purchases,
    simulation,
    tasks,
    team,
)
from .core.config import get_settings
from .db import get_engine, get_sessionmaker
from .deps import purge_expired_refresh_sessions
from .security import prime_dummy_password_hash
from .seed import seed_startup

logger = logging.getLogger("goatfarm")

# Request ID of the in-flight request, bound into every log record by
# _RequestIdFilter so a user report can be correlated with server logs.
_request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    # Schema is owned by Alembic (alembic upgrade head); startup seeds are
    # idempotent reference data, role presets and task backfills.
    logger.info("startup (environment=%s)", get_settings().environment)
    # Warm the timing-equalization dummy hash so the first unknown-email
    # login pays no cold-start cost (audit 2026-08-08 LOW).
    prime_dummy_password_hash()
    try:
        async with get_sessionmaker()() as db:
            await seed_startup(db)
            # Sweep expired refresh sessions on boot so the table doesn't
            # accumulate 14-day-old rows forever (audit 2026-08-08 MED).
            # Restarts happen regularly enough that on-boot is sufficient
            # until a proper scheduler is introduced.
            removed = await purge_expired_refresh_sessions(db)
            if removed:
                logger.info("purged %d expired refresh_sessions rows", removed)
            await db.commit()
    except Exception:
        logger.critical("startup seeding failed; refusing to serve", exc_info=True)
        raise
    yield
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
    """Standard 422 shape ({"detail": [...]}), sanitized so the offending
    input values can never break response serialization."""
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    return JSONResponse(status_code=422, content={"detail": _json_safe(jsonable_encoder(errors))})


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log the traceback (with request ID) but return an opaque 500 — no
    internals leak to the client."""
    logger.error("unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


async def healthz() -> dict[str, str]:
    """Liveness: the process is up. Unauthenticated, no dependencies."""
    return {"status": "ok"}


async def readyz() -> JSONResponse:
    """Readiness: the DB pool can serve a query (SELECT 1)."""
    try:
        async with get_sessionmaker()() as db:
            await db.execute(text("SELECT 1"))
    except Exception:
        logger.exception("readiness probe failed")
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return JSONResponse(content={"status": "ready"})


def create_app() -> FastAPI:
    _configure_logging()
    settings = get_settings()
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
    app.add_exception_handler(RequestValidationError, request_validation_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        # Enumerated (not "*") so the credentialed preflight surface matches
        # exactly what the API and SPA use.
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Farm-Id"],
    )
    trusted = [h.strip() for h in settings.trusted_proxy_hosts.split(",") if h.strip()]
    if trusted:
        # Only honor X-Forwarded-For when it arrives from a configured proxy;
        # with the default (trust nothing) a client can spoof the header but
        # it is ignored, so it can't steer the auth rate limiter.
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=trusted)

    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Honor a well-formed inbound ID (proxy/load-balancer correlation);
        # anything else gets a fresh UUID so client input can't forge logs.
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _REQUEST_ID_RE.fullmatch(incoming) else uuid.uuid4().hex
        token = _request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            _request_id_var.reset(token)
        response.headers["X-Request-ID"] = request_id
        return response

    app.get("/healthz", include_in_schema=True)(healthz)
    app.get("/readyz", include_in_schema=True)(readyz)
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
    return app


app = create_app()
