"""Exercise only the local freshness verifier against synthetic artifacts."""
import datetime, hashlib, json, pathlib, subprocess, sys, tempfile
root=pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0,str(root/'backend/scripts'))
from recovery_inventory import REQUIRED_KEY_MATERIAL
with tempfile.TemporaryDirectory(prefix='goatfarm-a26-backup-') as tmp:
    tmp=pathlib.Path(tmp)
    archive=tmp/'goatfarm-2026-10-01T00-00-00Z-synthetic001.dump.gpg'
    archive.write_bytes(b'unchanged synthetic archive bytes: timestamp is never authenticated by this monitor')
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name+'.sha256').write_text(digest+'  '+archive.name+'\n')
    inventory={'schema_version':1,'generated_at':(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(hours=72)).isoformat(),'screening_objects':{'bucket':'audit-bucket','prefix':'raw','versioning':'Enabled','recovery_point':'unchanged-old-recovery-point','restore_receipt':'unchanged-old-receipt','manifest':{'name':'objects.json','bytes':1,'sha256':'a'*64}},'key_material':{n:{'sha256':'b'*64,'escrow_receipt':'synthetic-offline-receipt'} for n in REQUIRED_KEY_MATERIAL},'database_artifact':{'name':archive.name,'sha256':digest}}
    sidecar=archive.with_name(archive.name+'.recovery.json')
    sidecar.write_text(json.dumps(inventory))
    command=[sys.executable,str(root/'backend/scripts/check_backup_freshness.py'),str(tmp),'--max-age-hours','26']
    before=subprocess.run(command,text=True,capture_output=True)
    inventory['generated_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    sidecar.write_text(json.dumps(inventory))
    after=subprocess.run(command,text=True,capture_output=True)
    print(json.dumps({'before_exit':before.returncode,'before_stderr':before.stderr,'after_exit':after.returncode,'after_stdout':after.stdout,'only_changed_field':'unsigned recovery.json generated_at','archive_sha256_unchanged':hashlib.sha256(archive.read_bytes()).hexdigest()==digest,'object_recovery_point_unchanged':True},indent=2))
