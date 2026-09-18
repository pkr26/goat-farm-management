"""Async database engine/session (SQLAlchemy 2.0 + asyncpg)."""

import asyncio
import json
import ssl
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .core.config import get_settings


class DatabaseTlsSettings(Protocol):
    """The configuration subset that selects authenticated DB transport."""

    @property
    def db_sslmode(self) -> str: ...

    @property
    def db_sslrootcert_path(self) -> Path | None: ...


class DatabaseRuntimeSettings(DatabaseTlsSettings, Protocol):
    """The subset of configuration needed to open an application DB pool."""

    database_url: str
    db_pool_size: int
    db_max_overflow: int
    db_pool_timeout: int
    db_statement_timeout_ms: int


def database_ssl_connect_arg(settings: DatabaseTlsSettings) -> str | ssl.SSLContext:
    """Return asyncpg's TLS argument without relying on ``~/.postgresql``.

    Passing the mode string ``verify-full`` makes asyncpg search for a
    per-user ``root.crt``. The non-root container image intentionally has no
    such home directory, so a production pool would fail only at first
    connection. An explicit standard-library context instead uses either an
    operator-mounted CA file or the image's normal system trust store, while
    preserving verify-full hostname checking.

    ``allow``/``prefer`` retain asyncpg's mode-string fallback semantics;
    production validation never permits either one.
    """
    if settings.db_sslmode not in {"verify-ca", "verify-full"}:
        return settings.db_sslmode
    context = ssl.create_default_context(
        cafile=str(settings.db_sslrootcert_path) if settings.db_sslrootcert_path else None
    )
    context.check_hostname = settings.db_sslmode == "verify-full"
    return context


class Base(DeclarativeBase):
    pass


class JSONText(TypeDecorator[str]):
    """JSONB storage behind the codebase's JSON-text Python interface.

    The four JSON columns (roles.permissions, planner_plans.targets /
    .assumptions, simulation_scenarios.assumptions) historically lived in
    Text with every reader/writer speaking ``json.dumps``/``json.loads``
    strings. The database now stores jsonb — validity and array/object shape
    enforced by CHECK constraints — while the ORM attribute keeps the
    historical str interface, so call sites need no mechanical rewrite:
    bind accepts pre-encoded JSON text (or structures) and result rows come
    back as JSON text.

    TypeDecorator (rather than subclassing JSONB) is required: the asyncpg
    dialect re-adapts plain JSONB subclasses to its own codec type, which
    would double-encode text binds. jsonb normalization (key order,
    whitespace) may differ from the originally written bytes; every consumer
    parses rather than compares the text.
    """

    impl = JSONB
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: object) -> object | None:
        if isinstance(value, str):
            # Already-encoded JSON text: parse so the underlying JSON
            # serializer cannot double-encode it into a string scalar.
            parsed: object = json.loads(value)
            return parsed
        return value

    def result_processor(self, dialect: object, coltype: object) -> Any:
        # Deliberately not process_result_value: the TypeDecorator chain would
        # first run the raw JSONB impl's json.loads, but SQLAlchemy installs a
        # jsonb codec on asyncpg connections that already hands over parsed
        # Python objects — re-parsing those raises. Accept either shape.
        def process(value: object | None) -> str | None:
            if value is None or isinstance(value, str):
                return value if value is None else str(value)
            return json.dumps(value)

        return process


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def create_engine(settings: DatabaseRuntimeSettings) -> AsyncEngine:
    """Create an independent pool from an explicit settings projection.

    The API retains its process-global pool below. The screening worker calls
    this factory with its least-privilege settings, which avoids importing the
    API's JWT/cookie configuration just to connect to PostgreSQL.
    """
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={
            "server_settings": {"statement_timeout": str(settings.db_statement_timeout_ms)},
            "ssl": database_ssl_connect_arg(settings),
        },
    )


def create_sessionmaker(settings: DatabaseRuntimeSettings) -> async_sessionmaker[AsyncSession]:
    """Create a session factory owned by a non-API process."""
    return async_sessionmaker(bind=create_engine(settings), autoflush=False, expire_on_commit=False)


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings())
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(), autoflush=False, expire_on_commit=False
        )
    return _sessionmaker


def reset_engine() -> None:
    """Test hook: drop cached engine/session so settings changes apply."""
    global _engine, _sessionmaker
    engine, _engine, _sessionmaker = _engine, None, None
    if engine is None:
        return
    try:
        asyncio.run(engine.dispose())
    except RuntimeError:
        # The engine is bound to another (possibly closed) event loop — e.g.
        # conftest's one-shot seeding runs on a throwaway loop, or a loop is
        # already running here. Its connections die with that loop; dropping
        # the engine un-disposed is harmless in the test harness.
        pass


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session
