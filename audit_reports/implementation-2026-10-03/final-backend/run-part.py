from pathlib import Path
import os,json,subprocess,sys
backend=Path('/Users/pradeepreddy/Desktop/goat-farm-management-main/backend')
manifest=json.loads(Path('/tmp/herdly-be-final-split/manifest.json').read_text())
part=manifest['parts'][int(sys.argv[1])-1]
env=os.environ|{'GOATFARM_TEST_DB':part['database'],'COVERAGE_FILE':part['coverage_file']}
command=[str(backend/'.venv/bin/python'),'-m','coverage','run','--branch','--source=app','-m','pytest','-q',f'--junitxml={part["junit"]}',*part['files']]
print(json.dumps({'part':part['index'],'database':part['database'],'expected_collected':part['collected'],'command':command}),flush=True)
result=subprocess.run(command,cwd=backend,env=env)
receipt={'part':part['index'],'exit_code':result.returncode,'expected_collected':part['collected'],'database':part['database'],'coverage_file':part['coverage_file'],'junit':part['junit']}
Path(f'/tmp/herdly-be-final-split/receipt-{part["index"]}.json').write_text(json.dumps(receipt,indent=2))
sys.exit(result.returncode)
