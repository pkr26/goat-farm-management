"""Disposable populated f4→f8 rehearsal; no repository or real provider writes."""
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg

BACKEND = Path('/Users/pradeepreddy/Desktop/goat-farm-management-main/backend')
sys.path.insert(0, str(BACKEND))
MANIFEST = json.loads(Path('/tmp/herdly-populated-migration-manifest.json').read_text())
ARTIFACT = Path(MANIFEST['artifact'])
DATABASES = MANIFEST['databases']
OLD_HEAD = 'f4a8c2e6d1b9'
SCOPE_HEAD = 'f5b9d3e7a2c0'
HEAD = 'f8e2f6a0c5d3'
PASSWORD = 'Synthetic-historical-owner-password-1234'
FACT_TABLES = ('farms', 'roles', 'farm_memberships', 'notification_recipients',
               'refresh_sessions', 'screening_images', 'screening_crops',
               'screening_runs', 'screening_findings', 'notification_log')
CHECKS = []

for name in DATABASES.values():
    if not re.fullmatch(r'herdly_populated_(valid|preflight)_[a-f0-9]{12}_test', name):
        raise RuntimeError('Refusing an unowned database name')
os.environ['GOATFARM_DATABASE_URL'] = f'postgresql+asyncpg://localhost:5432/{DATABASES["valid"]}'
os.environ['GOATFARM_MIGRATION_DATABASE_URL'] = os.environ['GOATFARM_DATABASE_URL']
os.environ['GOATFARM_AUTH_RATE_LIMIT_ENABLED'] = 'false'
os.environ['GOATFARM_SCREENING_ENABLED'] = 'false'

def check(condition, label, **details):
    if not condition:
        raise AssertionError(label)
    item = {'check': label, 'passed': True, **details}
    CHECKS.append(item)
    print(json.dumps(item, default=str), flush=True)

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()

async def migrate(kind, target, label, error=None):
    env = os.environ.copy()
    env['GOATFARM_DATABASE_URL'] = f'postgresql+asyncpg://localhost:5432/{DATABASES[kind]}'
    env['GOATFARM_MIGRATION_DATABASE_URL'] = env['GOATFARM_DATABASE_URL']
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'alembic', 'upgrade', target,
        cwd=BACKEND, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    text = output.decode()
    (ARTIFACT / f'{label}.log').write_text(text)
    if error is None:
        check(process.returncode == 0, f'{label}: upgrade succeeded', target=target)
    else:
        check(process.returncode != 0 and error in text, f'{label}: actionable preflight rejected', expected_error=error)
    return text

async def column_map(conn):
    rows = await conn.fetch("""
      SELECT table_name, column_name FROM information_schema.columns
      WHERE table_schema='public' AND table_name = ANY($1::text[])
      ORDER BY table_name, ordinal_position
    """, list(FACT_TABLES))
    result = {}
    for row in rows:
        result.setdefault(row['table_name'], []).append(row['column_name'])
    return result

async def facts(conn, columns):
    result = {}
    for table, names in columns.items():
        select = ', '.join(f'"{name}"' for name in names)
        rows = await conn.fetch(f'SELECT {select} FROM "{table}" ORDER BY id')
        result[table] = [dict(row) for row in rows]
    return result

async def schema(conn):
    queries = {
      'columns': "SELECT table_name,column_name,data_type,is_nullable,column_default FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position",
      'constraints': "SELECT rel.relname, c.conname, c.contype, c.convalidated, pg_get_constraintdef(c.oid) definition FROM pg_constraint c JOIN pg_class rel ON rel.oid=c.conrelid JOIN pg_namespace ns ON ns.oid=rel.relnamespace WHERE ns.nspname='public' ORDER BY rel.relname,c.conname",
      'indexes': "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY tablename,indexname",
      'triggers': "SELECT rel.relname,t.tgname,pg_get_triggerdef(t.oid) definition FROM pg_trigger t JOIN pg_class rel ON rel.oid=t.tgrelid JOIN pg_namespace ns ON ns.oid=rel.relnamespace WHERE ns.nspname='public' AND NOT t.tgisinternal ORDER BY rel.relname,t.tgname",
      'functions': "SELECT p.proname,pg_get_functiondef(p.oid) definition FROM pg_proc p JOIN pg_namespace ns ON ns.oid=p.pronamespace WHERE ns.nspname='public' ORDER BY p.proname",
    }
    return {name: [dict(row) for row in await conn.fetch(query)] for name, query in queries.items()}

