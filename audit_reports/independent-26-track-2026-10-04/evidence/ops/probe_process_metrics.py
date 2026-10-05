"""Independent Python processes demonstrate the deployed metric boundary."""
import json,os,pathlib,subprocess,sys
root=pathlib.Path(__file__).resolve().parents[4]
env={k:v for k,v in os.environ.items() if not k.startswith('GOATFARM_')}
env['GOATFARM_ENVIRONMENT']='development';env['PYTHONPATH']=str(root/'backend')
worker='from app import metrics; metrics.record_screening_provider_call("anthropic", True); print(metrics.render().decode())'
api='from app import metrics; print(metrics.render().decode())'
w=subprocess.run([sys.executable,'-c',worker],cwd=root/'backend',env=env,text=True,capture_output=True,check=True)
a=subprocess.run([sys.executable,'-c',api],cwd=root/'backend',env=env,text=True,capture_output=True,check=True)
def sample(s): return [x for x in s.splitlines() if x.startswith('goatfarm_screening_provider_calls_total')]
print(json.dumps({'worker_process_samples':sample(w.stdout),'api_process_samples':sample(a.stdout),'shared_prometheus_multiprocess_configuration_present':bool(os.environ.get('PROMETHEUS_MULTIPROC_DIR'))},indent=2))
