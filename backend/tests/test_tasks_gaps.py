"""Task-cadence gap tests (2026-09-23 verification plan, category 8).

- the overdue/today split follows the FARM's local calendar, not the
  server's: a farm whose local date already differs from the server's
  right now classifies its duties by its own today;
- the monthly interval rounds survive the Jan-31 → Feb-1 edge: sweeping on
  both sides of the boundary produces exactly one WEIGHING duty, not two,
  and no gap.
"""

from datetime import date, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Farm, Task
from app.services.cadence import _ensure_interval_rounds
from app.utils import today

from .conftest import owner_with_farm, register


def _local_today(tz_name: str) -> date:
    from datetime import datetime

    return datetime.now(ZoneInfo(tz_name)).date()


async def test_overdue_classification_uses_farm_local_today(client: httpx.AsyncClient) -> None:
    """Find a timezone whose local date differs from the server's right now;
    that farm's board must classify by its own date."""
    server_today = today()  # deployment-default timezone
    candidates = [
        "Pacific/Kiritimati",  # UTC+14 — first to cross midnight
        "Pacific/Pago_Pago",  # UTC-11 — last to cross midnight
        "Asia/Kolkata",
        "America/New_York",
    ]
    tz_name = next(
        (name for name in candidates if _local_today(name) != server_today), None
    )
    if tz_name is None:
        # Every candidate happens to share the server's date right now —
        # the boundary is untestable at this instant; assert the invariant
        # trivially holds for a same-date farm instead.
        tz_name = "Asia/Kolkata"
    farm_today = _local_today(tz_name)

    headers = await register(client, email="midnight@farm.in")
    created = await client.post(
        "/api/auth/farms",
        json={"name": "Edge Clock Farm", "timezone": tz_name},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    owner = headers | {"X-Farm-Id": str(created.json()["id"])}

    for label, due in (
        ("EDGE-TODAY", farm_today),
        ("EDGE-YESTERDAY", farm_today - timedelta(days=1)),
        ("EDGE-TOMORROW", farm_today + timedelta(days=1)),
    ):
        resp = await client.post(
            "/api/tasks",
            json={"title": f"Duty {label}", "category": "OTHER", "due_date": due.isoformat()},
            headers=owner,
        )
        assert resp.status_code == 201, resp.text

    board = (await client.get("/api/tasks", headers=owner)).json()
    by_title = {
        tab: [t["title"] for t in board[tab]]
        for tab in ("today", "overdue", "upcoming")
    }
    assert any("EDGE-TODAY" in title for title in by_title["today"]), by_title
    assert any("EDGE-YESTERDAY" in title for title in by_title["overdue"]), by_title
    assert any("EDGE-TOMORROW" in title for title in by_title["upcoming"]), by_title
    # None of them may leak into the wrong board even when the farm's date
    # differs from the server's.
    assert not any("EDGE-YESTERDAY" in t for t in by_title["today"] + by_title["upcoming"])
    assert not any("EDGE-TODAY" in t for t in by_title["overdue"] + by_title["upcoming"])


async def test_interval_rounds_jan31_to_feb1_no_duplicate_no_gap(client: httpx.AsyncClient) -> None:
    """Sweep the monthly (30-day lookback) WEIGHING round on Jan 31 and
    again on Feb 1: exactly one duty must exist across the window."""
    headers = await register(client, email="cadence@farm.in")
    created = await client.post(
        "/api/auth/farms", json={"name": "Cadence Farm"}, headers=headers
    )
    assert created.status_code == 201, created.text
    farm_id = int(created.json()["id"])
    owner = headers | {"X-Farm-Id": str(farm_id)}
    animal = await client.post(
        "/api/animals",
        json={
            "tag_number": "CAD-1",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "date_of_birth": "2024-01-01",
            "historical_import_reason": "cadence fixture",
        },
        headers=owner,
    )
    assert animal.status_code == 201, animal.text

    async with get_sessionmaker()() as db:
        farm = (await db.execute(select(Farm).where(Farm.id == farm_id))).scalar_one()
        # Farm was introduced "today"; the interval rounds' backfill floor is
        # the earliest introduction, so anchor it a month back.
        from app.models import Animal

        row = (await db.execute(select(Animal).where(Animal.farm_id == farm_id))).scalar_one()
        row.purchase_date = today() - timedelta(days=60)
        await db.commit()

        await _ensure_interval_rounds(db, farm_id, date(2027, 1, 31))
        await db.commit()
        await _ensure_interval_rounds(db, farm_id, date(2027, 2, 1))
        await db.commit()

        rounds = (
            await db.execute(
                select(Task).where(Task.farm_id == farm_id, Task.category == "WEIGHING")
            )
        ).scalars().all()

    assert len(rounds) == 1, (
        f"Jan-31→Feb-1 sweep produced {len(rounds)} weighing rounds (dup/gap): "
        f"{[(r.title, str(r.due_date)) for r in rounds]}"
    )