async def seed(conn):
    from app.security import hash_password
    instant = await conn.fetchval("SELECT timezone('UTC', now())")
    reviewed = instant - timedelta(minutes=10)
    password_hash = hash_password(PASSWORD)
    async with conn.transaction():
        await conn.execute("INSERT INTO users(id,email,name,password_hash) VALUES(1,'synthetic-owner@rehearsal.test','Synthetic owner',$1),(2,'synthetic-reviewer@rehearsal.test','Original reviewer',$1)", password_hash)
        await conn.execute("INSERT INTO farms(id,name,owner_id,timezone) VALUES(1,'Cutover in-flight farm',1,'Asia/Kolkata'),(2,'Completed calls farm',1,'America/Phoenix'),(3,'No historical calls farm',1,'Pacific/Kiritimati')")
        await conn.execute("INSERT INTO roles(id,farm_id,name,permissions) VALUES(1,2,'Synthetic reviewer','[\"screening.view\"]'::jsonb)")
        await conn.execute("INSERT INTO farm_memberships(id,user_id,farm_id,role_id,is_active) VALUES(1,2,2,1,true)")
        await conn.execute("""INSERT INTO notification_recipients(id,farm_id,membership_id,phone,daily_digest,screening_flags,kidding_watch,overdue_critical,feed_reorder,verified,created_at,updated_at)
          VALUES(1,2,1,'+15555550101',false,true,false,false,false,true,$1,$1)""", instant)
        await conn.execute("""INSERT INTO notification_log(id,farm_id,recipient_id,alert_class,payload_hash,local_date,status,provider_message_id)
          VALUES(1,2,1,'SCREENING_FLAG',$1,$2,'SENT','synthetic-historical-message')""", 'a'*64, instant.date())
        await conn.execute("""INSERT INTO refresh_sessions(id,user_id,jti,family_id,expires_at,created_at,consumed_at,revoked_at,replacement_jti)
          VALUES(1,1,'legacy-active','legacy-family-active',$1,$2,NULL,NULL,NULL),
                (2,1,'legacy-consumed','legacy-family-rotated',$1,$2,$2,NULL,'legacy-successor'),
                (3,1,'legacy-revoked','legacy-family-revoked',$1,$2,NULL,$2,NULL)""", instant + timedelta(days=2), instant)
        await conn.execute("""INSERT INTO screening_images(id,farm_id,s3_bucket,s3_key,status,width,height,sha256,byte_size,screening_attempts)
          VALUES(101,1,'synthetic-local-only','legacy/processing.jpg','PROCESSING',640,480,$1,1024,1),
                (102,1,'synthetic-local-only','legacy/flagged-1.jpg','FLAGGED',640,480,$2,2048,2),
                (201,2,'synthetic-local-only','legacy/flagged-2.jpg','FLAGGED',640,480,$3,3072,1),
                (202,2,'synthetic-local-only','legacy/healthy.jpg','HEALTHY',640,480,$4,4096,1),
                (301,3,'synthetic-local-only','legacy/pending.jpg','PENDING',640,480,$5,5120,0)""", '1'*64, '2'*64, '3'*64, '4'*64, '5'*64)
        await conn.execute("""INSERT INTO screening_crops(id,farm_id,image_id,crop_index,box_x,box_y,box_w,box_h,status)
          VALUES(1001,1,102,0,0,0,100,100,'FLAGGED'),(2001,2,201,0,10,10,100,100,'FLAGGED'),(2002,2,202,0,20,20,100,100,'HEALTHY')""")
        await conn.execute("""INSERT INTO screening_runs(id,farm_id,image_id,crop_id,stage,run_status,verdict,confidence,provider,model,prompt_version,latency_ms,detail,error,created_at)
          VALUES(1001,1,102,1001,'GATE','OK','flagged',0.910,'synthetic','legacy-gate','v1',12,'{\"observation\":\"legacy fact\"}',NULL,$1),
                (5001,2,201,2001,'GATE','OK','flagged',0.920,'synthetic','legacy-gate','v1',13,'{\"observation\":\"original crop\"}',NULL,$1),
                (5002,2,202,NULL,'GATE','ERROR',NULL,NULL,'synthetic','legacy-gate','v1',14,NULL,'synthetic historical provider failure',$1),
                (5003,2,201,NULL,'GATE','OK','flagged',0.810,'synthetic','legacy-gate','v1',15,NULL,NULL,$2)""", instant, instant - timedelta(days=3))
        await conn.execute("""INSERT INTO screening_findings(id,farm_id,run_id,crop_id,region,label,confidence,note,status,severity,reviewed_by_id,reviewed_at,review_note)
          VALUES(9001,2,5001,2001,'mouth','Original pending observation',0.920,'original pending note','PENDING_REVIEW','mild',NULL,NULL,NULL),
                (9002,2,5001,2001,'skin','Original confirmed observation',0.900,'original confirmed note','CONFIRMED','moderate',1,$1,'original owner decision'),
                (9003,2,5001,2001,'hoof','Original rejected observation',0.800,'original rejected note','REJECTED','mild',2,$1,'original reviewer decision'),
                (9004,1,1001,1001,'general','Original processing-farm observation',0.910,'original whole-farm note','PENDING_REVIEW','mild',NULL,NULL,NULL)""", reviewed)
        # Explicit historical fixture IDs must not collide with new runtime inserts.
        for table in ('users', 'farms', 'roles', 'farm_memberships', 'notification_recipients',
                      'notification_log', 'refresh_sessions', 'screening_images', 'screening_crops',
                      'screening_runs', 'screening_findings'):
            await conn.execute(f"SELECT setval(pg_get_serial_sequence('{table}','id'), (SELECT max(id) FROM {table}), true)")
    return reviewed

