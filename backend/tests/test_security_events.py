"""2026-09-16 audit DET-1/DET-2: the platform's strongest security signals —
refresh-token family revocation, invalid/tampered access tokens, revoked-
generation token reuse, RBAC denials, and DPR document downloads — must emit
structured ``goatfarm.audit security_event`` records, not just 401/403/404
status lines a SIEM cannot alert on."""

import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import update

from app.core.config import get_settings
from app.db import get_sessionmaker
from app.models import RefreshSession
from app.security import decode_refresh_claims

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


async def test_invalid_access_token_emits_event(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="goatfarm.audit"):
        resp = await client.get("/api/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert resp.status_code == 401
    assert any("event='auth.token.invalid'" in m for m in _events(caplog)), _events(caplog)


async def test_revoked_generation_token_emits_event(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    old_headers = await register(client, "evt-version@farm.in")
    changed = await client.post(
        "/api/auth/change-password",
        json={"current_password": OWNER_PW, "new_password": "New-Pass-45678!"},
        headers=old_headers,
    )
    assert changed.status_code == 200, changed.text
    with caplog.at_level(logging.INFO, logger="goatfarm.audit"):
        resp = await client.get("/api/auth/me", headers=old_headers)
    assert resp.status_code == 401
    assert any("event='auth.token.version_mismatch'" in m for m in _events(caplog)), _events(caplog)


async def test_rbac_denial_emits_event(
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

    with caplog.at_level(logging.INFO, logger="goatfarm.audit"):
        denied = await client.post(
            "/api/finance/new",
            json={"date": "2026-09-16", "type": "EXPENSE", "category": "FEED", "amount": 10.0},
            headers=worker,
        )
    assert denied.status_code == 403
    assert any(
        "event='rbac.denied'" in m and "permission='finance.manage'" in m for m in _events(caplog)
    ), _events(caplog)


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
