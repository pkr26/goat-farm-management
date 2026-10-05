"""Feed synthetic evidence only to the checked-in CI validators."""
import json,pathlib,subprocess,sys,tempfile
root=pathlib.Path(__file__).resolve().parents[4]
results=[]
with tempfile.TemporaryDirectory(prefix='goatfarm-a26-gates-') as tmp:
 tmp=pathlib.Path(tmp)
 for case,status,score in [('timeout_reported_as_complete','INCONCLUSIVE_TIMEOUT',100),('survivor_reported_as_100_percent','SURVIVED',100),('nonfinite_score','SURVIVED',float('nan'))]:
  plan={'mutants':['m1']}; report={'campaign_id':'fresh-audit','executed':1,'complete_measured':1,'score':score}; record={'id':'m1','campaign_id':'fresh-audit','selection_mode':'complete','status':status}
  for name,data in [('plan.json',plan),('report.json',report),('results.jsonl',record)]: (tmp/name).write_text(json.dumps(data)+'\n')
  p=subprocess.run([sys.executable,str(root/'.github/scripts/check_mutation_report.py'),'--plan',str(tmp/'plan.json'),'--report',str(tmp/'report.json'),'--results',str(tmp/'results.jsonl'),'--format','backend','--minimum','80'],text=True,capture_output=True)
  results.append({'case':case,'expected':'nonzero rejection','actual_exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr})
 for case,data in [('sarif_zero_runs',{'version':'2.1.0','runs':[]}),('sarif_failed_invocation',{'version':'2.1.0','runs':[{'tool':{'driver':{'name':'synthetic-scanner'}},'invocations':[{'executionSuccessful':False}],'results':[]}]})]:
  (tmp/'result.sarif').write_text(json.dumps(data))
  p=subprocess.run([sys.executable,str(root/'.github/scripts/gate_sarif.py'),str(tmp/'result.sarif')],text=True,capture_output=True)
  results.append({'case':case,'expected':'nonzero rejection','actual_exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr})
print(json.dumps(results,indent=2))
