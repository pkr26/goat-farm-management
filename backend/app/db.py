"""Async database engine/session (SQLAlchemy 2.0 + asyncpg)."""

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

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


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout,
            # Verify each checked-out connection is alive (survives Postgres
            # restarts/failover), and recycle connections well under typical
            # NAT/LB idle reaping so a dead connection never serves a request.
            pool_pre_ping=True,
            pool_recycle=1800,
            connect_args={
                "server_settings": {"statement_timeout": str(settings.db_statement_timeout_ms)},
                "ssl": settings.db_sslmode,
            },
        )
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
