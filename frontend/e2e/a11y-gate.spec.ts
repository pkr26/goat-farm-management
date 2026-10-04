import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

import { createAnimal, e2eCredentials, monthsAgo, openAnimalProfile, signIn, uniqueTag } from "./helpers";

/**
 * Accessibility gate (ITEM 10, 2026-09-21 playbook): axe-core over the pages
 * operators actually touch, on both languages' shells. Critical/serious
 * findings fail the gate; the complete axe result (including moderate and
 * minor impacts) is attached to the Playwright report for every state.
 */

const PAGES = [
  "/dashboard",
  "/owner",
  "/animals",
  "/buckets",
  "/breeding",
  "/kidding",
  "/feeding",
  "/feeding/inventory",
  "/feeding/recipes",
  "/purchases",
  "/reports",
  "/tasks",
  "/worker",
  "/login",
  "/register",
  "/farm-select",
  "/worker/login",
  "/worker/offline",
  "/screening", // 2026-09-23 verification: the review queue is a worker-facing page too
  "/team",
  "/finance/insurance",
  "/health",
  "/planner",
  "/simulation",
  "/finance",
];

const PUBLIC_PAGES = new Set(["/login", "/register", "/worker/login", "/worker/offline"]);
const WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22a", "wcag22aa"];

async function scanAndReport(page: Page, testInfo: TestInfo, state: string) {
  const results = await new AxeBuilder({ page })
    .withTags(WCAG_TAGS)
    // Base UI deliberately exposes these visually-hidden focus sentinels as
    // role=button only in WebKit so VoiceOver's virtual cursor can enter the
    // focus trap. They are framework mechanics, not application commands.
    .exclude("[data-base-ui-focus-guard]")
    .analyze();
  await testInfo.attach(`axe-${state.replace(/[^a-z0-9-]+/gi, "-")}.json`, {
    body: Buffer.from(JSON.stringify({ url: page.url(), violations: results.violations }, null, 2)),
    contentType: "application/json",
  });
  const serious = results.violations.filter((violation) =>
    violation.impact === "critical" || violation.impact === "serious"
  );
  const summary = serious
    .map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target).slice(0, 3).join(", ")}`)
    .join("; ");
  expect(serious, `serious a11y violations in ${state}: ${summary}`).toEqual([]);
}

test.describe("a11y gate", () => {
  for (const language of ["en", "te"] as const) {
    for (const path of PAGES) {
      test(`${path} (${language}) has no serious accessibility violations`, async ({ page, request }, testInfo) => {
        test.setTimeout(60_000);
        const credentials = e2eCredentials();
        if (!PUBLIC_PAGES.has(path)) {
          await signIn(page);
        }
        await page.addInitScript((locale) => {
          window.localStorage.setItem("herdly.language", locale);
        }, language);
        if (path === "/worker/login") {
          const login = await request.post("/api/auth/login", {
            data: { email: credentials.email, password: credentials.password },
          });
          expect(login.ok()).toBeTruthy();
          const { access_token } = await login.json() as { access_token: string };
          const farms = await request.get("/api/auth/farms", {
            headers: { Authorization: `Bearer ${access_token}` },
          });
          expect(farms.ok()).toBeTruthy();
          const farm = (await farms.json() as { id: number; name: string }[]).find(item => item.name === credentials.farmName);
          expect(farm).toBeDefined();
          await page.addInitScript((id) => {
            window.localStorage.setItem("herdly.tabletFarm", String(id));
          }, farm!.id);
        }
        const response = await page.goto(path);
        expect(response?.status(), `Expected the actual ${path} surface`).toBe(200);
        await expect(page).toHaveURL(new RegExp(`${path.replaceAll("/", "\\/")}$`));
        // Wait for real content: the app's own loading states settle (the
        // dashboard skeleton gives way to headings) or the static shell paints.
        // networkidle never settles — background polling keeps connections open.
        try {
          await page.waitForLoadState("networkidle", { timeout: 8_000 });
        } catch {
          /* polling pages never idle — the content wait below is the real gate */
        }
        await page.getByRole("heading", { level: 1 }).or(page.getByRole("main")).first().waitFor({ timeout: 15_000 });
        // Bootstrap can redirect after the first URL assertion. Scanning a
        // delayed login page must not count as coverage of this protected route.
        await expect(page).toHaveURL(new RegExp(`${path.replaceAll("/", "\\/")}$`));

        await scanAndReport(page, testInfo, `${path}-${language}-landing`);
        await expect(page).toHaveURL(new RegExp(`${path.replaceAll("/", "\\/")}$`));
      });
    }
  }

  test("animal validation modal on a manager phone and dashboard on a portrait tablet", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 360, height: 800 });
    await signIn(page);
    await page.goto("/animals?new=1");
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    // The create form deliberately accepts an empty tag and generates one on
    // the server. Exercise a genuinely invalid state instead of submitting a
    // valid blank form and accidentally creating an animal.
    await dialog.getByLabel("Purchase date").fill("9999-12-31");
    await dialog.locator('button[type="submit"]').click();
    await expect(dialog.getByRole("alert").first()).toBeVisible();
    await scanAndReport(page, testInfo, "animals-invalid-create-phone");

    await page.setViewportSize({ width: 768, height: 1024 });
    await page.goto("/dashboard");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await scanAndReport(page, testInfo, "dashboard-portrait-tablet");
  });

  test("animal detail state has no serious accessibility violations", async ({ page }, testInfo) => {
    await signIn(page);
    const tag = uniqueTag("A11Y");
    await createAnimal(page, {
      tag,
      historicalImportReason: "Accessibility detail-state fixture",
      dateOfBirth: monthsAgo(14),
      entryWeightKg: 24,
      bucket: "FOUNDATION",
    });
    await openAnimalProfile(page, tag);
    await scanAndReport(page, testInfo, "animal-detail");
  });

  test("permissions error recovery state has no serious accessibility violations", async ({ page }, testInfo) => {
    await signIn(page);
    await page.route("**/api/auth/permissions", (route) =>
      route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Unavailable" }) }),
    );
    await page.goto("/dashboard");
    await expect(page.getByRole("alert").first()).toBeVisible();
    await scanAndReport(page, testInfo, "permissions-error");
  });
});