async def atomic_rejection(conn, baseline_columns, label, expected_head, error):
    before_schema = await schema(conn)
    before_facts = await facts(conn, baseline_columns)
    await migrate('preflight', 'head', label, error)
    after_schema = await schema(conn)
    after_facts = await facts(conn, baseline_columns)
    check(await conn.fetchval('SELECT version_num FROM alembic_version') == expected_head,
          label + ': revision unchanged', revision=expected_head)
    check(before_schema == after_schema, label + ': all schema DDL rolled back', schema_sha256=digest(before_schema))
    check(before_facts == after_facts, label + ': all original data unchanged', data_sha256=digest(before_facts))

async def constraints_after_head(conn):
    cases = [
      ("INSERT INTO screening_findings(farm_id,run_id,crop_id,label,status,reviewed_by_id) VALUES(2,5001,2001,'bad partial','CONFIRMED',1)", '23514', 'new partial review rejected'),
      ("INSERT INTO screening_runs(farm_id,image_id,crop_id,stage,run_status,verdict,provider,model,prompt_version,latency_ms) VALUES(2,202,2001,'GATE','OK','flagged','synthetic','synthetic','v1',1)", '23503', 'new cross-image crop rejected'),
      ("INSERT INTO screening_findings(farm_id,run_id,crop_id,label,status) VALUES(2,5001,2002,'bad finding ancestry','PENDING_REVIEW')", '23514', 'new mismatched finding crop rejected'),
    ]
    for sql, state, label in cases:
        try:
            async with conn.transaction():
                await conn.execute(sql)
        except asyncpg.PostgresError as error:
            check(error.sqlstate == state, label, sqlstate=error.sqlstate)
        else:
            raise AssertionError(label)

