"""Simulation / screening / notification gap tests (2026-09-23 plan, cats 11-13).

- MIRR known-answer (the only one of NPV/IRR/MIRR without a pinned series);
- seeded Monte Carlo determinism: same assumptions + same seed →
  byte-identical aggregate output; a different seed must differ;
- the run-budget rejection is answered quickly (wall-clock bounded), never
  by hanging;
- screening cost canary: a gate-healthy photo costs EXACTLY one provider
  call — zero specialists, zero cross-check;
- quiet-hours minute boundaries (20:59 sends, 21:01 holds; 05:59 holds,
  06:01 sends);
- kidding-watch duties open exactly 5 days before the expected kidding.
"""

import time
from datetime import date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import ScreeningRun, Task
from app.simulation import SimulationAssumptions, mirr
from app.simulation.montecarlo import run_monte_carlo
from app.utils import today

from .conftest import owner_with_farm
from .test_e2e_lifecycle_audit import breed, make_animal
from .test_screening import (
    CountingProvider,
    FakeStorage,
    _cycle_settings,
    _jpeg_bytes,
    _register_fake_objects,
)

# ---------------------------------------------------------------------------
# Category 11 — simulation
# ---------------------------------------------------------------------------


def test_mirr_known_answers() -> None:
    # Symmetric one-period case: -1000 now, +1210 in a year, 10% both rates
    # → 21.0% exactly (benefits compound over zero remaining periods).
    assert mirr([-1000.0, 1210.0], [0.0, 1.0], 0.10, 0.10) == pytest.approx(0.21, abs=1e-12)

    # Asymmetric rates, hand-computed:
    # costs@t0 = 100; benefits to horizon t=2 at 8%: 50*1.08 + 80 = 134
    # MIRR = sqrt(134/100) - 1 ≈ 15.7642%
    value = mirr([-100.0, 50.0, 80.0], [0.0, 1.0, 2.0], 0.12, 0.08)
    expected = (134.0 / 100.0) ** 0.5 - 1.0
    assert value == pytest.approx(expected, abs=1e-12)
    assert value == pytest.approx(0.15758369, abs=1e-7)  # sqrt(1.34) - 1

    # Degenerate series have no answer.
    assert mirr([1.0, 2.0], [0.0, 1.0], 0.1, 0.1) is None
    assert mirr([-1.0, -2.0], [0.0, 1.0], 0.1, 0.1) is None


def test_monte_carlo_seed_determinism_byte_identical() -> None:
    a = SimulationAssumptions()
    first = run_monte_carlo(a)
    second = run_monte_carlo(a)
    assert first.model_dump() == second.model_dump(), (
        "same seed produced different aggregates — determinism leak"
    )

    from dataclasses import replace as dc_replace

    from app.simulation import RiskAssumptions  # noqa: F401  (availability check)

    drifted = a.model_copy(deep=True)
    drifted.risk.seed = a.risk.seed + 1
    third = run_monte_carlo(drifted)
    assert third.model_dump() != first.model_dump(), (
        "different seed produced identical output — seed ignored"
    )
    _ = dc_replace


async def test_simulation_run_rejection_is_wall_clock_bounded(client: httpx.AsyncClient) -> None:
    """An over-budget run request must be refused by admission control in
    bounded time — never answered by grinding the CPU."""
    owner = await owner_with_farm(client, email="budget@farm.in")
    start = time.perf_counter()
    resp = await client.post(
        "/api/simulation/run",
        json={"horizon_months": 240, "monte_carlo_runs": 20000},
        headers=owner,
    )
    elapsed = time.perf_counter() - start
    assert resp.status_code in (400, 409, 422), (
        f"maximal run accepted or blew up: {resp.status_code} {resp.text[:150]}"
    )
    assert elapsed < 10.0, f"admission control took {elapsed:.1f}s — it computed instead of refusing"


# ---------------------------------------------------------------------------
# Category 12 — screening cost canary
# ---------------------------------------------------------------------------


