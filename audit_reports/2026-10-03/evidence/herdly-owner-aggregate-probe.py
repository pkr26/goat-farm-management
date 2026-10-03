import asyncio,datetime as dt,sys
from decimal import Decimal
from uuid import uuid4
sys.path.insert(0,'/Users/pradeepreddy/Desktop/goat-farm-management-main/backend')
import asyncpg
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.db import Base
from app.models import Animal,Farm,Transaction,User,WeightRecord
from app.api.owner import owner_overview,owner_benchmarks
from app.utils import today,utcnow
async def main():
    name='herdly_owner_audit_'+uuid4().hex[:8]+'_test';admin=await asyncpg.connect('postgresql://localhost:5432/postgres');await admin.execute(f'CREATE DATABASE "{name}"');engine=create_async_engine(f'postgresql+asyncpg://localhost:5432/{name}');maker=async_sessionmaker(engine,expire_on_commit=False,autoflush=False)
    try:
        async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
        async with maker() as db:
            u=User(email='owner@audit.invalid',password_hash='placeholder');db.add(u);await db.flush();f=Farm(name='Owner audit',owner_id=u.id);db.add(f);await db.flush();now=today(f.timezone)
            animal=Animal(farm_id=f.id,tag_number='LOSING-WEIGHT',sex='F',source='PURCHASED',current_bucket='FOUNDATION',status='ACTIVE');db.add(animal);await db.flush()
            db.add_all([WeightRecord(animal_id=animal.id,date=now-dt.timedelta(days=10),weight_kg=20),WeightRecord(animal_id=animal.id,date=now,weight_kg=15)])
            original=Transaction(farm_id=f.id,date=now,type='EXPENSE',category='FEED',amount=Decimal('1000'),voided_at=utcnow(),voided_by_id=u.id,void_reason='Audit correction');db.add(original);await db.flush();db.add(Transaction(farm_id=f.id,date=now,type='EXPENSE',category='FEED',amount=Decimal('600'),correction_of_id=original.id));await db.commit()
            overview=await owner_overview(db,u);benchmark=await owner_benchmarks(db,u,days=90)
            print({'active_expense':600,'owner_month_expense':str(overview.farms[0].month_expense),'true_daily_gain':-.5,'owner_avg_daily_gain':benchmark.farms[0].avg_daily_gain_kg,'owner_feed_cost_per_kg_gain':benchmark.farms[0].feed_cost_per_kg_gain})
    finally:
        await engine.dispose();await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)');await admin.close()
asyncio.run(main())
