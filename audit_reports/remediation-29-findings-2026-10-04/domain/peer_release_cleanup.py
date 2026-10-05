"""Independent cleanup ownership adversarial controls; only a local fake gh runs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[3]
script=ROOT/'.github/scripts/cleanup_release_versions.py'
digest='sha256:'+'a'*64
other='sha256:'+'b'*64
image='ghcr.io/example/goatfarm-backend'
run_tag='v5-run-123-2-publish-amd64'
receipt={'image':image,'tag':run_tag,'digest':digest}
results=[]
scenarios=[
 ('failed-preflight',[],[],[{'id':1,'name':digest,'metadata':{'container':{'tags':['v5-publish-amd64']}}}],0),
 ('multiple-pages-wrong-digest-same-tag',[receipt],[],[{'id':2,'name':other,'metadata':{'container':{'tags':[run_tag]}}}],0),
 ('unowned-tag-on-owned-digest',[receipt],[],[{'id':3,'name':digest,'metadata':{'container':{'tags':[run_tag,'v4']}}}],0),
 ('untagged-owned-digest',[receipt],[],[{'id':4,'name':digest,'metadata':{'container':{'tags':[]}}}],0),
 ('owned-final-plus-publish-alias',[receipt],[{'image':image,'tag':'v5','digest':digest}],[{'id':5,'name':digest,'metadata':{'container':{'tags':[run_tag,'v5']}}}],1),
 ('foreign-final-on-owned-publish',[receipt],[],[{'id':6,'name':digest,'metadata':{'container':{'tags':[run_tag,'v5']}}}],0),
]
for name,publishes,finals,versions,expected in scenarios:
 with tempfile.TemporaryDirectory(prefix='goatfarm-fix29-peer-release-') as temp:
  folder=Path(temp); fake=folder/'gh'; log=folder/'calls.jsonl'
  fake.write_text('#!'+sys.executable+'\nimport os,sys,json\nfrom pathlib import Path\nwith Path(os.environ["CALL_LOG"]).open("a") as out: out.write(json.dumps(sys.argv[1:])+"\\n")\nif "--slurp" in sys.argv: print(os.environ["VERSION_PAGES"])\n')
  fake.chmod(0o700)
  (folder/'goatfarm-release-123-2-owned.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in finals))
  env=os.environ|{'PATH':str(folder)+os.pathsep+os.environ['PATH'],'RELEASE_OWNER':'Example','GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'2','RUNNER_TEMP':temp,'OWNED_PUBLISHES':json.dumps(publishes),'CALL_LOG':str(log),'VERSION_PAGES':json.dumps([[],versions])}
  run=subprocess.run([sys.executable,str(script)],env=env,capture_output=True,text=True,timeout=10)
  assert run.returncode==0,(name,run.stderr)
  calls=[json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
  deletes=[c for c in calls if 'DELETE' in c]
  assert len(deletes)==expected,(name,calls)
  if name=='failed-preflight':assert not calls
  results.append({'case':name,'delete_calls':deletes,'stdout':run.stdout})
print(json.dumps({'passed':len(results),'controls':results},indent=2))
