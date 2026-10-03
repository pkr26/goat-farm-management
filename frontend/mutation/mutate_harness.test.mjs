// Real Vitest contracts use temporary projects, never the shared application.
import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildCoverageMap, collectCoverage } from './mutate_cover.mjs';
import { digest, hash, inputs, latestCompatible } from './mutate_identity.mjs';
import { classifyVitest, createCampaign, guardMutant, judge, loadDone, runVitest, smoke } from './mutate_run.mjs';
import { summarize } from './mutate_report.mjs';
import { mutationTransformPlugin } from './mutate_transform.mjs';

const directory = path.dirname(fileURLToPath(import.meta.url));
const roots = [];
after(() => { for (const root of roots) rmSync(root, {recursive:true,force:true}); });
function fixture(testSource) {
  const root=mkdtempSync(path.join(os.tmpdir(),'herdly-fm-contract-')); roots.push(root);
  mkdirSync(path.join(root,'mutation'));mkdirSync(path.join(root,'src'));
  symlinkSync(path.resolve(directory,'../node_modules'),path.join(root,'node_modules'));
  for(const file of readdirSync(directory).filter(file=>file.endsWith('.mjs')&&!file.endsWith('.test.mjs'))) copyFileSync(path.join(directory,file),path.join(root,'mutation',file));
  writeFileSync(path.join(root,'package.json'),'{"type":"module"}');
  writeFileSync(path.join(root,'vitest.mutation.config.ts'),`import {defineConfig} from 'vitest/config';import {mutationTransformPlugin} from './mutation/mutate_transform.mjs';export default defineConfig({plugins:[mutationTransformPlugin()],test:{environment:'node',maxWorkers:1,include:['src/**/*.test.ts'],testTimeout:2000,hookTimeout:2000,coverage:{provider:'v8'}}});`);
  const source='export function alpha(){ return 1; }\nexport function beta(){ return 1; }\nexport function compared(){ return 1 === 1; }\n';
  writeFileSync(path.join(root,'src/logic.ts'),source);
  writeFileSync(path.join(root,'src/alpha.test.ts'),testSource??`import {expect,test} from 'vitest';import {alpha,beta,compared} from './logic';test('alpha is one',()=>{expect(alpha()).toBe(1);expect(beta()).toBe(1);expect(compared()).toBe(true);});`);
  writeFileSync(path.join(root,'src/beta.test.ts'),`import {expect,test} from 'vitest';import {beta} from './logic';test('beta is one',()=>expect(beta()).toBe(1));`);
  const first=source.indexOf('1;'),second=source.indexOf('1;',first+1),comparison=source.indexOf('===');
  const manifest={fileMeta:{'src/logic.ts':{sha256:hash(source)}},mutants:[
    {id:'m1',file:'src/logic.ts',line:1,op:'number',edits:[{start:first,end:first+1,original:'1',text:'2'}]},
    {id:'m2',file:'src/logic.ts',line:2,op:'number',edits:[{start:second,end:second+1,original:'1',text:'2'}]},
    {id:'m3',file:'src/logic.ts',line:3,op:'compare',edits:[{start:comparison,end:comparison+3,original:'===',text:'!=='}]},
  ]};
  writeFileSync(path.join(root,'mutation/manifest.json'),JSON.stringify(manifest));
  const map=buildCoverageMap({currentInputs:inputs(root),testFiles:['src/alpha.test.ts','src/beta.test.ts'],replacements:{'src/alpha.test.ts':{'src/logic.ts':[1,2,3]},'src/beta.test.ts':{'src/logic.ts':[2]}}});
  writeFileSync(path.join(root,'mutation/coverage-map.json'),JSON.stringify(map));
  return {root,source,manifest,campaign:createCampaign({root,full:true,timeoutMs:20000})};
}
function row(campaign,mutant,verdict,selectionMode='complete') {return {id:mutant.id,campaignId:campaign.id,provenance:campaign.provenance,mutantDigest:digest(mutant),verdict,selectionMode,baseline:{verdict:'SURVIVED'}};}

