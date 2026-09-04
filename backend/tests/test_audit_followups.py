"""Behavioral coverage for the follow-up audit fixes (post 2026-09-01).

Each test drives the rule through the real API or the real migration the way
an operator (or attacker) would:

* the dairy weaning duty completing while the dam was re-bred at her own
  60-day VWP (the audit found the pre-flight demanded a RESTING transition
  for a dam the completion never moves);
* the must-change-password fence's explicit route allowlist;
* sub-milli milk litres failing as 422 input, not a flush-time 500;
* legacy provenance-less milk income staying correctable;
* the species reference-data migration reaching existing databases;
* the dairy due-window suggestion matching the EDD-60 dry-off write path;
* the creep ration requiring the kid to be inside the weaning window;
* the register per-email probe limiter never locking a fresh address.
"""

import asyncio
import os
import subprocess
import sys
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Animal, BucketMove, Farm, Transaction
from app.services.dashboard import ready_to_move_suggestions

from .conftest import owner_with_farm


# ---------------------------------------------------------------------------
# Dairy weaning with a re-bred dam (H1: the standard protocol flow)
# ---------------------------------------------------------------------------
async def test_dairy_weaning_completes_when_dam_rebred_at_vwp(client: httpx.AsyncClient):
    """The app's own dairy protocol: calve → fresh pen +10d → RESTING → AI at
    the 60-day VWP → dam back in BREEDING → day-90 milk-weaning graduates the
    heifer. The completion used to 409 because the pre-flight demanded a
    "weaning" RESTING transition from a dam the completion never moves."""
    from .test_audit_remediation import _dairy_dam_ready
    from .test_dairy import _dairy_owner

    headers = await _dairy_owner(client, email="wean-rebred@farm.in")
    animal = await _dairy_dam_ready(client, headers, "D-WEAN-REBRED")
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
    calving_day = confirmed["expected_kidding_date"]
    calved = await client.post(
        "/api/kidding",
        json={
            "breeding_record_id": confirmed["id"],
            "date": calving_day,
            "ease": "NORMAL",
            "kids": [{"sex": "F", "status": "ALIVE"}],
        },
        headers=headers,
    )
    assert calved.status_code in (200, 201), calved.text
    dam_id = calved.json()["doe_id"]
    heifer_id = calved.json()["kids"][0]["animal_id"]

    # Historical fixture: the on-time fresh-pen exit (calving +10 days, the
    # species' postpartum recovery window) recorded with its true date — the
    # move API stamps today, so the historical move is written directly.
    farm_id = int(headers["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        owner_id = (await db.execute(select(Farm.owner_id).where(Farm.id == farm_id))).scalar_one()
        db.add(
            BucketMove(
                animal_id=dam_id,
                from_bucket="RECOVERY",
                to_bucket="RESTING",
                reason="Fresh period complete",
                effective_date=date.fromisoformat(calving_day) + timedelta(days=10),
                created_by_id=owner_id,
            )
        )
        dam = await db.get(Animal, dam_id)
        dam.current_bucket = "RESTING"
        await db.commit()

    # AI at exactly the 60-day VWP floor (allowed: the fence rejects only
    # strictly earlier dates) moves the bred-back dam into BREEDING.
    service_day = date.fromisoformat(calving_day) + timedelta(days=60)
    rebred = await client.post(
        "/api/breeding",
        json={
            "doe_id": dam_id,
            "breeding_date": service_day.isoformat(),
            "method": "AI",
            "semen_sire_name": "Karanfir 777",
        },
        headers=headers,
    )
    assert rebred.status_code == 201, rebred.text

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
    completed = await client.post(f"/api/tasks/{weaning['id']}/complete", headers=headers)
    assert completed.status_code == 200, completed.text
    heifer = (await client.get(f"/api/animals/{heifer_id}", headers=headers)).json()
    assert heifer.get("animal", heifer)["current_bucket"] == "FOUNDATION"
    dam_after = (await client.get(f"/api/animals/{dam_id}", headers=headers)).json()
    # The re-bred dam is untouched by the calf graduation.
    assert dam_after.get("animal", dam_after)["current_bucket"] == "BREEDING"


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
# Sub-milli milk litres: 422 input, never a flush-time 500
# ---------------------------------------------------------------------------
async def test_milk_income_sub_milli_litres_rejected_as_422(client: httpx.AsyncClient):
    from .test_audit_remediation import _dairy_dam_ready
    from .test_dairy import _dairy_owner

    headers = await _dairy_owner(client, email="milli@farm.in")
    animal = await _dairy_dam_ready(client, headers, "D-MILLI")
    reading = await client.post(
        "/api/milk/new",
        json={
            "animal_id": animal["id"],
            "date": date.today().isoformat(),
            "shift": "MORNING",
            "litres": 8.0,
        },
        headers=headers,
    )
    assert reading.status_code in (200, 201), reading.text
    sale = await client.post(
        "/api/finance/new",
        json={
            "date": date.today().isoformat(),
            "type": "INCOME",
            "category": "MILK",
            "amount": 0.04,
            "milk_litres": 0.0004,
            "milk_unit_price_per_litre": 100.0,
        },
        headers=headers,
    )
    assert sale.status_code == 422, sale.text
    # The storage floor is enforced at input validation (detail is the
    # pydantic error list), never deferred to a flush-time CHECK violation.
    assert "milk_litres" in sale.text


# ---------------------------------------------------------------------------
# Legacy provenance-less milk income stays correctable
# ---------------------------------------------------------------------------
async def test_legacy_milk_income_without_provenance_can_be_corrected(client: httpx.AsyncClient):
    from .test_audit_remediation import _dairy_dam_ready
    from .test_dairy import _dairy_owner

    headers = await _dairy_owner(client, email="legacy-milk@farm.in")
    milker = await _dairy_dam_ready(client, headers, "D-LEGACY-MILK")
    reading = await client.post(
        "/api/milk/new",
        json={
            "animal_id": milker["id"],
            "date": date.today().isoformat(),
            "shift": "MORNING",
            "litres": 8.0,
        },
        headers=headers,
    )
    assert reading.status_code in (200, 201), reading.text
    farm_id = int(headers["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        owner_id = (await db.execute(select(Farm.owner_id).where(Farm.id == farm_id))).scalar_one()
        legacy = Transaction(
            farm_id=farm_id,
            date=date.today() - timedelta(days=200),
            type="INCOME",
            category="MILK",
            amount=Decimal("15000.00"),
            notes="pre-provenance ledger row",
            created_by_id=owner_id,
        )
        db.add(legacy)
        await db.commit()
        legacy_id = legacy.id

    corrected = await client.post(
        f"/api/finance/transactions/{legacy_id}/correct",
        json={
            "date": (date.today() - timedelta(days=200)).isoformat(),
            "type": "INCOME",
            "category": "MILK",
            "amount": 15000,
            "reason": "amount typo",
        },
        headers=headers,
    )
    assert corrected.status_code == 201, corrected.text

    # But a row that CARRIES provenance must not be corrected into dropping it.
    sale = await client.post(
        "/api/finance/new",
        json={
            "date": date.today().isoformat(),
            "type": "INCOME",
            "category": "MILK",
            "amount": 480,
            "milk_litres": 8.0,
            "milk_unit_price_per_litre": 60.0,
        },
        headers=headers,
    )
    assert sale.status_code == 201, sale.text
    dropped = await client.post(
        f"/api/finance/transactions/{sale.json()['id']}/correct",
        json={
            "date": date.today().isoformat(),
            "type": "INCOME",
            "category": "MILK",
            "amount": 480,
            "reason": "tries to drop provenance",
        },
        headers=headers,
    )
    assert dropped.status_code == 422
    assert "provenance" in dropped.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Species reference-data migration reaches existing databases
# ---------------------------------------------------------------------------
def test_species_reference_data_migration_reaches_existing_databases():
    import asyncio

    asyncio.run(_migration_scenario())


async def _migration_scenario():
    """Seed changes are create-only (ON CONFLICT DO NOTHING), so ORF removal,
    PPR 36→12 and the EDD−21→−60 duty re-dating only reach deployed farms via
    migration b5d7f9a1c3e5. This reproduces a pre-migration database and runs
    the real alembic revisions against it."""
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
                      (farm_type, name, first_dose_age_months, booster_weeks,
                       repeat_months, timing_note)
                    VALUES ('GOAT','PPR',3,NULL,36,'Core vaccine; repeat every 3 years'),
                           ('GOAT','ORF',4,NULL,6,'Every 6 months')
                    """
                )
                await conn.execute(
                    """
                    INSERT INTO bucket_definitions
                      (farm_type, code, name, who, exit_rule, daily_kg_per_head, sort_order)
                    VALUES ('GOAT','QUARANTINE','Quarantine Ward',
                            'Newly purchased animals','45-day protocol',0.8,1)
                    """
                )
                await conn.execute(
                    """
                    INSERT INTO users (email, password_hash, created_at)
                    VALUES ('mig@x.io','h',now())
                    """
                )
                await conn.execute(
                    """
                    INSERT INTO farms (name, timezone, farm_type, owner_id, created_at)
                    SELECT 'D','Asia/Kolkata','BUFFALO_DAIRY', id, now() FROM users
                    """
                )
                await conn.execute(
                    """
                    INSERT INTO animals (farm_id, tag_number, sex, breed, source,
                        current_bucket, status, cull_candidate, movement_restricted,
                        suspected_scheduled_disease, created_at)
                    SELECT id,'D-1','F','Murrah','PURCHASED','BREEDING','ACTIVE',
                        false,false,false,now() FROM farms WHERE name='D'
                    """
                )
                await conn.execute(
                    """
                    INSERT INTO breeding_records (farm_id, doe_id, breeding_date, method,
                        heat_cycle_number, ultrasound_done, pregnant, ultrasound_result_date,
                        outcome, expected_kidding_date)
                    SELECT f.id, a.id, CURRENT_DATE - 200, 'AI', 1, true, true,
                        CURRENT_DATE - 140, 'CONFIRMED_PREGNANT', CURRENT_DATE + 120
                    FROM farms f JOIN animals a ON a.farm_id = f.id WHERE f.name = 'D'
                    """
                )
                await conn.execute(
                    """
                    INSERT INTO tasks (farm_id, title, category, status, due_date,
                        breeding_record_id, auto_generated)
                    SELECT f.id, 'Move D-1 to DELIVERY (dry off, calving in ~2 weeks)',
                        'BUCKET_MOVE','PENDING', br.expected_kidding_date - 21, br.id, true
                    FROM farms f, breeding_records br
                    WHERE f.name='D' AND br.farm_id = f.id
                    """
                )
            finally:
                await conn.close()

        dsn = f"{base}/{scratch}".replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(dsn)
        try:
            ppr = await conn.fetchrow(
                "SELECT repeat_months FROM vaccine_templates WHERE farm_type='GOAT' AND name='PPR'"
            )
            assert ppr["repeat_months"] == 12
            orf = await conn.fetchval("SELECT count(*) FROM vaccine_templates WHERE name='ORF'")
            assert orf == 0
            rate = await conn.fetchval(
                "SELECT daily_kg_per_head FROM bucket_definitions "
                "WHERE farm_type='GOAT' AND code='QUARANTINE'"
            )
            assert float(rate) == pytest.approx(1.1)
            duty = await conn.fetchrow(
                """
                SELECT t.due_date, b.expected_kidding_date
                  FROM tasks t JOIN breeding_records b ON b.id = t.breeding_record_id
                 WHERE t.category = 'BUCKET_MOVE'
                """
            )
            assert duty["due_date"] == duty["expected_kidding_date"] - timedelta(days=60)
        finally:
            await conn.close()
    finally:
        await admin_exec(f'DROP DATABASE IF EXISTS "{scratch}"')


# ---------------------------------------------------------------------------
# Dairy due-window suggestion follows the EDD-60 dry-off write path
# ---------------------------------------------------------------------------
async def test_dairy_due_window_suggestion_follows_dry_off_point(client: httpx.AsyncClient):
    from .test_audit_remediation import _dairy_dam_ready
    from .test_dairy import _dairy_owner

    headers = await _dairy_owner(client, email="duewin@farm.in")
    animal = await _dairy_dam_ready(client, headers, "D-DUEWIN")
    breeding_day = date.today() - timedelta(days=255)  # gestation day 255 of ~310
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
    scanned = await client.post(
        f"/api/breeding/{resp.json()['id']}/ultrasound",
        json={"pregnant": True, "date": (breeding_day + timedelta(days=65)).isoformat()},
        headers=headers,
    )
    assert scanned.status_code == 200, scanned.text
    moved = await client.post(
        f"/api/animals/{animal['id']}/move",
        json={"to_bucket": "PREGNANCY_LATE"},
        headers=headers,
    )
    assert moved.status_code == 200, moved.text

    farm_id = int(headers["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        suggestions, _total = await ready_to_move_suggestions(
            db, farm, limit=50, include_breeding=True
        )
    delivery = [s for s in suggestions if s.get("to") == "DELIVERY"]
    # Gestation day 255 is past the species' EDD-60 dry-off point (day 250)
    # but short of the old generic 90% threshold (day 279): the suggestion
    # must fire now, in step with the duty the write path generates.
    assert delivery, f"no DELIVERY suggestion at gestation day 255: {suggestions}"
    assert any("250" in str(s.get("reason", "")) for s in delivery), suggestions


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
