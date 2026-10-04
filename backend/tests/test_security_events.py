"""Security-event durability and bounded hostile-failure aggregation.

Real state transitions are append-only and transactionally projected. Inputs
an attacker can repeat without changing state use fixed-cardinality counters,
so detection remains possible without turning the audit table into a remote
write-amplification primitive.
"""

import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.api import auth as auth_api
from app.audit import (
    drain_transient_security_signals,
    emit_transient_security_signal_summary,
    note_transient_security_signal,
    security_event,
)
from app.core.config import get_settings
from app.db import get_engine, get_sessionmaker
from app.deps import revoke_session_family
from app.models import RefreshSession, SecurityEvent, User
from app.ratelimit import auth_limiter
from app.security import decode_access_claims, decode_refresh_claims

from .conftest import OWNER_PW, owner_with_farm, register

COOKIE = get_settings().refresh_cookie_name


def _events(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("security_event")
    ]


def set_refresh_cookie(client: httpx.AsyncClient, token: str) -> None:
    # Mirror tests/test_auth_extended.py: scope to the httpx test host so the
    # replayed token is actually sent (and replaces, not duplicates, rotated
    # cookies).
    client.cookies.clear()
    client.cookies.set(COOKIE, token, domain="test.local", path="/")


def test_transient_signal_cardinality_is_allowlisted_under_novel_input() -> None:
    drain_transient_security_signals()
    for _ in range(10_000):
        note_transient_security_signal("auth.token.invalid")
    with pytest.raises(ValueError, match="Unsupported transient security signal"):
        note_transient_security_signal("auth.token.invalid.attacker-value")  # type: ignore[arg-type]

    assert drain_transient_security_signals() == {"auth.token.invalid": 10_000}


async def test_security_event_is_atomic_with_commit_and_log_projection(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="goatfarm.audit"):
        async with get_sessionmaker()() as db:
            security_event(
                "test.security.committed",
                "committed security-event fixture",
                session=db,
                user_id=71,
            )
            await db.flush()
            assert not _events(caplog)
            await db.commit()

    events = _events(caplog)
    assert len(events) == 1
    assert "event='test.security.committed'" in events[0]
    assert "user_id=71" in events[0]

    async with get_sessionmaker()() as db:
        row = (
            await db.execute(
                select(SecurityEvent).where(SecurityEvent.event == "test.security.committed")
            )
        ).scalar_one()
    assert row.summary == "committed security-event fixture"
    assert row.targets == {"user_id": 71}


async def test_security_event_rollback_persists_and_projects_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="goatfarm.audit"):
        async with get_sessionmaker()() as db:
            security_event(
                "test.security.rolled_back",
                "rolled-back security-event fixture",
                session=db,
                user_id=72,
            )
            await db.flush()
            await db.rollback()

    assert not _events(caplog)
    async with get_sessionmaker()() as db:
        row = (
            await db.execute(
                select(SecurityEvent.id).where(SecurityEvent.event == "test.security.rolled_back")
            )
        ).scalar_one_or_none()
    assert row is None


async def test_security_event_rows_are_database_enforced_append_only() -> None:
    async with get_sessionmaker()() as db:
        security_event(
            "test.security.append_only",
            "append-only security-event fixture",
            session=db,
            user_id=73,
        )
        await db.commit()
        event_id = (
            await db.execute(
                select(SecurityEvent.id).where(SecurityEvent.event == "test.security.append_only")
            )
        ).scalar_one()

        with pytest.raises(DBAPIError, match="security_events is append-only"):
            await db.execute(
                update(SecurityEvent)
                .where(SecurityEvent.id == event_id)
                .values(summary="rewritten")
            )
        await db.rollback()

        with pytest.raises(DBAPIError, match="security_events is append-only"):
            await db.execute(delete(SecurityEvent).where(SecurityEvent.id == event_id))
        await db.rollback()

        row = await db.get(SecurityEvent, event_id)
        assert row is not None
        assert row.summary == "append-only security-event fixture"


