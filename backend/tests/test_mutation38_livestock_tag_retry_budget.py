"""Real namespace collisions respect the documented bounded registration retry."""

from collections.abc import AsyncGenerator
from datetime import timedelta
from types import TracebackType

import httpx
import pytest
from sqlalchemy import func, literal, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from app.api._shared import unique_constraint_name
from app.db import get_db, get_sessionmaker
from app.main import create_app
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import confirm, iso, make_breeding, make_buck, make_doe


async def _reserve_stillborn_tags(client: httpx.AsyncClient, tags: list[str]) -> dict[str, str]:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, tag="RETRY-DAM")
    buck = await make_buck(client, owner, tag="RETRY-SIRE")
    breeding = await make_breeding(
        client,
        owner,
        doe["id"],
        buck["id"],
        breeding_date=iso(today() - timedelta(days=150)),
    )
    breeding = await confirm(client, owner, breeding["id"], kid_count=len(tags))
    kidding = await client.post(
        "/api/kidding",
        headers=owner,
        json={
            "breeding_record_id": breeding["id"],
            "date": iso(today()),
            "ease": "NORMAL",
            "kids": [{"tag": tag, "sex": "F", "status": "STILLBORN"} for tag in tags],
        },
    )
    assert kidding.status_code == 201, kidding.text
    return owner


async def test_two_generated_namespace_collisions_fail_without_a_third_registration(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await _reserve_stillborn_tags(client, ["G-22222", "G-33333"])
    # Control only the random source. Both tag probes, INSERTs, trigger-backed
    # unique violations and savepoint rollbacks use the real application/DB.
    random_symbols = iter("222223333344444")

    def choose_symbol(alphabet: str) -> str:
        return next(random_symbols)

    monkeypatch.setattr("app.services.animals.secrets.choice", choose_symbol)
    response = await client.post(
        "/api/animals",
        headers=owner,
        json={"sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Tag 'G-33333' already exists on this farm."
    register = await client.get("/api/animals", headers=owner)
    assert register.status_code == 200, register.text
    assert {animal["tag_number"] for animal in register.json()["animals"]} == {
        "RETRY-DAM",
        "RETRY-SIRE",
    }


async def test_explicit_namespace_conflict_returns_400_while_next_writer_holds_namespace(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await _reserve_stillborn_tags(client, ["EXPLICIT-STILLBORN"])
    farm_id = int(owner["X-Farm-Id"])

    async def bounded_request_session() -> AsyncGenerator[AsyncSession]:
        async with get_sessionmaker()() as db:
            # Keep a forbidden extra INSERT's lock wait bounded. The expected
            # conflict response needs no new namespace lock after rollback.
            await db.execute(text("SET LOCAL lock_timeout = '750ms'"))
            yield db

    original_exit = AsyncSessionTransaction.__aexit__
    namespace_held = False
    async with get_sessionmaker()() as next_writer:

        async def hand_namespace_to_next_writer(
            transaction: AsyncSessionTransaction,
            exception_type: type[BaseException] | None,
            exception: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            nonlocal namespace_held
            await original_exit(transaction, exception_type, exception, traceback)
            if (
                not namespace_held
                and isinstance(exception, IntegrityError)
                and unique_constraint_name(exception) == "uq_stillborn_tag_farm_namespace"
            ):
                # Scheduling seam only: let the real conflicting INSERT and
                # rollback finish, then let another normal namespace writer
                # acquire the exact lock used by the kidding service.
                await next_writer.execute(select(func.pg_advisory_xact_lock(literal(farm_id))))
                namespace_held = True

        monkeypatch.setattr(AsyncSessionTransaction, "__aexit__", hand_namespace_to_next_writer)
        app = create_app()
        app.dependency_overrides[get_db] = bounded_request_session
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as requester:
            requester.cookies.update(client.cookies)
            response = await requester.post(
                "/api/animals",
                headers=owner | {"Idempotency-Key": "explicit-tag-collision-budget"},
                json={
                    "tag_number": "EXPLICIT-STILLBORN",
                    "sex": "F",
                    "source": "PURCHASED",
                    "current_bucket": "FOUNDATION",
                },
            )
        assert response.status_code == 400, response.text
        assert namespace_held, "the real trigger collision did not release its savepoint lock"
        assert response.json()["detail"] == "Tag 'EXPLICIT-STILLBORN' already exists on this farm."
        await next_writer.rollback()
