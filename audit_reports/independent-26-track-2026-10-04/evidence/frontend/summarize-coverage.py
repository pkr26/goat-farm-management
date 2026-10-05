import json, math
from pathlib import Path
root=Path.cwd()
out=root/'audit_reports/independent-26-track-2026-10-04/evidence/frontend'
summary=json.loads((out/'coverage/coverage-summary.json').read_text())
metrics=['statements','branches','functions','lines']
files={Path(k).relative_to(root/'frontend').as_posix():v for k,v in summary.items() if k!='total'}
rules=[
('Global',None,[90,87,90,90]),
('src/app/[(]app[)]/**','src/app/(app)/',[80,60,80,80]),
('src/app/**','src/app/',[55,50,55,55]),
('src/components/**','src/components/',[75,70,80,75]),
('src/lib/**','src/lib/',[85,85,85,85]),
('src/proxy.ts','src/proxy.ts',[80,80,80,80]),
('src/app/error.tsx','src/app/error.tsx',[80,80,80,80]),
('src/app/manifest.ts','src/app/manifest.ts',[80,80,80,80]),
('src/app/healthz/route.ts','src/app/healthz/route.ts',[80,80,80,80]),
('src/app/[(]app[)]/layout.tsx','src/app/(app)/layout.tsx',[80,80,80,80]),
('src/app/worker/layout.tsx','src/app/worker/layout.tsx',[80,70,70,80]),
]
output=[]
for name,pattern,floors in rules:
 selected=files if pattern is None else {k:v for k,v in files.items() if k.startswith(pattern) if pattern.endswith('/') or k==pattern}
 values={}
 for metric,floor in zip(metrics,floors):
  total=sum(value[metric]['total'] for value in selected.values())
  covered=sum(value[metric]['covered'] for value in selected.values())
  pct=100 if total==0 else math.floor(covered*10000/total)/100
  values[metric]={'covered':covered,'total':total,'pct':pct,'minimum':floor,'passed':pct>=floor}
 output.append({'rule':name,'files':len(selected),'metrics':values,'passed':all(v['passed'] for v in values.values())})
(out/'coverage-threshold-results.json').write_text(json.dumps(output,indent=2)+'\n')
rows=['| Configured scope | Files | Statements actual / floor | Branches actual / floor | Functions actual / floor | Lines actual / floor | Result |',
'|---|---:|---:|---:|---:|---:|---|']
for item in output:
 rows.append('| `'+item['rule']+'` | '+str(item['files'])+' | '+' | '.join(f"{item['metrics'][m]['pct']:.2f}% / {item['metrics'][m]['minimum']}%" for m in metrics)+' | '+('PASS' if item['passed'] else 'FAIL')+' |')
(out/'coverage-threshold-table.md').write_text('\n'.join(rows)+'\n')
print('\n'.join(rows))
