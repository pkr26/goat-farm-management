"""Read-only-to-project probes; database is unique, transient, and removed."""
import asyncio
import datetime as dt
import io
import json
import sys
from types import SimpleNamespace
from uuid import uuid4

sys.path.insert(0, "/Users/pradeepreddy/Desktop/goat-farm-management-main/backend")
import asyncpg
import httpx
from PIL import Image
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.db import Base
from app.models import Farm, FeedInventory, ScreeningImage, ScreeningRun, Task, User
from app.services.notifications import service as notifications
from app.services.screening.pipeline import run_screening_cycle
from app.services.screening.providers import OpenAICompatibleProvider, ProviderAnswer
from app.services.screening.rotation import ProviderRotation
from app.services.screening.s3 import ScreeningObjectInfo
from app.simulation.finance import irr, irr_roots, npv
from app.utils import today


async def provider_probe():
    bodies = []
    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
    settings = SimpleNamespace(screening_openai_api_key=SecretStr("audit-fake-key"),
        screening_openai_model="audit-model", screening_openai_base_url="https://audit.invalid",
        screening_provider_timeout_seconds=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(settings, client=client)
        for prompt in ("DETECTION-SENTINEL", "VETERINARY-GATE-SENTINEL", "SPECIALIST-SENTINEL"):
            await provider.complete(b"jpeg", prompt)
    print("SCREENING_PROMPTS", json.dumps({"different_stage_requests_identical":
        bodies[0] == bodies[1] == bodies[2], "any_prompt_sent":
        any("SENTINEL" in json.dumps(body) for body in bodies)}))


class MockStorage:
    bucket = "audit-bucket"
    def __init__(self): self.objects = {}
    def object_info(self, key):
        data = self.objects.get(key)
        return None if data is None else ScreeningObjectInfo(size=len(data), etag="audit-etag", version_id=None,
            content_type="image/jpeg", metadata={})
    def download(self, key, **kwargs): return self.objects[key]
    def upload(self, key, data, content_type): self.objects[key] = data


class CountingQualityProvider:
    name = "audit-provider"
    model = "audit-model"
    def __init__(self): self.calls = 0
    async def complete(self, jpeg, system_prompt):
        self.calls += 1
        return ProviderAnswer(text=json.dumps({"flagged": False, "confidence": 0.1,
            "quality_problem": True, "observations": []}), provider=self.name, model=self.model,
            latency_ms=1)


async def db_probes():
    name = "herdly_domain_audit_" + uuid4().hex[:10] + "_test"
    admin = await asyncpg.connect("postgresql://localhost:5432/postgres", timeout=3)
    await admin.execute(f'CREATE DATABASE "{name}"')
    engine = create_async_engine(f"postgresql+asyncpg://localhost:5432/{name}")
    try:
        async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with maker() as db:
            owner = User(email="audit@audit.invalid", password_hash="audit-placeholder")
            db.add(owner); await db.flush()
            farm = Farm(name="Isolated audit farm", owner_id=owner.id, timezone="America/Phoenix")
            db.add(farm); await db.flush()
            storage = MockStorage()
            for index in range(10):
                buffer = io.BytesIO()
                Image.new("RGB", (32, 32), (index * 20, 80, 150)).save(buffer, format="JPEG")
                key = f"raw/{farm.id}/2026-10-03/{index}.jpg"
                storage.objects[key] = buffer.getvalue()
                db.add(ScreeningImage(farm_id=farm.id, bucket="BREEDING", s3_bucket=storage.bucket,
                    s3_key=key, status="PENDING"))
            await db.commit()
            settings = SimpleNamespace(screening_max_images_per_cycle=10,
                screening_presign_expiry_seconds=900, screening_stale_processing_after_seconds=1800,
                screening_daily_call_budget_per_farm=8, screening_image_max_edge_px=1024,
                screening_crop_detection_enabled=False, screening_max_crops_per_image=5)
            provider = CountingQualityProvider()
            summary = await run_screening_cycle(db, settings, storage, ProviderRotation([provider]))
            runs = int((await db.execute(select(func.count()).select_from(ScreeningRun))).scalar_one())
            statuses = list((await db.execute(select(ScreeningImage.status))).scalars())
            print("SCREENING_BUDGET_AND_QUALITY", json.dumps({"daily_budget":8,
                "claimed":summary.claimed,"actual_provider_calls":provider.calls,"run_rows":runs,
                "all_model_answers_quality_problem":True,"persisted_statuses":statuses}))
            reference = today(farm.timezone)
            for index in range(12):
                db.add(FeedInventory(farm_id=farm.id, ingredient=f"audit-item-{index:02d}", category="CONCENTRATE",
                    qty_on_hand=0, reorder_level=10))
            for index in range(70):
                db.add(Task(farm_id=farm.id, title=f"Audit duty {index}", due_date=reference-dt.timedelta(days=4),
                    category="OTHER",status="PENDING"))
            await db.commit()
            captured = []
            async def capture(*args, **kwargs): captured.append(kwargs["message"]); return 0
            original = notifications.notify_alert_class
            notifications.notify_alert_class = capture
            try:
                await notifications.feed_reorder_daily(db, settings, provider, farm)
                await notifications.overdue_critical_sweep(db, settings, provider, farm)
            finally: notifications.notify_alert_class = original
            print("NOTIFICATION_UNDERCOUNTS", json.dumps({"actual_low_stock_items":12,
                "actual_critical_overdue_duties":70,"messages":captured}))
    finally:
        await engine.dispose()
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()


async def main():
    await provider_probe()
    flows = [-1.08, 3.27, -3.2, 1.0]
    times = [0.0, 0.5, 1.0, 1.5]
    exact_rates = [1/(0.8**2)-1, 1/(0.9**2)-1, 1/(1.5**2)-1]
    print("IRR_FALSE_UNIQUENESS", json.dumps({"irr":irr(flows,times), "roots_reported":irr_roots(flows,times),
        "known_exact_roots":exact_rates,"npv_at_exact_roots":[npv(r,flows,times) for r in exact_rates]}))
    await db_probes()


asyncio.run(main())
