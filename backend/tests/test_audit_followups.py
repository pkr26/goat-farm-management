"""Behavioral coverage for the follow-up audit fixes (post 2026-09-01).

Each test drives the rule through the real API or the real migration the way
an operator (or attacker) would:

* the must-change-password fence's explicit route allowlist;
* the species reference-data migration reaching existing databases;
* the creep ration requiring the kid to be inside the weaning window;
* the register per-email probe limiter never locking a fresh address.
"""

import asyncio
import os
import subprocess
import sys
from datetime import date, timedelta

import httpx
import pytest

from app.db import get_sessionmaker
from app.models import Animal

from .conftest import owner_with_farm


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
                cwd=os.getcwd(),
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
    email_key = f"register-email:{owner_email.lower()}"

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
