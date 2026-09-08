import { expect, test, type Page } from "@playwright/test";

import { signIn } from "./helpers";

/** Wait until the breed-preset defaults have loaded (the "Plan" button needs
 *  them) and the planner's three static sections are rendered. */
async function plannerReady(page: Page) {
  await page.goto("/planner");
  await expect(page.getByRole("heading", { name: /^Planner$/ })).toBeVisible({
    timeout: 20_000,
  });
  for (const card of ["Sale targets", "Plan basis", "Saved plans"]) {
    await expect(page.getByText(card, { exact: true }).first()).toBeVisible();
  }
  // Preset assumptions load asynchronously; "Add target" stays disabled until
  // they arrive, so waiting for it doubles as the defaults-loaded signal.
  await expect(page.getByRole("button", { name: "Add target" })).toBeEnabled({
    timeout: 20_000,
  });
}

test.describe("planner", () => {
  test("renders its sections, plans a sale target, and keeps owner access", async ({
    page,
  }) => {
    test.setTimeout(180_000);
    await signIn(page);
    await plannerReady(page);

    // Owner (farm creator) holds simulation.view — the permission wall must
    // not appear on this owner/manager-facing page.
    await expect(page.getByText("You don't have access to this page.")).toHaveCount(0);
    // Empty states before any interaction.
    await expect(page.getByText("No sale targets yet")).toBeVisible();
    await expect(page.getByText("No saved plans")).toBeVisible();

    // Without a target the run is blocked client-side.
    await expect(page.getByRole("button", { name: "Plan", exact: true })).toBeDisabled();
    // "Add target" defaults to a sale of 20 male growers one year out — the
    // smallest meaningful plan with no extra fixtures.
    await page.getByRole("button", { name: "Add target" }).click();
    await expect(page.getByRole("spinbutton", { name: "Count", exact: true })).toHaveValue("20");
    await expect(page.getByRole("button", { name: "Plan", exact: true })).toBeEnabled();

    await page.getByRole("button", { name: "Plan", exact: true }).click();
    // Feasibility verdict card plus the dated action list and the requirement
    // chains are the page's core output sections. The verdict branch itself is
    // deliberately not pinned: the plan anchors to the current month, so the
    // feasibility math legitimately shifts with the calendar — asserting one
    // branch would turn the suite into a time bomb. A regression that breaks
    // the verdict rendering itself still fails here.
    await expect(
      page.getByText(/^(The plan is feasible|The plan cannot fully close)( · stale — re-run after edits)?$/),
    ).toBeVisible({ timeout: 120_000 });
    await expect(page.getByText("What to do and when", { exact: true })).toBeVisible();
    await expect(
      page.getByText("Why these numbers — each target worked backward", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("Stage plan — the herd shape the targets require", { exact: true }),
    ).toBeVisible();
    // No error banner survived the run. Scoped to the page's inline error
    // paragraphs (<p role="alert">) because Next.js's route announcer is also
    // role="alert" on a <div> and would otherwise always match with count 1.
    await expect(page.locator('p[role="alert"]')).toHaveCount(0);
  });

  test("a target in the plan's start month is rejected inline and blocks the run", async ({
    page,
  }) => {
    test.setTimeout(90_000);
    await signIn(page);
    await plannerReady(page);

    await page.getByRole("button", { name: "Add target" }).click();
    // The plan start is the current month; a sale needs lead time, so moving
    // the target into that same month must fail validation client-side.
    const startMonth = await page.locator("#planner-start").inputValue();
    await page.getByLabel("Sale month").fill(startMonth);
    await expect(
      page.getByText("Target 1: the month must come after the plan start"),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Plan", exact: true })).toBeDisabled();
    // No report section exists for the blocked run.
    await expect(page.getByText("What to do and when", { exact: true })).toHaveCount(0);
  });
});
