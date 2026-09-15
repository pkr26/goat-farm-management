"""Bakrid hold advisory on the dashboard (services/dashboard.bakrid_hold_advisory).

A male whose projected market finish (DOB + 9 months, the meat-sale window's
upper age) lands inside the two months before the next Bakrid is worth
holding for the festival premium; the dashboard surfaces the structured
``{"key": "bakrid_hold", "args": {"count", "festival_date"}}`` advisory.
"""

from datetime import date, timedelta

import httpx
import pytest

from app.services.dashboard import next_bakrid_date
from app.utils import today

from .conftest import owner_with_farm, provisioned_worker_login

FROZEN = date(2026, 3, 1)
NEXT_BAKRID = "2026-05-28"  # simulation/market.py calendar, verified year


@pytest.fixture()
def frozen_business_date(monkeypatch: pytest.MonkeyPatch) -> date:
    """Pin the farm-local business date the advisory computes against."""

    def frozen_today(timezone_name: str) -> date:
        assert timezone_name == "Asia/Kolkata"
        return FROZEN

    monkeypatch.setattr("app.services.dashboard.today", frozen_today)
    return FROZEN


def iso_days_ago(days: int) -> str:
    return (today() - timedelta(days=days)).isoformat()


async def make_animal(
    client: httpx.AsyncClient,
    headers: dict,
    tag: str,
    *,
    sex: str,
    date_of_birth: str,
) -> dict:
    resp = await client.post(
        "/api/animals",
        json={
            "tag_number": tag,
            "sex": sex,
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "date_of_birth": date_of_birth,
            "historical_import_reason": "Bakrid advisory test fixture",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def change_status(
    client: httpx.AsyncClient, headers: dict, animal_id: int, new_status: str, **extra: object
) -> None:
    resp = await client.post(
        f"/api/animals/{animal_id}/status",
        json={"new_status": new_status, "date": today().isoformat(), **extra},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


def test_next_bakrid_date_walks_the_calendar() -> None:
    assert next_bakrid_date(date(2026, 5, 27)) == date(2026, 5, 28)
    assert next_bakrid_date(date(2026, 5, 28)) == date(2027, 5, 17)  # same-day looks ahead
    assert next_bakrid_date(date(2050, 8, 28)) is None  # beyond the calendar: no next date


async def test_bakrid_hold_advisory_counts_finishing_males(
    client: httpx.AsyncClient, frozen_business_date: date
) -> None:
    headers = await owner_with_farm(client)
    # Window: finish (DOB + 9 mo) inside [2026-03-28, 2026-05-28].
    await make_animal(client, headers, "BK-A", sex="M", date_of_birth="2025-07-15")  # → 04-15 ✓
    await make_animal(client, headers, "BK-B", sex="M", date_of_birth="2024-01-01")  # already past
    await make_animal(client, headers, "BK-C", sex="M", date_of_birth="2025-08-01")  # → 05-01 ✓
    await make_animal(client, headers, "BK-D", sex="M", date_of_birth="2025-09-01")  # → 06-01 ✗
    await make_animal(client, headers, "BK-DOE", sex="F", date_of_birth="2025-07-15")  # not male
    sold = await make_animal(client, headers, "BK-SOLD", sex="M", date_of_birth="2025-07-15")
    await change_status(client, headers, sold["id"], "SOLD", sale_price=8000.0)

    dashboard = await client.get("/api/dashboard", headers=headers)
    assert dashboard.status_code == 200, dashboard.text
    advisory = dashboard.json()["advisory"]
    assert advisory is not None
    assert advisory["key"] == "bakrid_hold"
    assert advisory["args"] == {"count": 2, "festival_date": NEXT_BAKRID}


async def test_bakrid_hold_advisory_absent_without_qualifying_males(
    client: httpx.AsyncClient, frozen_business_date: date
) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "BK-E", sex="M", date_of_birth="2024-01-01")
    await make_animal(client, headers, "BK-DOE-2", sex="F", date_of_birth="2025-07-15")

    dashboard = await client.get("/api/dashboard", headers=headers)
    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json()["advisory"] is None


async def test_bakrid_hold_advisory_follows_the_animals_view_gate(
    client: httpx.AsyncClient, frozen_business_date: date
) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "BK-F", sex="M", date_of_birth="2025-07-15")

    # A dashboard-only worker (no animals.view) must not learn herd composition.
    role = await client.post(
        "/api/team/roles",
        json={"name": "Dashboard Only", "permissions": ["dashboard.view"]},
        headers=headers,
    )
    assert role.status_code == 201, role.text
    worker = await client.post(
        "/api/team/workers",
        json={
            "name": "Watcher",
            "email": "watcher@farm.in",
            "password": "workerpass123",
            "role_id": role.json()["id"],
        },
        headers=headers,
    )
    assert worker.status_code == 201, worker.text
    watcher, _ = await provisioned_worker_login(client, "watcher@farm.in", "workerpass123")
    watcher = watcher | {"X-Farm-Id": headers["X-Farm-Id"]}

    dashboard = await client.get("/api/dashboard", headers=watcher)
    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json()["advisory"] is None


EOF_MARKER_NOT_USED = None
