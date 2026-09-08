import { expect, test, type Page } from "@playwright/test";

import { signIn } from "./helpers";

/** Wait until the run-setup form is rendered with the default toy herd. */
async function opsSimReady(page: Page) {
  await page.goto("/ops-simulation");
  await expect(
    page.getByRole("heading", { name: /^Ops Simulation$/ }),
  ).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("Run setup", { exact: true })).toBeVisible();
  // Default preset (toy herd: 10 does + 1 buck) is preloaded into the table.
  await expect(page.getByLabel(/^Tag for row 1 \(D1\)/)).toBeVisible();
}

test.describe("ops simulation", () => {
  test("renders its sections and replays the toy herd for one week", async ({
    page,
  }) => {
    test.setTimeout(180_000);
    await signIn(page);
    await opsSimReady(page);

    // Owner (farm creator) holds simulation.view — the permission wall must
    // not appear on this owner/manager-facing page.
    await expect(page.getByText("You don't have access to this page.")).toHaveCount(0);
    await expect(page.getByText("No run yet")).toBeVisible();
    for (const label of ["Start date", "Horizon (days)", "Seed"]) {
      await expect(page.getByLabel(label, { exact: true })).toBeVisible();
    }
    // Both seeded herd presets and the row editor are offered.
    await expect(
      page.getByRole("button", { name: "Toy herd (10 does + 1 buck)" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Mixed herd (pregnancy + quarantine + kids)" }),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Add animal" })).toBeVisible();

    // Smallest legal run: the 7-day horizon floor keeps the replay cheap.
    await page.getByLabel("Horizon (days)", { exact: true }).fill("7");
    await page.getByRole("button", { name: "Run 7 days" }).click();

    // All four summary cards plus every result section render.
    for (const stat of [
      "Herd (final day)",
      "Services → conceptions",
      "Bucket moves",
      "Feed delivered",
    ]) {
      await expect(page.getByText(stat, { exact: true })).toBeVisible({ timeout: 120_000 });
    }
    for (const section of ["Day timeline", "Transition matrix", "Animal journeys", "How this run works"]) {
      await expect(page.getByText(section, { exact: true })).toBeVisible();
    }
    // Day 1 is selected by default and shows its (routine or eventful) header.
    await expect(page.getByRole("heading", { name: /^Day 1 —/ })).toBeVisible();
    // The deterministic-seed footer names the model and the run parameters.
    await expect(page.getByText(/^Model .+ · seed 2026 · 7 days · 11 head at the start\.$/)).toBeVisible();
  });

  test("a row without a tag is reported inline, blocks the run, and never reaches the API", async ({
    page,
  }) => {
    test.setTimeout(90_000);
    await signIn(page);
    await opsSimReady(page);

    // If a request ever escaped the client-side guard it would be a bug;
    // abort rather than let it reach the backend so the counter below stays
    // a truthful "never sent", not "sent and failed".
    const runRequests: string[] = [];
    await page.route("**/api/ops-sim/run", (route) => {
      runRequests.push(route.request().url());
      return route.abort();
    });

    // Clearing the first animal's tag makes the herd unrunnable client-side.
    // The label regex must include the "(D1)" suffix: a bare /^Tag for row 1/
    // also matches rows 10 and 11 ("Tag for row 10 (D10)", "…row 11 (B1)")
    // and fails Playwright's strict mode.
    await page.getByLabel(/^Tag for row 1 \(D1\)/).fill("");
    await expect(
      page.locator("p").filter({ hasText: "Every animal needs a tag." }),
    ).toBeVisible();

    // Pressing Run must surface the same error as a toast and return before
    // the mutation can fire (sonner toasts render as [data-sonner-toast]).
    await page.getByRole("button", { name: /^Run \d+ days$/ }).click();
    await expect(
      page
        .locator("[data-sonner-toast]")
        .filter({ hasText: "Every animal needs a tag." }),
    ).toBeVisible();
    expect(runRequests).toEqual([]);

    // No result sections exist for the blocked run.
    await expect(page.getByText("Day timeline", { exact: true })).toHaveCount(0);
    await expect(page.getByText("Transition matrix", { exact: true })).toHaveCount(0);
  });
});
