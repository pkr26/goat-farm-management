"""Run from backend with its venv Python; no database or network calls."""
import os, json
for name in tuple(os.environ):
    if name.startswith('GOATFARM_'):
        del os.environ[name]
os.environ.update({
 'GOATFARM_ENVIRONMENT': 'production',
 'GOATFARM_DATABASE_URL': 'postgresql+asyncpg://worker:synthetic@db.example.invalid/goatfarm',
 'GOATFARM_DB_SSLMODE': 'verify-full',
 'GOATFARM_SCREENING_ENABLED': 'true',
 'GOATFARM_S3_BUCKET': 'audit-synthetic-bucket',
 'GOATFARM_S3_ACCESS_KEY_ID': 'synthetic-access-key',
 'GOATFARM_S3_SECRET_ACCESS_KEY': 'synthetic-secret-key',
 'GOATFARM_SCREENING_ANTHROPIC_API_KEY': 'synthetic-provider-key',
})
from app.core.config import get_screening_worker_settings
from app.models import ScreeningImage
from app.services.screening.pipeline import _record_run
settings = get_screening_worker_settings()
result = {'worker_settings_valid': True, 'production': settings.environment, 'screening_enabled': settings.screening_enabled}
image = ScreeningImage(id=1, farm_id=1)
try:
    run = _record_run(image, stage='gate', provider='anthropic', model='synthetic-model', prompt_version='audit')
    result['record_run_succeeded'] = True
except Exception as exc:
    result['record_run_succeeded'] = False
    result['exception_type'] = type(exc).__name__
    result['error'] = str(exc)
print(json.dumps(result, indent=2))
