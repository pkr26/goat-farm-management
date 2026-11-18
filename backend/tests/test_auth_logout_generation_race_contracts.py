"""Exact-session cancellation must reauthorize after a concurrent cookie logout."""

import asyncio

import asyncpg
import httpx
import pytest
from sqlalchemy import Select, event, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import auth as auth_api
from app.core.config import get_settings
from app.db import get_engine, get_sessionmaker
from app.deps import revoke_session_family
from app.models import RefreshSession, User
from app.security import decode_access_claims, decode_refresh_claims

from .conftest import OWNER_PW, register


class _AccountLockQuery:
    """Observe the real cancelling request's final account-lock query."""

    def __init__(self) -> None:
        self.enabled = False
        self.reached = asyncio.Event()
        self.pid: int | None = None

    def capture(
        self,
        connection: Connection,
        _cursor: object,
        _statement: str,
        _parameters: object,
        context: object,
        _executemany: bool,
    ) -> None:
        if not self.enabled or self.reached.is_set():
            return
        compiled = getattr(context, "compiled", None)
        query = getattr(compiled, "statement", None)
        if (
            not isinstance(query, Select)
            or query._for_update_arg is None
            or not any(column.get("entity") is User for column in query.column_descriptions)
        ):
            return
        driver = connection.connection.driver_connection
        assert isinstance(driver, asyncpg.Connection)
        self.pid = driver.get_server_pid()
        self.reached.set()


async def _wait_for_account_lock(
    query: _AccountLockQuery, holder_pid: int, request: asyncio.Task[httpx.Response]
) -> None:
    await asyncio.wait_for(query.reached.wait(), timeout=10)
    assert query.pid is not None
    async with asyncio.timeout(10):
        while not request.done():
            async with get_sessionmaker()() as observer:
                blocked = await observer.scalar(
                    text("SELECT :holder = ANY(pg_blocking_pids(:waiter))"),
                    {"holder": holder_pid, "waiter": query.pid},
                )
            if blocked:
                return
            await asyncio.sleep(0)
    raise AssertionError("Cancellation must wait for the other browser's real account lock")


