import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg

BACKEND = Path('/Users/pradeepreddy/Desktop/goat-farm-management-main/backend')
ARTIFACT = Path('/tmp/herdly-populated-migration-' + uuid4().hex[:12])
ARTIFACT.mkdir()
OLD_HEAD = 'f4a8c2e6d1b9'

async def main():
    admin = await asyncpg.connect('postgresql://localhost:5432/postgres')
    names = {kind: f'herdly_populated_{kind}_{uuid4().hex[:12]}_test' for kind in ('valid', 'preflight')}
    created = []
    try:
        for kind, name in names.items():
            await admin.execute(f'CREATE DATABASE "{name}"')
            created.append(name)
            env = os.environ.copy()
            env['GOATFARM_DATABASE_URL'] = f'postgresql+asyncpg://localhost:5432/{name}'
            env['GOATFARM_MIGRATION_DATABASE_URL'] = env['GOATFARM_DATABASE_URL']
            run = subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', OLD_HEAD], cwd=BACKEND, env=env, capture_output=True, text=True)
            (ARTIFACT / f'{kind}-old-head.log').write_text(run.stdout + run.stderr)
            if run.returncode:
                raise RuntimeError(f'{kind} old-head preparation failed; see {ARTIFACT}')
        connection = await asyncpg.connect(f'postgresql://localhost:5432/{names["valid"]}')
        try:
            rows = await connection.fetch("""
                SELECT table_name, column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name = ANY($1::text[])
                ORDER BY table_name, ordinal_position
            """, ['users', 'farms', 'refresh_sessions', 'screening_images', 'screening_crops', 'screening_runs', 'screening_findings', 'notification_log'])
            schema = [dict(row) for row in rows]
            (ARTIFACT / 'historical-schema.json').write_text(json.dumps(schema, indent=2))
            print(json.dumps(schema, indent=2))
        finally:
            await connection.close()
        manifest = {'artifact': str(ARTIFACT), 'databases': names, 'old_head': OLD_HEAD}
        Path('/tmp/herdly-populated-migration-manifest.json').write_text(json.dumps(manifest, indent=2))
        print('MANIFEST', json.dumps(manifest))
    except BaseException:
        for name in created:
            await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        raise
    finally:
        await admin.close()

asyncio.run(main())