async def runtime_checks(conn):
    import httpx
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from app.core.config import get_settings
    from app.db import get_engine
    from app.main import create_app
    from app.security import issue_refresh_token, issue_token
    from app.services.screening.budget import ScreeningBudgetExhausted, reserve_provider_attempt

    settings = get_settings()
    app = create_app()
    legacy_refresh = issue_token(1, 'refresh', 1800, jti='legacy-active', extra_claims={'fid': 'legacy-family-active'})
    legacy_access = issue_token(1, 'access', 1800, extra_claims={'ver': 0})
    explicit_against_legacy_row = issue_refresh_token(1, jti='legacy-active', family_id='legacy-family-active')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        for label, token in [('signed unscoped legacy refresh', legacy_refresh), ('current scope cannot relabel a legacy row', explicit_against_legacy_row)]:
            client.cookies.clear()
            client.cookies.set(settings.refresh_cookie_name, token)
            response = await client.post('/api/auth/refresh')
            check(response.status_code == 401, label + ': API rejects', status=response.status_code)
        response = await client.get('/api/auth/permissions', headers={'Authorization': 'Bearer ' + legacy_access, 'X-Farm-Id': '2'})
        check(response.status_code == 401, 'signed unscoped legacy access: API rejects', status=response.status_code)
        client.cookies.clear()
        response = await client.post('/api/auth/login', json={'email': 'synthetic-owner@rehearsal.test', 'password': PASSWORD})
        check(response.status_code == 200, 'fresh password reauthentication works', status=response.status_code)
        access = response.json()['access_token']
        response = await client.get('/api/auth/permissions', headers={'Authorization': 'Bearer ' + access, 'X-Farm-Id': '2'})
        check(response.status_code == 200 and response.json()['is_owner'], 'fresh session retains legitimate farm authority')
        response = await client.post('/api/auth/refresh')
        check(response.status_code == 200, 'fresh scoped password refresh rotates', status=response.status_code)
    check(await conn.fetchval("SELECT count(*) FROM refresh_sessions WHERE id<=3 AND session_origin IS NULL AND farm_id IS NULL AND membership_id IS NULL") == 3,
          'legacy sessions remain unknown, never fabricated PASSWORD/PIN')
    check(await conn.fetchval("SELECT count(*) FROM refresh_sessions WHERE id>3 AND session_origin='PASSWORD' AND farm_id IS NULL AND membership_id IS NULL") == 2,
          'new password family retains explicit scope through rotation')

    now = await conn.fetchval("SELECT timezone('UTC', now())")
    hold = await conn.fetchrow("SELECT local_date,reserved_calls FROM screening_daily_budgets WHERE farm_id=1")
    expected_local_date = await conn.fetchval("SELECT (now() AT TIME ZONE 'Asia/Kolkata')::date")
    check(hold is not None and hold['reserved_calls'] == 2147483647 and hold['local_date'] == expected_local_date,
          'in-flight cutover farm is conservatively held through its local day', **dict(hold))
    check(await conn.fetchval('SELECT count(*) FROM screening_daily_budgets WHERE farm_id IN (2,3)') == 0,
          'migration does not invent allowance or ledger for completed/empty farms')
    engine = create_async_engine(settings.database_url)
    try:
        async with AsyncSession(engine) as parent:
            try:
                await reserve_provider_attempt(parent, farm_id=1, timezone_name='Asia/Kolkata', provider='synthetic-no-network', cap=10, now=now)
            except ScreeningBudgetExhausted:
                check(True, 'held farm cannot admit an outbound attempt at cutover')
            else:
                raise AssertionError('In-flight hold admitted a provider attempt')
            attempt = str(uuid4())
            # Exercise an actual pending image transaction, not a no-op
            # rollback on an otherwise unused parent session.
            await parent.execute(text('UPDATE screening_images SET screening_attempts=screening_attempts+1 WHERE id=201'))
            returned = await reserve_provider_attempt(parent, farm_id=2, timezone_name='America/Phoenix', provider='synthetic-no-network', cap=10, attempt_id=attempt, now=now)
            await parent.rollback()
            check(await conn.fetchval('SELECT screening_attempts FROM screening_images WHERE id=201') == 1,
                  'parent image update rolled back while independent reservation stayed durable')
            check(returned == attempt and await conn.fetchval('SELECT reserved_calls FROM screening_daily_budgets WHERE farm_id=2') == 5,
                  'first reservation charges two attempts per same-local-day legacy run plus one durable attempt', old_runs_today=2, reserved_calls=5)
            repeated = await reserve_provider_attempt(parent, farm_id=2, timezone_name='America/Phoenix', provider='synthetic-no-network', cap=10, attempt_id=attempt, now=now)
            check(repeated == attempt and await conn.fetchval('SELECT reserved_calls FROM screening_daily_budgets WHERE farm_id=2') == 5,
                  'same attempt identity is idempotent after parent rollback')
            await reserve_provider_attempt(parent, farm_id=3, timezone_name='Pacific/Kiritimati', provider='synthetic-no-network', cap=1, now=now)
            try:
                await reserve_provider_attempt(parent, farm_id=3, timezone_name='Pacific/Kiritimati', provider='synthetic-no-network', cap=1, now=now)
            except ScreeningBudgetExhausted:
                check(await conn.fetchval('SELECT reserved_calls FROM screening_daily_budgets WHERE farm_id=3') == 1,
                      'empty farm starts with its first real reservation and cap stops the next')
            else:
                raise AssertionError('Budget exceeded its cap')
    finally:
        await engine.dispose()
        await get_engine().dispose()
    check(await conn.fetchval('SELECT count(*) FROM screening_call_reservations') == 2,
          'no rejected call fabricated a provider reservation', reservation_count=2)

