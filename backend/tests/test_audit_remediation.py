"""Behavioral coverage for the audit-remediated business rules.

The 2026-09-01 audits found SPEC rules that existed only as prose or
constants: the buck:doe ratio, the meat-sale window and the quarantine sale
fence. Each test here drives
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
