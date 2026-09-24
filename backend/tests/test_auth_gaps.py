"""Auth gap tests (2026-09-23 verification plan, category 10).

- the plain token_version invalidation: changing the password must 401 an
  access JWT issued before the change (the race variants are pinned in
  test_auth_token_version_races; the direct case was not);
- PIN timing equalization, statistically: a wrong PIN against a REAL
  roster name must cost the same (within generous bounds) as a wrong PIN
  against an unknown name — otherwise the roster endpoint doubles as a
  membership oracle through the timing channel.
"""

import statistics
import time

import httpx

from .conftest import OWNER_PW, owner_with_farm, register


async def test_password_change_invalidates_issued_access_tokens(client: httpx.AsyncClient) -> None:
    headers = await register(client, email="tokenver@farm.in", password=OWNER_PW)
    me_before = await client.get("/api/auth/me", headers=headers)
    assert me_before.status_code == 200, me_before.text

    changed = await client.post(
        "/api/auth/change-password",
        json={"current_password": OWNER_PW, "new_password": "new-password-123"},
        headers=headers,
    )
    assert changed.status_code == 200, changed.text

    stale = await client.get("/api/auth/me", headers=headers)
    assert stale.status_code == 401, (
        f"pre-change access token still valid after password change: {stale.status_code}"
    )

    fresh = await client.post(
        "/api/auth/login",
        json={"email": "tokenver@farm.in", "password": "new-password-123"},
    )
    assert fresh.status_code == 200, fresh.text


async def test_wrong_pin_timing_is_equalized_between_known_and_unknown_names(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="pintiming@farm.in")
    role = await client.post(
        "/api/team/roles",
        json={"name": "Pin", "permissions": ["tasks.view"]},
        headers=owner,
    )
    assert role.status_code == 201, role.text
    worker = await client.post(
        "/api/team/workers",
        json={
            "email": "pinworker@farm.in",
            "name": "Real Worker",
            "role_id": role.json()["id"],
            "pin": "4321",
        },
        headers=owner,
    )
    assert worker.status_code in (201, 200), worker.text
    farm_id = owner["X-Farm-Id"]

    roster = await client.get(
        "/api/auth/worker-roster", params={"farm_id": farm_id}
    )
    assert roster.status_code == 200, roster.text
    body = roster.json()
    entries = body["items"] if isinstance(body, dict) else body
    entry = next(e for e in entries if e.get("display_name") == "Real Worker")
    membership_id = entry["membership_id"]

    known_ms: list[float] = []
    unknown_ms: list[float] = []
    for _ in range(25):  # interleaved to cancel machine drift
        for label, mid, sink in (
            ("known", membership_id, known_ms),
            ("unknown", 999_999, unknown_ms),
        ):
            start = time.perf_counter()
            resp = await client.post(
                "/api/auth/worker-login",
                json={"farm_id": int(farm_id), "membership_id": mid, "pin": "9999"},
            )
            sink.append((time.perf_counter() - start) * 1000)
            assert resp.status_code == 401, f"{label}: {resp.status_code} {resp.text[:100]}"

    k_med, u_med = statistics.median(known_ms), statistics.median(unknown_ms)
    # Generous bounds (CI jitter): a real oracle (skipping the Argon2 verify
    # for unknown names) is typically a 2-3x gap; allow 5x/150ms so only a
    # genuine structural difference trips this.
    assert k_med < max(150.0, 5 * u_med) and u_med < max(150.0, 5 * k_med), (
        f"PIN timing oracle: known-name median {k_med:.1f} ms vs "
        f"unknown-name median {u_med:.1f} ms"
    )
