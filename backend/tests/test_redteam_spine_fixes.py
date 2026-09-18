"""Regression tests for the 2026-09-13 red-team spine remediations.

Covers the fixes made directly in the remediation pass (reports
audit_reports/2026-09-13/01..17 + 18_REMEDIATION_LOG.md):

* RT-M-1 / RT-C-4 — required Idempotency-Key on feeding/mix,
  feeding/inventory/{id}/add, purchases/new, auth/farms, and the
  managed-purchase branch of POST /api/animals.
* RT-C-1 / RT-HIJ-1 — the purchase cascade is permission-coupled in both
  directions (animals.create ⇔ purchases.manage).
* RT-C-2 — the meat-sale minimum-age gate follows sex+age, not the bucket.
* RT-C-3 — owner overrides out of a purchased quarantine require a pristine
  protocol; the non-override path still 409s.
* RT-DE-1 / RT-C-5 — overrides cannot strand an open service outside the
  reproductive workflow buckets.
* RT-A-1 — the per-email login ceiling is soft: a correct password still
  logs in while the account is under distributed attack.
* RT-B-4 — X-Farm-Id accepts canonical ASCII digits only.
* RT-M2-1 / RT-M2-2 / RT-M-6 — unknown GOATFARM_* env vars refuse boot,
  production force-disables /metrics, multi-worker production refuses boot.
* RT-HIJ-4 / RT-HIJ-5 — dispense dates cannot predate the farm; ration
  overrides are domain-capped.
"""

from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Settings, get_settings
from app.main import (
    _enforce_production_private_key_mode,
    _log_safe_path,
    _metrics_method,
    create_app,
)
from app.ratelimit import auth_limiter
from tests.conftest import login_and_rotate, owner_with_farm, register


