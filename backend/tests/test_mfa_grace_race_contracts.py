"""MFA expiry is revalidated after real awaited user-lock/nonce-cleanup work."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Self
from uuid import uuid4

import asyncpg
import httpx
import pytest
from sqlalchemy import Connection, delete, event, func, select, text

from app import security
from app.api import auth as auth_api
from app.db import get_engine, get_sessionmaker
from app.models import ConsumedMfaChallenge, RefreshSession, TotpRecoveryCode, User
from app.security import JWT_CLOCK_SKEW_SECONDS, TOTP_STEP_SECONDS, _totp_code_for_step

from .conftest import OWNER_PW, register
from .test_totp import _enroll_and_activate
from .test_totp import stable_totp_key as stable_totp_key

pytestmark = pytest.mark.usefixtures("stable_totp_key")


@dataclass(frozen=True)
class _Proof:
    user_id: int
    secret: str
    token: str
    jti: str
    expires: int


@dataclass(frozen=True)
class _State:
    last_step: int | None
    nonce_present: bool
    consumed_at: datetime | None
    sessions: int


async def _issued_proof(client: httpx.AsyncClient, email: str) -> _Proof:
    headers = await register(client, email)
    secret, _codes = await _enroll_and_activate(client, headers)
    me = await client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    client.cookies.clear()
    login = await client.post("/api/auth/login", json={"email": email, "password": OWNER_PW})
    assert login.status_code == 200 and login.json().get("access_token") is None
    token = str(login.json()["mfa_token"])
    claims, expired = security._decode_payload_result(token, "mfa")
    assert claims is not None and not expired
    return _Proof(int(me.json()["id"]), secret, token, str(claims["jti"]), int(claims["exp"]))


def _freeze(monkeypatch: pytest.MonkeyPatch, at: datetime) -> list[datetime]:
    clock = [at]

    class ClockDateTime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            return cls.fromtimestamp(clock[0].timestamp(), tz)

    # The issuer produced the original MFA token before either clock changed.
    # PyJWT's real iat clock therefore still validates that genuine proof;
    # the API's expiry and authenticator clocks advance together below.
    monkeypatch.setattr(security, "datetime", ClockDateTime)
    monkeypatch.setattr(auth_api, "utcnow", lambda: clock[0].replace(tzinfo=None))
    return clock


async def _exchange(
    client: httpx.AsyncClient, proof: _Proof, authenticator_at: datetime
) -> httpx.Response:
    step = int(authenticator_at.timestamp() // TOTP_STEP_SECONDS)
    return await client.post(
        "/api/auth/totp/challenge",
        json={"mfa_token": proof.token, "code": _totp_code_for_step(proof.secret, step)},
    )


async def _state(proof: _Proof) -> _State:
    async with get_sessionmaker()() as db:
        step = await db.scalar(select(User.totp_last_step).where(User.id == proof.user_id))
        nonce = await db.get(ConsumedMfaChallenge, proof.jti)
        sessions = await db.scalar(
            select(func.count())
            .select_from(RefreshSession)
            .where(RefreshSession.user_id == proof.user_id)
        )
        return _State(
            step, nonce is not None, nonce.consumed_at if nonce else None, int(sessions or 0)
        )


class _QueryBarrier:
    """Observe an actual driver query; never replace or block its SQL work."""

    def __init__(self, matches: Callable[[str], bool], clock: list[datetime]) -> None:
        self.matches = matches
        self.clock = clock
        self.reached = asyncio.Event()
        self.pid: int | None = None
        self.at: datetime | None = None

    def capture(
        self,
        connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if self.reached.is_set() or not self.matches(statement):
            return
        driver = connection.connection.driver_connection
        assert isinstance(driver, asyncpg.Connection)
        self.pid = driver.get_server_pid()
        self.at = self.clock[0]
        self.reached.set()


async def _wait_for_actual_block(
    request: asyncio.Task[httpx.Response],
    barrier: _QueryBarrier,
    holder_pid: int,
) -> None:
    boundary = asyncio.create_task(barrier.reached.wait())
    try:
        await asyncio.wait({boundary, request}, timeout=10, return_when=asyncio.FIRST_COMPLETED)
        assert boundary.done() and barrier.reached.is_set(), (
            "The authentic request must reach the intended real SQL boundary"
        )
        assert not request.done() and barrier.pid is not None
        # Confirm native PostgreSQL blocking, rather than assuming that the
        # before-cursor event alone means the server has received the query.
        async with asyncio.timeout(10):
            while not request.done():
                async with get_sessionmaker()() as observer:
                    blocked = await observer.scalar(
                        text("SELECT :holder = ANY(pg_blocking_pids(:waiter))"),
                        {"holder": holder_pid, "waiter": barrier.pid},
                    )
                if blocked:
                    return
                await asyncio.sleep(0)
        raise AssertionError("The intended real PostgreSQL lock did not block this request")
    finally:
        if not boundary.done():
            boundary.cancel()
        await asyncio.gather(boundary, return_exceptions=True)


async def test_spent_mfa_cannot_cross_clock_grace_while_waiting_for_the_real_user_lock(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proof = await _issued_proof(client, "mfa-user-lock-race@farm.in")
    clock = _freeze(monkeypatch, datetime.fromtimestamp(proof.expires - TOTP_STEP_SECONDS - 1, UTC))
    first = await _exchange(client, proof, clock[0])
    assert first.status_code == 200, first.text
    client.cookies.clear()
    before = await _state(proof)
    assert before.nonce_present
    enter_at = datetime.fromtimestamp(proof.expires + 59, UTC)
    release_at = datetime.fromtimestamp(proof.expires + 61, UTC)
    clock[0] = enter_at
    claims, expired = security._decode_payload_result(proof.token, "mfa")
    assert claims is not None and not expired
    barrier = _QueryBarrier(lambda sql: "FROM users" in sql and "FOR UPDATE" in sql, clock)
    engine = get_engine().sync_engine
    async with get_sessionmaker()() as holder:
        await holder.execute(select(User.id).where(User.id == proof.user_id).with_for_update())
        holder_pid = await holder.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(holder_pid, int)
        event.listen(engine, "before_cursor_execute", barrier.capture)
        request = asyncio.create_task(_exchange(client, proof, release_at))
        try:
            await _wait_for_actual_block(request, barrier, holder_pid)
            assert barrier.at == enter_at
            clock[0] = release_at
            assert security._decode_payload_result(proof.token, "mfa")[1] is True
            await holder.commit()
            response = await asyncio.wait_for(request, timeout=10)
        finally:
            event.remove(engine, "before_cursor_execute", barrier.capture)
            if not request.done():
                request.cancel()
            await holder.rollback()
            await asyncio.gather(request, return_exceptions=True)
    after = await _state(proof)
    assert response.status_code == 401, (
        "A spent MFA proof must not mint another session after a user-lock wait crosses grace: "
        f"status={response.status_code}, before={before}, after={after}"
    )
    assert "access_token" not in response.json()
    assert after == before, (
        "Replay refusal must preserve the spent nonce and unused authenticator step"
    )


async def test_unused_mfa_cannot_cross_clock_grace_while_waiting_for_real_nonce_cleanup(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proof = await _issued_proof(client, "mfa-cleanup-wait-race@farm.in")
    enter_at = datetime.fromtimestamp(proof.expires + 59, UTC)
    release_at = datetime.fromtimestamp(proof.expires + 61, UTC)
    clock = _freeze(monkeypatch, enter_at)
    claims, expired = security._decode_payload_result(proof.token, "mfa")
    assert claims is not None and not expired
    before = await _state(proof)
    assert not before.nonce_present
    historical_jti = uuid4().hex
    historical_expiry = datetime.fromtimestamp(proof.expires, UTC).replace(tzinfo=None) - timedelta(
        minutes=10
    )
    async with get_sessionmaker()() as db:
        db.add(
            ConsumedMfaChallenge(
                jti=historical_jti,
                expires_at=historical_expiry,
                consumed_at=historical_expiry - timedelta(seconds=30),
            )
        )
        await db.commit()
    barrier = _QueryBarrier(
        lambda sql: sql.startswith("DELETE FROM consumed_mfa_challenges"), clock
    )
    engine = get_engine().sync_engine
    async with get_sessionmaker()() as holder:
        # This is a valid older consumed nonce, deleted by an overlapping real
        # transaction. The request's global cleanup must wait on that row.
        await holder.execute(
            delete(ConsumedMfaChallenge).where(ConsumedMfaChallenge.jti == historical_jti)
        )
        holder_pid = await holder.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(holder_pid, int)
        event.listen(engine, "before_cursor_execute", barrier.capture)
        request = asyncio.create_task(_exchange(client, proof, release_at))
        try:
            await _wait_for_actual_block(request, barrier, holder_pid)
            assert barrier.at == enter_at, "The pre-consumption decode must still be live at59s"
            clock[0] = release_at
            assert security._decode_payload_result(proof.token, "mfa")[1] is True
            await holder.commit()
            response = await asyncio.wait_for(request, timeout=10)
        finally:
            event.remove(engine, "before_cursor_execute", barrier.capture)
            if not request.done():
                request.cancel()
            await holder.rollback()
            await asyncio.gather(request, return_exceptions=True)
    after = await _state(proof)
    async with get_sessionmaker()() as observer:
        assert await observer.get(ConsumedMfaChallenge, historical_jti) is None
    assert response.status_code == 401, (
        "An unused proof must be revalidated after awaited cleanup crosses its accepted expiry: "
        f"status={response.status_code}, before={before}, after={after}"
    )
    assert "access_token" not in response.json()
    assert after == before, (
        "Expired consumption must roll back its nonce, authenticator step and session"
    )


async def test_expired_mfa_does_not_wait_for_cleanup_after_its_user_lock_wait(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = client._transport
    if not isinstance(transport, httpx.ASGITransport):
        raise RuntimeError("This public-flow scheduling test requires its ASGI transport")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=transport.app), base_url="http://test"
    ) as browser:
        headers = await register(browser, "mfa-expired-admission@farm.in")
        secret, recovery_codes = await _enroll_and_activate(browser, headers)
        me = await browser.get("/api/auth/me", headers=headers)
        assert me.status_code == 200, me.text
        browser.cookies.clear()
        login = await browser.post(
            "/api/auth/login",
            json={"email": "mfa-expired-admission@farm.in", "password": OWNER_PW},
        )
        assert login.status_code == 200 and login.json().get("access_token") is None
        token = str(login.json()["mfa_token"])
        claims, expired = security._decode_payload_result(token, "mfa")
        if claims is None or expired:
            raise RuntimeError("The public issuer did not return a live MFA proof")
        proof = _Proof(int(me.json()["id"]), secret, token, str(claims["jti"]), int(claims["exp"]))
        clock = _freeze(
            monkeypatch, datetime.fromtimestamp(proof.expires - TOTP_STEP_SECONDS - 1, UTC)
        )
        first = await _exchange(browser, proof, clock[0])
        assert first.status_code == 200, first.text
        browser.cookies.clear()
        before = await _state(proof)
        if not before.nonce_present:
            raise RuntimeError("A real successful MFA exchange must own the spent-row fixture")

        async def recovery_usage() -> list[tuple[int, datetime | None]]:
            async with get_sessionmaker()() as db:
                rows = (
                    await db.execute(
                        select(TotpRecoveryCode.id, TotpRecoveryCode.used_at)
                        .where(TotpRecoveryCode.user_id == proof.user_id)
                        .order_by(TotpRecoveryCode.id)
                    )
                ).all()
                return [(row.id, row.used_at) for row in rows]

        unused_before = await recovery_usage()
        if not unused_before or any(used is not None for _, used in unused_before):
            raise RuntimeError("The real enrollment must provide unused recovery codes")
        recovery_code = recovery_codes[0]
        enter_at = datetime.fromtimestamp(proof.expires + JWT_CLOCK_SKEW_SECONDS - 1, UTC)
        expired_at = datetime.fromtimestamp(proof.expires + JWT_CLOCK_SKEW_SECONDS + 1, UTC)
        clock[0] = enter_at
        body, expired = security._decode_payload_result(proof.token, "mfa")
        if body is None or expired:
            raise RuntimeError("The request must start inside its genuine accepted grace")

        user_pid: int | None = None
        cleanup_pid: int | None = None

        def observe_user(
            connection: Connection,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: bool,
        ) -> None:
            nonlocal user_pid
            if user_pid is None and "FROM users" in statement and "FOR UPDATE" in statement:
                driver = connection.connection.driver_connection
                if not isinstance(driver, asyncpg.Connection):
                    raise RuntimeError("Cannot identify the real MFA user-lock connection")
                user_pid = driver.get_server_pid()

        def observe_cleanup(
            connection: Connection,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: bool,
        ) -> None:
            nonlocal cleanup_pid
            if cleanup_pid is None and statement.startswith("DELETE FROM consumed_mfa_challenges"):
                driver = connection.connection.driver_connection
                if not isinstance(driver, asyncpg.Connection):
                    raise RuntimeError("Cannot identify the real MFA cleanup connection")
                cleanup_pid = driver.get_server_pid()

        request: asyncio.Task[httpx.Response] | None = None
        response: httpx.Response | None = None
        cleanup_blocked = False
        observation_complete = False
        cleanup_listener = False
        engine = get_engine().sync_engine
        async with (
            get_sessionmaker()() as holder,
            get_sessionmaker()() as janitor,
            get_sessionmaker()() as observer,
        ):

            async def blocked_by(waiter: int, owner: int) -> bool:
                try:
                    return bool(
                        await observer.scalar(
                            text(
                                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                "WHERE pid=:waiter AND datname=current_database() "
                                "AND wait_event_type='Lock' "
                                "AND :owner=ANY(pg_blocking_pids(pid)))"
                            ),
                            {"waiter": waiter, "owner": owner},
                        )
                    )
                finally:
                    await observer.rollback()

            await holder.execute(select(User.id).where(User.id == proof.user_id).with_for_update())
            holder_pid = await holder.scalar(text("SELECT pg_backend_pid()"))
            if not isinstance(holder_pid, int):
                raise RuntimeError("Cannot identify the owned User lock")
            event.listen(engine, "before_cursor_execute", observe_user)
            try:
                request = asyncio.create_task(
                    browser.post(
                        "/api/auth/totp/challenge",
                        json={"mfa_token": proof.token, "code": recovery_code},
                    )
                )
                async with asyncio.timeout(15):
                    while True:
                        if request.done():
                            request.result()
                            raise RuntimeError("MFA request ended before its controlled User wait")
                        if user_pid is not None and await blocked_by(user_pid, holder_pid):
                            break
                        await asyncio.sleep(0)

                clock[0] = expired_at
                body, expired = security._decode_payload_result(proof.token, "mfa")
                if body is None or not expired:
                    raise RuntimeError(
                        "The forward clock must cross accepted expiry before release"
                    )
                janitor_pid = await janitor.scalar(text("SELECT pg_backend_pid()"))
                if not isinstance(janitor_pid, int):
                    raise RuntimeError("Cannot identify the owned nonce janitor")
                deleted = await janitor.execute(
                    delete(ConsumedMfaChallenge)
                    .where(
                        ConsumedMfaChallenge.jti == proof.jti,
                        ConsumedMfaChallenge.expires_at
                        < clock[0].replace(tzinfo=None) - timedelta(seconds=JWT_CLOCK_SKEW_SECONDS),
                    )
                    .returning(ConsumedMfaChallenge.jti)
                )
                if deleted.scalar_one_or_none() != proof.jti:
                    raise RuntimeError("The janitor must own the real now-expired consumed row")
                # The janitor's own DELETE is complete before this observer is
                # enabled. Recovery verification can recheckout a different
                # connection, so capture the request's actual cleanup PID anew.
                event.listen(engine, "before_cursor_execute", observe_cleanup)
                cleanup_listener = True
                await holder.commit()
                async with asyncio.timeout(20):
                    while True:
                        if request.done():
                            break
                        if cleanup_pid is not None and await blocked_by(cleanup_pid, janitor_pid):
                            cleanup_blocked = True
                            break
                        await asyncio.sleep(0)
                observation_complete = True
            finally:
                try:
                    try:
                        await holder.rollback()
                    finally:
                        if observation_complete:
                            await janitor.commit()
                        else:
                            await janitor.rollback()
                finally:
                    try:
                        if request is not None:
                            if observation_complete:
                                try:
                                    response = await asyncio.wait_for(asyncio.shield(request), 15)
                                finally:
                                    if not request.done():
                                        request.cancel()
                                        await asyncio.wait_for(
                                            asyncio.gather(request, return_exceptions=True), 10
                                        )
                            else:
                                if not request.done():
                                    request.cancel()
                                await asyncio.wait_for(
                                    asyncio.gather(request, return_exceptions=True), 15
                                )
                    finally:
                        event.remove(engine, "before_cursor_execute", observe_user)
                        if cleanup_listener:
                            event.remove(engine, "before_cursor_execute", observe_cleanup)
        if response is None:
            raise RuntimeError("The released MFA request has no HTTP outcome")
        after = await _state(proof)
        assert response.status_code == 401, response.text
        assert "access_token" not in response.json()
        assert after.sessions == before.sessions and after.last_step == before.last_step
        assert await recovery_usage() == unused_before
        assert not after.nonce_present, "The committed janitor legitimately removed the expired row"
        assert not cleanup_blocked, (
            "A proof already expired after its User wait must reject before depending on "
            "unrelated consumed-nonce cleanup ownership"
        )
