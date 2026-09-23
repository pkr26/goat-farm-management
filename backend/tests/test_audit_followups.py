"""Behavioral coverage for the follow-up audit fixes (post 2026-09-01).

Each test drives the rule through the real API or the real migration the way
an operator (or attacker) would:

* the must-change-password fence's explicit route allowlist;
* the species reference-data migration reaching existing databases;
* the ORF retirement surviving farms whose dosing history cites the template;
* the creep ration requiring the kid to be inside the weaning window;
* the register per-email probe limiter never locking a fresh address.
"""

import asyncio
import logging
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

from app.db import get_sessionmaker
from app.models import Animal

from .conftest import login_and_rotate, owner_with_farm


# ---------------------------------------------------------------------------
# Rotation fence: explicit allowlist, not the whole /api/auth/ prefix
# ---------------------------------------------------------------------------
async def test_rotation_fence_allowlist_blocks_identity_acts(client: httpx.AsyncClient):
    WORKER_PW = "workerpass123"
    owner = await owner_with_farm(client)
    role = await client.post(
        "/api/team/roles",
        json={"name": "Fence Check", "permissions": ["dashboard.view"]},
        headers=owner,
    )
    assert role.status_code == 201, role.text
    created = await client.post(
        "/api/team/workers",
        json={
            "email": "fence-allow@farm.in",
            "role_id": role.json()["id"],
            "password": WORKER_PW,
            "name": "Fence",
        },
        headers=owner,
    )
    assert created.status_code == 201, created.text
    logged_in = await client.post(
        "/api/auth/login",
        json={"email": "fence-allow@farm.in", "password": WORKER_PW},
        headers={"X-No-Auto-Rotate": "1"},
    )
    assert logged_in.status_code == 200, logged_in.text
    bearer = {"Authorization": f"Bearer {logged_in.json()['access_token']}"}

    # Bootstrap routes the banner needs stay reachable (permissions and the
    # farm selector are farm-scoped GETs).
    scoped = bearer | {"X-Farm-Id": owner["X-Farm-Id"]}
    assert (await client.get("/api/auth/me", headers=bearer)).status_code == 200
    assert (await client.get("/api/auth/permissions", headers=scoped)).status_code == 200
    assert (await client.get("/api/auth/farms", headers=bearer)).status_code == 200
    # Identity-level acts are fenced: creating farms under the worker's
    # identity, deleting the account, exporting its data.
    new_farm = await client.post("/api/auth/farms", json={"name": "Laundered"}, headers=bearer)
    assert new_farm.status_code == 403
    assert "change it" in new_farm.json()["detail"].lower()
    exported = await client.get("/api/auth/account/export", headers=bearer)
    assert exported.status_code == 403
    deleted = await client.request(
        "DELETE",
        "/api/auth/account",
        json={"password": WORKER_PW},
        headers=bearer,
    )
    assert deleted.status_code == 403


# ---------------------------------------------------------------------------
# Species reference-data migration reaches existing databases
# ---------------------------------------------------------------------------
def test_species_reference_data_migration_reaches_existing_databases():
    import asyncio

    asyncio.run(_migration_scenario())


