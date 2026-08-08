"""Async database engine/session (SQLAlchemy 2.0 + asyncpg)."""

import asyncio
from collections.abc import AsyncGenerator

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
