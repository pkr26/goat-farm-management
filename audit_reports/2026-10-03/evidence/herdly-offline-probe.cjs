const fs=require('node:fs'),vm=require('node:vm'),crypto=require('node:crypto'),path=require('node:path');
const ROOT='/Users/pradeepreddy/Desktop/goat-farm-management-main';
const ts=require(path.join(ROOT,'frontend/node_modules/typescript'));
const disk=new Map();let throws=false;
const storage={getItem:k=>disk.get(k)??null,setItem:(k,v)=>{if(throws)throw Error('QuotaExceededError');disk.set(k,v)},removeItem:k=>disk.delete(k)};
let releaseFirst,enteredFirst,firstEntered=new Promise(r=>enteredFirst=r),firstGate=new Promise(r=>releaseFirst=r),calls=[];
const ctx=vm.createContext({console,Date,URL,Headers,Response,AbortController,AbortSignal,DOMException,TextEncoder,crypto:crypto.webcrypto,atob,window:{localStorage:storage,sessionStorage:storage},navigator:{onLine:true},setTimeout,clearTimeout,fetch:async(path,init)=>{
 const farm=init.headers.get('X-Farm-Id');calls.push({path,farm});
 if(calls.length===1){enteredFirst();await firstGate}
 return new Response(JSON.stringify(farm==='10'?{status:'DONE'}:{detail:'Task not found'}),{status:farm==='10'?200:404,headers:{'Content-Type':'application/json'}})
}});
const cache={};
function load(name){
 const file=path.join(ROOT,'frontend/src',name.slice(2)+'.ts');
 if(cache[file])return cache[file].exports;
 const module={exports:{}};cache[file]=module;
 const js=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 const fn=vm.runInContext('(function(exports,require,module){'+js+'\n})',ctx,{filename:file});fn(module.exports,load,module);return module.exports;
}
(async()=>{
 const queue=load('@/lib/offline-queue'),api=load('@/lib/api-client');
 const a={actorScope:'1',farmScope:'10'};
 throws=true;
 const saved=queue.enqueueOfflineMutation('/api/tasks/1/complete',{method:'POST',headers:{'Idempotency-Key':'quota-key'}},a);
 console.log('quota test:',{saved,persistedCount:queue.readOfflineQueue().length});
 throws=false;
 queue.enqueueOfflineMutation('/api/tasks/1/complete',{method:'POST',headers:{'Idempotency-Key':crypto.randomUUID()}},a);
 queue.enqueueOfflineMutation('/api/tasks/2/complete',{method:'POST',headers:{'Idempotency-Key':crypto.randomUUID()}},a);
 api.setAccessToken('opaque-token','1');api.setCurrentFarmId('10');
 const capturedUser={id:1},capturedFarmId=10;
 const scopes=()=>({actorScope:String(capturedUser.id),farmScope:String(capturedFarmId)});
 const drain=queue.drainOfflineQueue(a,api.apiFetch,scopes);
 await firstEntered;
 api.setCurrentFarmId('20');releaseFirst();
 const outcome=await drain;
 console.log('actual API farm-switch test:',{calls,outcome,persistedCount:queue.readOfflineQueue().length});
})().catch(e=>{console.error(e);process.exitCode=1});
