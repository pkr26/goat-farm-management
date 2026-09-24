import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { signIn } from "./helpers";

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
  "/screening", // 2026-09-23 verification: the review queue is a worker-facing page too
];

test.describe("a11y gate", () => {
  for (const path of PAGES) {
    test(`${path} has no serious accessibility violations`, async ({ page }) => {
      test.setTimeout(60_000);
      if (["/dashboard", "/tasks", "/worker", "/screening"].includes(path)) {
        await signIn(page);
      }
      if (path === "/worker/login") {
        await page.addInitScript(() => {
          window.localStorage.setItem("herdly.tabletFarm", "1");
        });
      }
      await page.goto(path);
      // Wait for real content: the app's own loading states settle (the
      // dashboard skeleton gives way to headings) or the static shell paints.
      // networkidle never settles — background polling keeps connections open.
      try {
        await page.waitForLoadState("networkidle", { timeout: 8_000 });
      } catch {
        /* polling pages never idle — the content wait below is the real gate */
      }
      await page.getByRole("heading", { level: 1 }).or(page.getByRole("main")).first().waitFor({ timeout: 15_000 });

      const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
        .analyze();

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
});
