import { createRequire } from 'node:module';
import { readFileSync, writeFileSync } from 'node:fs';
const require = createRequire(`${process.cwd()}/frontend/package.json`);
const { firefox } = require('@playwright/test');
const state = JSON.parse(readFileSync('/tmp/herdly-firefox-linux-runtime-state.json', 'utf8'));
const receipt = { started_at: new Date().toISOString(), endpoint: state.endpoint, image: state.image, client_version: require('@playwright/test/package.json').version, exposeNetwork: '<loopback>', authenticated: false, javaScriptEnabled: false, requests: [] };
const started = Date.now();
let browser;
try {
  console.log('checkpoint: connect start');
  browser = await firefox.connect(state.endpoint, { timeout: 10000, exposeNetwork: '<loopback>' });
  receipt.browser_version = browser.version();
  console.log('checkpoint: connected Firefox ' + receipt.browser_version);
  const context = await browser.newContext({ javaScriptEnabled: false });
  await context.route('**/api/**', route => route.abort());
  context.on('request', request => { const url = new URL(request.url()); if (url.protocol !== 'data:') receipt.requests.push({ origin: url.origin, path: url.pathname }); });
  const page = await context.newPage();
  await page.goto('data:text/html,<title>Herdly Firefox runtime probe</title><p>runtime ready</p>', { timeout: 10000 });
  receipt.data_url_title = await page.title();
  console.log('checkpoint: data URL title ' + receipt.data_url_title);
  if (receipt.data_url_title !== 'Herdly Firefox runtime probe') throw new Error('data URL title mismatch');
  const response = await page.goto('http://localhost:3000/login', { timeout: 5000, waitUntil: 'commit' });
  receipt.app_url = 'http://localhost:3000/login';
  receipt.app_status = response?.status();
  if (receipt.app_status !== 200) throw new Error(`unexpected app HTTP status ${receipt.app_status}`);
  if (receipt.requests.some(request => request.path.startsWith('/api/'))) throw new Error('unexpected API request with JavaScript disabled');
  receipt.result = 'passed';
  await context.close();
} catch (error) {
  receipt.result = 'failed';
  receipt.error = String(error);
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  receipt.elapsed_ms = Date.now() - started;
  receipt.finished_at = new Date().toISOString();
  writeFileSync('/tmp/herdly-firefox-linux-probe-receipt.json', JSON.stringify(receipt, null, 2) + '\n');
  console.log(JSON.stringify(receipt, null, 2));
}
// The one-shot probe has closed its owned context/browser; exit residual bridge handles.
process.exit(process.exitCode || 0);