async def _make_animal(
    client: httpx.AsyncClient,
    headers: dict,
    tag: str,
    *,
    sex: str = "F",
    bucket: str = "FOUNDATION",
    dob_days: int = 800,
    weight_kg: float | None = 26.0,
) -> dict:
    payload: dict = {
        "tag_number": tag,
        "sex": sex,
        "source": "PURCHASED",
        "current_bucket": bucket,
        "date_of_birth": (date.today() - timedelta(days=dob_days)).isoformat(),
        "historical_import_reason": "Red-team spine fixture",
    }
    if weight_kg is not None:
        payload["weight_kg"] = weight_kg
        payload["weight_date"] = payload["date_of_birth"]
    resp = await client.post("/api/animals", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _custom_role(client: httpx.AsyncClient, owner: dict, name: str, perms: list[str]) -> int:
    resp = await client.post(
        "/api/team/roles",
        json={"name": name, "description": "red-team fixture", "permissions": perms},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _add_worker(client: httpx.AsyncClient, owner: dict, role_id: int, email: str) -> None:
    resp = await client.post(
        "/api/team/workers",
        json={"name": "Worker", "email": email, "password": "workerpass1234", "role_id": role_id},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text


async def _worker_headers(client: httpx.AsyncClient, email: str, farm_id: str) -> dict:
    # Rotate explicitly at first login: the default client never completes
    # the forced must-change-password rotation on a worker's behalf.
    headers = await login_and_rotate(client, email, "workerpass1234")
    return headers | {"X-Farm-Id": farm_id}


# ---------------------------------------------------------------------------
# RT-M-1 / RT-C-4: required idempotency keys on money/graph mutations
# ---------------------------------------------------------------------------


async def test_required_idempotency_keys_on_money_routes(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each newly hardened route must 422 a keyless request with a payload
    that is otherwise valid — proving the missing key is the reason."""
    owner = await owner_with_farm(client, "keyless@farm.in", "Keyless Farm")

    # Disable the test client's automatic key injection for these calls.
    import tests.conftest as conftest

    monkeypatch.setattr(conftest, "IDEMPOTENCY_REQUIRED_PATHS", set())
    monkeypatch.setattr(conftest, "AUTO_KEY_MANAGED_PURCHASE", False)

    inventory = (await client.get("/api/feeding/inventory", headers=owner)).json()
    items = (
        inventory["items"] if isinstance(inventory, dict) and "items" in inventory else inventory
    )
    item_id = items[0]["id"]

    cases: list[tuple[str, dict]] = [
        ("/api/feeding/mix", {"recipe_code": "FATTENING_50_50", "batch_kg": 5}),
        (f"/api/feeding/inventory/{item_id}/add", {"qty_kg": 5, "price_per_kg": 10}),
        (
            "/api/purchases/new",
            {"date": date.today().isoformat(), "count": 2, "sex": "F", "create_animals": False},
        ),
        ("/api/auth/farms", {"name": "Another Farm"}),
    ]
    for path, payload in cases:
        resp = await client.post(path, json=payload, headers=owner)
        assert resp.status_code == 422, (path, resp.status_code, resp.text)
        assert "Idempotency-Key" in resp.text, path

    # The managed-purchase branch of POST /api/animals (money-booking) also
    # demands a key; the historical-import branch does not.
    managed = {
        "tag_number": "KEY-MANAGED-1",
        "sex": "F",
        "source": "PURCHASED",
        "current_bucket": "QUARANTINE",
        "purchase_date": date.today().isoformat(),
        "purchase_price": 1000,
    }
    resp = await client.post("/api/animals", json=managed, headers=owner)
    assert resp.status_code == 422, resp.text
    assert "Idempotency-Key" in resp.text

    historical = {
        "tag_number": "KEY-HIST-1",
        "sex": "F",
        "source": "PURCHASED",
        "current_bucket": "FOUNDATION",
        "historical_import_reason": "import needs no key",
    }
    resp = await client.post("/api/animals", json=historical, headers=owner)
    assert resp.status_code == 201, resp.text

    # With keys restored the guarded routes work again.
    monkeypatch.setattr(
        conftest, "IDEMPOTENCY_REQUIRED_PATHS", {"/api/auth/farms", "/api/purchases/new"}
    )
    resp = await client.post(
        "/api/purchases/new",
        json={"date": date.today().isoformat(), "count": 1, "sex": "F", "create_animals": False},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# RT-C-1 / RT-HIJ-1: permission coupling around the purchase cascade
# ---------------------------------------------------------------------------


async def test_animals_create_cannot_trigger_purchase_cascade(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, "cascade-a@farm.in", "Cascade A")
    role_id = await _custom_role(client, owner, "Herd Clerk", ["animals.view", "animals.create"])
    await _add_worker(client, owner, role_id, "clerk@farm.in")
    worker = await _worker_headers(client, "clerk@farm.in", owner["X-Farm-Id"])

    managed = {
        "tag_number": "CLERK-P-1",
        "sex": "F",
        "source": "PURCHASED",
        "current_bucket": "QUARANTINE",
        "purchase_date": date.today().isoformat(),
        "purchase_price": 2500,
    }
    resp = await client.post("/api/animals", json=managed, headers=worker)
    assert resp.status_code == 403, resp.text
    assert "purchase-management permission" in resp.text

    # The owner (all permissions) can — the cascade itself still works.
    resp = await client.post(
        "/api/animals",
        json=managed | {"tag_number": "OWNER-P-1"},
        headers=owner | {"Idempotency-Key": "spine-owner-p1"},
    )
    assert resp.status_code == 201, resp.text


async def test_purchase_batch_stub_creation_requires_animals_create(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, "cascade-b@farm.in", "Cascade B")
    role_id = await _custom_role(
        client, owner, "Buyer Only", ["purchases.view", "purchases.manage"]
    )
    await _add_worker(client, owner, role_id, "buyer@farm.in")
    worker = await _worker_headers(client, "buyer@farm.in", owner["X-Farm-Id"])

    resp = await client.post(
        "/api/purchases/new",
        json={
            "date": date.today().isoformat(),
            "count": 2,
            "sex": "F",
            "total_price": 5000,
            "create_animals": True,
        },
        headers=worker,
    )
    assert resp.status_code == 403, resp.text
    assert "animal-creation permission" in resp.text

    # Without stub creation the batch is a pure procurement record: allowed.
    resp = await client.post(
        "/api/purchases/new",
        json={"date": date.today().isoformat(), "count": 2, "sex": "F", "create_animals": False},
        headers=worker,
    )
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# RT-C-2: meat-sale age gate follows sex+age, not the bucket
# ---------------------------------------------------------------------------


async def _bred_confirmed_doe(client: httpx.AsyncClient, owner: dict, bred_days_ago: int) -> dict:
    doe = await _make_animal(client, owner, "XO-F-1", bucket="BREEDING", dob_days=600)
    buck = await _make_animal(
        client, owner, "XO-M-1", sex="M", bucket="BREEDING", dob_days=600, weight_kg=32.0
    )
    bred = await client.post(
        "/api/breeding",
        json={
            "doe_id": doe["id"],
            "buck_id": buck["id"],
            "breeding_date": (date.today() - timedelta(days=bred_days_ago)).isoformat(),
        },
        headers=owner,
    )
    assert bred.status_code == 201, bred.text
    breeding_id = bred.json()["id"]
    detail = (await client.get(f"/api/breeding/{breeding_id}", headers=owner)).json()
    scan = await client.post(
        f"/api/breeding/{breeding_id}/ultrasound",
        json={"pregnant": True, "kid_count": 1, "date": detail["ultrasound_date"]},
        headers=owner,
    )
    assert scan.status_code == 200, scan.text
    return {"doe_id": doe["id"], "breeding_id": breeding_id, "scan": scan.json()}


async def test_meat_sale_age_gate_covers_recovery_kids(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, "meatage@farm.in", "Meat Age Farm")
    ctx = await _bred_confirmed_doe(client, owner, bred_days_ago=155)
    ekd = date.fromisoformat(ctx["scan"]["expected_kidding_date"])
    kidding = await client.post(
        "/api/kidding",
        json={
            "breeding_record_id": ctx["breeding_id"],
            "date": ekd.isoformat(),
            "ease": "NORMAL",
            "kids": [{"tag": "MA-K-1", "sex": "M", "birth_weight": 2.8, "status": "ALIVE"}],
        },
        headers=owner,
    )
    assert kidding.status_code == 201, kidding.text
    kid_id = next(k["animal_id"] for k in kidding.json()["kids"] if k.get("animal_id"))
    profile = (await client.get(f"/api/animals/{kid_id}", headers=owner)).json()["animal"]
    assert profile["current_bucket"] == "RECOVERY"  # unweaned kid riding with the dam

    # Selling the underage male kid from RECOVERY must hit the meat-sale gate.
    resp = await client.post(
        f"/api/animals/{kid_id}/status",
        json={"new_status": "SOLD", "date": date.today().isoformat()},
        headers=owner,
    )
    assert resp.status_code == 422, resp.text
    assert "meat-sale window" in resp.text

    # Culling remains open for the same animal (the documented escape hatch).
    resp = await client.post(
        f"/api/animals/{kid_id}/status",
        json={"new_status": "CULLED", "date": date.today().isoformat(), "notes": "injury"},
        headers=owner,
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# RT-C-3: owner override out of purchased quarantine requires pristine protocol
# ---------------------------------------------------------------------------


async def test_override_quarantine_release_requires_pristine_protocol(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, "pristine@farm.in", "Pristine Farm")
    # A managed purchase: animal lands in QUARANTINE with a pristine batch.
    resp = await client.post(
        "/api/animals",
        json={
            "tag_number": "PRIS-Q-1",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "QUARANTINE",
            "purchase_date": date.today().isoformat(),
            "purchase_price": 1500,
        },
        headers=owner | {"Idempotency-Key": "spine-pris-q1"},
    )
    assert resp.status_code == 201, resp.text
    animal_id = resp.json()["id"]

    override = await client.post(
        f"/api/animals/{animal_id}/move",
        json={"to_bucket": "FOUNDATION", "reason": "owner correction", "history_override": True},
        headers=owner,
    )
    # Pristine protocol (day 0, nothing done): the override may release.
    assert override.status_code == 200, override.text

    # The non-override path still 409s for a second batch animal.
    resp = await client.post(
        "/api/animals",
        json={
            "tag_number": "PRIS-Q-2",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "QUARANTINE",
            "purchase_date": date.today().isoformat(),
            "purchase_price": 1500,
        },
        headers=owner | {"Idempotency-Key": "spine-pris-q2"},
    )
    animal2 = resp.json()["id"]
    plain = await client.post(
        f"/api/animals/{animal2}/move",
        json={"to_bucket": "FOUNDATION", "reason": "worker move"},
        headers=owner,
    )
    assert plain.status_code == 409, plain.text
    assert "guarded batch task" in plain.text

    # The exploit shape the FOUNDATION-only fence missed: a mid-protocol
    # batch animal side-stepping biosecurity to a non-FOUNDATION bucket
    # (BREEDING) by owner override must 409 exactly like a release would.
    resp = await client.post(
        "/api/animals",
        json={
            "tag_number": "PRIS-Q-3",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "QUARANTINE",
            "purchase_date": date.today().isoformat(),
            "purchase_price": 1500,
        },
        headers=owner | {"Idempotency-Key": "spine-pris-q3"},
    )
    assert resp.status_code == 201, resp.text
    animal3 = resp.json()["id"]
    purchases = await client.get("/api/purchases", headers=owner)
    assert purchases.status_code == 200, purchases.text
    batch_id = max(batch["id"] for batch in purchases.json()["batches"])
    detail = await client.get(f"/api/purchases/{batch_id}", headers=owner)
    assert detail.status_code == 200, detail.text
    first_task = detail.json()["tasks"][0]["id"]
    completed = await client.post(f"/api/tasks/{first_task}/complete", headers=owner)
    assert completed.status_code == 200, completed.text
    side_step = await client.post(
        f"/api/animals/{animal3}/move",
        json={"to_bucket": "BREEDING", "reason": "owner correction", "history_override": True},
        headers=owner,
    )
    assert side_step.status_code == 409, side_step.text
    assert side_step.json()["detail"] == (
        "This animal cannot leave quarantine by override because its "
        "purchase-batch protocol has started, ended, or is incomplete."
    )


# ---------------------------------------------------------------------------
# RT-DE-1 / RT-C-5: overrides cannot strand an open pregnancy
# ---------------------------------------------------------------------------


async def test_override_cannot_strand_open_pregnancy(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, "strand@farm.in", "Strand Farm")
    ctx = await _bred_confirmed_doe(client, owner, bred_days_ago=60)

    # Override into a dead-end bucket: refused.
    dead_end = await client.post(
        f"/api/animals/{ctx['doe_id']}/move",
        json={"to_bucket": "FOUNDATION", "reason": "correction", "history_override": True},
        headers=owner,
    )
    assert dead_end.status_code == 409, dead_end.text
    assert "open breeding or pregnancy" in dead_end.text

    # Override into a workflow bucket is the sanctioned correction.
    sanctioned = await client.post(
        f"/api/animals/{ctx['doe_id']}/move",
        json={"to_bucket": "DELIVERY", "reason": "correction", "history_override": True},
        headers=owner,
    )
    assert sanctioned.status_code == 200, sanctioned.text


# ---------------------------------------------------------------------------
# RT-A-1: soft per-email lockout
# ---------------------------------------------------------------------------


@pytest.fixture()
def rate_limit_one(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GOATFARM_AUTH_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("GOATFARM_AUTH_RATE_LIMIT_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("GOATFARM_AUTH_RATE_LIMIT_WINDOW_SECONDS", "300")
    get_settings.cache_clear()
    auth_limiter.clear()
    yield
    auth_limiter.clear()
    get_settings.cache_clear()


@pytest.mark.usefixtures("rate_limit_one")
async def test_soft_email_lockout_still_admits_correct_password(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await register(client, "victim-soft@farm.in", "realpass12345")
    monkeypatch.setenv("GOATFARM_TRUSTED_PROXY_HOSTS", "127.0.0.1")
    get_settings.cache_clear()
    app: FastAPI = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as proxied:
        # Fill the IP-agnostic per-email ceiling from rotating addresses. The
        # failure that exactly reaches the ceiling answers 429 (the soft
        # ceiling announces itself on the trip); earlier ones are plain 401s.
        for ip, expected in (("1.1.1.1", 401), ("2.2.2.2", 401), ("3.3.3.3", 429)):
            resp = await proxied.post(
                "/api/auth/login",
                json={"email": "victim-soft@farm.in", "password": "wrongpass1"},
                headers={"X-Forwarded-For": ip},
            )
            assert resp.status_code == expected, (ip, resp.text)
        # The ceiling now trips for wrong passwords from a fresh address …
        resp = await proxied.post(
            "/api/auth/login",
            json={"email": "victim-soft@farm.in", "password": "wrongpass1"},
            headers={"X-Forwarded-For": "4.4.4.4"},
        )
        assert resp.status_code == 429, resp.text
        # … but the victim's CORRECT password still logs in (RT-A-1).
        resp = await proxied.post(
            "/api/auth/login",
            json={"email": "victim-soft@farm.in", "password": "realpass12345"},
            headers={"X-Forwarded-For": "5.5.5.5"},
        )
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# RT-B-4: canonical X-Farm-Id spellings
# ---------------------------------------------------------------------------


async def test_x_farm_id_rejects_non_canonical_integers(client: httpx.AsyncClient) -> None:
    headers = await register(client, "farmid@farm.in")
    # httpx refuses non-ASCII header values, so the Unicode-digit spellings
    # cannot even be sent by this client; the ASCII non-canonical spellings
    # are the testable surface.
    for spelling in ("+5", "5_0", "0x5"):
        resp = await client.get("/api/auth/permissions", headers=headers | {"X-Farm-Id": spelling})
        assert resp.status_code == 400, (spelling, resp.status_code)


# ---------------------------------------------------------------------------
# RT-M2-1 / RT-M2-2 / RT-M-6: settings surface hardening
# ---------------------------------------------------------------------------


def test_unknown_goatfarm_env_var_refuses_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOATFARM_TYPOD_KNOB", "1")
    with pytest.raises(ValueError, match="GOATFARM_TYPOD_KNOB"):
        Settings(_env_file=None)


def _production_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOATFARM_ENVIRONMENT", "production")
    monkeypatch.setenv("GOATFARM_COOKIE_SECURE", "true")
    monkeypatch.setenv("GOATFARM_CORS_ORIGINS", '["https://farm.example.com"]')
    monkeypatch.setenv("GOATFARM_ALLOWED_HOSTS", '["farm.example.com"]')
    monkeypatch.setenv("GOATFARM_MIN_PASSWORD_LENGTH", "12")
    monkeypatch.setenv("GOATFARM_DB_SSLMODE", "verify-full")
    monkeypatch.setenv("GOATFARM_DATABASE_URL", "postgresql+asyncpg://u:p@db/goatfarm")
    monkeypatch.setenv("GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET", "x" * 40)
    monkeypatch.setenv(
        "GOATFARM_TOTP_ENCRYPTION_KEY", "VFRUVFRUVFRUVFRUVFRUVFRUVFRUVFRUVFRUVFRUVFQ"
    )
    monkeypatch.setenv("GOATFARM_JWT_PRIVATE_KEY_PATH", "/nonexistent/key.pem")
    monkeypatch.setenv("GOATFARM_JWT_PUBLIC_KEY_PATH", "/nonexistent/key.pub")
    monkeypatch.delenv("UVICORN_WORKERS", raising=False)
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)


def test_production_force_disables_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    _production_env(monkeypatch)
    settings = Settings(_env_file=None)
    assert settings.metrics_enabled is False


def test_production_refuses_multi_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    _production_env(monkeypatch)
    monkeypatch.setenv("UVICORN_WORKERS", "4")
    with pytest.raises(ValueError, match="one uvicorn worker"):
        Settings(_env_file=None)


# ---------------------------------------------------------------------------
# RT-HIJ-4 / RT-HIJ-5: feeding hardening
# ---------------------------------------------------------------------------


async def test_ration_setting_domain_cap(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, "ration@farm.in", "Ration Farm")
    resp = await client.post(
        "/api/feeding/settings",
        json={"bucket": "FOUNDATION", "daily_kg_per_head": 60},
        headers=owner,
    )
    assert resp.status_code == 422, resp.text
    resp = await client.post(
        "/api/feeding/settings",
        json={"bucket": "FOUNDATION", "daily_kg_per_head": 2.5},
        headers=owner,
    )
    assert resp.status_code == 204, resp.text


async def test_dispense_date_cannot_predate_farm(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, "dispdate@farm.in", "Dispense Farm")
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    resp = await client.post(
        "/api/feeding/dispense",
        json={
            "bucket": "FOUNDATION",
            "shift": "MORNING",
            "recipe_code": "DRY_ROUGHAGE",
            "qty_kg": 1,
            "date": yesterday,
        },
        headers=owner,
    )
    assert resp.status_code == 422, resp.text
    assert "before the farm was created" in resp.text


# ---------------------------------------------------------------------------
# RT-A-3 / RT-M-3 / RT-M-4: production key mode + log label hygiene
# ---------------------------------------------------------------------------


def test_production_refuses_group_readable_private_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    key = tmp_path / "jwt_private.pem"
    key.write_text("placeholder — the mode check runs before any parsing")
    key.chmod(0o640)
    _production_env(monkeypatch)
    monkeypatch.setenv("GOATFARM_JWT_PRIVATE_KEY_PATH", str(key))
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="group/other accessible"):
            _enforce_production_private_key_mode()
    finally:
        get_settings.cache_clear()


def test_production_accepts_owner_only_private_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    key = tmp_path / "jwt_private.pem"
    key.write_text("placeholder — the mode check runs before any parsing")
    key.chmod(0o600)
    _production_env(monkeypatch)
    monkeypatch.setenv("GOATFARM_JWT_PRIVATE_KEY_PATH", str(key))
    get_settings.cache_clear()
    try:
        _enforce_production_private_key_mode()  # must not raise
    finally:
        get_settings.cache_clear()


def test_development_skips_private_key_mode_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    key = tmp_path / "jwt_private.pem"
    key.write_text("placeholder — the mode check runs before any parsing")
    key.chmod(0o666)
    monkeypatch.setenv("GOATFARM_JWT_PRIVATE_KEY_PATH", str(key))
    get_settings.cache_clear()
    try:
        _enforce_production_private_key_mode()  # must not raise
    finally:
        get_settings.cache_clear()


def test_log_safe_path_escapes_every_control_character() -> None:
    # RT-M-4: line forgery via \r/\n AND terminal-escape smuggling via
    # C1/DEL/ESC must both be impossible in request logs; the mnemonic
    # spellings stay grep-friendly.
    escaped = _log_safe_path("/api/x\x1b[31my\r\nz\u2028w\u007f")
    assert "\r" not in escaped and "\n" not in escaped
    assert "\x1b" not in escaped and "\u2028" not in escaped and "\x7f" not in escaped
    assert "\\r" in escaped and "\\n" in escaped
    assert "\\u001b" in escaped and "\\u2028" in escaped and "\\u007f" in escaped


def test_metrics_method_allowlists_standard_verbs() -> None:
    # RT-M-3: an attacker-chosen method token must collapse to one OTHER
    # label, never mint a new Prometheus series per spelling.

    class _MethodRequest:
        def __init__(self, method: str) -> None:
            self.method = method

    for verb in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
        assert _metrics_method(_MethodRequest(verb)) == verb
    assert _metrics_method(_MethodRequest("FROB")) == "OTHER"
    assert _metrics_method(_MethodRequest("get")) == "OTHER"
