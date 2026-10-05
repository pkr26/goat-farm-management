"""Independent counterexample/control for D26-07; no DB or external calls."""
import json,os
for key in tuple(os.environ):
 if key.startswith('GOATFARM_'):del os.environ[key]
from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import _run_core
results=[]
for sale_price,tax_rate in [(100000,0.3),(120000,0.3),(100000,0.0)]:
 a=SimulationAssumptions().model_dump()
 a['meta']['horizon_months']=12
 a['herd'].update(does=0,bucks=0,auto_purchase_bucks=False)
 a['costs'].update(shed_cost_per_animal_place=0,equipment_cost_per_animal=0,misc_overhead_per_month=0,family_labour=True)
 a['finance'].update(loan_fraction_of_project_cost=0,working_capital_months=0,income_tax_rate=tax_rate)
 a['sales'].update(transport_cost_per_head=0,selling_cost_fraction=0)
 a['events']=[{'month':1,'kind':'purchase','animal_class':'doe','count':1,'price_per_head':100000},{'month':1,'kind':'sale','animal_class':'doe','count':1,'price_per_head':sale_price}]
 result=_run_core(SimulationAssumptions.model_validate(a))
 results.append({'purchase':100000,'sale':sale_price,'tax_rate':tax_rate,'economic_gain':sale_price-100000,'tax_on_gain':(sale_price-100000)*tax_rate,'actual_tax':sum(row.tax for row in result.months),'closing_head':result.months[-1].total_herd,'book_depreciation':sum(row.depreciation for row in result.months),'terminal_book':result.terminal_value_breakdown.breeding_stock,'terminal_livestock':result.terminal_value_breakdown.livestock})
print(json.dumps(results,indent=2))
