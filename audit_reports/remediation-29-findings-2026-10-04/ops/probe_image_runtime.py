"""Runtime smoke isolated from real networks/volumes, on both supported architectures."""
from pathlib import Path
import json,subprocess,time
OUT=Path(__file__).resolve().parent
DOCKER=str(Path.home()/'.local/bin/docker')
results=[]
def command(args,timeout=90):
 r=subprocess.run([DOCKER,*args],capture_output=True,text=True,timeout=timeout)
 return {'command':['docker',*args],'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
for arch,suffix in [('arm64','104'),('amd64','104-amd64')]:
 for name in ['backend','frontend','postgres']:
  ref=f'goatfarm-fix29-{name}:{suffix}'
  present=command(['image','inspect',ref,'--format','{{.Id}} {{.Architecture}}'])
  if present['exit']:continue
  assert present['stdout'].strip().endswith(arch),present
  record={'arch':arch,'image':ref,'identity':present['stdout'].strip()}
  if name=='backend':
   code='import asyncio,httpx,ssl,sqlite3; from app.main import create_app; from app.worker.metrics_server import worker_metrics_server\nasync def check():\n async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()),base_url="http://localhost") as c:\n  r=await c.get("/healthz"); print(r.status_code,r.text,ssl.OPENSSL_VERSION,sqlite3.sqlite_version); assert r.status_code==200\nasyncio.run(check())'
   record['smoke']=command(['run','--rm','--network','none','--platform',f'linux/{arch}',ref,'python','-c',code])
  else:
   container=f'goatfarm-fix29-{name}-smoke-{arch}'
   args=['run','-d','--name',container,'--network','none','--platform',f'linux/{arch}']
   if name=='postgres':
    args+=['-e','POSTGRES_PASSWORD=synthetic-smoke-only-104','--tmpfs','/var/lib/postgresql/data:rw,size=512m']
   args += [ref]
   launched=command(args);record['launch']=launched
   if launched['exit']==0:
    try:
     smoke=None
     for attempt in range(30):
      if name=='postgres':
       smoke=command(['exec',container,'psql','-U','postgres','-d','postgres','-v','ON_ERROR_STOP=1','-c','SELECT version(); CREATE TABLE smoke_probe (n integer); INSERT INTO smoke_probe VALUES (29); SELECT sum(n) FROM smoke_probe;'])
      else:
       smoke=command(['exec',container,'node','-e','fetch("http://127.0.0.1:3000/healthz").then(async r=>{console.log(r.status,await r.text());process.exit(r.ok?0:1)}).catch(()=>process.exit(1))'])
      if smoke['exit']==0:break
      time.sleep(1)
     record['smoke']=smoke
     record['logs']=command(['logs',container])
    finally:
     record['cleanup']=command(['rm','-f','-v',container])
  results.append(record)
  (OUT/'image-runtime-smoke.json').write_text(json.dumps(results,indent=2)+'\n')
  assert record['smoke']['exit']==0,record
  print(name,arch,'passed',flush=True)
