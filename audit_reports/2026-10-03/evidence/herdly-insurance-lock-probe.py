import asyncio, datetime as dt, sys
from decimal import Decimal
from uuid import uuid4
sys.path.insert(0, '/Users/pradeepreddy/Desktop/goat-farm-management-main/backend')
import asyncpg
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.db import Base
from app.models import Animal, Farm, InsurancePolicy, User
from app.services.finance import renew_insurance_policy, lapse_policies_for_animal
from app.utils import today
async def main():
    name='herdly_insurance_audit_'+uuid4().hex[:8]+'_test'
    admin=await asyncpg.connect('postgresql://localhost:5432/postgres')
    await admin.execute(f'CREATE DATABASE "{name}"')
    engine=create_async_engine(f'postgresql+asyncpg://localhost:5432/{name}')
    maker=async_sessionmaker(engine,expire_on_commit=False,autoflush=False)
    try:
        async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
        async with maker() as db:
            u=User(email='insurance-audit@audit.invalid',password_hash='placeholder');db.add(u);await db.flush()
            f=Farm(name='Insurance audit',owner_id=u.id,timezone='America/Phoenix');db.add(f);await db.flush()
            a=Animal(farm_id=f.id,tag_number='AUDIT-GOAT',sex='F',source='PURCHASED',current_bucket='FOUNDATION',status='ACTIVE');db.add(a);await db.flush()
            now=today(f.timezone)
            p=InsurancePolicy(farm_id=f.id,animal_id=a.id,policy_number='AUDIT-POLICY',insurer='Audit',sum_insured=Decimal('1000'),premium=Decimal('10'),start_date=now-dt.timedelta(days=30),renewal_date=now+dt.timedelta(days=60));db.add(p);await db.commit()
            fid,aid,pid=f.id,a.id,p.id
        async with maker() as renew_db, maker() as exit_db:
            rf=await renew_db.get(Farm,fid);ef=await exit_db.get(Farm,fid)
            policy=(await renew_db.execute(select(InsurancePolicy).where(InsurancePolicy.id==pid).with_for_update())).scalar_one()
            animal=(await exit_db.execute(select(Animal).where(Animal.id==aid).with_for_update())).scalar_one()
            animal.status='SOLD';animal.status_date=today(ef.timezone)
            async def exit_mutation():
                try:
                    await lapse_policies_for_animal(exit_db,ef,animal)
                    await exit_db.commit()
                    return 'exit committed'
                except Exception as ex:
                    await exit_db.rollback()
                    return f'exit {type(ex).__name__}: {getattr(ex.orig,"sqlstate",None)}'
            async def renewal_mutation():
                try:
                    await renew_insurance_policy(renew_db,rf,policy,renewal_date=policy.renewal_date+dt.timedelta(days=365),premium=None,recorded_by_id=None)
                    await renew_db.commit()
                    return 'renewal committed'
                except Exception as ex:
                    await renew_db.rollback()
                    return f'renewal {type(ex).__name__}: {getattr(ex.orig,"sqlstate",None)}'
            exit_task=asyncio.create_task(exit_mutation())
            await asyncio.sleep(0.1)
            print(await asyncio.wait_for(asyncio.gather(exit_task,renewal_mutation()),10))
    finally:
        await engine.dispose();await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)');await admin.close()
asyncio.run(main())
