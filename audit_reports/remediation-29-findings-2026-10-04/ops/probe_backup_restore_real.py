"""Real local PostgreSQL/GPG backup and restore. No host DB, provider, or external network."""
from pathlib import Path
import datetime,json,os,shutil,subprocess,time
ROOT=Path(__file__).resolve().parents[3];OUT=Path(__file__).resolve().parent
SCRATCH=Path.home()/'.cache/goatfarm-fix29-ops/restore-control'
DOCKER=str(Path.home()/'.local/bin/docker')
CONTAINER='goatfarm-fix29-real-recovery'
SOURCE='goatfarm_test_fix29_ops_backup_source';TARGET='goatfarm_test_fix29_ops_backup_target'
PG_IMAGE='goatfarm-fix29-postgres:104'
GPG_IMAGE=json.loads(subprocess.check_output([DOCKER,'image','inspect','ruby:3.3'],text=True))[0]['Id']
assert not SCRATCH.exists();SCRATCH.mkdir(parents=True,mode=0o700)
for name in ['keys','tmp','bin','backups']:(SCRATCH/name).mkdir(mode=0o700)
logs=[];launched=False

def invoke(command,env=None,check=True,timeout=90):
 result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=timeout)
 logs.append({'command':command,'exit':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
 (OUT/'real-backup-restore.json').write_text(json.dumps(logs,indent=2)+'\n')
 if check and result.returncode:raise RuntimeError(result.stdout+result.stderr)
 return result
try:
 invoke([DOCKER,'run','-d','--name',CONTAINER,'--network','none','-e','POSTGRES_PASSWORD=synthetic-recovery-fixture','--tmpfs','/var/lib/postgresql/data:rw,size=512m','-v',f'{SCRATCH}:{SCRATCH}',PG_IMAGE]);launched=True
 for _ in range(30):
  if invoke([DOCKER,'exec',CONTAINER,'pg_isready','-h','127.0.0.1','-U','postgres'],check=False).returncode==0:break
  time.sleep(1)
 for database in [SOURCE,TARGET]:invoke([DOCKER,'exec',CONTAINER,'createdb','-U','postgres',database])
 seed="CREATE TABLE audit_probe (id integer PRIMARY KEY, label text NOT NULL); INSERT INTO audit_probe SELECT n, 'synthetic row ' || n FROM generate_series(1,29) n; CREATE TABLE alembic_version(version_num varchar(32) NOT NULL); INSERT INTO alembic_version VALUES ('ff6a7b8c9d01'); SELECT count(*), sum(id) FROM audit_probe;"
 invoke([DOCKER,'exec',CONTAINER,'psql','-U','postgres','-d',SOURCE,'-v','ON_ERROR_STOP=1','-c',seed])
 for tool in ['pg_dump','pg_restore','psql']:
  wrapper=SCRATCH/'bin'/tool
  wrapper.write_text('#!/usr/bin/env python3\nimport os,sys\nargs='+repr([DOCKER,'exec','-i'])+'\nfor key in ("PGPASSFILE","PGSSLMODE","PGSSLROOTCERT"):\n if key in os.environ: args += ["-e", key+"="+os.environ[key]]\nos.execv('+repr(DOCKER)+',args+'+repr([CONTAINER,tool])+'+sys.argv[1:])\n');wrapper.chmod(0o700)
 wrapper=SCRATCH/'bin/gpg'
 gpg_command=[DOCKER,'run','--rm','-i','--network','none','-v',f'{SCRATCH}:{SCRATCH}','-e',f'GNUPGHOME={SCRATCH}/keys',GPG_IMAGE,'gpg']
 wrapper.write_text('#!/usr/bin/env python3\nimport os,sys,subprocess\ncommand='+repr(gpg_command)+'\nargs=sys.argv[1:]\nif "--status-fd" in args and args[args.index("--status-fd")+1]=="3":\n args[args.index("--status-fd")+1]="1"\n result=subprocess.run(command+args,capture_output=True)\n os.write(3,result.stdout)\n sys.stderr.buffer.write(result.stderr)\n sys.exit(result.returncode)\nos.execv(command[0],command+args)\n');wrapper.chmod(0o700)
 env={k:v for k,v in os.environ.items() if not k.startswith('GOATFARM_')}
 env.update({'PATH':str(SCRATCH/'bin')+os.pathsep+os.environ['PATH'],'TMPDIR':str(SCRATCH/'tmp'),'GNUPGHOME':str(SCRATCH/'keys'),'GOATFARM_ENVIRONMENT':'development','GOATFARM_DB_SSLMODE':'disable','GOATFARM_DATABASE_URL':f'postgresql://postgres:synthetic-recovery-fixture@127.0.0.1:5432/{SOURCE}'})
 invoke(['gpg','--batch','--pinentry-mode','loopback','--passphrase','','--quick-generate-key','Fix29 database recovery <synthetic@example.invalid>','rsa2048','sign','1d'],env)
 listing=invoke(['gpg','--batch','--with-colons','--list-secret-keys'],env).stdout
 fingerprint=next(line.split(':')[9] for line in listing.splitlines() if line.startswith('fpr:'))
 invoke(['gpg','--batch','--pinentry-mode','loopback','--passphrase','','--quick-add-key',fingerprint,'rsa2048','encrypt','1d'],env)
 env.update({'GOATFARM_BACKUP_GPG_SIGNER_FINGERPRINT':fingerprint,'GOATFARM_BACKUP_GPG_RECIPIENT':fingerprint})
 manifest=SCRATCH/'objects.jsonl';manifest.write_text('{"key":"synthetic","version":"synthetic-v1"}\n')
 inventory=SCRATCH/'unbound.json';now=datetime.datetime.now(datetime.timezone.utc).isoformat()
 command=[str(ROOT/'backend/.venv/bin/python'),str(ROOT/'backend/scripts/recovery_inventory.py'),'capture','--output',str(inventory),'--object-bucket','synthetic-bucket','--object-prefix','raw','--object-recovery-point','synthetic-snapshot','--object-recovered-at',now,'--object-restore-receipt','local-fixture-only','--object-manifest',str(manifest)]
 for name in ['jwt_private','jwt_public','totp_encryption','idempotency_hmac','database_ca','backup_gpg']:
  command += ['--identity',name+'=synthetic-key-identity','--escrow-receipt',name+'=synthetic-key-receipt']
 invoke(command,env);env['GOATFARM_RECOVERY_INVENTORY_FILE']=str(inventory)
 invoke(['bash',str(ROOT/'backend/scripts/backup.sh'),str(SCRATCH/'backups')],env)
 archives=list((SCRATCH/'backups').glob('*.dump.gpg'));assert len(archives)==1
 archive=archives[0]
 invoke([str(ROOT/'backend/.venv/bin/python'),str(ROOT/'backend/scripts/check_backup_freshness.py'),str(SCRATCH/'backups')],env)
 env.update({'GOATFARM_RESTORE_DATABASE_URL':f'postgresql://postgres:synthetic-recovery-fixture@127.0.0.1:5432/{TARGET}','GOATFARM_RESTORE_CONFIRM':TARGET,'GOATFARM_RESTORE_GPG_SIGNER_FINGERPRINT':fingerprint})
 invoke(['bash',str(ROOT/'backend/scripts/restore.sh'),str(archive)],env)
 verified=invoke([DOCKER,'exec',CONTAINER,'psql','-U','postgres','-d',TARGET,'-tA','-c','SELECT count(*),sum(id),min(label),max(label) FROM audit_probe;'])
 assert verified.stdout.strip()=='29|435|synthetic row 1|synthetic row 9',verified.stdout
 # A second restore must reject this nonempty target before destructive writes.
 refused=invoke(['bash',str(ROOT/'backend/scripts/restore.sh'),str(archive)],env,check=False)
 assert refused.returncode==2 and 'contains user schema objects' in refused.stderr.lower()
 logs.append({'result':'passed','restored_rows':29,'restored_id_sum':435,'network':'none','TLS':'development-only disable','object_store':'synthetic receipt only; no live restoration claimed'})
 print('real backup, signed manifest, freshness, restore, SQL verification and nonempty refusal passed')
finally:
 if launched:invoke([DOCKER,'rm','-f','-v',CONTAINER],check=False)
 shutil.rmtree(SCRATCH)
 logs.append({'cleanup':'owned container/anonymous volume/temp keyring and plaintext removed'})
 (OUT/'real-backup-restore.json').write_text(json.dumps(logs,indent=2)+'\n')
