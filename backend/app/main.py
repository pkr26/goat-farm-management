"""FastAPI app factory. Dev: uvicorn app.main:app --reload (from backend/)."""

import math
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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
from .db import get_sessionmaker
from .seed import seed_startup


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    # Schema is owned by Alembic (alembic upgrade head); startup seeds are
    # idempotent reference data, role presets and task backfills.
    async with get_sessionmaker()() as db:
        await seed_startup(db)
    yield


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


def create_app() -> FastAPI:
    app = FastAPI(title="Goat Farm Management API", version="2.0.0", lifespan=lifespan)
    app.add_exception_handler(RequestValidationError, request_validation_handler)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    trusted = [h.strip() for h in get_settings().trusted_proxy_hosts.split(",") if h.strip()]
    if trusted:
        # Only honor X-Forwarded-For when it arrives from a configured proxy;
        # with the default (trust nothing) a client can spoof the header but
        # it is ignored, so it can't steer the auth rate limiter.
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=trusted)
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