test('concurrent real Vitest mutations pass exact clean baselines and never write live source',async()=>{
  const {root,source,manifest,campaign}=fixture();
  const results=await Promise.all(manifest.mutants.slice(0,2).map(mutant=>judge(mutant,campaign)));
  assert.deepEqual(results.map(result=>result.verdict),['KILLED','KILLED'],JSON.stringify(results));
  assert.ok(results.every(result=>result.baseline.verdict==='SURVIVED'&&result.receipt.tests.some(task=>task.errors.some(error=>error.name==='AssertionError'))));
  assert.equal(readFileSync(path.join(root,'src/logic.ts'),'utf8'),source);
});

test('real thrown errors, collection failures, and assertion failures in setup are infrastructure',async()=>{
  for(const source of [
    `import {test} from 'vitest';test('crash',()=>{throw new Error('runtime infrastructure');});`,
    `import {test} from 'vitest';import './absent';test('never',()=>{});`,
    `import {test,expect,beforeEach} from 'vitest';beforeEach(()=>expect(1).toBe(2));test('hook fails',()=>{});`,
  ]) {const {root}=fixture(source);const result=await runVitest(null,['src/alpha.test.ts'],20000,{root});assert.equal(result.verdict,'INFRA_ERROR',JSON.stringify(result));}
});

test('a failing clean baseline prevents a mutant kill',async()=>{
  const {campaign,manifest}=fixture(`import {test,expect} from 'vitest';test('baseline fails',()=>expect(1).toBe(9));`);
  const result=await judge(manifest.mutants[0],campaign);assert.equal(result.verdict,'INFRA_ERROR');assert.equal(result.baseline.verdict,'KILLED');
});

test('timeout and empty or nonexistent test selections do not count as kills',async()=>{
  const {root}=fixture();assert.equal((await runVitest(null,['src/alpha.test.ts'],1,{root})).verdict,'INCONCLUSIVE_TIMEOUT');
  assert.equal((await runVitest(null,[],100,{root})).verdict,'INFRA_ERROR');
  assert.equal((await runVitest(null,['src/missing.test.ts'],20000,{root})).verdict,'INFRA_ERROR');
});

test('stale originals, overlapping edits, invalid contexts and source drift fail before spawning',async()=>{
  const {root,manifest,source}=fixture(),target=manifest.mutants[0];
  const publish=()=>writeFileSync(path.join(root,'mutation/manifest.json'),JSON.stringify(manifest));
  target.edits[0].original='9';publish();assert.equal((await runVitest('m1',['src/alpha.test.ts'],20000,{root})).verdict,'INVALID');
  target.edits[0].original='1';target.edits[0].text='continue';publish();assert.throws(()=>guardMutant('m1',root),/MUTATION_INVALID/);
  target.edits[0].text='2';target.edits.push({...target.edits[0]});publish();assert.throws(()=>guardMutant('m1',root),/overlapping/);
  target.edits.pop();publish();writeFileSync(path.join(root,'src/logic.ts'),source+'// drift\n');assert.throws(()=>guardMutant('m1',root),/fingerprint/);
});

test('the transform independently verifies exact fingerprints and keeps original bytes',()=>{
  const {root,source}=fixture(),previous=process.env.MUTANT_ID;
  try {process.env.MUTANT_ID='m1';const plugin=mutationTransformPlugin({root});assert.match(plugin.transform(source,path.join(root,'src/logic.ts')).code,/return 2/);assert.throws(()=>plugin.transform(source+' ',path.join(root,'src/logic.ts')),/fingerprint/);assert.equal(readFileSync(path.join(root,'src/logic.ts'),'utf8'),source);}
  finally {if(previous===undefined)delete process.env.MUTANT_ID;else process.env.MUTANT_ID=previous;}
});

test('test/dependency changes and reused ids invalidate provenance',()=>{
  const {root,campaign,manifest}=fixture(),original=row(campaign,manifest.mutants[0],'KILLED');assert.equal(latestCompatible([original],campaign).size,1);
  const changed={...campaign,manifest:{...manifest,mutants:[{...manifest.mutants[0],edits:[]}]}};assert.equal(latestCompatible([original],changed).size,0);
  writeFileSync(path.join(root,'pnpm-lock.yaml'),'changed dependency');assert.throws(()=>createCampaign({root}),/stale/);
});

