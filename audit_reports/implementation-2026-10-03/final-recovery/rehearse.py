import asyncio,hashlib,json,os,subprocess,time
from pathlib import Path
from uuid import uuid4
import asyncpg

BACKEND=Path('/Users/pradeepreddy/Desktop/goat-farm-management-main/backend')
SOURCE='herdly_e2e_fixes_20261003_test'
TARGET='herdly_recovery_'+uuid4().hex[:12]+'_test'
DUMP=Path('/tmp/herdly-local-recovery-final.dump')

async def facts(connection):
    tables=await connection.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
    counts={row['tablename']:await connection.fetchval('SELECT count(*) FROM "'+row['tablename'].replace('"','""')+'"') for row in tables}
    digests={}
    for table in counts:
        quoted=table.replace('"','""')
        rows=await connection.fetch('SELECT row_to_json(t)::text FROM "'+quoted+'" AS t ORDER BY row_to_json(t)::text')
        digests[table]=hashlib.sha256('\n'.join(row[0] for row in rows).encode()).hexdigest()
    revision=await connection.fetchval('SELECT version_num FROM alembic_version')
    unvalidated=await connection.fetchval("SELECT count(*) FROM pg_constraint WHERE connamespace='public'::regnamespace AND NOT convalidated")
    return counts,revision,unvalidated,digests

async def main():
    before_connection=await asyncpg.connect(f'postgresql://localhost:5432/{SOURCE}')
    try:
        async with before_connection.transaction(isolation='repeatable_read',readonly=True):
            before=await facts(before_connection)
            snapshot=await before_connection.fetchval('SELECT pg_export_snapshot()')
            with DUMP.open('wb') as output:
                result=await asyncio.to_thread(subprocess.run,['docker','exec','goatfarm-audit-pg','pg_dump','-U','pradeepreddy','-d',SOURCE,'--format=custom','--snapshot='+snapshot],stdout=output,stderr=subprocess.PIPE)
            if result.returncode: raise RuntimeError(result.stderr.decode())
    finally:
        await before_connection.close()
    admin=await asyncpg.connect('postgresql://localhost:5432/postgres')
    created=False
    started=time.monotonic()
    try:
        await admin.execute(f'CREATE DATABASE "{TARGET}"');created=True
        with DUMP.open('rb') as source:
            restored=await asyncio.to_thread(subprocess.run,['docker','exec','-i','goatfarm-audit-pg','pg_restore','-U','pradeepreddy','-d',TARGET,'--no-owner','--exit-on-error'],stdin=source,capture_output=True)
        if restored.returncode: raise RuntimeError(restored.stderr.decode())
        connection=await asyncpg.connect(f'postgresql://localhost:5432/{TARGET}')
        try: after=await facts(connection)
        finally: await connection.close()
        assert after==before,'Restored counts/revision/constraint validation differ from the exported snapshot'
        env=os.environ|{'GOATFARM_DATABASE_URL':f'postgresql+asyncpg://localhost:5432/{TARGET}','GOATFARM_MIGRATION_DATABASE_URL':f'postgresql+asyncpg://localhost:5432/{TARGET}'}
        parity=await asyncio.to_thread(subprocess.run,[str(BACKEND/'.venv/bin/python'),'-m','alembic','check'],cwd=BACKEND,env=env,capture_output=True,text=True)
        if parity.returncode: raise RuntimeError(parity.stdout+parity.stderr)
        print(json.dumps({'scope':'Local synthetic PostgreSQL exported-snapshot restore; excludes object-store/config/production RPO/RTO','restored_table_count':len(after[0]),'restored_row_count':sum(after[0].values()),'revision':after[1],'unvalidated_constraints':after[2],'all_snapshot_counts_match':True,'all_row_data_digests_match':True,'table_data_sha256':after[3],'model_parity':parity.stdout.strip(),'restore_and_verify_seconds':round(time.monotonic()-started,3)},indent=2),flush=True)
    finally:
        if created: await admin.execute(f'DROP DATABASE "{TARGET}" WITH (FORCE)')
        await admin.close()
        DUMP.unlink(missing_ok=True)
        print('Owned restore database and dump cleaned',flush=True)

asyncio.run(main())
