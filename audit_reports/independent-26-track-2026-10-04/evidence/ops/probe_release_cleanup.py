"""Extract repository release shell steps and execute with fake gh only.
No GitHub/registry request or actual deletion is possible in this probe.
"""
import json, os, pathlib, subprocess, tempfile, textwrap
root=pathlib.Path(__file__).resolve().parents[4]
workflow=(root/'.github/workflows/release.yml').read_text()
def step_script(title):
    block=workflow.split('      - name: '+title+'\n',1)[1]
    block=block.split('\n      - name:',1)[0].split('\n  release:',1)[0]
    script=block.split('        run: |\n',1)[1]
    return textwrap.dedent(script).replace("${{ github.repository_owner }}", 'audit-owner')
preflight=step_script('Refuse an existing immutable release version')
cleanup=step_script('Remove incomplete release and per-arch publish tags on failure')
with tempfile.TemporaryDirectory(prefix='goatfarm-a26-release-') as tmp:
    tmp=pathlib.Path(tmp); bindir=tmp/'bin'; bindir.mkdir()
    fake=bindir/'gh'
    fake.write_text('''#!/usr/bin/env python3
import sys,json,os
args=sys.argv[1:]
with open(os.environ['AUDIT_COMMAND_LOG'],'a') as f: f.write(json.dumps(args)+'\\n')
if args[:2]==['release','view']: sys.exit(0)
if args[:2]==['api','orgs/audit-owner']: sys.exit(0)
if args[:3]==['api','-X','DELETE']: sys.exit(0)
if args[:2]==['api','--paginate']:
 print(json.dumps([{'id':101,'metadata':{'container':{'tags':['v1.2.3-publish-amd64']}}},{'id':102,'metadata':{'container':{'tags':['v1.2.3-publish-arm64']}}}]))
 sys.exit(0)
sys.exit('unexpected fake gh invocation')
'''); fake.chmod(0o700)
    env=dict(os.environ,PATH=str(bindir)+os.pathsep+os.environ['PATH'],TAG='v1.2.3',GITHUB_REPOSITORY='audit-owner/audit-repo',BACKEND_IMAGE='ghcr.io/audit-owner/goatfarm-backend',FRONTEND_IMAGE='ghcr.io/audit-owner/goatfarm-frontend',EDGE_IMAGE='ghcr.io/audit-owner/goatfarm-edge',RUNNER_TEMP=str(tmp),AUDIT_COMMAND_LOG=str(tmp/'commands.jsonl'))
    before=subprocess.run(['bash','-c',preflight],env=env,text=True,capture_output=True)
    after=subprocess.run(['bash','-c',cleanup],env=env,text=True,capture_output=True)
    commands=[json.loads(x) for x in (tmp/'commands.jsonl').read_text().splitlines()]
    result={'assumed_state':'v1.2.3 and per-arch publish tags from a previous successful release exist; this invocation publishes nothing','preflight_exit':before.returncode,'preflight_stdout':before.stdout,'assembly_ownership_marker_exists':(tmp/'goatfarm-release-assembly-owned').exists(),'cleanup_exit':after.returncode,'cleanup_stdout':after.stdout,'delete_requests':[x for x in commands if x[:3]==['api','-X','DELETE']]}
    print(json.dumps(result,indent=2))