test('resume retries latest error, timeout and uncovered rows and ignores historical ids',()=>{
  const {root,campaign,manifest}=fixture(),file=path.join(root,'mutation/results.jsonl');
  const rows=[{id:'m2',verdict:'KILLED'},row(campaign,manifest.mutants[0],'KILLED'),row(campaign,manifest.mutants[0],'INFRA_ERROR'),row(campaign,manifest.mutants[1],'INCONCLUSIVE_TIMEOUT'),row(campaign,manifest.mutants[2],'NO_COVERAGE')];
  writeFileSync(file,rows.map(item=>JSON.stringify(item)).join('\n'));assert.deepEqual([...loadDone(campaign)],[]);
  writeFileSync(file,JSON.stringify(row(campaign,manifest.mutants[0],'SURVIVED'))+'\n');assert.deepEqual([...loadDone(campaign)],['m1']);
});

test('true full mode executes every one of 1001 coverers and the exact baseline',async()=>{
  const {campaign,manifest}=fixture(),files=Array.from({length:1001},(_,i)=>`src/cover-${i}.test.ts`);
  campaign.coverage={testFiles:files,files:{'src/logic.ts':{'1':files.map((_,i)=>i)}}};const selected=[];
  const result=await judge(manifest.mutants[0],campaign,{run:async(id,tests)=>{selected.push([id,tests.length]);return {verdict:'SURVIVED',ms:0};}});
  assert.deepEqual(selected,[[null,1001],['m1',1001]]);assert.equal(result.selectionMode,'complete');assert.equal(result.tests,1001);
});

test('latest errors, timeouts and sampled kills are excluded from complete score',()=>{
  const {campaign,manifest}=fixture();const result=summarize(campaign,[row(campaign,manifest.mutants[0],'KILLED'),row(campaign,manifest.mutants[0],'INFRA_ERROR'),row(campaign,manifest.mutants[1],'INCONCLUSIVE_TIMEOUT'),row(campaign,manifest.mutants[2],'KILLED','sampled')]);
  assert.equal(result.score,null);assert.equal(result.scored,0);assert.equal(result.sampled,1);assert.equal(result.counts.INFRA_ERROR,1);
});

test('partial coverage replaces old paths, removes deleted tests and invalidates changed source positions',()=>{
  const currentInputs={'src/a.ts':'A','src/one.test.ts':'one','src/two.test.ts':'two','mutation/script.mjs':'harness'};
  const previous=buildCoverageMap({currentInputs,testFiles:['src/one.test.ts','src/two.test.ts'],replacements:{'src/one.test.ts':{'src/a.ts':[1,2]},'src/two.test.ts':{'src/a.ts':[2,3]}}});
  const map=buildCoverageMap({previous,currentInputs,testFiles:['src/one.test.ts'],replacements:{'src/one.test.ts':{'src/a.ts':[3]}}});assert.deepEqual(map.files,{'src/a.ts':{'3':[0]}});assert.equal(map.complete,true);assert.equal(map.contributions['src/two.test.ts'],undefined);
  const drift=buildCoverageMap({previous,currentInputs:{...currentInputs,'src/a.ts':'CHANGED'},testFiles:['src/one.test.ts','src/two.test.ts'],replacements:{'src/one.test.ts':{'src/a.ts':[7]}}});assert.equal(drift.complete,false);assert.deepEqual(drift.files,{'src/a.ts':{'7':[0]}});
});

test('real V8 coverage publishes atomically and failed partial refresh preserves exact map bytes',async()=>{
  const {root}=fixture();const map=await collectCoverage({root,concurrency:1});assert.equal(map.complete,true);assert.ok(map.files['src/logic.ts']);
  const mapFile=path.join(root,'mutation/coverage-map.json'),before=readFileSync(mapFile);writeFileSync(path.join(root,'src/alpha.test.ts'),`import {test,expect} from 'vitest';test('fail',()=>expect(1).toBe(2));`);
  await assert.rejects(()=>collectCoverage({root,only:['alpha'],concurrency:1}),/published map unchanged/);assert.deepEqual(readFileSync(mapFile),before);
});

