"""Behavioral coverage for the audit-remediated business rules.

The 2026-09-01 audits found SPEC rules that existed only as prose or
constants: the buck:doe ratio, the meat-sale window, the dairy voluntary
waiting period, the species-aware cull threshold, the dairy fresh-pen exit,
milk-recording context and the quarantine sale fence. Each test here drives
the rule through the real API the way an operator (or attacker) would.
"""

from datetime import date, timedelta

import httpx

from .conftest import owner_with_farm


async def _make_doe(client, headers, tag, dob_days=400, bucket="FOUNDATION", weight=25.0):
    payload = {
        "tag_number": tag,
        "sex": "F",
        "source": "BORN",
        "current_bucket": bucket,
        "date_of_birth": (date.today() - timedelta(days=dob_days)).isoformat(),
        "historical_import_reason": "Remediation test fixture",
    }
    if weight is not None:
        payload["weight_kg"] = weight
        payload["weight_date"] = (date.today() - timedelta(days=10)).isoformat()
    resp = await client.post("/api/animals", json=payload, headers=headers)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _make_buck(client, headers, tag):
    resp = await client.post(
        "/api/animals",
        json={
            "tag_number": tag,
            "sex": "M",
            "source": "BORN",
            "current_bucket": "BREEDING",
            "date_of_birth": (date.today() - timedelta(days=500)).isoformat(),
            "historical_import_reason": "Remediation test fixture",
            "weight_kg": 30.0,
            "weight_date": (date.today() - timedelta(days=10)).isoformat(),
        },
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _dairy_dam_ready(client, headers: dict, tag: str):
    """A purchased adult milking buffalo with a weight record old enough for
    services dated ~a year back (the shared fixture's weight_date postdates
    those breedings, which fails the as-of weight gate)."""
    resp = await client.post(
        "/api/animals",
        json={
            "tag_number": tag,
            "sex": "F",
            "breed": "Murrah",
            "source": "PURCHASED",
            "current_bucket": "BREEDING",
            "date_of_birth": (date.today() - timedelta(days=1500)).isoformat(),
            "purchase_date": (date.today() - timedelta(days=430)).isoformat(),
            "purchase_price": 110000,
            "historical_import_reason": "Remediation test fixture",
            "weight_kg": 520,
            "weight_date": (date.today() - timedelta(days=420)).isoformat(),
        },
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Buck:doe mating policy (M-1 goat adversarial: 1 buck serviced 21 does)
# ---------------------------------------------------------------------------
async def test_buck_open_service_cap_rejects_the_twenty_first_doe(client: httpx.AsyncClient):
    """A sire is refused his 21st open service under the 1:20 policy."""
    headers = await owner_with_farm(client)
    buck = await _make_buck(client, headers, "B-CAP")
    for i in range(20):
        doe = await _make_doe(client, headers, f"D-CAP-{i:02d}")
        resp = await client.post(
            "/api/breeding",
            json={
                "doe_id": doe["id"],
                "buck_id": buck["id"],
                "breeding_date": (date.today() - timedelta(days=5)).isoformat(),
                "method": "NATURAL",
            },
            headers=headers,
        )
        assert resp.status_code == 201, f"service {i}: {resp.text}"
    extra = await _make_doe(client, headers, "D-CAP-XX")
    resp = await client.post(
        "/api/breeding",
        json={
            "doe_id": extra["id"],
            "buck_id": buck["id"],
            "breeding_date": (date.today() - timedelta(days=5)).isoformat(),
            "method": "NATURAL",
        },
        headers=headers,
    )
    assert resp.status_code == 409
    assert "mating policy" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Meat-sale window (M-2 goat adversarial: 3-month/12 kg kid sold for ₹5,000)
# ---------------------------------------------------------------------------
async def test_meat_sale_window_rejects_a_young_male_kid(client: httpx.AsyncClient):
    headers = await owner_with_farm(client)
    resp = await client.post(
        "/api/animals",
        json={
            "tag_number": "K-YOUNG",
            "sex": "M",
            "source": "BORN",
            "current_bucket": "MALE_KIDS",
            "date_of_birth": (date.today() - timedelta(days=90)).isoformat(),
            "historical_import_reason": "Remediation test fixture",
        },
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text
    kid = resp.json()
    sold = await client.post(
        f"/api/animals/{kid['id']}/status",
        json={"new_status": "SOLD", "sale_price": 5000},
        headers=headers,
    )
    assert sold.status_code == 422
    assert "meat-sale window" in sold.json()["detail"]
    # A cull remains the legal early exit (injury/illness), priced or not.
    culled = await client.post(
        f"/api/animals/{kid['id']}/status",
        json={"new_status": "CULLED"},
        headers=headers,
    )
    assert culled.status_code in (200, 409), culled.text


async def test_quarantine_sale_fence(client: httpx.AsyncClient):
    """Biosecurity: no SELL out of the 45-day quarantine; cull stays open."""
    headers = await owner_with_farm(client)
    resp = await client.post(
        "/api/animals",
        json={
            "tag_number": "Q-SELL",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "QUARANTINE",
        },
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text
    animal = resp.json()
    sold = await client.post(
        f"/api/animals/{animal['id']}/status",
        json={"new_status": "SOLD", "sale_price": 9000},
        headers=headers,
    )
    assert sold.status_code == 409
    assert "quarantine" in sold.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Voluntary waiting period (HIGH-4 dairy: AI recordable the day after calving)
# ---------------------------------------------------------------------------
async def test_dairy_vwp_rejects_an_early_postpartum_service(client: httpx.AsyncClient):
    from .test_dairy import _dairy_owner

    headers = await _dairy_owner(client, email="vwp-audit@farm.in")
    animal = await _dairy_dam_ready(client, headers, "D-VWP-AUDIT")
    # Breed far enough back that calving+61d is still in the past.
    breeding_date = date.today() - timedelta(days=380)
    resp = await client.post(
        "/api/breeding",
        json={
            "doe_id": animal["id"],
            "breeding_date": breeding_date.isoformat(),
            "method": "AI",
            "semen_sire_name": "Karanvir 999",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    confirmed = (
        await client.post(
            f"/api/breeding/{resp.json()['id']}/ultrasound",
            json={
                "pregnant": True,
                "date": (breeding_date + timedelta(days=65)).isoformat(),
            },
            headers=headers,
        )
    ).json()
    calved = await client.post(
        "/api/kidding",
        json={
            "breeding_record_id": confirmed["id"],
            "date": confirmed["expected_kidding_date"],
            "ease": "NORMAL",
            "kids": [{"sex": "F", "status": "ALIVE"}],
        },
        headers=headers,
    )
    assert calved.status_code in (200, 201), calved.text
    # Day 11 post-calving, dam history-overridden back to a breeding-ready
    # bucket: the VWP must still refuse the service.
    dam = calved.json()
    moved = await client.post(
        f"/api/animals/{dam['doe_id']}/move",
        json={
            "to_bucket": "RESTING",
            "history_override": True,
            "reason": "Test fixture: early rest",
        },
        headers=headers,
    )
    assert moved.status_code == 200, moved.text
    early = await client.post(
        "/api/breeding",
        json={
            "doe_id": dam["doe_id"],
            "breeding_date": (
                date.fromisoformat(confirmed["expected_kidding_date"]) + timedelta(days=11)
            ).isoformat(),
            "method": "AI",
            "semen_sire_name": "Karanvir 999",
        },
        headers=headers,
    )
    assert early.status_code == 409
    assert "voluntary waiting period" in early.json()["detail"]
    # Day 61 post-calving the same service is accepted.
    legal = await client.post(
        "/api/breeding",
        json={
            "doe_id": dam["doe_id"],
            "breeding_date": (
                date.fromisoformat(confirmed["expected_kidding_date"]) + timedelta(days=61)
            ).isoformat(),
            "method": "AI",
            "semen_sire_name": "Karanvir 999",
        },
        headers=headers,
    )
    assert legal.status_code == 201, legal.text


# ---------------------------------------------------------------------------
# Species-aware cull rule (HIGH-3 dairy: flag fires after 2, promised 3)
# ---------------------------------------------------------------------------
async def test_dairy_cull_flag_fires_on_the_third_failed_service(client: httpx.AsyncClient):
    from .test_dairy import _dairy_owner

    headers = await _dairy_owner(client, email="cull-audit@farm.in")
    animal = await _dairy_dam_ready(client, headers, "D-CULL-AUDIT")
    # Each failed cycle: service, PD+60 negative, next service after that PD.
    for attempt in range(3):
        breeding_day = date.today() - timedelta(days=300 - attempt * 61)
        resp = await client.post(
            "/api/breeding",
            json={
                "doe_id": animal["id"],
                "breeding_date": breeding_day.isoformat(),
                "method": "AI",
                "semen_sire_name": f"Sire {attempt}",
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        br = resp.json()
        result = await client.post(
            f"/api/breeding/{br['id']}/ultrasound",
            json={
                "pregnant": False,
                "date": (breeding_day + timedelta(days=60)).isoformat(),
            },
            headers=headers,
        )
        assert result.status_code == 200, result.text
        body = (await client.get(f"/api/animals/{animal['id']}", headers=headers)).json()
        flagged = body.get("animal", body)["cull_candidate"]
        if attempt < 2:
            assert flagged is False, f"flag fired early on service {attempt + 1}"
        else:
            assert flagged is True, "flag did not fire on the third failed service"


# ---------------------------------------------------------------------------
# Fresh-pen exit with a live calf (BLOCKER B1: dairy dam stranded to day 90)
# ---------------------------------------------------------------------------
async def test_dairy_fresh_pen_exit_with_live_calf(client: httpx.AsyncClient):
    from .test_dairy import _breed_and_confirm, _dairy_owner

    headers = await _dairy_owner(client, email="freshpen-audit@farm.in")
    animal = await _dairy_dam_ready(client, headers, "D-MILK-AUDIT")
    confirmed = await _breed_and_confirm(client, headers, animal["id"])
    calved = await client.post(
        "/api/kidding",
        json={
            "breeding_record_id": confirmed["id"],
            "date": confirmed["expected_kidding_date"],
            "ease": "NORMAL",
            "kids": [{"sex": "F", "status": "ALIVE"}],
        },
        headers=headers,
    )
    assert calved.status_code in (200, 201), calved.text
    duties = (await client.get("/api/tasks?limit=200", headers=headers)).json()
    # Include every tab: the +10-day fresh-pen duty lands on today's date when
    # the test runs in a timezone whose calendar day matches the farm's
    # (Asia/Kolkata) — a due-today duty is filed under "today", not "overdue".
    duty_rows = [
        *(duties.get("today") or []),
        *(duties.get("overdue") or []),
        *(duties.get("upcoming") or []),
    ]
    assert duty_rows, "no duties returned"
    fresh = next(
        task
        for task in duty_rows
        if task["category"] == "BUCKET_MOVE"
        and task.get("breeding_record_id") == confirmed["id"]
        and "RESTING after the fresh period" in task["title"]
    )
    # Calving was 10 days ago (breeding −320 d + 310 d), so the +10-day
    # fresh-pen duty is due today — filed under the "today" tab, which the
    # duty_rows concatenation above includes.
    # The audit's blocker: completing it used to 409 "invalid while a kid
    # survives" because the survivorship check lacked the species gate, and
    # skip refused as "the only way out" — stranding the dam to day 90.
    # Completing it now must move the dam to RESTING with her calf alive.
    completed = await client.post(f"/api/tasks/{fresh['id']}/complete", headers=headers)
    assert completed.status_code == 200, completed.text
    dam = (await client.get(f"/api/animals/{calved.json()['doe_id']}", headers=headers)).json()
    assert dam.get("animal", dam)["current_bucket"] == "RESTING"


# ---------------------------------------------------------------------------
# Day-90 milk weaning graduates heifers (D-7: the duty was a silent no-op)
# ---------------------------------------------------------------------------
async def test_dairy_weaning_duty_graduates_heifers_to_foundation(client: httpx.AsyncClient):
    """The V1 verification found the route's lock predicate only loaded
    RECOVERY offspring, so the day-90 duty completed without moving the
    calf-shed heifers it promises to graduate. The lock filter is now
    species-gated; assert the actual bucket move end-to-end."""
    from .test_dairy import _dairy_owner

    headers = await _dairy_owner(client, email="wean-audit@farm.in")
    animal = await _dairy_dam_ready(client, headers, "D-WEAN-AUDIT")
    breeding_day = date.today() - timedelta(days=410)
    resp = await client.post(
        "/api/breeding",
        json={
            "doe_id": animal["id"],
            "breeding_date": breeding_day.isoformat(),
            "method": "AI",
            "semen_sire_name": "Karanvir 999",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    confirmed = (
        await client.post(
            f"/api/breeding/{resp.json()['id']}/ultrasound",
            json={"pregnant": True, "date": (breeding_day + timedelta(days=65)).isoformat()},
            headers=headers,
        )
    ).json()
    calved = await client.post(
        "/api/kidding",
        json={
            "breeding_record_id": confirmed["id"],
            "date": confirmed["expected_kidding_date"],
            "ease": "NORMAL",
            "kids": [{"sex": "F", "status": "ALIVE"}],
        },
        headers=headers,
    )
    assert calved.status_code in (200, 201), calved.text
    heifer_id = calved.json()["kids"][0]["animal_id"]

    duties = (await client.get("/api/tasks?limit=200", headers=headers)).json()
    # All three tabs: a duty due exactly today is filed under "today".
    duty_rows = [
        *(duties.get("today") or []),
        *(duties.get("overdue") or []),
        *(duties.get("upcoming") or []),
    ]
    weaning = next(
        task
        for task in duty_rows
        if task["category"] == "WEANING"
        and task.get("breeding_record_id") == confirmed["id"]
        and "Wean calves" in task["title"]
    )
    # Calving was ~100 days ago, so the day-90 duty is already actionable
    # (and the due-date provenance guard stays intact — no backdating).
    completed = await client.post(f"/api/tasks/{weaning['id']}/complete", headers=headers)
    assert completed.status_code == 200, completed.text
    heifer = (await client.get(f"/api/animals/{heifer_id}", headers=headers)).json()
    assert heifer.get("animal", heifer)["current_bucket"] == "FOUNDATION"


# ---------------------------------------------------------------------------
# Milk-recording context (HIGH-1 dairy: yields for dry/calf/pregnant animals)
# ---------------------------------------------------------------------------
async def test_milk_context_rejects_dry_calf_and_backdated_readings(client: httpx.AsyncClient):
    from .test_dairy import _dairy_owner

    headers = await _dairy_owner(client, email="milk-audit@farm.in")
    animal = await _dairy_dam_ready(client, headers, "D-FRESH-AUDIT")
    today = date.today()

    # 1. The dam is a purchased adult, so the imported-in-milk allowance
    #    covers her context; the fences below exercise chronology and the
    #    day cap on her.

    # 2. Chronology: a reading dated before a BORN calf's birth is fabricated
    #    history (the dam above is a historical import whose purchase fence
    #    covers only her arrival; the calf's DOB is exact).
    resp = await client.post(
        "/api/animals",
        json={
            "tag_number": "MILK-CALF",
            "sex": "F",
            "source": "BORN",
            "current_bucket": "FEMALE_KIDS",
            "date_of_birth": (date.today() - timedelta(days=105)).isoformat(),
            "historical_import_reason": "Remediation test fixture",
        },
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text
    calf = resp.json()
    resp = await client.post(
        "/api/milk/new",
        json={
            "animal_id": calf["id"],
            "date": (date.today() - timedelta(days=400)).isoformat(),
            "shift": "MORNING",
            "litres": 3.0,
        },
        headers=headers,
    )
    assert resp.status_code == 422
    # The calf-shed cohort fence fires (the lactation-context audit fence).
    assert "milking string" in resp.json()["detail"]

    # 3. Daily sanity band: three shifts summing past the 40 L/day cap.
    for shift, litres in (("MORNING", 18.0), ("AFTERNOON", 15.0)):
        resp = await client.post(
            "/api/milk/new",
            json={
                "animal_id": animal["id"],
                "date": today.isoformat(),
                "shift": shift,
                "litres": litres,
            },
            headers=headers,
        )
        assert resp.status_code in (201, 422), resp.text
    resp = await client.post(
        "/api/milk/new",
        json={
            "animal_id": animal["id"],
            "date": today.isoformat(),
            "shift": "NIGHT",
            "litres": 20.0,
            "correction_reason": "typo",
        },
        headers=headers,
    )
    assert resp.status_code == 422
    assert "sanity band" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# System ledger categories (L-2 goat adversarial: fabricated ANIMAL_SALE)
# ---------------------------------------------------------------------------
async def test_manual_rows_cannot_impersonate_system_categories(client: httpx.AsyncClient):
    from .conftest import create_farm, register

    headers = await register(client, "ledger-audit@farm.in")
    headers = await create_farm(client, headers, "Ledger Audit Farm")
    for category in ("ANIMAL_SALE", "ANIMAL_PURCHASE"):
        resp = await client.post(
            "/api/finance/new",
            json={
                "date": date.today().isoformat(),
                "type": "INCOME",
                "category": category,
                "amount": 99999.0,
            },
            headers=headers,
        )
        assert resp.status_code == 422
        assert "cannot be created manually" in str(resp.json()["detail"])


# ---------------------------------------------------------------------------
# Forced credential rotation (L-5 backend security)
# ---------------------------------------------------------------------------
async def test_owner_provisioned_worker_must_rotate_before_acting(client: httpx.AsyncClient):

    WORKER_PW = "workerpass123"
    owner = await owner_with_farm(client)
    role = await client.post(
        "/api/team/roles",
        json={"name": "Audit Mover", "permissions": ["dashboard.view", "tasks.view"]},
        headers=owner,
    )
    assert role.status_code == 201, role.text
    created = await client.post(
        "/api/team/workers",
        json={
            "email": "rotate-audit@farm.in",
            "role_id": role.json()["id"],
            "password": WORKER_PW,
            "name": "Rotate",
        },
        headers=owner,
    )
    assert created.status_code == 201, created.text

    logged_in = await client.post(
        "/api/auth/login",
        json={"email": "rotate-audit@farm.in", "password": WORKER_PW},
        headers={"X-No-Auto-Rotate": "1"},
    )
    body = logged_in.json()
    assert body["user"]["must_change_password"] is True
    bearer = {"Authorization": f"Bearer {body['access_token']}"}

    # Domain reads/mutations are blocked until the rotation completes...
    blocked = await client.get("/api/dashboard", headers=bearer | {"X-Farm-Id": owner["X-Farm-Id"]})
    assert blocked.status_code == 403
    assert "change it" in blocked.json()["detail"].lower()
    # ...while the auth routes that clear the flag stay reachable.
    me = await client.get("/api/auth/me", headers=bearer)
    assert me.status_code == 200

    changed = await client.post(
        "/api/auth/change-password",
        json={"current_password": WORKER_PW, "new_password": f"{WORKER_PW}!rotated"},
        headers=bearer,
    )
    assert changed.status_code == 200, changed.text
    fresh = {"Authorization": f"Bearer {changed.json()['access_token']}"}
    allowed = await client.get("/api/dashboard", headers=fresh | {"X-Farm-Id": owner["X-Farm-Id"]})
    assert allowed.status_code == 200, allowed.text