async def _migration_scenario():
    """Seed changes are create-only (ON CONFLICT DO NOTHING), so ORF removal
    and PPR 36→12 only reach deployed farms via migration b5d7f9a1c3e5. This
    reproduces a pre-migration database and runs the real alembic revisions
    against it."""
    from app.core.config import get_settings

    admin_url = get_settings().database_url
    # Point at the maintenance DB (postgres) to create/drop the scratch
    # database; the settings URL names the app/test database itself.
    base = admin_url.rsplit("/", 1)[0]
    scratch = "goatfarm_migration_test"

    def _dsn(url: str) -> str:
        return url.replace("postgresql+asyncpg://", "postgresql://")

    async def admin_exec(sql: str) -> None:
        import asyncpg

        conn = await asyncpg.connect(_dsn(f"{base}/postgres"))
        try:
            await conn.execute(sql)
        finally:
            await conn.close()

    await admin_exec(f'DROP DATABASE IF EXISTS "{scratch}"')
    await admin_exec(f'CREATE DATABASE "{scratch}"')
    try:
        env = {**os.environ, "GOATFARM_DATABASE_URL": f"{base}/{scratch}"}
        for target in ("e3a5b7c9d1f2", "head"):
            proc = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-m", "alembic", "upgrade", target],
                env=env,
                check=True,
                capture_output=True,
                cwd=str(Path.cwd()),
            )
            assert proc.returncode == 0
            if target == "head":
                break
            # Legacy rows exactly as a pre-remediation farm held them.
            import asyncpg

            dsn = f"{base}/{scratch}".replace("postgresql+asyncpg://", "postgresql://")
            conn = await asyncpg.connect(dsn)
            try:
                await conn.execute(
                    """
                    INSERT INTO vaccine_templates
                      (name, first_dose_age_months, booster_weeks,
                       repeat_months, timing_note)
                    VALUES ('PPR',3,NULL,36,'Core vaccine; repeat every 3 years'),
                           ('ORF',4,NULL,6,'Every 6 months')
                    """
                )
                await conn.execute(
                    """
                    INSERT INTO bucket_definitions
                      (code, name, who, exit_rule, daily_kg_per_head, sort_order)
                    VALUES ('QUARANTINE','Quarantine Ward',
                            'Newly purchased animals','45-day protocol',0.8,1)
                    """
                )
                await conn.execute(
                    """
                    INSERT INTO users (email, password_hash, created_at)
                    VALUES ('mig@x.io','h',now())
                    """
                )
            finally:
                await conn.close()

        dsn = f"{base}/{scratch}".replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(dsn)
        try:
            ppr = await conn.fetchrow(
                "SELECT repeat_months FROM vaccine_templates WHERE name='PPR'"
            )
            assert ppr["repeat_months"] == 12
            orf = await conn.fetchval("SELECT count(*) FROM vaccine_templates WHERE name='ORF'")
            assert orf == 0
            rate = await conn.fetchval(
                "SELECT daily_kg_per_head FROM bucket_definitions WHERE code='QUARANTINE'"
            )
            assert float(rate) == pytest.approx(1.1)
        finally:
            await conn.close()
    finally:
        await admin_exec(f'DROP DATABASE IF EXISTS "{scratch}"')


# ---------------------------------------------------------------------------
# ORF retirement: dosing history linked to the template survives it (P2-13)
# ---------------------------------------------------------------------------
def test_orf_linked_health_events_survive_the_reference_data_migration():
    """2026-09-20 audit P2-13: farms that already recorded ORF vaccinations
    hold FK references to the template row, and the column carrying that
    reference is guarded by an immutability trigger. The migration must
    detach, delete and re-arm the guard in one upgrade."""
    import asyncio

    asyncio.run(_orf_upgrade_scenario())


