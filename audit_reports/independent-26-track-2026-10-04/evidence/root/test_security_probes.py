"""Independent audit probes: assertions preserve observed defective behavior.

Run from backend with -p tests.conftest and an owned GOATFARM_TEST_DB.
These are reproduction receipts, not acceptance tests for the desired behavior.
"""
import httpx
import pytest
from sqlalchemy import func, select

from app.db import get_sessionmaker
from app.main import create_app
from app.models import SecurityEvent
from tests.conftest import register


async def test_oversized_numeric_farm_header_returns_500(client):
    headers = await register(client, "audit-header@farm.test")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(), raise_app_exceptions=False),
        base_url="http://test",
    ) as probe:
        response = await probe.get(
            "/api/animals", headers={**headers, "X-Farm-Id": "9" * 5000}
        )
    print("numeric_farm_header_digits=5000", "status=", response.status_code)
    assert response.status_code == 500


@pytest.mark.parametrize("path", ["/api/auth/logout-session", "/api/auth/logout"])
async def test_logout_does_not_record_durable_event(client, path):
    headers = await register(client, "audit-logout@farm.test")
    async with get_sessionmaker()() as db:
        before = await db.scalar(select(func.count()).select_from(SecurityEvent))
    response = await client.post(path, headers=headers)
    assert response.status_code == 204
    after_access = await client.get("/api/auth/me", headers=headers)
    assert after_access.status_code == 401
    async with get_sessionmaker()() as db:
        after = await db.scalar(select(func.count()).select_from(SecurityEvent))
    print("path=", path, "logout_status=204", "revoked_access_status=401", "security_event_delta=", after-before)
    assert after == before
