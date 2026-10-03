"""Read-only pure-function repros from exhaustive backend audit."""
import sys
from datetime import date
sys.path.insert(0,'/Users/pradeepreddy/Desktop/goat-farm-management-main/backend')
from app.simulation.assumptions import SimulationAssumptions
from app.simulation.backward_planner import PlannerTarget, _ClassWindow, _requirement_chain, _effective_conception, month_offset
from app.simulation.daily_ops import AnimalStartSpec,DailyOpsInput,DailyOpsParams,run_daily_ops
from app.simulation.engine import _run_core,male_weight_at_age
from app.simulation.vocabulary import GOAT_NOUNS
p=SimulationAssumptions().model_dump()
p['reproduction'].update(litter_size=2.,stillbirth_rate=0.,sex_ratio_female=.5,conception_rate=1.,max_services_before_cull=1)
p['mortality'].update(kid_pre_weaning=0.,kid_post_weaning=0.,grower=0.,adult=0.)
a=SimulationAssumptions.model_validate(p)
t=PlannerTarget(year_month='2028-01',animal_class='male_grower',count=100.)
c=_requirement_chain(a,t,month_offset(a.meta.start_year_month,t.year_month),True,_ClassWindow(a,'male_grower'),GOAT_NOUNS)
print('backward-chain',c.model_dump())
p['reproduction'].update(conception_rate=0.,max_services_before_cull=0)
a=SimulationAssumptions.model_validate(p)
print('zero-conception-unlimited',_effective_conception(a))
p=SimulationAssumptions().model_dump()
p['herd'].update(does=0,bucks=0,female_kids=0,male_kids=10,female_weaners=0,male_weaners=0,female_growers=0,male_growers=0)
a=SimulationAssumptions.model_validate(p);r=_run_core(a)
print('opening-male-valuation',{'stock_cost':r.stock_cost,'consistent_male_valuation':10*male_weight_at_age(1,a.growth,a.growth.adult_weight_buck_kg)*a.sales.meat_price_per_kg})
p=DailyOpsParams(adult_annual_mortality=0.,kid_pre_weaning_mortality=0.,kid_post_weaning_mortality=0.,grower_annual_mortality=0.,abortion_rate=0.)
r=run_daily_ops(DailyOpsInput(start_date=date(2026,10,3),horizon_days=7,animals=[AnimalStartSpec(tag='Q1',sex='F',bucket='QUARANTINE',age_months=12,days_in_bucket=44)],params=p));d=r.days[0]
print('stale-shift-destinations',{'moves':[x.model_dump() for x in d.moves],'post_move_delivery':[(x.time,x.building,x.headline) for x in d.tasks if x.category=='FEED' and x.time in ('13:30','19:30')],'occupancy':[x.model_dump() for x in d.occupancy]})
for does,bucks in [(3,1),(100,5),(500,25),(2000,100)]:
 p=SimulationAssumptions().model_dump();p['herd'].update(does=does,bucks=bucks,max_breeding_does=does);p['finance'].update(nlm_subsidy=True,loan_fraction_of_project_cost=0.)
 a=SimulationAssumptions.model_validate(p);r=_run_core(a)
 print('NLM-subsidy',{'does':does,'bucks':bucks,'project_cost':round(r.project_cost,2),'subsidy_amount':r.subsidy_amount,'cashflow_month0':r.cash_flows[0]})