async def _orf_upgrade_scenario():
    """Reproduce a pre-remediation farm at b5d7f9a1c3e5's down_revision
    (e3a5b7c9d1f2) with an administered ORF dose linked to the template, then
    run the real alembic chain over it.

    Before the fix this aborted the upgrade twice over: deleting the template
    first died on the FK (23503), and the naive detach-only order died on
    trg_health_event_schedule_template_id_immutable (23514) — the very guard
    d7e8f9a0b1c2 put on health_events.schedule_template_id. The scenario
    proves both hazards are real on this database before asserting the fixed
    order survives them."""
    from app.core.config import get_settings

    admin_url = get_settings().database_url
    base = admin_url.rsplit("/", 1)[0]
    scratch = "goatfarm_test_orf_upgrade"

    def _dsn(url: str) -> str:
        return url.replace("postgresql+asyncpg://", "postgresql://")

    async def admin_exec(sql: str) -> None:
        import asyncpg

        conn = await asyncpg.connect(_dsn(f"{base}/postgres"))
        try:
            await conn.execute(sql)
        finally:
            await conn.close()

    await admin_exec(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)')
    await admin_exec(f'CREATE DATABASE "{scratch}"')
    try:
        env = {**os.environ, "GOATFARM_DATABASE_URL": f"{base}/{scratch}"}

        async def alembic_upgrade(target: str) -> None:
            proc = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-m", "alembic", "upgrade", target],
                env=env,
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(Path.cwd()),
            )
            assert proc.returncode == 0, proc.stdout + proc.stderr

        await alembic_upgrade("e3a5b7c9d1f2")

        import asyncpg

        conn = await asyncpg.connect(_dsn(f"{base}/{scratch}"))
        try:
            owner_id = await conn.fetchval(
                """
                INSERT INTO users (email, password_hash, created_at)
                VALUES ('orf-history@farm.in', 'h', now())
                RETURNING id
                """
            )
            farm_id = await conn.fetchval(
                """
                INSERT INTO farms (name, owner_id, created_at)
                VALUES ('ORF History Farm', $1, now())
                RETURNING id
                """,
                owner_id,
            )
            # The GOAT ORF vaccine template exactly as the pre-remediation
            # seed inserted it (farm_type defaults to 'GOAT').
            orf_id = await conn.fetchval(
                """
                INSERT INTO vaccine_templates
                  (name, first_dose_age_months, booster_weeks,
                   repeat_months, timing_note)
                VALUES ('ORF', 4, NULL, 6, 'Every 6 months')
                RETURNING id
                """
            )
            animal_id = await conn.fetchval(
                """
                INSERT INTO animals (
                  farm_id, tag_number, breed, sex, source, current_bucket,
                  status, cull_candidate, created_at, movement_restricted,
                  suspected_scheduled_disease
                ) VALUES (
                  $1, 'ORF-HX-1', 'Osmanabadi', 'F', 'PURCHASED', 'FOUNDATION',
                  'ACTIVE', false, now(), false, false
                ) RETURNING id
                """,
                farm_id,
            )
            # An administered dose recorded against the ORF calendar;
            # d7e8f9a0b1c2's backfill is what produced this link shape.
            event_id = await conn.fetchval(
                """
                INSERT INTO health_events (
                  farm_id, animal_id, date, type, product_name,
                  schedule_template_name, schedule_template_id,
                  suspected_scheduled_disease
                ) VALUES (
                  $1, $2, CURRENT_DATE, 'VACCINE', 'ORF vaccine', 'ORF', $3,
                  false
                ) RETURNING id
                """,
                farm_id,
                animal_id,
                orf_id,
            )
            # Honesty check: on this database the trigger d7e8f9a0b1c2 left
            # behind is live, so the detach the migration must perform is
            # exactly the UPDATE the database refuses — the 23514 that
            # aborted the naive NULL-only fix.
            with pytest.raises(asyncpg.CheckViolationError) as blocked:
                await conn.execute(
                    "UPDATE health_events SET schedule_template_id = NULL WHERE id = $1",
                    event_id,
                )
            assert blocked.value.sqlstate == "23514"
            assert "health event schedule template link is immutable" in blocked.value.message
        finally:
            await conn.close()

        await alembic_upgrade("head")

        conn = await asyncpg.connect(_dsn(f"{base}/{scratch}"))
        try:
            survivor = await conn.fetchrow(
                """
                SELECT animal_id, product_name, schedule_template_name,
                       schedule_template_id
                FROM health_events WHERE id = $1
                """,
                event_id,
            )
            # The recorded dose survives with every clinical fact intact;
            # only the schedule-source link retires with its template.
            assert survivor["animal_id"] == animal_id
            assert survivor["product_name"] == "ORF vaccine"
            assert survivor["schedule_template_name"] == "ORF"
            assert survivor["schedule_template_id"] is None
            assert (
                await conn.fetchval("SELECT count(*) FROM vaccine_templates WHERE name = 'ORF'")
                == 0
            )
            # The guard the migration had to sidestep is back in place...
            assert (
                await conn.fetchval(
                    """
                    SELECT count(*) FROM pg_trigger
                    WHERE tgname = 'trg_health_event_schedule_template_id_immutable'
                      AND NOT tgisinternal
                    """
                )
                == 1
            )
            # ...and functional: relinking a row is refused again. (The probe
            # template needs only a live row to point at; farm_type itself
            # was dropped from vaccine_templates later by bd201c1cdc1b.)
            probe_id = await conn.fetchval(
                """
                INSERT INTO vaccine_templates
                  (name, first_dose_age_months, repeat_months, timing_note)
                VALUES ('PPR', 3, 12, 'Core vaccine')
                RETURNING id
                """
            )
            with pytest.raises(asyncpg.CheckViolationError) as refused:
                await conn.execute(
                    "UPDATE health_events SET schedule_template_id = $1 WHERE id = $2",
                    probe_id,
                    event_id,
                )
            assert refused.value.sqlstate == "23514"
            assert "health event schedule template link is immutable" in refused.value.message
            # A value-preserving UPDATE that mentions the column still passes:
            # the guard fires on change, not on the column being named.
            await conn.execute(
                "UPDATE health_events SET schedule_template_id = NULL WHERE id = $1",
                event_id,
            )
        finally:
            await conn.close()
    finally:
        await admin_exec(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)')


