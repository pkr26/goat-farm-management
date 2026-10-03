import asyncio,sys,logging
from uuid import uuid4
sys.path.insert(0,'/Users/pradeepreddy/Desktop/goat-farm-management-main/backend')
import asyncpg
from sqlalchemy import select,func
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.db import Base
from app.models import Animal,Farm,User,Task
from app.services import cadence
logging.disable(logging.CRITICAL)
async def main():
    name='herdly_cadence_audit_'+uuid4().hex[:8]+'_test';admin=await asyncpg.connect('postgresql://localhost:5432/postgres');await admin.execute(f'CREATE DATABASE "{name}"');engine=create_async_engine(f'postgresql+asyncpg://localhost:5432/{name}');maker=async_sessionmaker(engine,expire_on_commit=False,autoflush=False)
    try:
        async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
        async with maker() as db:
            u=User(email='cadence@audit.invalid',password_hash='placeholder');db.add(u);await db.flush()
            for i in range(3):
                f=Farm(name=f'Farm {i}',owner_id=u.id);db.add(f);await db.flush();db.add(Animal(farm_id=f.id,tag_number=f'GOAT-{i}',sex='F',source='PURCHASED',current_bucket='FOUNDATION',status='ACTIVE'))
            await db.commit()
        faults=[];count=0;original=cadence.ensure_cadence_tasks
        async def fail_first(db,farm):
            nonlocal count
            count+=1
            if count==1:raise RuntimeError('Injected first-farm failure')
            try:return await original(db,farm)
            except Exception as ex:faults.append(type(ex).__name__);raise
        cadence.ensure_cadence_tasks=fail_first
        try:
            async with maker() as db:
                result=await cadence.ensure_cadence_farm_batch(db,batch_size=10)
                tasks=list((await db.execute(select(Task.farm_id,func.count()).group_by(Task.farm_id))).all())
                print({'batch_result':result,'higher_farm_errors':faults,'generated_tasks_by_farm':tasks})
        finally:cadence.ensure_cadence_tasks=original
    finally:
        await engine.dispose();await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)');await admin.close()
asyncio.run(main())