async def test_refresh_reuse_emits_family_revoked_event(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    await register(client, "evt-reuse@farm.in")
    old = client.cookies.get(COOKIE)
    assert old
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 200
    rotated = client.cookies.get(COOKIE)
    assert rotated and rotated != old

    old_claims = decode_refresh_claims(old)
    assert old_claims is not None
    async with get_sessionmaker()() as db:
        await db.execute(
            update(RefreshSession)
            .where(RefreshSession.jti == old_claims.jti)
            .values(
                consumed_at=datetime.now(UTC).replace(tzinfo=None)
                - timedelta(seconds=get_settings().refresh_reuse_grace_seconds + 1)
            )
        )
        await db.commit()

    client.cookies.set(COOKIE, old, domain="test.local", path="/")
    with caplog.at_level(logging.INFO, logger="goatfarm.audit"):
        resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 401
    events = _events(caplog)
    assert any(
        "event='auth.refresh.family_revoked'" in message
        and "reuse outside the rotation grace" in message
        for message in events
    ), events


async def test_compacted_refresh_replay_is_durable_only_when_family_state_changes(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    """A compacted predecessor revokes its live successor exactly once.

    Once the family is already revoked, replaying the same signed predecessor
    is hostile failure traffic, not another state transition, and therefore
    must not grow the append-only security-event table.
    """
    await register(client, "evt-compacted-replay@farm.in")
    predecessor = client.cookies.get(COOKIE)
    assert predecessor
    claims = decode_refresh_claims(predecessor)
    assert claims is not None and claims.family_id is not None

    rotated = await client.post("/api/auth/refresh")
    assert rotated.status_code == 200, rotated.text
    async with get_sessionmaker()() as db:
        await db.execute(delete(RefreshSession).where(RefreshSession.jti == claims.jti))
        await db.commit()

    drain_transient_security_signals()
    set_refresh_cookie(client, predecessor)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="goatfarm.audit"):
        first = await client.post("/api/auth/refresh")
        second = await client.post("/api/auth/refresh")
        summary = emit_transient_security_signal_summary(300)

    assert first.status_code == 401
    assert second.status_code == 401
    assert summary == {"auth.refresh.invalid": 2}
    projected = [
        message for message in _events(caplog) if "event='auth.refresh.family_revoked'" in message
    ]
    assert len(projected) == 1, projected

    async with get_sessionmaker()() as db:
        durable = await db.scalar(
            select(func.count())
            .select_from(SecurityEvent)
            .where(SecurityEvent.event == "auth.refresh.family_revoked")
        )
        live_family_rows = await db.scalar(
            select(func.count())
            .select_from(RefreshSession)
            .where(
                RefreshSession.family_id == claims.family_id,
                RefreshSession.user_id == claims.user_id,
                RefreshSession.revoked_at.is_(None),
            )
        )
    assert durable == 1
    assert live_family_rows == 0


async def test_invalid_access_token_signal_is_aggregated_without_database_writes(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    drain_transient_security_signals()
    async with get_sessionmaker()() as db:
        rows_before = await db.scalar(
            select(func.count())
            .select_from(SecurityEvent)
            .where(SecurityEvent.event == "auth.token.invalid")
        )

    commits = 0
    statements = 0

    def count_commit(_session: Session) -> None:
        nonlocal commits
        commits += 1

    def count_statement(*_args: object, **_kwargs: object) -> None:
        nonlocal statements
        statements += 1

    sync_engine = get_engine().sync_engine
    event.listen(Session, "after_commit", count_commit)
    event.listen(sync_engine, "before_cursor_execute", count_statement)
    with caplog.at_level(logging.INFO, logger="goatfarm.audit"):
        try:
            responses = [
                await client.get(
                    "/api/auth/me",
                    headers={"Authorization": f"Bearer not.a.jwt{i}"},
                )
                for i in range(32)
            ]
        finally:
            event.remove(sync_engine, "before_cursor_execute", count_statement)
            event.remove(Session, "after_commit", count_commit)
        summary = emit_transient_security_signal_summary(300)

    assert {response.status_code for response in responses} == {401}
    assert statements == 0
    assert commits == 0
    assert summary == {"auth.token.invalid": 32}
    assert not any("event='auth.token.invalid'" in message for message in _events(caplog))
    signals = [
        record.getMessage()
        for record in caplog.records
        if record.name == "goatfarm.audit" and record.getMessage().startswith("security_signal_")
    ]
    assert (
        len([message for message in signals if message.startswith("security_signal_started")]) == 1
    )
    summaries = [message for message in signals if message.startswith("security_signal_summary")]
    assert len(summaries) == 1
    assert "event='auth.token.invalid'" in summaries[0]
    assert "count=32" in summaries[0]
    assert "not.a.jwt" not in " ".join(signals)

    async with get_sessionmaker()() as db:
        rows_after = await db.scalar(
            select(func.count())
            .select_from(SecurityEvent)
            .where(SecurityEvent.event == "auth.token.invalid")
        )
    assert rows_before == rows_after


async def test_invalid_access_token_saturation_has_no_per_request_application_logs(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A saturated attacker IP stays visible through fixed-key summaries.

    Rejections after the IP ceiling must not add an application log line (or
    copy an IP/token into one) for every request; an HTTP server may maintain
    its own access log, but the auth layer does not amplify it.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "auth_rate_limit_max_attempts", 1)
    auth_limiter.clear()
    drain_transient_security_signals()
    caplog.clear()
    try:
        with caplog.at_level(logging.INFO):
            responses = [
                await client.get(
                    "/api/auth/me",
                    headers={"Authorization": f"Bearer novel.invalid.token-{index}"},
                )
                for index in range(24)
            ]
            summary = emit_transient_security_signal_summary(300)
        token_buckets = [
            bucket for bucket in auth_limiter._hits if bucket[0] == "access-token-invalid-token"
        ]
    finally:
        auth_limiter.clear()

    assert {response.status_code for response in responses} == {401, 429}
    assert all(response.status_code == 429 for response in responses[-10:])
    assert summary == {"auth.token.invalid": 24}
    assert len(token_buckets) == 10
    auth_records = [
        record.getMessage()
        for record in caplog.records
        if record.name in {"goatfarm.auth", "goatfarm.deps"}
    ]
    assert auth_records == []
    assert "novel.invalid.token" not in " ".join(record.getMessage() for record in caplog.records)


async def test_invalid_logout_tokens_do_not_open_or_commit_database_transactions(
    client: httpx.AsyncClient,
) -> None:
    """Logout remains a local cookie clear when every credential is invalid."""
    drain_transient_security_signals()
    commits = 0
    statements = 0

    def count_commit(_session: Session) -> None:
        nonlocal commits
        commits += 1

    def count_statement(*_args: object, **_kwargs: object) -> None:
        nonlocal statements
        statements += 1

    sync_engine = get_engine().sync_engine
    event.listen(Session, "after_commit", count_commit)
    event.listen(sync_engine, "before_cursor_execute", count_statement)
    try:
        responses = [
            await client.post(
                "/api/auth/logout",
                headers={"Authorization": f"Bearer logout.invalid.token-{index}"},
            )
            for index in range(24)
        ]
    finally:
        event.remove(sync_engine, "before_cursor_execute", count_statement)
        event.remove(Session, "after_commit", count_commit)

    assert {response.status_code for response in responses} == {204}
    assert statements == 0
    assert commits == 0
    assert emit_transient_security_signal_summary(300) == {"auth.token.invalid": 24}


async def test_concurrent_logout_session_does_not_commit_after_losing_revocation_race(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent revoker can win after the handler's final live check.

    The losing handler remains an idempotent 204, but it must roll back instead
    of manufacturing a second no-op commit/WAL record.
    """
    headers = await register(client, "evt-repeat-session-logout@farm.in")
    claims = decode_access_claims(headers["Authorization"].removeprefix("Bearer "))
    assert claims is not None
    family_id = claims.family_id
    assert family_id is not None

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/logout-session",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("test", 80),
            "client": ("127.0.0.1", 1234),
        }
    )
    request.state.authenticated_token_version = claims.token_version
    request.state.authenticated_family_id = family_id
    request.state.authenticated_scope = claims.scope

    original_generation_check = auth_api._require_authenticated_generation

    async def revoke_immediately_after_live_check(
        db: AsyncSession,
        checked_request: Request,
        checked_user: User,
        token_version: int,
    ) -> None:
        await original_generation_check(db, checked_request, checked_user, token_version)
        async with get_sessionmaker()() as racer:
            changed = await revoke_session_family(racer, family_id, user_id=claims.user_id)
            assert changed > 0
            await racer.commit()

    monkeypatch.setattr(
        auth_api,
        "_require_authenticated_generation",
        revoke_immediately_after_live_check,
    )

    commits = 0

    def count_commit(_session: Session) -> None:
        nonlocal commits
        commits += 1

    event.listen(Session, "after_commit", count_commit)
    try:
        async with get_sessionmaker()() as db:
            user = (await db.execute(select(User).where(User.id == claims.user_id))).scalar_one()
            response = await auth_api.logout_session(request, db, user)
    finally:
        event.remove(Session, "after_commit", count_commit)

    assert response.status_code == 204
    # The only commit belongs to the simulated concurrent winner. The route's
    # losing transaction is explicitly rolled back.
    assert commits == 1


async def test_revoked_generation_token_is_aggregated_without_durable_growth(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    old_headers = await register(client, "evt-version@farm.in")
    changed = await client.post(
        "/api/auth/change-password",
        json={"current_password": OWNER_PW, "new_password": "New-Pass-45678!"},
        headers=old_headers,
    )
    assert changed.status_code == 200, changed.text
    drain_transient_security_signals()
    async with get_sessionmaker()() as db:
        rows_before = await db.scalar(
            select(func.count())
            .select_from(SecurityEvent)
            .where(SecurityEvent.event == "auth.token.version_mismatch")
        )
    with caplog.at_level(logging.INFO, logger="goatfarm.audit"):
        responses = [await client.get("/api/auth/me", headers=old_headers) for _ in range(24)]
        summary = emit_transient_security_signal_summary(300)
    assert {response.status_code for response in responses} == {401}
    assert summary == {"auth.token.version_mismatch": 24}
    assert not any("event='auth.token.version_mismatch'" in m for m in _events(caplog))
    signals = [
        record.getMessage()
        for record in caplog.records
        if record.name == "goatfarm.audit" and record.getMessage().startswith("security_signal_")
    ]
    assert len(signals) == 2, signals
    assert old_headers["Authorization"] not in " ".join(signals)
    async with get_sessionmaker()() as db:
        rows_after = await db.scalar(
            select(func.count())
            .select_from(SecurityEvent)
            .where(SecurityEvent.event == "auth.token.version_mismatch")
        )
    assert rows_before == rows_after


async def test_rbac_denial_is_aggregated_without_durable_growth(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    owner = await owner_with_farm(client, email="evt-owner@farm.in")
    team = await client.get("/api/team", headers=owner)
    assert team.status_code == 200
    viewer = next(r for r in team.json()["roles"] if r.get("code") == "VIEWER")
    created = await client.post(
        "/api/team/workers",
        json={
            "email": "evt-viewer@farm.in",
            "password": "Worker-Pass-123",
            "name": "Event Viewer",
            "role_id": viewer["id"],
        },
        headers=owner,
    )
    assert created.status_code == 201, created.text
    resp = await client.post(
        "/api/auth/login",
        json={"email": "evt-viewer@farm.in", "password": "Worker-Pass-123"},
        headers={"X-No-Auto-Rotate": "1"},
    )
    assert resp.status_code == 200
    rotated = await client.post(
        "/api/auth/change-password",
        json={"current_password": "Worker-Pass-123", "new_password": "Worker-Pass-456"},
        headers={"Authorization": f"Bearer {resp.json()['access_token']}"},
    )
    assert rotated.status_code == 200, rotated.text
    worker = {"Authorization": f"Bearer {rotated.json()['access_token']}"}
    worker |= {"X-Farm-Id": owner["X-Farm-Id"]}

    drain_transient_security_signals()
    async with get_sessionmaker()() as db:
        rows_before = await db.scalar(
            select(func.count())
            .select_from(SecurityEvent)
            .where(SecurityEvent.event == "rbac.denied")
        )
    with caplog.at_level(logging.INFO, logger="goatfarm.audit"):
        denied = await client.post(
            "/api/finance/new",
            json={"date": "2026-09-16", "type": "EXPENSE", "category": "FEED", "amount": 10.0},
            headers=worker,
        )
        summary = emit_transient_security_signal_summary(300)
    assert denied.status_code == 403
    assert summary == {"rbac.denied": 1}
    assert not any("event='rbac.denied'" in m for m in _events(caplog))
    async with get_sessionmaker()() as db:
        rows_after = await db.scalar(
            select(func.count())
            .select_from(SecurityEvent)
            .where(SecurityEvent.event == "rbac.denied")
        )
    assert rows_before == rows_after


async def test_dpr_download_emits_event(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    owner = await owner_with_farm(client, email="evt-dpr@farm.in")
    defaults = await client.get("/api/simulation/defaults", headers=owner)
    assert defaults.status_code == 200
    assumptions = defaults.json().get("assumptions", defaults.json())
    created = await client.post(
        "/api/planner/plans",
        json={
            "name": "Event Plan",
            "start_year_month": "2026-10",
            "targets": [{"year_month": "2027-03", "animal_class": "doe", "count": 40}],
            "assumptions": assumptions,
        },
        headers=owner,
    )
    assert created.status_code == 201, created.text
    plan_id = created.json()["id"]
    with caplog.at_level(logging.INFO, logger="goatfarm.audit"):
        resp = await client.get(f"/api/planner/plans/{plan_id}/dpr", headers=owner)
    assert resp.status_code == 200
    assert any(
        "event='planner.dpr.download'" in m and f"plan_id={plan_id}" in m for m in _events(caplog)
    ), _events(caplog)