# ---------------------------------------------------------------------------
# Creep ration requires the kid to be inside the weaning window (goat biology)
# ---------------------------------------------------------------------------
async def test_creep_line_requires_weaning_age(client: httpx.AsyncClient):
    headers = await owner_with_farm(client)
    farm_id = int(headers["X-Farm-Id"])
    async with get_sessionmaker()() as db:

        def _animal(tag, dob_days, dam_id=None):
            return Animal(
                farm_id=farm_id,
                tag_number=tag,
                breed="Osmanabadi",
                sex="F",
                source="BORN",
                current_bucket="RECOVERY",
                status="ACTIVE",
                dam_id=dam_id,
                date_of_birth=date.today() - timedelta(days=dob_days),
                cull_candidate=False,
                movement_restricted=False,
                suspected_scheduled_disease=False,
            )

        grandma = _animal("C-GRAM", 900)
        db.add(grandma)
        await db.flush()
        yearling = _animal("C-YEARL", 400, dam_id=grandma.id)
        kid = _animal("C-KID", 20, dam_id=None)
        db.add_all([yearling, kid])
        await db.flush()
        kid.dam_id = yearling.id
        await db.commit()

    plan = await client.get("/api/feeding/plan", headers=headers)
    assert plan.status_code == 200, plan.text
    lines = plan.json()["lines"]
    creep = [line for line in lines if line["recipe_code"] == "CREEP"]
    assert creep and creep[0]["heads"] == 1, lines
    # The yearling dam — lactating, in RECOVERY with her calf, but 400 days
    # old — must never be planned on the calf creep line.
    adult_recovery = [
        line for line in lines if line["bucket"] == "RECOVERY" and line["recipe_code"] != "CREEP"
    ]
    assert adult_recovery and adult_recovery[0]["heads"] >= 2, lines


# ---------------------------------------------------------------------------
# Register probe limiter: duplicates throttled, fresh addresses never blocked
# ---------------------------------------------------------------------------
async def test_register_email_probe_lockout_never_blocks_a_fresh_address(
    client: httpx.AsyncClient, monkeypatch
):
    from app.api import auth as auth_api
    from app.core.config import Settings
    from app.ratelimit import SlidingWindowRateLimiter

    # One shared settings object feeds both the per-IP register throttle and
    # the per-email probe bucket; keep the IP ceiling far away so every 429
    # observed below is attributable to the EMAIL bucket alone.
    settings = Settings(
        auth_rate_limit_enabled=True,
        auth_rate_limit_max_attempts=50,
        auth_rate_limit_window_seconds=300,
    )
    monkeypatch.setattr(auth_api, "get_settings", lambda: settings)
    monkeypatch.setattr(auth_api, "register_email_limiter", SlidingWindowRateLimiter())
    monkeypatch.setattr(auth_api, "auth_limiter", SlidingWindowRateLimiter())

    owner = await owner_with_farm(client)
    me = await client.get("/api/auth/me", headers=owner)
    owner_email = me.json()["email"]
    email_key = auth_api._register_email_probe_key(owner_email)

    # 49 prior duplicate-email probes (per-IP usage stays at 1).
    for _ in range(49):
        auth_api.register_email_limiter.record("register-email", email_key, 300, max_attempts=50)

    # The 50th duplicate probe charges the bucket through the HTTP path and
    # is still answered as a duplicate (the enumeration oracle itself).
    probe = await client.post(
        "/api/auth/register", json={"email": owner_email, "password": "probepass123"}
    )
    assert probe.status_code == 400, probe.text
    # The 51st is refused by the per-email bucket (per-IP usage is ~2 of 50,
    # so this 429 can only come from the email probe budget).
    throttled = await client.post(
        "/api/auth/register", json={"email": owner_email, "password": "probepass123"}
    )
    assert throttled.status_code == 429, throttled.text

    # The address an attacker can never pre-lock: unregistered, so probing it
    # never charged its bucket under the duplicate-only recording rule — and
    # even this owner's successful registration resets their own bucket.
    fresh = await client.post(
        "/api/auth/register",
        json={"email": "never-probed-before@farm.in", "password": "freshpass123"},
    )
    assert fresh.status_code == 201, fresh.text