async def test_cookie_logout_generation_winner_preserves_the_denied_cancellers_refresh_family(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    email = "logout-generation-race@farm.in"
    first = await register(client, email)
    first_claims = decode_access_claims(first["Authorization"].removeprefix("Bearer "))
    assert first_claims is not None and first_claims.scope.origin == "PASSWORD"
    first_cookie = client.cookies.get(get_settings().refresh_cookie_name)
    assert first_cookie is not None
    first_refresh = decode_refresh_claims(first_cookie)
    assert first_refresh is not None and first_refresh.family_id == first_claims.family_id

    transport = client._transport
    assert isinstance(transport, httpx.ASGITransport)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=transport.app), base_url="http://test"
    ) as other_browser:
        signed_in = await other_browser.post(
            "/api/auth/login", json={"email": email, "password": OWNER_PW}
        )
        assert signed_in.status_code == 200, signed_in.text
        other_claims = decode_access_claims(signed_in.json()["access_token"])
        assert other_claims is not None and other_claims.scope.origin == "PASSWORD"
        assert other_claims.user_id == first_claims.user_id
        assert other_claims.token_version == first_claims.token_version
        assert other_claims.family_id != first_claims.family_id

        winner_locked = asyncio.Event()
        allow_winner = asyncio.Event()
        winner_pid: int | None = None
        real_revoke_family = revoke_session_family

        async def pause_cookie_logout_under_its_account_lock(
            db: AsyncSession, family_id: str, *, user_id: int | None = None
        ) -> int:
            nonlocal winner_pid
            if family_id == other_claims.family_id and not winner_locked.is_set():
                # The real cookie route already holds User -> its own refresh
                # row here. Keep those actual locks while the other request
                # authenticates its old JWT and reaches its final User lock.
                pid = await db.scalar(text("SELECT pg_backend_pid()"))
                assert isinstance(pid, int)
                winner_pid = pid
                winner_locked.set()
                await asyncio.wait_for(allow_winner.wait(), timeout=15)
            return await real_revoke_family(db, family_id, user_id=user_id)

        monkeypatch.setattr(
            auth_api, "revoke_session_family", pause_cookie_logout_under_its_account_lock
        )
        observed = _AccountLockQuery()
        sync_engine = get_engine().sync_engine
        event.listen(sync_engine, "before_cursor_execute", observed.capture)
        winner: asyncio.Task[httpx.Response] | None = None
        cancel: asyncio.Task[httpx.Response] | None = None
        try:
            # Cookie only: this revokes B's family and advances the access
            # generation, while A's genuine refresh family stays usable.
            winner = asyncio.create_task(
                other_browser.post("/api/auth/logout", headers={"Origin": "http://test"})
            )
            await asyncio.wait_for(winner_locked.wait(), timeout=10)
            assert winner_pid is not None
            # The middleware executes requests in child tasks. Arm the query
            # observer only after B is held at its real lock; its next User
            # lock must therefore belong to A, independently of task identity.
            observed.enabled = True
            cancel = asyncio.create_task(client.post("/api/auth/logout-session", headers=first))
            await _wait_for_account_lock(observed, winner_pid, cancel)
            allow_winner.set()
            won = await asyncio.wait_for(winner, timeout=15)
            cancelled = await asyncio.wait_for(cancel, timeout=15)
        finally:
            allow_winner.set()
            event.remove(sync_engine, "before_cursor_execute", observed.capture)
            pending = [task for task in (winner, cancel) if task is not None]
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=15)

        assert won.status_code == 204, won.text
        async with get_sessionmaker()() as observer:
            current_generation = await observer.scalar(
                select(User.token_version).where(User.id == first_claims.user_id)
            )
            family_rows = (
                await observer.execute(
                    select(RefreshSession.family_id, RefreshSession.revoked_at).where(
                        RefreshSession.user_id == first_claims.user_id
                    )
                )
            ).all()
        assert current_generation is not None
        assert current_generation > first_claims.token_version
        assert client.cookies.get(get_settings().refresh_cookie_name) == first_cookie
        renewed = await client.post("/api/auth/refresh", headers={"Origin": "http://test"})
        assert (cancelled.status_code, renewed.status_code) == (401, 200), (
            "A cancellation that lost the final account-generation lock must be denied "
            "without revoking its otherwise-live refresh family: "
            f"cancel={cancelled.status_code} {cancelled.text}; "
            f"refresh={renewed.status_code} {renewed.text}"
        )
        assert cancelled.json()["detail"] == "Session is no longer valid"
        assert any(family == first_claims.family_id for family, _ in family_rows)
        assert all(
            revoked_at is None
            for family, revoked_at in family_rows
            if family == first_claims.family_id
        )
        assert any(family == other_claims.family_id for family, _ in family_rows)
        assert all(
            revoked_at is not None
            for family, revoked_at in family_rows
            if family == other_claims.family_id
        )
        renewed_claims = decode_access_claims(renewed.json()["access_token"])
        assert renewed_claims is not None
        assert renewed_claims.family_id == first_claims.family_id
        assert renewed_claims.token_version == current_generation


