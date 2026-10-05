"""Short, offline peer controls; no container build or application edits."""
import hashlib,json,os,pathlib,shutil,subprocess,tempfile
root=pathlib.Path(__file__).resolve().parents[4]
node=pathlib.Path.home()/'.local/opt/node24/bin/node'
corepack=pathlib.Path.home()/'.local/opt/node24/lib/node_modules/corepack/dist/pnpm.js'
direct_pnpm=pathlib.Path.home()/'.cache/node/corepack/v1/pnpm/9.15.9/bin/pnpm.cjs'
package=root/'frontend/package.json'
before=hashlib.sha256(package.read_bytes()).hexdigest()
env={k:v for k,v in os.environ.items() if not k.startswith('COREPACK_')}
env.update(COREPACK_ENABLE_NETWORK='0',COREPACK_ENABLE_DOWNLOAD_PROMPT='0',CI='1')
results=[]
def run(name,args,cwd):
    p=subprocess.run(args,cwd=cwd,env=env,text=True,capture_output=True,timeout=10)
    result={'case':name,'command':[str(x) for x in args],'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
    results.append(result)
    return result
with tempfile.TemporaryDirectory(prefix='goatfarm-a26-peer-container-') as raw:
    tmp=pathlib.Path(raw)
    for name in ['package.json','pnpm-lock.yaml']:
        shutil.copyfile(root/'frontend'/name,tmp/name)
    original=run('actual packageManager via Corepack; network disabled',[node,corepack,'--version'],tmp)
    assert original['exit']!=0 and 'expected a semver version' in original['stderr']
    fixture=json.loads((tmp/'package.json').read_text())
    fixture['packageManager']='pnpm@9.15.9'
    (tmp/'package.json').write_text(json.dumps(fixture))
    valid=run('temporary valid version countercontrol; network disabled',[node,corepack,'--version'],tmp)
    assert valid['exit']==0 and valid['stdout'].strip()=='9.15.9'
    missing=run('same Docker COPY inputs; direct pinned pnpm offline install',[node,direct_pnpm,'install','--offline','--ignore-scripts','--frozen-lockfile'],tmp)
    assert missing['exit']!=0 and 'ENOENT' in missing['stdout']+missing['stderr'] and 'braces@3.0.3.patch' in missing['stdout']+missing['stderr']
print(json.dumps({'network_policy':'Corepack network disabled; pnpm --offline; no container builds','package_sha256_before':before,'package_sha256_after':hashlib.sha256(package.read_bytes()).hexdigest(),'patch_exists_in_repository':(root/'frontend/patches/braces@3.0.3.patch').is_file(),'results':results},indent=2))
