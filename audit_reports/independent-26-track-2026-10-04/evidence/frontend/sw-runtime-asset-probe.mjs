import vm from 'node:vm';
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const origin = 'https://isolated-audit.invalid';
const backing = new Map();
const url = (input) => new URL(typeof input === 'string' ? input : input.url, origin).href;
const cache = {
  match: async (input) => backing.get(url(input))?.clone(),
  put: async (input, response) => { backing.set(url(input), response.clone()); },
  delete: async (input) => backing.delete(url(input)),
  keys: async () => [...backing.keys()].map((key) => new Request(key)),
  addAll: async (inputs) => { for (const input of inputs) await cache.put(input, new Response('/* immutable chunk */')); },
};
const handlers = {};
const shell = () => new Response('<html><script src="/_next/static/chunks/shell.hash.js"></script></html>', {headers:{'Content-Type':'text/html'}});
const ctx = vm.createContext({ URL, Response, Request, Set, Promise, setTimeout, clearTimeout,
 self:{location:{origin}, addEventListener:(type, handler) => { handlers[type] = handler; }, skipWaiting:async()=>{}, clients:{claim:async()=>{}}},
 caches:{open:async()=>cache, match:cache.match, keys:async()=>['herdly-worker-v3'], delete:async()=>true},
 fetch:async(input)=>url(input).includes('/_next/static/') ? new Response('/* Telugu lazy locale */') : shell(),
});
vm.runInContext(await fs.readFile('frontend/public/sw.js', 'utf8'), ctx);
let install;
handlers.install({waitUntil:(p)=>install=p});
await install;
const locale = new Request(`${origin}/_next/static/chunks/te.lazy.hash.js`);
let served; let durable;
handlers.fetch({request:locale, respondWith:(p)=>served=p, waitUntil:(p)=>durable=p});
await served; await durable;
assert.ok(await cache.match(locale));
console.log('After online lazy Telugu load: cached =', !!await cache.match(locale));
ctx.request = new Request(`${origin}/worker`);
ctx.response = shell();
await vm.runInContext('cacheCompleteShell(request, response)',ctx);
console.log('After one same-build worker navigation: cached =', !!await cache.match(locale));
ctx.response = shell();
await vm.runInContext('cacheCompleteShell(request, response)',ctx);
console.log('After two same-build worker navigations: cached =', !!await cache.match(locale));
assert.equal(await cache.match(locale), undefined);
console.log('CONFIRMED: same-build shell refreshes evict the current build\'s lazily loaded locale asset.');
