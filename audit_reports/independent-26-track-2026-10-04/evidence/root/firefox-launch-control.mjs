import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const require = createRequire(new URL('../../../../frontend/package.json', import.meta.url));
const {firefox} = require('@playwright/test');
const started = Date.now();
function note(stage, extra={}) { console.log(JSON.stringify({stage,elapsed_ms:Date.now()-started,...extra})); }
let browser;
try {
  note('launch_start', {pid:process.pid,node:process.version,executable:firefox.executablePath(),timeout_ms:15000});
  browser = await firefox.launch({headless:true,timeout:15000});
  note('launch_success');
  const page = await browser.newPage();
  await page.goto('about:blank', {timeout:3000,waitUntil:'load'});
  note('about_blank_success', {url:page.url(),title:await page.title()});
} catch (error) {
  note('failure', {name:error.name,message:error.message,stack:error.stack});
  process.exitCode=1;
} finally {
  if(browser) { await browser.close(); note('browser_closed'); }
}