async def test_gate_healthy_photo_costs_exactly_one_provider_call(client: httpx.AsyncClient) -> None:
    from app.services.screening.pipeline import run_screening_cycle
    from app.services.screening.rotation import ProviderRotation

    headers = await owner_with_farm(client, email="canary@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    capture_day = today().isoformat()

    storage = FakeStorage()
    # A portrait photo → the CountingProvider's gate answers HEALTHY.
    storage.objects[f"raw/{farm_id}/{capture_day}/healthy.jpg"] = _jpeg_bytes(1000, 2000)

    providers = [CountingProvider(name="alpha"), CountingProvider(name="beta")]
    rotation = ProviderRotation(providers)
    async with get_sessionmaker()() as db:
        await _register_fake_objects(db, farm_id, storage)
        summary = await run_screening_cycle(db, _cycle_settings(), storage, rotation)

    assert (summary.healthy, summary.flagged) == (1, 0)
    # The cost canary: exactly ONE provider invocation total (the gate).
    assert sum(p.calls for p in providers) == 1, (
        f"healthy photo burned {sum(p.calls for p in providers)} model calls "
        f"(per-provider: {[p.calls for p in providers]}) — the cascade ran past the gate"
    )
    async with get_sessionmaker()() as db:
        runs = list((await db.execute(select(ScreeningRun))).scalars())
    assert [run.stage for run in runs] == ["GATE"], (
        f"non-gate stages ran for a healthy photo: {[r.stage for r in runs]}"
    )


# ---------------------------------------------------------------------------
# Category 13 — notification boundaries
# ---------------------------------------------------------------------------


def test_quiet_hours_minute_boundaries() -> None:
    """21:00–06:00 farm-local quiet window: 20:59 sends, 21:01 holds,
    05:59 holds, 06:01 sends (the app's window is hour-granular, so the
    minute edges pin that no off-by-one swallows or releases an alert)."""
    from app.core.config import get_settings
    from app.services.notifications.service import _in_quiet_hours

    settings = get_settings()
    assert (settings.notifications_quiet_start_hour, settings.notifications_quiet_end_hour) == (
        21,
        6,
    )
    send_ok = datetime(2026, 9, 23, 20, 59)
    hold_late = datetime(2026, 9, 23, 21, 1)
    hold_early = datetime(2026, 9, 23, 5, 59)
    release = datetime(2026, 9, 23, 6, 1)
    assert _in_quiet_hours(settings, send_ok) is False
    assert _in_quiet_hours(settings, hold_late) is True
    assert _in_quiet_hours(settings, hold_early) is True
    assert _in_quiet_hours(settings, release) is False


async def test_kidding_watch_opens_exactly_five_days_before_due(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="watch@farm.in")
    doe = await make_animal(client, owner, "WATCH-F")
    buck = await make_animal(client, owner, "WATCH-M", sex="M", weight_kg=32.0)
    br = await breed(client, owner, doe["id"], buck["id"], today() - timedelta(days=140))
    detail = (await client.get(f"/api/breeding/{br['id']}", headers=owner)).json()
    scan = await client.post(
        f"/api/breeding/{br['id']}/ultrasound",
        json={"pregnant": True, "kid_count": 1, "date": detail["ultrasound_date"]},
        headers=owner,
    )
    assert scan.status_code == 200, scan.text
    detail = (await client.get(f"/api/breeding/{br['id']}", headers=owner)).json()
    raw_expected = detail["expected_kidding_date"]
    expected = date.fromisoformat(raw_expected) if isinstance(raw_expected, str) else raw_expected
    assert isinstance(expected, date)

    async with get_sessionmaker()() as db:
        duties = (
            await db.execute(
                select(Task).where(
                    Task.farm_id == int(owner["X-Farm-Id"]),
                    Task.category == "KIDDING_WATCH",
                )
            )
        ).scalars().all()
    assert duties, "no kidding-watch duties generated"
    earliest = min(d.due_date for d in duties)
    assert earliest == expected - timedelta(days=5), (
        f"watch window opened {earliest} (= E{((earliest - expected).days)}d), "
        f"expected exactly E-5 (expected {expected})"
    )
    assert all(expected - timedelta(days=5) <= d.due_date <= expected for d in duties)
    _ = Decimal  # noqa: F841