test('smoke requires SURVIVED baseline and CLI exits nonzero on required failure',async()=>{
  const {root,campaign}=fixture(`import {test,expect} from 'vitest';test('baseline fails',()=>expect(1).toBe(2));`);
  assert.equal(await smoke(campaign,{run:async()=>({verdict:'INCONCLUSIVE_TIMEOUT'}),judgeRun:async()=>({verdict:'KILLED'})}),false);
  const child=spawnSync(process.execPath,['mutation/mutate_run.mjs','--smoke'],{cwd:root,encoding:'utf8',timeout:30000});assert.equal(child.status,1,child.stdout+child.stderr);
});

test('dry-run preserves sources/maps/results and does not start Vitest',()=>{
  const {root}=fixture(),source=readFileSync(path.join(root,'src/logic.ts')),map=readFileSync(path.join(root,'mutation/coverage-map.json'));
  const child=spawnSync(process.execPath,['mutation/mutate_run.mjs','--full','--dry-run'],{cwd:root,encoding:'utf8',timeout:10000});assert.equal(child.status,0,child.stderr);assert.deepEqual(JSON.parse(child.stdout).targets,['m1','m2','m3']);
  assert.deepEqual(readFileSync(path.join(root,'src/logic.ts')),source);assert.deepEqual(readFileSync(path.join(root,'mutation/coverage-map.json')),map);assert.equal(existsSync(path.join(root,'mutation/results.jsonl')),false);
});

test('all process exits require a structured assertion receipt to be credited a kill',()=>{
  const receipt={reason:'failed',suiteErrors:[],unhandledErrors:[],tests:[{state:'fail',hooks:{},errors:[{name:'AssertionError'}]}]};assert.equal(classifyVitest(1,receipt),'KILLED');for(const code of [null,0,2,3,4,5])assert.equal(classifyVitest(code,receipt),'INFRA_ERROR');assert.equal(classifyVitest(1,null),'INFRA_ERROR');
});

test('successful smoke returns zero after actual baseline and repeated assertion kills',()=>{
  const {root}=fixture();
  const child=spawnSync(process.execPath,['mutation/mutate_run.mjs','--smoke'],{cwd:root,encoding:'utf8',timeout:30000});
  assert.equal(child.status,0,child.stdout+child.stderr);assert.equal(JSON.parse(child.stdout).smoke,'passed');
});

test('reverification runs all 41 coverers without dedicated tests and appends a complete receipt',()=>{
  const {root,manifest}=fixture();
  const files=[];
  const replacements={};
  for(let index=0;index<41;index++) {
    const file=`src/consumer-${index}.test.ts`;files.push(file);
    writeFileSync(path.join(root,file),`import {test,expect} from 'vitest';import {alpha} from './logic';test('consumer ${index}',()=>expect(alpha()).toBe(1));`);
    replacements[file]={'src/logic.ts':[1]};
  }
  writeFileSync(path.join(root,'mutation/coverage-map.json'),JSON.stringify(buildCoverageMap({currentInputs:inputs(root),testFiles:files,replacements})));
  const campaign=createCampaign({root});
  const resultFile=path.join(root,'mutation/results.jsonl');writeFileSync(resultFile,JSON.stringify(row(campaign,manifest.mutants[0],'SURVIVED'))+'\n');
  const child=spawnSync(process.execPath,['mutation/mutate_reverify.mjs','--write','--workers','1'],{cwd:root,encoding:'utf8',timeout:30000});
  assert.equal(child.status,0,child.stdout+child.stderr);
  const latest=JSON.parse(readFileSync(resultFile,'utf8').trim().split('\n').at(-1));
  assert.equal(latest.verdict,'KILLED');assert.equal(latest.tests,41);assert.equal(latest.selectionMode,'complete');assert.equal(latest.baseline.verdict,'SURVIVED');
});