async def test_cookie_logout_generation_winner_rejects_an_already_verified_password_login(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new login may retry, but an older in-flight generation cannot mint a session."""
    email = "password-login-logout-generation-race@farm.in"
    transport = client._transport
    assert isinstance(transport, httpx.ASGITransport)
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=transport.app), base_url="http://test"
        ) as winner_browser,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=transport.app), base_url="http://test"
        ) as pending_browser,
    ):
        issued = await register(winner_browser, email)
        claims = decode_access_claims(issued["Authorization"].removeprefix("Bearer "))
        assert claims is not None and claims.scope.origin == "PASSWORD"
        cookie = winner_browser.cookies.get(get_settings().refresh_cookie_name)
        assert cookie is not None
        refresh = decode_refresh_claims(cookie)
        assert refresh is not None and refresh.family_id == claims.family_id
        async with get_sessionmaker()() as observer:
            before = (
                await observer.execute(
                    select(User.token_version, User.password_hash).where(User.id == claims.user_id)
                )
            ).one()
            original_session_ids = set(
                (
                    await observer.scalars(
                        select(RefreshSession.id).where(RefreshSession.user_id == claims.user_id)
                    )
                ).all()
            )

        winner_locked = asyncio.Event()
        release_winner = asyncio.Event()
        winner_pid: int | None = None
        real_revoke = revoke_session_family

        async def park_after_real_cookie_family_revocation(
            db: AsyncSession, family_id: str, *, user_id: int | None = None
        ) -> int:
            nonlocal winner_pid
            changed = await real_revoke(db, family_id, user_id=user_id)
            if family_id == refresh.family_id and not winner_locked.is_set():
                if changed <= 0:
                    raise RuntimeError("The cookie logout did not revoke its real live family")
                # The public cookie route already owns User -> RefreshSession;
                # this delegated UPDATE is real, still uncommitted, and has
                # not yet advanced the generation or released either lock.
                pid = await db.scalar(text("SELECT pg_backend_pid()"))
                if not isinstance(pid, int):
                    raise RuntimeError("The cookie logout has no native database PID")
                winner_pid = pid
                winner_locked.set()
                await asyncio.wait_for(release_winner.wait(), timeout=60)
            return changed

        monkeypatch.setattr(
            auth_api, "revoke_session_family", park_after_real_cookie_family_revocation
        )
        observed = _AccountLockQuery()
        engine = get_engine().sync_engine
        event.listen(engine, "before_cursor_execute", observed.capture)
        winner: asyncio.Task[httpx.Response] | None = None
        pending: asyncio.Task[httpx.Response] | None = None
        primary_error: BaseException | None = None
        try:
            winner = asyncio.create_task(
                winner_browser.post("/api/auth/logout", headers={"Origin": "http://test"})
            )
            await asyncio.wait_for(winner_locked.wait(), timeout=15)
            if winner_pid is None:
                raise RuntimeError("Cookie logout did not expose its real lock owner")
            # Arm after the winner's User query and UPDATE. The next real User
            # FOR UPDATE comes from password login after its scalar snapshot,
            # transaction rollback, and unmocked password verification.
            observed.enabled = True
            pending = asyncio.create_task(
                pending_browser.post("/api/auth/login", json={"email": email, "password": OWNER_PW})
            )
            await asyncio.wait_for(observed.reached.wait(), timeout=30)
            if observed.pid is None:
                raise RuntimeError("Password login did not expose its final native query PID")
            async with asyncio.timeout(15):
                while True:
                    async with get_sessionmaker()() as observer:
                        blocked = await observer.scalar(
                            text("SELECT :holder = ANY(pg_blocking_pids(:waiter))"),
                            {"holder": winner_pid, "waiter": observed.pid},
                        )
                    if blocked:
                        break
                    if pending.done():
                        pending.result()  # Preserve any genuine request exception.
                        raise RuntimeError("Password login never waited on the logout's User lock")
                    if winner.done():
                        winner.result()
                        raise RuntimeError("Cookie logout released its lock before observation")
                    await asyncio.sleep(0)
            release_winner.set()
            won = await asyncio.wait_for(winner, timeout=15)
            denied = await asyncio.wait_for(pending, timeout=15)
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            release_winner.set()
            event.remove(engine, "before_cursor_execute", observed.capture)
            actors = [task for task in (winner, pending) if task is not None]
            for task in actors:
                if not task.done():
                    task.cancel()
            try:
                outcomes = await asyncio.wait_for(
                    asyncio.gather(*actors, return_exceptions=True), timeout=15
                )
            except BaseException as cleanup_error:
                if primary_error is not None:
                    cleanup_error.add_note(f"Earlier request failure: {primary_error!r}")
                raise
            for outcome in outcomes:
                if (
                    isinstance(outcome, BaseException)
                    and not isinstance(outcome, asyncio.CancelledError)
                    and outcome is not primary_error
                ):
                    raise outcome from primary_error

        # The requests and their transactions are drained before any outcome
        # assertion. Observe admission before a genuinely new login can add a
        # refresh row of its own.
        async with get_sessionmaker()() as observer:
            after = (
                await observer.execute(
                    select(User.token_version, User.password_hash).where(User.id == claims.user_id)
                )
            ).one()
            sessions = (
                await observer.execute(
                    select(
                        RefreshSession.id, RefreshSession.family_id, RefreshSession.revoked_at
                    ).where(RefreshSession.user_id == claims.user_id)
                )
            ).all()
        pending_cookie = pending_browser.cookies.get(get_settings().refresh_cookie_name)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=transport.app), base_url="http://test"
        ) as fresh_browser:
            fresh = await fresh_browser.post(
                "/api/auth/login", json={"email": email, "password": OWNER_PW}
            )

        assert won.status_code == 204, won.text
        assert after.token_version > before.token_version
        assert after.password_hash == before.password_hash
        assert denied.status_code == 401, denied.text
        assert "access_token" not in denied.json()
        assert pending_cookie is None
        assert {row.id for row in sessions} == original_session_ids
        assert any(row.family_id == refresh.family_id for row in sessions)
        assert all(
            row.revoked_at is not None for row in sessions if row.family_id == refresh.family_id
        )
        assert fresh.status_code == 200, fresh.text
        fresh_claims = decode_access_claims(fresh.json()["access_token"])
        assert fresh_claims is not None
        assert fresh_claims.user_id == claims.user_id
        assert fresh_claims.token_version == after.token_version
