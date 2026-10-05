"""Bounded app-independent child-process/event-loop control in the same pinned Node image."""
from pathlib import Path
import json,subprocess
OUT=Path(__file__).resolve().parent
DOCKER=str(Path.home()/'.local/bin/docker')
PINS=json.loads((OUT/'resolved-image-pins.json').read_text())
entry=PINS['node']
script='''const {spawn}=require('node:child_process');
console.log(JSON.stringify({arch:process.arch,node:process.version,uv:process.versions.uv}));
(async()=>{for(let round=0;round<3;round++) await Promise.all(Array.from({length:8},()=>new Promise((resolve,reject)=>{const child=spawn(process.execPath,['-e','process.stdout.write("control"); setImmediate(()=>process.exit(0));']);let data='';child.stdout.on('data',d=>data+=d);child.stderr.on('data',()=>{});child.on('error',reject);child.on('close',code=>code===0&&data==='control'?resolve():reject(new Error(`child ${code}`)));})));console.log('24 child event-loop controls passed');})().catch(e=>{console.error(e);process.exitCode=1;});'''
results=[]
for arch in ['arm64','amd64']:
 child=next(x['digest'] for x in entry['index']['manifests'] if x.get('platform',{}).get('architecture')==arch and x['platform'].get('os')=='linux')
 name=f'goatfarm-fix29-node-control-{arch}'
 command=[DOCKER,'run','--rm','-i','--name',name,'--network','none','--platform',f'linux/{arch}',f'node:24-alpine@{child}','node','-']
 try:
  result=subprocess.run(command,input=script,capture_output=True,text=True,timeout=30)
  results.append({'architecture':arch,'image_child_digest':child,'exit':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
 except subprocess.TimeoutExpired as exc:
  results.append({'architecture':arch,'timeout_seconds':30,'stdout':str(exc.stdout),'stderr':str(exc.stderr)})
 finally:
  subprocess.run([DOCKER,'rm','-f',name],capture_output=True,check=False)
(OUT/'node-emulation-control.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps(results,indent=2))
