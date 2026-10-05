import { firefox } from '../../../frontend/node_modules/@playwright/test/index.mjs';

const started = Date.now();
let browser;
try {
  browser = await firefox.launch({ headless: true, timeout: 20_000 });
  const page = await browser.newPage();
  await page.setContent('<!doctype html><title>Firefox control</title><main>Control page</main>');
  console.log(JSON.stringify({ passed: true, title: await page.title(), elapsedMs: Date.now() - started }));
} catch (error) {
  console.error(JSON.stringify({ passed: false, elapsedMs: Date.now() - started, error: String(error) }));
  process.exitCode = 1;
} finally {
  await browser?.close();
}
