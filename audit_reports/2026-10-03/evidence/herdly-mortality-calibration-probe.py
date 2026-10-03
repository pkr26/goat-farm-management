import asyncio, datetime as dt, sys
from decimal import Decimal
from uuid import uuid4
sys.path.insert(0, '/Users/pradeepreddy/Desktop/goat-farm-management-main/backend')
import asyncpg
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.db import Base
from app.models import Animal, Farm, User, WeightRecord
from app.services.simulation_calibration import calibrate_farm_assumptions
from app.utils import today
async def main():
    name='herdly_mortality_audit_'+uuid4().hex[:8]+'_test'
    admin=await asyncpg.connect('postgresql://localhost:5432/postgres')
    await admin.execute(f'CREATE DATABASE "{name}"');engine=create_async_engine(f'postgresql+asyncpg://localhost:5432/{name}')
    maker=async_sessionmaker(engine,expire_on_commit=False,autoflush=False)
    try:
        async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
        async with maker() as db:
            u=User(email='sale-audit@audit.invalid',password_hash='placeholder');db.add(u);await db.flush()
            farm=Farm(name='Sale audit',owner_id=u.id,timezone='America/Phoenix');db.add(farm);await db.flush();now=today(farm.timezone)
            for i in range(10):
                animal=Animal(farm_id=farm.id,tag_number=f'AUDIT-{i}',sex='F',source='PURCHASED',current_bucket='FOUNDATION',status='DEAD' if i==0 else 'ACTIVE',date_of_birth=now-dt.timedelta(days=180),status_date=now-dt.timedelta(days=1) if i==0 else None);db.add(animal);await db.flush()

            await db.commit()
            out=await calibrate_farm_assumptions(db,farm,breed='osmanabadi',system='stall_fed',lookback_months=24)
            evidence=[x.model_dump(mode='json') for x in out.evidence if x.path=='mortality.kid_post_weaning']
            print({'observed_deaths':1,'exposed_animals':10,'calibrated_phase_rate':out.assumptions.mortality.kid_post_weaning,'correct_phase_rate':1-(1-out.assumptions.mortality.kid_post_weaning)**.25,'evidence':evidence})
    finally:
        await engine.dispose();await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)');await admin.close()
asyncio.run(main())
