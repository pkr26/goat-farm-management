"""Kidding-date guards — formerly "reviewed suspected bugs", now enforced.

This module used to pin the ACCEPTANCE of kiddings recorded far outside
the gestation window (a 0-day or 100-day "gestation" returned 201),
because the pre-existing suite relied on same-day kiddings and real farm
record-keeping is backdated. Phase 3 of the hardening audit (B8.1) reversed
that decision: `services.record_kidding` now enforces a generous
100–200 day gestation sanity band (150-day gestation, window 145–155) — wide
enough for any plausible backdated entry, tight enough that a "kidding"
recorded 1 day or 3 years post-breeding is rejected as a data-entry error.
The tests that needed same-day kiddings were updated to realistic ~150-day
offsets, and the boundary behavior is pinned in
tests/test_adversarial.py::test_kidding_gestation_window.

SPEC's "Gestation: 150 days (kidding window 145–155)" is ALSO enforced where
it is actionable for planning: expected_kidding_date = breeding_date + 150
(app/models.py), and the kidding list's upcoming/overdue windows.
"""

from datetime import timedelta

import httpx

from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import (
    bred_doe,
    confirm,
    iso,
    kid_on_ekd_raw,
    pregnant_doe,
)


# Boundary acceptance: a kidding at exactly breeding + 100 days (the floor of
# the sanity band, e.g. an early preterm record entered after the fact) is
# still accepted — the band rejects only physiologically absurd dates.
async def test_kidding_at_gestation_floor_accepted(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    # bred 250 days ago, ultrasound-confirmed; kidding at breeding + 100 days
    _doe, _buck, br = await bred_doe(client, headers, breeding_date=today() - timedelta(days=250))
    br = await confirm(client, headers, br["id"])
    floor_date = today() - timedelta(days=150)  # breeding_date + 100
    resp = await kid_on_ekd_raw(client, headers, br, date=iso(floor_date))
    assert resp.status_code == 201
    assert resp.json()["date"] == iso(floor_date)


# A "kidding" on the breeding date itself (0-day gestation) is below the
# 100-day floor and now rejected (409 from the service guard) instead of
# silently corrupting gestation statistics.
async def test_kidding_on_breeding_date_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    breeding_date = today() - timedelta(days=160)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    resp = await kid_on_ekd_raw(client, headers, br, date=iso(breeding_date))
    assert resp.status_code == 409
    assert "gestation" in resp.json()["detail"]
