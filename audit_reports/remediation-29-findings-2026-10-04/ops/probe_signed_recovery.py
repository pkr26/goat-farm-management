"""Real GPG positive/negative controls against the corrected manifest CLI; no database I/O."""
from pathlib import Path
import json, os, subprocess, sys, hashlib, datetime, shutil
ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent
SCRATCH=Path.home()/'.cache/goatfarm-fix29-ops/gpg-control'
assert not SCRATCH.exists()
SCRATCH.mkdir(parents=True,mode=0o700)
(SCRATCH/'keys').mkdir(mode=0o700)
(SCRATCH/'tmp').mkdir(mode=0o700)
(SCRATCH/'bin').mkdir(mode=0o700)
DOCKER=str(Path.home()/'.local/bin/docker')
info=json.loads(subprocess.check_output([DOCKER,'image','inspect','ruby:3.3'],text=True))[0]
GPG_IMAGE=info['Id']
wrapper=SCRATCH/'bin/gpg'
wrapper.write_text('#!/usr/bin/env python3\nimport os,sys\nos.execv('+repr(DOCKER)+','+repr([DOCKER,'run','--rm','-i','--network','none','-v',f'{SCRATCH}:{SCRATCH}','-e',f'GNUPGHOME={SCRATCH}/keys',GPG_IMAGE,'gpg'])+'+sys.argv[1:])\n')
wrapper.chmod(0o700)
env={k:v for k,v in os.environ.items() if not k.startswith('GOATFARM_')}
env.update({'PATH':str(SCRATCH/'bin')+os.pathsep+os.environ['PATH'],'TMPDIR':str(SCRATCH/'tmp'),'GNUPGHOME':str(SCRATCH/'keys')})
def run(args,*,check=True):
 r=subprocess.run(args,env=env,capture_output=True,text=True,timeout=90)
 if check and r.returncode:raise RuntimeError(r.stdout+r.stderr)
 return r
try:
 run(['gpg','--batch','--pinentry-mode','loopback','--passphrase','','--quick-generate-key','Fix29 recovery probe <synthetic@example.invalid>','rsa2048','sign','1d'])
 listing=run(['gpg','--batch','--with-colons','--list-secret-keys']).stdout
 fingerprint=next(line.split(':')[9] for line in listing.splitlines() if line.startswith('fpr:'))
 env['GOATFARM_BACKUP_GPG_SIGNER_FINGERPRINT']=fingerprint
 now=datetime.datetime.now(datetime.timezone.utc)
 source=SCRATCH/'source.json';archive=SCRATCH/'goatfarm-probe.dump.gpg';bound=Path(str(archive)+'.recovery.json')
 archive.write_bytes(b'Explicit synthetic database fixture; no restore claim.')
 digest=hashlib.sha256(archive.read_bytes()).hexdigest();Path(str(archive)+'.sha256').write_text(digest+'  '+archive.name+'\n')
 payload={'schema_version':2,'generated_at':now.isoformat(),'screening_objects':{'bucket':'synthetic','prefix':'raw','versioning':'Enabled','recovery_point':'snapshot-probe','recovered_at':now.isoformat(),'restore_receipt':'receipt-probe','manifest':{'name':'manifest','bytes':1,'sha256':'a'*64}},'key_material':{k:{'sha256':'b'*64,'escrow_receipt':'receipt-'+k} for k in ['jwt_private','jwt_public','totp_encryption','idempotency_hmac','database_ca','backup_gpg']}}
 helper=str(ROOT/'backend/scripts/recovery_inventory.py');checker=str(ROOT/'backend/scripts/check_backup_freshness.py');python=str(ROOT/'backend/.venv/bin/python')
 def bind():
  source.write_text(json.dumps(payload));bound.unlink(missing_ok=True)
  return run([python,helper,'bind','--inventory',str(source),'--output',str(bound),'--archive-name',archive.name,'--archive-sha256',digest,'--database-recovered-at',now.isoformat(),'--max-age-hours','744'])
 bind()
 original=bound.read_text()
 results=[]
 def verify(label):
  r=run([python,checker,str(SCRATCH),'--max-age-hours','26'],check=False)
  results.append({'case':label,'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
  return r.returncode
 assert verify('fresh-signed-manifest')==0
 data=json.loads(original);data['generated_at']=(now+datetime.timedelta(seconds=1)).isoformat();bound.write_text(json.dumps(data));assert verify('timestamp-tamper')==2
 bound.write_text(original);data=json.loads(original);data['key_material']['jwt_private']['escrow_receipt']='substituted';bound.write_text(json.dumps(data));assert verify('escrow-tamper')==2
 bound.write_text(original);env['GOATFARM_BACKUP_GPG_SIGNER_FINGERPRINT']='C'*40;assert verify('unexpected-trust-anchor')==2;env['GOATFARM_BACKUP_GPG_SIGNER_FINGERPRINT']=fingerprint
 payload['screening_objects']['recovered_at']=(now-datetime.timedelta(hours=72)).isoformat();bind();assert verify('fresh-signature-stale-object-component')==2
 results.append({'gpg_image_id':GPG_IMAGE,'signer_fingerprint':fingerprint,'no_private_key_retained':True})
 (OUT/'signed-recovery-controls.json').write_text(json.dumps(results,indent=2)+'\n')
 print(json.dumps({'controls':5,'result':'passed'}))
finally:
 shutil.rmtree(SCRATCH)
