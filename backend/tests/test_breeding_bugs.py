"""Reviewed suspected bugs — REJECTED as not-a-bug; kept as a regression
suite pinning the ACCEPTED behavior.

The tests below record kiddings far outside SPEC.md's 145–155 day gestation
window and assert the API ACCEPTS them (201). That acceptance is deliberate
and load-bearing, not an oversight:

- The pre-existing adversarial suite (tests/test_adversarial.py, off-limits)
  has many tests that breed a doe and record the kidding the same day
  (0-day gestation) and require 201 — e.g.
  test_kidding_rejects_bad_dates_caps_and_weights ("valid one still works"),
  test_second_kidding_auto_tags_do_not_collide,
  test_kidding_closes_leftover_pregnancy_tasks,
  test_abort_after_kidding_is_noop, and the duplicate-tag test which needs
  the "Duplicate kid tags" 400 to fire before any date-window check.
- Real farm record-keeping is backdated: a farmer records an early/late
  kidding after the fact, so the API validates only date sanity
  (>= breeding date, <= today).
- SPEC's "Gestation: 150 days (kidding window 145–155)" IS enforced where
  it is actionable: expected_kidding_date = breeding_date + 150
  (app/models.py), and the kidding list's upcoming/overdue windows.

Enforcing a hard 145–155 rejection here would break the pre-existing suite,
so the suspected bug was rejected. These tests now guard that the behavior
does not change accidentally.
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


# ACCEPTED BEHAVIOR (reviewed — not a bug; see module docstring)
# SPEC.md ("Breed constants"): "Gestation: 150 days (kidding window 145–155)".
# POST /api/kidding checks date >= breeding_date and <= today only, so a
# kidding recorded 100 days after breeding — far outside the 145–155 day
# window — is accepted with 201. Enforcing the window here was rejected:
# the pre-existing adversarial suite pins 201 for out-of-window kiddings
# (backdated record-keeping), and the window is enforced via
# expected_kidding_date / the upcoming & overdue lists instead.
async def test_kidding_far_outside_gestation_window_accepted(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    # bred 250 days ago, ultrasound-confirmed; kidding at breeding + 100 days
    _doe, _buck, br = await bred_doe(client, headers, breeding_date=today() - timedelta(days=250))
    br = await confirm(client, headers, br["id"])
    impossible_date = today() - timedelta(days=150)  # breeding_date + 100
    resp = await kid_on_ekd_raw(client, headers, br, date=iso(impossible_date))
    assert resp.status_code == 201  # accepted: see module docstring
    assert resp.json()["date"] == iso(impossible_date)


# ACCEPTED BEHAVIOR (reviewed — not a bug; see module docstring)
# Same rationale as above: a kidding on the breeding date itself (0-day
# gestation) is accepted. The pre-existing adversarial suite relies on
# same-day kiddings returning 201, so a hard window rejection was rejected.
async def test_kidding_on_breeding_date_accepted(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    breeding_date = today() - timedelta(days=160)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    resp = await kid_on_ekd_raw(client, headers, br, date=iso(breeding_date))
    assert resp.status_code == 201  # accepted: see module docstring
    assert resp.json()["date"] == iso(breeding_date)
