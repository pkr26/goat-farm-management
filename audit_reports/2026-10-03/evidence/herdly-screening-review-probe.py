import asyncio,sys
from uuid import uuid4
sys.path.insert(0,'/Users/pradeepreddy/Desktop/goat-farm-management-main/backend')
import asyncpg
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.db import Base
from app.models import Farm,User,ScreeningImage,ScreeningRun,ScreeningFinding
from app.schemas.screening import ScreeningFindingReviewIn
from app.api.screening import review_finding
async def main():
    name='herdly_review_audit_'+uuid4().hex[:8]+'_test';admin=await asyncpg.connect('postgresql://localhost:5432/postgres');await admin.execute(f'CREATE DATABASE "{name}"');engine=create_async_engine(f'postgresql+asyncpg://localhost:5432/{name}');maker=async_sessionmaker(engine,expire_on_commit=False,autoflush=False)
    try:
        async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
        async with maker() as db:
            u=User(email='review@audit.invalid',password_hash='placeholder');db.add(u);await db.flush();f=Farm(name='Review audit',owner_id=u.id);db.add(f);await db.flush();im=ScreeningImage(farm_id=f.id,s3_bucket='audit',s3_key='audit.jpg',status='FLAGGED');db.add(im);await db.flush();run=ScreeningRun(farm_id=f.id,image_id=im.id,stage='GATE',run_status='OK',verdict='flagged',provider='audit',model='audit',prompt_version='audit',latency_ms=1);db.add(run);await db.flush();finding=ScreeningFinding(farm_id=f.id,run_id=run.id,label='audit');db.add(finding);await db.commit();fid,uid,finding_id=f.id,u.id,finding.id
        for note in ('abc\0def',):
            async with maker() as db:
                f=await db.get(Farm,fid);u=await db.get(User,uid)
                payload=ScreeningFindingReviewIn(status='REJECTED',review_note=note)
                try: await review_finding(finding_id,payload,db,f,u,{'health.manage'})
                except Exception as ex:
                    print('invalid note accepted by schema, route failure:',type(ex).__name__,getattr(getattr(ex,'orig',None),'sqlstate',None));await db.rollback()
        async with maker() as db:
            f=await db.get(Farm,fid);u=await db.get(User,uid)
            await review_finding(finding_id,ScreeningFindingReviewIn(status='REJECTED',review_note='initial'),db,f,u,{'health.manage'})
            first=await review_finding(finding_id,ScreeningFindingReviewIn(status='REJECTED',expected_status='REJECTED',review_note='reviewer A'),db,f,u,{'health.manage'})
            second=await review_finding(finding_id,ScreeningFindingReviewIn(status='REJECTED',expected_status='REJECTED',review_note='stale reviewer B'),db,f,u,{'health.manage'})
            print('same expected status stale edits both succeed:',first.review_note,second.review_note)
    finally:
        await engine.dispose();await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)');await admin.close()
asyncio.run(main())
