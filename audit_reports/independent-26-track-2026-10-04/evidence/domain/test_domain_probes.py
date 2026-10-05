"""Independent audit probes; assert actual behavior to preserve reproducible evidence.
Run from backend with -p tests.conftest and explicit disposable DB environment.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
import json
from typing import cast
import pytest
from sqlalchemy import select, text
from app.db import get_sessionmaker
from app.models import Animal, Farm, FeedInventory, ScreeningFinding, ScreeningImage, Transaction
from app.services.screening.pipeline import run_screening_cycle
from app.services.screening.providers import ProviderAnswer
from app.services.screening.rotation import ProviderRotation
from app.services.screening.s3 import ScreeningStorage
from app.utils import today
from tests.conftest import owner_with_farm
from tests.test_screening import FakeStorage, _cycle_settings, _jpeg_bytes, _register_fake_objects

async def test_sale_dob_can_follow_purchase(client):
    owner = await owner_with_farm(client)
    reference = today()
    bought = reference - timedelta(days=730)
    estimated = reference - timedelta(days=365)
    created = await client.post('/api/animals', headers=owner, json={
        'tag_number':'DOB-CONTRADICTION','sex':'M','source':'PURCHASED',
        'current_bucket':'FOUNDATION','purchase_date':bought.isoformat(),
        'historical_import_reason':'Import previously acquired goat with unknown age',
    })
    assert created.status_code == 201, created.text
    sold = await client.post(f"/api/animals/{created.json()['id']}/status", headers=owner,
                             json={'new_status':'SOLD','estimated_dob':estimated.isoformat(),'sale_price':1000.0})
    assert sold.status_code == 200, sold.text
    body = sold.json()
    assert body['purchase_date'] < body['estimated_dob']
    print('DOB_CHRONOLOGY', json.dumps({key:body[key] for key in ['id','purchase_date','estimated_dob','status','sale_price']}))

async def test_cost_numerator_is_truncated_at_real_cap(client):
    owner = await owner_with_farm(client)
    farm_id = int(owner['X-Farm-Id'])
    reference = today()
    async with get_sessionmaker()() as db:
        # A real production cap of 20,000 newest rows. The first old expense
        # is omitted when 20,000 newer income rows consume the same row cap.
        db.add(Transaction(farm_id=farm_id,date=reference-timedelta(days=180),type='EXPENSE',category='OTHER',amount=Decimal('60000.00')))
        await db.flush()
        await db.execute(text("INSERT INTO transactions (farm_id, date, type, category, amount) SELECT :farm_id, :date, 'INCOME', 'OTHER', 1 FROM generate_series(1,19999)"),{'farm_id':farm_id,'date':reference})
        db.add(Transaction(farm_id=farm_id,date=reference,type='EXPENSE',category='OTHER',amount=Decimal('1000.00')))
        await db.commit()
    response = await client.get('/api/simulation/calibration', params={'lookback_months':12},headers=owner)
    assert response.status_code == 200, response.text
    body = response.json()
    costs = {x['path']:x for x in body['evidence'] if x['path'].startswith('costs.')}
    assert costs['costs.misc_overhead_per_month']['calibrated_value'] == pytest.approx(1000/6)
    print('COST_TRUNCATION',json.dumps({'recorded_other_expense':61000,'expected_misc_per_month':61000/6,'returned_misc_per_month':body['assumptions']['costs']['misc_overhead_per_month'],'cost_evidence':costs,'warnings':body['warnings']}))

async def test_flagged_without_observations_has_no_reviewable_finding(client):
    owner = await owner_with_farm(client)
    farm_id = int(owner['X-Farm-Id'])
    storage = FakeStorage()
    storage.objects[f'raw/{farm_id}/{today().isoformat()}/empty-flag.jpg'] = _jpeg_bytes(500,300)
    class EmptyFlag:
        name='empty-flag'; model='synthetic'
        async def complete(self,image_jpeg,system_prompt):
            response = {'conditions':[]} if 'veterinary specialist' in system_prompt else {'flagged':True,'confidence':0.9,'quality_problem':False,'observations':[]}
            return ProviderAnswer(text=json.dumps(response),provider=self.name,model=self.model,latency_ms=1)
    async with get_sessionmaker()() as db:
        await _register_fake_objects(db,farm_id,storage)
        result = await run_screening_cycle(db,_cycle_settings(),cast(ScreeningStorage,storage),ProviderRotation([EmptyFlag()]))
        image = (await db.execute(select(ScreeningImage))).scalar_one()
        findings = (await db.execute(select(ScreeningFinding))).scalars().all()
        assert image.status == 'FLAGGED'
        assert len(findings)==0
        print('EMPTY_FLAG',json.dumps({'image_status':image.status,'finding_count':len(findings),'cycle_flagged':result.flagged}))

async def test_first_local_day_dispense_rejected(client,monkeypatch):
    import app.api.feeding as api
    import app.services.chronology as chronology
    owner = await owner_with_farm(client)
    farm_id = int(owner['X-Farm-Id'])
    business_date = today()-timedelta(days=2)
    created_utc = datetime.combine(business_date+timedelta(days=1),datetime.min.time())+timedelta(minutes=30)
    monkeypatch.setattr(api,'today',lambda *args:business_date)
    monkeypatch.setattr(chronology,'today',lambda *args:business_date)
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm,farm_id)
        farm.timezone='America/Phoenix'; farm.created_at=created_utc
        from app.models.feed_rules import DRY_ROUGHAGE_INGREDIENT
        item = (await db.execute(select(FeedInventory).where(FeedInventory.farm_id==farm_id,FeedInventory.ingredient==DRY_ROUGHAGE_INGREDIENT))).scalar_one()
        item.qty_on_hand=100
        await db.commit()
    from app.models.feed_rules import DRY_ROUGHAGE
    response=await client.post('/api/feeding/dispense',headers=owner,json={'bucket':'QUARANTINE','shift':'NIGHT','recipe_code':DRY_ROUGHAGE,'qty_kg':1.0,'date':business_date.isoformat()})
    assert response.status_code==422,response.text
    assert 'before the farm was created' in response.text
    print('DISPENSE_TIMEZONE',json.dumps({'created_utc':created_utc.isoformat(),'farm_local_date':business_date.isoformat(),'timezone':'America/Phoenix','response':response.json()}))

async def test_malformed_specialist_region_is_silently_omitted(client):
    owner=await owner_with_farm(client)
    farm_id=int(owner['X-Farm-Id'])
    storage=FakeStorage()
    storage.objects[f'raw/{farm_id}/{today().isoformat()}/region-loss.jpg']=_jpeg_bytes(500,300)
    class MixedSpecialist:
        name='mixed-specialist';model='synthetic'
        async def complete(self,image_jpeg,system_prompt):
            if 'veterinary specialist' not in system_prompt:
                response={'flagged':True,'confidence':0.9,'observations':[
                    {'region':'mouth','label':'mouth lesion','confidence':0.9},
                    {'region':'eye','label':'severe eye injury','confidence':0.9}]}
            elif 'skin, lips' in system_prompt:
                response={'conditions':[{'disease':'ORF','confidence':0.8,'severity':'moderate'}]}
            else:
                response={'conditions':[{'disease':'EYE_TRAUMA','confidence':'high','severity':'severe'}]}
            return ProviderAnswer(text=json.dumps(response),provider=self.name,model=self.model,latency_ms=1)
    async with get_sessionmaker()() as db:
        await _register_fake_objects(db,farm_id,storage)
        await run_screening_cycle(db,_cycle_settings(),cast(ScreeningStorage,storage),ProviderRotation([MixedSpecialist()]))
        findings=(await db.execute(select(ScreeningFinding))).scalars().all()
        assert [(x.region,x.label) for x in findings]==[('mouth','ORF')]
        print('LOST_REGION',json.dumps({'gate_regions':['mouth','eye'],'reviewable_findings':[(x.region,x.label) for x in findings]}))

async def test_calibration_misses_death_after_operational_weaning(client):
    from app.models import BreedingRecord,KiddingRecord,KidEntry,BucketMove
    owner=await owner_with_farm(client)
    farm_id=int(owner['X-Farm-Id'])
    born=today()-timedelta(days=180)
    ids=[]
    async with get_sessionmaker()() as db:
        buck=Animal(farm_id=farm_id,tag_number='MORT-SIRE',sex='M',source='BORN',current_bucket='FOUNDATION',date_of_birth=born-timedelta(days=900))
        db.add(buck);await db.flush()
        for index in range(5):
            doe=Animal(farm_id=farm_id,tag_number=f'MORT-DAM-{index}',sex='F',source='BORN',current_bucket='RESTING',date_of_birth=born-timedelta(days=900))
            db.add(doe);await db.flush()
            bred=born-timedelta(days=150)
            br=BreedingRecord(farm_id=farm_id,doe_id=doe.id,buck_id=buck.id,breeding_date=bred,ultrasound_date=bred+timedelta(days=35),ultrasound_result_date=bred+timedelta(days=35),ultrasound_done=True,pregnant=True,expected_kidding_date=born,outcome='CONFIRMED_PREGNANT')
            db.add(br);await db.flush()
            litter=KiddingRecord(farm_id=farm_id,doe_id=doe.id,date=born,breeding_record_id=br.id)
            db.add(litter);await db.flush()
            for j in range(2):
                kid=Animal(farm_id=farm_id,tag_number=f'MORT-KID-{index}-{j}',sex='F',source='BORN',current_bucket='FEMALE_KIDS',date_of_birth=born,dam_id=doe.id,sire_id=buck.id,birth_type='TWIN',birth_weight=3.0)
                db.add(kid);await db.flush();ids.append(kid.id)
                db.add(KidEntry(farm_id=farm_id,kidding_record_id=litter.id,tag=kid.tag_number,sex='F',birth_weight=3.0,status='ALIVE',animal_id=kid.id))
                db.add_all([BucketMove(animal_id=kid.id,from_bucket=None,to_bucket='RECOVERY',effective_date=born,reason='Born'),BucketMove(animal_id=kid.id,from_bucket='RECOVERY',to_bucket='FEMALE_KIDS',effective_date=born+timedelta(days=60),reason='Weaned')])
        await db.commit()
    died=await client.post(f'/api/animals/{ids[0]}/status',headers=owner,json={'new_status':'DEAD','date':(born+timedelta(days=70)).isoformat(),'mortality_cause_code':'PNEUMONIA'})
    assert died.status_code==200,died.text
    response=await client.get('/api/simulation/calibration',headers=owner,params={'lookback_months':24})
    assert response.status_code==200,response.text
    body=response.json();ev={x['path']:x for x in body['evidence']}
    assert body['assumptions']['mortality']['kid_pre_weaning']==0
    async with get_sessionmaker()() as db:
        entry=(await db.execute(select(KidEntry).where(KidEntry.animal_id==ids[0]))).scalar_one()
        assert entry.status=='ALIVE'
    print('MISSED_KID_DEATH',json.dumps({'live_births':10,'recorded_deaths_by_3_months':1,'weaned_age_days':60,'died_age_days':70,'birth_entry_status':entry.status,'returned_preweaning_mortality':body['assumptions']['mortality']['kid_pre_weaning'],'evidence':ev['mortality.kid_pre_weaning']}))

def test_disposed_breeding_stock_keeps_depreciating_and_inflates_tax():
    from app.simulation.assumptions import SimulationAssumptions,HerdEventAssumptions
    from app.simulation.engine import _run_core
    a=SimulationAssumptions()
    a.meta.horizon_months=12
    a.herd.does=0;a.herd.bucks=0;a.herd.auto_purchase_bucks=False
    a.costs.shed_cost_per_animal_place=0.0;a.costs.equipment_cost_per_animal=0.0
    a.costs.misc_overhead_per_month=0.0;a.costs.family_labour=True
    a.finance.loan_fraction_of_project_cost=0.0;a.finance.working_capital_months=0
    a.finance.income_tax_rate=0.3
    a.sales.transport_cost_per_head=0.0;a.sales.selling_cost_fraction=0.0
    a.events=[HerdEventAssumptions(month=1,kind='purchase',animal_class='doe',count=1.0,price_per_head=100000.0),HerdEventAssumptions(month=1,kind='sale',animal_class='doe',count=1.0,price_per_head=100000.0)]
    a=SimulationAssumptions.model_validate(a.model_dump())
    result=_run_core(a)
    tax=sum(row.tax for row in result.months)
    assert tax>0
    print('DISPOSAL_TAX',json.dumps({'purchase_and_sale_same_month':100000,'closing_herd':result.months[-1].total_herd,'total_tax':tax,'total_depreciation':sum(row.depreciation for row in result.months),'terminal_breeding_stock':result.terminal_value_breakdown.breeding_stock,'terminal_livestock':result.terminal_value_breakdown.livestock}))

async def test_seasonal_normalization_changes_observed_price_levels(client):
    owner=await owner_with_farm(client)
    farm_id=int(owner['X-Farm-Id'])
    reference=today()
    # Four non-festival months in 2026, each with three equally weighted sales.
    # All lie in the actual lookback and below the stated clamp ceiling.
    async with get_sessionmaker()() as db:
        for month,unit_price in [(1,100),(2,100),(3,100),(4,400)]:
            for index in range(3):
                db.add(Animal(farm_id=farm_id,tag_number=f'SEASON-{month}-{index}',sex='M',source='PURCHASED',current_bucket='FOUNDATION',date_of_birth=date(2024,1,1),purchase_date=date(2024,6,1),status='SOLD',status_date=date(2026,month,15),sale_weight_kg=30.0,sale_price=Decimal(unit_price*30)))
        await db.commit()
    response=await client.get('/api/simulation/calibration',headers=owner,params={'lookback_months':24})
    assert response.status_code==200,response.text
    body=response.json();sales=body['assumptions']['sales']
    assert sales['meat_price_per_kg']==100
    reproduced=[sales['meat_price_per_kg']*factor for factor in sales['monthly_meat_price_multipliers'][:4]]
    assert reproduced==pytest.approx([80,80,80,320])
    print('SEASONAL_LEVEL_DRIFT',json.dumps({'observed_monthly_prices':[100,100,100,400],'calibrated_base':sales['meat_price_per_kg'],'implied_same_month_prices_before_growth':reproduced,'multipliers':sales['monthly_meat_price_multipliers']}))

async def test_catalog_and_tenant_integrity_control(client):
    from sqlalchemy.exc import IntegrityError
    owner=await owner_with_farm(client,'a26-integrity-a@example.test')
    other=await owner_with_farm(client,'a26-integrity-b@example.test')
    created=await client.post('/api/animals',headers=owner,json={'tag_number':'TENANT-A','sex':'F','source':'PURCHASED','current_bucket':'FOUNDATION','historical_import_reason':'Existing herd'})
    assert created.status_code==201,created.text
    async with get_sessionmaker()() as db:
        with pytest.raises(IntegrityError):
            await db.execute(text("INSERT INTO transactions (farm_id,date,type,category,amount,related_animal_id) VALUES (:farm,:date,'EXPENSE','OTHER',10,:animal)"),{'farm':int(other['X-Farm-Id']),'date':today(),'animal':created.json()['id']})
            await db.flush()
        await db.rollback()
        heads=(await db.execute(text('SELECT version_num FROM alembic_version'))).scalars().all()
        invalid=(await db.execute(text("SELECT indexrelid::regclass::text FROM pg_index JOIN pg_class ON pg_class.oid=indrelid JOIN pg_namespace ON pg_namespace.oid=relnamespace WHERE nspname='public' AND NOT indisvalid"))).scalars().all()
        unvalidated=(await db.execute(text("SELECT conname FROM pg_constraint JOIN pg_namespace ON pg_namespace.oid=connamespace WHERE nspname='public' AND NOT convalidated"))).scalars().all()
        assert len(heads)==1 and not invalid and not unvalidated
        print('CATALOG_CONTROL',json.dumps({'head':heads,'invalid_indexes':invalid,'unvalidated_constraints':unvalidated,'cross_tenant_ledger_link':'rejected'}))

async def test_concurrent_terminal_sale_control(client):
    import asyncio
    owner=await owner_with_farm(client)
    created=await client.post('/api/animals',headers=owner,json={'tag_number':'RACE-SALE','sex':'F','source':'PURCHASED','current_bucket':'FOUNDATION','historical_import_reason':'Existing herd'})
    assert created.status_code==201,created.text
    animal_id=created.json()['id']
    async def sell():
        return await client.post(f'/api/animals/{animal_id}/status',headers=owner,json={'new_status':'SOLD','sale_price':1000.0})
    responses=await asyncio.gather(sell(),sell())
    assert sorted(r.status_code for r in responses)==[200,409]
    async with get_sessionmaker()() as db:
        sales=(await db.execute(select(Transaction).where(Transaction.source_type=='ANIMAL_SALE',Transaction.source_id==animal_id))).scalars().all()
        assert len(sales)==1
        print('SALE_CONCURRENCY_CONTROL',json.dumps({'responses':sorted(r.status_code for r in responses),'sale_transactions':len(sales)}))

async def test_income_rows_inflate_expense_calibration_confidence(client):
    owner=await owner_with_farm(client)
    farm_id=int(owner['X-Farm-Id'])
    observed_on=today()-timedelta(days=30)
    async with get_sessionmaker()() as db:
        db.add(Transaction(farm_id=farm_id,date=observed_on,type='EXPENSE',category='OTHER',amount=Decimal('1000.00')))
        db.add_all([Transaction(farm_id=farm_id,date=observed_on,type='INCOME',category='OTHER',amount=Decimal('100.00')) for _ in range(30)])
        await db.commit()
    response=await client.get('/api/simulation/calibration',headers=owner)
    assert response.status_code==200,response.text
    evidence=next(x for x in response.json()['evidence'] if x['path']=='costs.misc_overhead_per_month')
    assert evidence['sample_size']==31 and evidence['confidence']=='high'
    print('INCOME_INFLATES_COST_CONFIDENCE',json.dumps({'expense_observations':1,'income_observations':30,'returned_sample_size':evidence['sample_size'],'returned_confidence':evidence['confidence']}))
