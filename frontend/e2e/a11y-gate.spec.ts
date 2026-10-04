import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { E2E_EMAIL, E2E_FARM_NAME, E2E_PASSWORD, signIn } from "./helpers";

/**
 * Accessibility gate (ITEM 10, 2026-09-21 playbook): axe-core over the pages
 * field workers actually touch, on both languages' shells. Zero
 * violations-or-bust on the serious rules (critical + serious); moderate/
 * minor findings are logged for triage without failing the gate — matching
 * how the a11y quickies were prioritized during the audit.
 */

const PAGES = [
  "/dashboard",
  "/tasks",
  "/worker",
  "/login",
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

test.describe("a11y gate", () => {
  for (const language of ["en", "te"] as const) {
    for (const path of PAGES) {
      test(`${path} (${language}) has no serious accessibility violations`, async ({ page, request }) => {
        test.setTimeout(60_000);
        if (!["/login", "/worker/login", "/worker/offline"].includes(path)) {
          await signIn(page);
        }
        await page.addInitScript((locale) => {
          window.localStorage.setItem("herdly.language", locale);
        }, language);
        if (path === "/worker/login") {
          const login = await request.post("/api/auth/login", {
            data: { email: E2E_EMAIL, password: E2E_PASSWORD },
          });
          expect(login.ok()).toBeTruthy();
          const { access_token } = await login.json() as { access_token: string };
          const farms = await request.get("/api/auth/farms", {
            headers: { Authorization: `Bearer ${access_token}` },
          });
          expect(farms.ok()).toBeTruthy();
          const farm = (await farms.json() as { id: number; name: string }[]).find(item => item.name === E2E_FARM_NAME);
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

        const results = await new AxeBuilder({ page })
          .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
          .analyze();
        await expect(page).toHaveURL(new RegExp(`${path.replaceAll("/", "\\/")}$`));

        const serious = results.violations.filter((violation) =>
          violation.impact === "critical" || violation.impact === "serious"
        );
        const summary = serious
          .map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target).slice(0, 3).join(", ")}`)
          .join("; ");
        expect(
          serious,
          `serious a11y violations on ${path}: ${summary}`,
        ).toEqual([]);
      });
    }
  }
});
