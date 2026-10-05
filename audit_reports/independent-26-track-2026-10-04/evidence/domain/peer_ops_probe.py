"""Independent audit peer check; process-local operations, no database/network I/O."""
import json, os, pathlib, subprocess, sys
root = pathlib.Path(__file__).resolve().parents[4]
base = {k:v for k,v in os.environ.items() if not k.startswith('GOATFARM_')}
base.update(GOATFARM_TEST_DB='goatfarm_test_a26_domain_peer', GOATFARM_DATABASE_URL='postgresql+asyncpg://audit:synthetic@db.example.invalid/goatfarm_test_a26_domain_peer', GOATFARM_MIGRATION_DATABASE_URL='postgresql+asyncpg://audit:synthetic@db.example.invalid/goatfarm_test_a26_domain_peer', PYTHONPATH=str(root/'backend'))
prod = dict(base, GOATFARM_ENVIRONMENT='production', GOATFARM_DB_SSLMODE='verify-full', GOATFARM_SCREENING_ENABLED='true', GOATFARM_S3_BUCKET='audit-synthetic', GOATFARM_S3_ACCESS_KEY_ID='synthetic', GOATFARM_S3_SECRET_ACCESS_KEY='synthetic', GOATFARM_SCREENING_ANTHROPIC_API_KEY='synthetic')
code = """import json
from app.core.config import get_screening_worker_settings
from app.models import ScreeningImage
from app.services.screening.pipeline import _record_run
settings = get_screening_worker_settings()
result={'worker_settings_valid':True}
try:
 _record_run(ScreeningImage(id=1,farm_id=1), stage='gate',provider='anthropic',model='synthetic',prompt_version='audit')
 result['record_run_succeeded']=True
except Exception as exc:
 result.update(record_run_succeeded=False,error_type=type(exc).__name__,error=str(exc))
print(json.dumps(result))
"""
def run(code, env):
 proc=subprocess.run([sys.executable, '-c', code],cwd=root/'backend',env=env,text=True,capture_output=True,check=True)
 return proc.stdout
results={'worker_production_default':json.loads(run(code,prod)), 'worker_production_metrics_disabled':json.loads(run(code,dict(prod,GOATFARM_METRICS_ENABLED='false')))}
dev=dict(base,GOATFARM_ENVIRONMENT='development')
worker=run('from app import metrics; metrics.record_screening_provider_call("anthropic", True); print(metrics.render().decode())',dev)
api=run('from app import metrics; print(metrics.render().decode())',dev)
results['metrics_processes']={'worker_samples':[x for x in worker.splitlines() if x.startswith('goatfarm_screening_provider_calls_total')], 'api_samples':[x for x in api.splitlines() if x.startswith('goatfarm_screening_provider_calls_total')]}
for name in ['backup_freshness','release_cleanup']:
 proc=subprocess.run([sys.executable, str(root/f'audit_reports/independent-26-track-2026-10-04/evidence/ops/probe_{name}.py')],cwd=root,env=base,text=True,capture_output=True,check=True)
 results[name]=json.loads(proc.stdout)
print(json.dumps(results,indent=2))
