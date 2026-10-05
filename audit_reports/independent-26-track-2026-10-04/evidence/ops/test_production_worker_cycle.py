"""Audit-only actual pipeline probe; ephemeral PostgreSQL + in-memory providers/storage."""
import datetime as dt
import json
import os
from pathlib import Path
import pytest
from sqlalchemy import select, func, update
from app.core.config import get_settings, get_screening_worker_settings
from app.db import get_sessionmaker
from app.models import ScreeningImage, ScreeningRun, ScreeningCallReservation
from app.services.screening.pipeline import run_screening_cycle
from app.services.screening.rotation import ProviderRotation
from app.utils import utcnow
from tests.conftest import owner_with_farm
from tests.test_screening import FakeStorage, CountingProvider, _jpeg_bytes, _register_fake_objects

pytest_plugins = ['tests.conftest']

@pytest.mark.parametrize('provider_fails', [False, True])
async def test_production_worker_actual_cycle(client, monkeypatch, provider_fails):
    headers = await owner_with_farm(client, email='audit-worker@synthetic.invalid')
    farm_id = int(headers['X-Farm-Id'])
    storage = FakeStorage()
    storage.objects[f'raw/{farm_id}/{dt.date.today().isoformat()}/tall.jpg'] = _jpeg_bytes(80,160)
    provider = CountingProvider(name='anthropic', fail=provider_fails)
    async with get_sessionmaker()() as db:
        await _register_fake_objects(db, farm_id, storage)
    observations=[]
    # The established isolated test pool remains TLS-off. Only configuration
    # validation is production; no production network/database is contacted.
    try:
      with monkeypatch.context() as m:
        for name in tuple(os.environ):
          if name.startswith('GOATFARM_'): m.delenv(name)
        for name,value in {
          'GOATFARM_ENVIRONMENT':'production',
          'GOATFARM_DATABASE_URL':'postgresql+asyncpg://worker:synthetic@db.example.invalid/goatfarm',
          'GOATFARM_DB_SSLMODE':'verify-full',
          'GOATFARM_SCREENING_ENABLED':'true',
          'GOATFARM_SCREENING_CROP_DETECTION_ENABLED':'false',
          'GOATFARM_S3_BUCKET':'goat-photos',
          'GOATFARM_S3_ACCESS_KEY_ID':'synthetic-access-key',
          'GOATFARM_S3_SECRET_ACCESS_KEY':'synthetic-secret-key',
          'GOATFARM_SCREENING_ANTHROPIC_API_KEY':'synthetic-provider-key',
        }.items():m.setenv(name,value)
        get_settings.cache_clear();get_screening_worker_settings.cache_clear()
        settings=get_screening_worker_settings()
        for iteration in range(5):
          async with get_sessionmaker()() as db:
            summary=await run_screening_cycle(db,settings,storage,ProviderRotation([provider]))
            image=(await db.execute(select(ScreeningImage))).scalar_one()
            runs=(await db.execute(select(func.count()).select_from(ScreeningRun))).scalar_one()
            charges=(await db.execute(select(func.count()).select_from(ScreeningCallReservation))).scalar_one()
            observations.append({'attempt':iteration+1,'summary_claimed':summary.claimed,'summary_errors':summary.errors,'status':image.status,'error':image.error,'screening_attempts':image.screening_attempts,'provider_calls':provider.calls,'durable_runs':runs,'durable_call_reservations':charges})
            if iteration<4:
              await db.execute(update(ScreeningImage).values(updated_at=utcnow()-dt.timedelta(hours=2)))
              await db.commit()
    finally:
      get_settings.cache_clear();get_screening_worker_settings.cache_clear()
    out=Path(__file__).with_name('worker-cycle-provider-'+('failure' if provider_fails else 'success')+'.json')
    out.write_text(json.dumps(observations,indent=2)+'\n')
    assert observations[0]['provider_calls']==1
    assert observations[0]['status']=='ERROR'
    assert observations[0]['durable_runs']==0
    assert observations[-1]['screening_attempts']==5
    assert 'terminal after 5 attempts' in observations[-1]['error']
