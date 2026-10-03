import asyncio,datetime as dt,sys
from decimal import Decimal
from uuid import uuid4
sys.path.insert(0,'/Users/pradeepreddy/Desktop/goat-farm-management-main/backend')
import asyncpg
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.db import Base
from app.models import Animal,Farm,Transaction,User,WeightRecord
import app.schemas.animals
from app.api.dashboard import dashboard, reports
from app.utils import today,utcnow
async def main():
    name='herdly_dashboard_perm_'+uuid4().hex[:8]+'_test';admin=await asyncpg.connect('postgresql://localhost:5432/postgres');await admin.execute(f'CREATE DATABASE "{name}"');engine=create_async_engine(f'postgresql+asyncpg://localhost:5432/{name}');maker=async_sessionmaker(engine,expire_on_commit=False,autoflush=False)
    try:
        async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
        async with maker() as db:
            u=User(email='owner@audit.invalid',password_hash='placeholder');db.add(u);await db.flush();f=Farm(name='Owner audit',owner_id=u.id);db.add(f);await db.flush();now=today(f.timezone)
            animal=Animal(farm_id=f.id,tag_number='LOSING-WEIGHT',sex='F',source='PURCHASED',current_bucket='FOUNDATION',status='ACTIVE');db.add(animal);await db.flush()
            db.add_all([WeightRecord(animal_id=animal.id,date=now-dt.timedelta(days=10),weight_kg=20),WeightRecord(animal_id=animal.id,date=now,weight_kg=15)])
            original=Transaction(farm_id=f.id,date=now,type='EXPENSE',category='FEED',amount=Decimal('1000'),voided_at=utcnow(),voided_by_id=u.id,void_reason='Audit correction');db.add(original);await db.flush();db.add(Transaction(farm_id=f.id,date=now,type='EXPENSE',category='FEED',amount=Decimal('600'),correction_of_id=original.id));await db.commit()
            d=await dashboard(db,u,f,{'dashboard.view'});r=await reports(db,f,{'reports.view'})
            print({'dashboard_total_active':d.total_active,'dashboard_status_totals':d.status_totals,'reports_total_active':r.total_active,'reports_status_counts':r.status_counts})
    finally:
        await engine.dispose();await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)');await admin.close()
asyncio.run(main())