async def test_register_email_probe_throttle_log_never_contains_the_raw_address(
    client: httpx.AsyncClient, monkeypatch, caplog: pytest.LogCaptureFixture
):
    """The probe bucket key is logged verbatim on a throttle decision, so it
    is a sha256 of the address — the raw email is PII and must never reach the
    log stream (same rule as every other token-derived limiter key)."""
    from app.api import auth as auth_api
    from app.core.config import Settings
    from app.ratelimit import SlidingWindowRateLimiter

    settings = Settings(
        auth_rate_limit_enabled=True,
        auth_rate_limit_max_attempts=50,
        auth_rate_limit_window_seconds=300,
    )
    monkeypatch.setattr(auth_api, "get_settings", lambda: settings)
    monkeypatch.setattr(auth_api, "register_email_limiter", SlidingWindowRateLimiter())
    monkeypatch.setattr(auth_api, "auth_limiter", SlidingWindowRateLimiter())

    owner = await owner_with_farm(client)
    me = await client.get("/api/auth/me", headers=owner)
    owner_email = me.json()["email"]
    email_key = auth_api._register_email_probe_key(owner_email)
    # Saturate the probe bucket directly so the very next HTTP probe throttles.
    for _ in range(50):
        auth_api.register_email_limiter.record("register-email", email_key, 300, max_attempts=50)

    with caplog.at_level(logging.INFO, logger="goatfarm.auth"):
        throttled = await client.post(
            "/api/auth/register", json={"email": owner_email, "password": "probepass123"}
        )
    assert throttled.status_code == 429, throttled.text
    lines = [
        record.getMessage()
        for record in caplog.records
        if "register-email throttled" in record.getMessage()
    ]
    assert len(lines) == 1, caplog.records
    # The hashed key identifies the bucket; the raw address appears nowhere.
    assert email_key in lines[0]
    assert all(owner_email not in record.getMessage() for record in caplog.records)


# ---------------------------------------------------------------------------
# ITEM 5 (2026-09-21 playbook): machine-readable ``code`` on mapped errors
# ---------------------------------------------------------------------------
async def test_mapped_error_statuses_carry_stable_codes(client: httpx.AsyncClient):
    """401/403/422 carry a code; 404 deliberately does not (there is nothing
    machine-actionable to distinguish about "not found")."""
    # 401 — the request-validation-free auth refusal.
    unauthenticated = await client.get("/api/auth/me")
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["code"] == "UNAUTHENTICATED"

    owner = await owner_with_farm(client, email="codes-owner@farm.in")
    role = await client.post(
        "/api/team/roles",
        json={"name": "No cross-farm lens", "permissions": ["dashboard.view"]},
        headers=owner,
    )
    assert role.status_code == 201, role.text
    created = await client.post(
        "/api/team/workers",
        json={
            "email": "codes-worker@farm.in",
            "role_id": role.json()["id"],
            "password": "workerpass123",
            "name": "Codes",
        },
        headers=owner,
    )
    assert created.status_code == 201, created.text
    worker = await login_and_rotate(client, "codes-worker@farm.in", "workerpass123")

    # 403 — permission denied (the cross-farm owner lens is ownership-only).
    forbidden = await client.get("/api/owner/overview", headers=worker)
    assert forbidden.status_code == 403, forbidden.text
    assert forbidden.json()["code"] == "PERMISSION_DENIED"

    # 422 (request validation) — the structured detail list is preserved
    # verbatim; the code rides alongside it.
    malformed = await client.post(
        "/api/auth/register", json={"email": "not-an-email", "password": 42}
    )
    assert malformed.status_code == 422, malformed.text
    body = malformed.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert isinstance(body["detail"], list) and body["detail"]
    assert {"type", "loc", "msg"} <= set(body["detail"][0])

    # 422 (deliberate HTTPException rejections) answers the same shape.
    rejected = await client.get("/api/owner/benchmarks", params={"days": 4000}, headers=owner)
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["code"] == "VALIDATION_ERROR"

    # 404 — no code key at all: only the four mapped statuses carry one.
    missing = await client.get("/api/animals/999999999", headers=owner)
    assert missing.status_code == 404
    assert "code" not in missing.json()


async def test_rate_limited_responses_carry_the_code_and_retry_hint(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """A 429 carries RATE_LIMITED plus the Retry-After header it always had —
    the code handler must not swallow response headers."""
    from app.core.config import get_settings
    from app.ratelimit import auth_limiter

    monkeypatch.setattr(get_settings(), "auth_rate_limit_enabled", True)
    auth_limiter.clear()
    try:
        throttled = None
        for _ in range(15):
            throttled = await client.post(
                "/api/auth/login",
                json={"email": "codes-throttle@farm.in", "password": "wrong-pass-1"},
            )
            if throttled.status_code == 429:
                break
        assert throttled is not None and throttled.status_code == 429, throttled.text
        assert throttled.json()["code"] == "RATE_LIMITED"
        assert throttled.headers.get("retry-after")
    finally:
        auth_limiter.clear()