async def main():
    connections = {}
    admin = await asyncpg.connect('postgresql://localhost:5432/postgres')
    original = {}
    baseline_columns = {}
    source_hashes = {str(path.relative_to(BACKEND)): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in BACKEND.joinpath('alembic').rglob('*.py')}
    try:
        for kind, name in DATABASES.items():
            connections[kind] = await asyncpg.connect(f'postgresql://localhost:5432/{name}')
            check(await connections[kind].fetchval('SELECT version_num FROM alembic_version') == OLD_HEAD,
                  kind + ': starts at old historical head', revision=OLD_HEAD)
            await seed(connections[kind])
            baseline_columns[kind] = await column_map(connections[kind])
            original[kind] = await facts(connections[kind], baseline_columns[kind])
            check(len(original[kind]['screening_findings']) == 4 and len(original[kind]['refresh_sessions']) == 3,
                  kind + ': populated representative historical fixtures', row_counts={table: len(rows) for table, rows in original[kind].items()})
        valid = connections['valid']
        invalid = connections['preflight']
        await migrate('valid', 'head', 'valid-f4-to-f8')
        check(await valid.fetchval('SELECT version_num FROM alembic_version') == HEAD, 'valid populated DB reaches f8 head', revision=HEAD)
        check(await facts(valid, baseline_columns['valid']) == original['valid'],
              'valid populated upgrade preserves every original fact column exactly', data_sha256=digest(original['valid']))
        check(await valid.fetchval('SELECT count(*) FROM screening_findings WHERE review_revision=0') == 4,
              'legacy review decisions stay revision zero, no reviewer/history invented')
        for table in ('screening_finding_reviews', 'health_rounds', 'health_round_targets', 'health_round_coverage', 'health_round_exclusions', 'notification_outbox'):
            check(await valid.fetchval(f'SELECT count(*) FROM {table}') == 0, table + ': no historical fact fabricated')
        check(await valid.fetchval('SELECT outbox_id FROM notification_log WHERE id=1') is None,
              'legacy notification dedupe remains unlinked with original SENT fact')

        await invalid.execute('UPDATE screening_findings SET reviewed_at=NULL WHERE id=9002')
        await atomic_rejection(invalid, baseline_columns['preflight'], 'partial-review-f4-to-head-rejected', OLD_HEAD,
            'Clinical preflight: inconsistent screening review metadata. Export and reconcile original records; no reviewer is fabricated.')
        original_review = next(row for row in original['preflight']['screening_findings'] if row['id'] == 9002)['reviewed_at']
        await invalid.execute('UPDATE screening_findings SET reviewed_at=$1 WHERE id=9002', original_review)
        await migrate('preflight', SCOPE_HEAD, 'reconciled-review-to-f5')
        await invalid.execute('UPDATE screening_runs SET image_id=202 WHERE id=5001')
        await atomic_rejection(invalid, baseline_columns['preflight'], 'wrong-run-image-crop-f5-to-head-rejected', SCOPE_HEAD,
            'Clinical preflight: inconsistent run/image/crop/finding ancestry. Export and reconcile original provenance before retrying.')
        await invalid.execute('UPDATE screening_runs SET image_id=201 WHERE id=5001')
        await invalid.execute('UPDATE screening_findings SET crop_id=2002 WHERE id=9001')
        await atomic_rejection(invalid, baseline_columns['preflight'], 'wrong-finding-crop-f5-to-head-rejected', SCOPE_HEAD,
            'Clinical preflight: inconsistent run/image/crop/finding ancestry. Export and reconcile original provenance before retrying.')
        await invalid.execute('UPDATE screening_findings SET crop_id=2001 WHERE id=9001')
        await migrate('preflight', 'head', 'reconciled-provenance-to-f8')
        check(await invalid.fetchval('SELECT version_num FROM alembic_version') == HEAD, 'reconciled populated DB reaches f8 head', revision=HEAD)
        check(await facts(invalid, baseline_columns['preflight']) == original['preflight'],
              'reconciled upgrade preserves/restores all original synthetic facts exactly', data_sha256=digest(original['preflight']))
        checkpoints = await valid.fetch('SELECT job_name, after_farm_id, completed_hour FROM maintenance_progress ORDER BY job_name')
        check([r['job_name'] for r in checkpoints] == ['cadence', 'notification_alerts'], 'only the two declared maintenance jobs are seeded')
        check(all(r['after_farm_id'] == 0 for r in checkpoints), 'initial checkpoints claim no farm processing')
        check(all(r['completed_hour'] is None for r in checkpoints), 'initial checkpoints claim no completed notification hour')
        await constraints_after_head(valid)
        await runtime_checks(valid)
        check(source_hashes == {str(path.relative_to(BACKEND)): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in BACKEND.joinpath('alembic').rglob('*.py')}, 'repository migration source unchanged')
        (ARTIFACT / 'summary.json').write_text(json.dumps({'old_head': OLD_HEAD, 'head': HEAD, 'checks': CHECKS}, indent=2, default=str))
    finally:
        for conn in connections.values():
            await conn.close()
        for name in DATABASES.values():
            await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        existing = await admin.fetch('SELECT datname FROM pg_database WHERE datname = ANY($1::text[])', list(DATABASES.values()))
        check(not existing, 'both owned disposable databases dropped', databases=list(DATABASES.values()))
        await admin.close()
        (ARTIFACT / 'summary.json').write_text(json.dumps({'old_head': OLD_HEAD, 'head': HEAD, 'checks': CHECKS}, indent=2, default=str))
        print('ARTIFACT', ARTIFACT, flush=True)

asyncio.run(main())
