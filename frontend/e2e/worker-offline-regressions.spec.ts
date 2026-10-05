import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("fresh worker is Telugu-first and the offline route owns one primary landmark", async ({ page }, testInfo) => {
  await page.goto("/worker/login");
  await expect(page.locator("html")).toHaveAttribute("lang", "te");
  await expect(page.getByRole("button", { name: "తెలుగు", exact: true })).toHaveAttribute("aria-pressed", "true");
  expect(await page.evaluate(() => localStorage.getItem("herdly.language"))).toBe("te");
  await page.goto("/worker/offline");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByRole("main")).toHaveCount(1);
  const results = await new AxeBuilder({ page }).withRules([
    "landmark-main-is-top-level", "landmark-no-duplicate-main", "landmark-unique",
  ]).analyze();
  await testInfo.attach("worker-landmarks.json", { body: JSON.stringify(results, null, 2), contentType: "application/json" });
  expect(results.violations).toEqual([]);
});

test("Telugu cold reload survives same-build shell refreshes without the HTTP cache", async ({ page, context, browserName }, testInfo) => {
  test.skip(browserName !== "chromium", "CDP controls the independent HTTP cache prerequisite on Chromium.");
  test.setTimeout(90_000);
  await page.goto("/worker/login");
  await expect(page.locator("html")).toHaveAttribute("lang", "te");
  await page.waitForFunction(() => !!navigator.serviceWorker.controller);
  // Initial imports precede worker control. The explicit warmup must retain
  // every loaded static chunk before any reload supplies a second chance.
  const assets = await page.evaluate(() => performance.getEntriesByType("resource").map((item) => item.name)
    .filter((name) => new URL(name).pathname.startsWith("/_next/static/")));
  expect(assets.length).toBeGreaterThan(0);
  await expect.poll(() => page.evaluate(async (urls) => {
    return (await Promise.all(urls.map((url) => caches.match(url)))).every(Boolean);
  }, assets)).toBe(true);
  const initialBuild = await page.locator('meta[name="herdly-build"]').getAttribute("content");
  expect(initialBuild).toBeTruthy();
  for (let i = 0; i < 3; i += 1) {
    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.getByRole("button", { name: "తెలుగు", exact: true })).toHaveAttribute("aria-pressed", "true");
    expect(await page.locator('meta[name="herdly-build"]').getAttribute("content")).toBe(initialBuild);
    // Wait for durable navigation publication, not just the live HTML.
    await expect.poll(() => page.evaluate(async () => {
      const response = await caches.match("/__herdly_worker_asset_state_v1__");
      return response ? (await response.json()).build : null;
    })).toBe(initialBuild);
  }
  const cdp = await context.newCDPSession(page);
  await cdp.send("Network.clearBrowserCache");
  await context.setOffline(true);
  try {
    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.locator("html")).toHaveAttribute("lang", "te");
    await expect(page.getByRole("button", { name: "తెలుగు", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("worker-setup-start")).toBeVisible();
    await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
    await testInfo.attach("offline-telugu.png", { body: await page.screenshot({ fullPage: true }), contentType: "image/png" });
  } finally { await context.setOffline(false); }
});
