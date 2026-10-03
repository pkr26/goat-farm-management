import asyncio,hashlib,json,pathlib,shutil,xml.etree.ElementTree as ET
import asyncpg
root=pathlib.Path('/Users/pradeepreddy/Desktop/goat-farm-management-main')
p=pathlib.Path('/tmp/herdly-be-final-split')
m=json.loads((p/'manifest.json').read_text())
assert len(m['all_collected_nodeids']) == len(set(m['all_collected_nodeids'])) == 5128
changed=[f for f,h in m['source_sha256'].items() if hashlib.sha256((root/f).read_bytes()).hexdigest()!=h]
assert not changed, changed
file_lists=[set(part['files']) for part in m['parts']]
assert not file_lists[0]&file_lists[1]
assert set(n.split('::')[0] for n in m['all_collected_nodeids']) == file_lists[0]|file_lists[1]
counts=dict(tests=0,failures=0,errors=0,skipped=0)
parts=[]
for n in (1,2):
 receipt=json.loads((p/f'receipt-{n}.json').read_text()); assert receipt['exit_code']==0
 suites=ET.parse(p/f'part-{n}.xml').getroot().findall('testsuite')
 c={key:sum(int(s.get(key,'0')) for s in suites) for key in counts}
 assert c['tests']==2564 and c['failures']==c['errors']==0,c
 for key in counts: counts[key]+=c[key]
 parts.append(dict(part=n,counts=c,receipt=receipt))
assert counts==dict(tests=5128,failures=0,errors=0,skipped=4),counts
cov=json.loads((p/'coverage.json').read_text())['totals']
assert cov['percent_covered'] >= m['required_aggregate_coverage']
async def cleanup_check():
 c=await asyncpg.connect('postgresql://localhost:5432/postgres')
 try: return await c.fetchval('select count(*) from pg_database where datname=any($1::text[])',[x['database'] for x in m['parts']])
 finally: await c.close()
remaining=asyncio.run(cleanup_check());assert remaining==0,remaining
out=root/'audit_reports/implementation-2026-10-03/final-backend';out.mkdir(exist_ok=True)
for f in p.iterdir():
 if f.is_file():shutil.copy2(f,out/f.name)
for n in (1,2):shutil.copy2(pathlib.Path(f'/tmp/herdly-be-final-part-{n}.log'),out/f'part-{n}.log')
shutil.copy2('/tmp/herdly-run-backend-part.py',out/'run-part.py')
shutil.copy2(__file__,out/'aggregate.py')
collection=pathlib.Path('/tmp/herdly-be-final-collection.log')
if collection.exists():shutil.copy2(collection,out/'collection.log')
summary=dict(collected=counts['tests'],passed=counts['tests']-counts['skipped'],skipped=counts['skipped'],failures=0,errors=0,parts=parts,coverage=cov,required_aggregate_coverage=m['required_aggregate_coverage'],line_percent=100*cov['covered_lines']/cov['num_statements'],branch_percent=100*cov['covered_branches']/cov['num_branches'],source_fingerprint_files_verified=len(m['source_sha256']),source_fingerprint_mismatches=changed,disjoint_complete_collection=True,owned_databases_remaining=remaining)
(out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))
