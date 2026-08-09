import { expect, test } from "@playwright/test";

import {
  createAnimal,
  monthsAgo,
  openAnimalProfile,
  pickRemoteOption,
  pickSelectOption,
  signIn,
  uniqueTag,
} from "./helpers";

test.describe("health flow", () => {
  test("record a vaccine event — event log, profile health card, schedule badge", async ({
    page,
  }) => {
    test.setTimeout(120_000);
    const tag = uniqueTag("E2E-VAX");
    // Product name starts with the seeded template name so the schedule's
    // fuzzy matcher links the event to the PPR template.
    const product = `PPR ${uniqueTag("vax")}`;
    await signIn(page);

    // 5 months old: every age-based first dose (3–4 months) is already due.
    await createAnimal(page, {
      tag,
      historicalImportReason: "E2E health-flow fixture",
      dateOfBirth: monthsAgo(5),
    });

    // Record a VACCINE event on the animal through the Health page dialog.
    await page.goto("/health");
    await page.getByRole("button", { name: "+ Add event" }).click();
    const dialog = page.getByRole("dialog", { name: "Add health event" });
    await expect(dialog).toBeVisible();
    await pickRemoteOption(dialog, "Animal *", new RegExp(tag), tag);
    // Type defaults to VACCINE.
    await dialog.getByLabel("Product name").fill(product);
    await dialog.getByLabel("Dose").fill("1 ml");
    await pickSelectOption(dialog, "Route", "SC");
    await dialog.getByLabel(/Total cost/).fill("120");
    await dialog.getByRole("button", { name: "Save event" }).click();
    await expect(page.getByText("Health event recorded.")).toBeVisible();
    await expect(dialog).toBeHidden();

    // The event shows in the farm-wide log.
    const logRow = page.getByRole("row", { name: new RegExp(tag) });
    await expect(logRow).toBeVisible();
    await expect(logRow.getByText("VACCINE")).toBeVisible();
    await expect(logRow.getByText(product)).toBeVisible();
    await expect(logRow.getByText("SC")).toBeVisible();

    // And on the animal's profile health card.
    await openAnimalProfile(page, tag);
    await expect(page.getByText("Health events (1)")).toBeVisible();
    await expect(page.getByRole("row", { name: new RegExp(product) })).toBeVisible();

    // The vaccination schedule flips the PPR row to DONE; the other templates
    // of an unvaccinated 5-month-old are still OVERDUE.
    await page.goto("/health");
    await pickRemoteOption(
      page.locator("body"),
      "View schedule for",
      new RegExp(tag),
      tag,
    );
    await page.getByRole("button", { name: "View" }).click();
    await expect(page).toHaveURL(/\/health\/schedule\/\d+\?returnTo=/, {
      timeout: 15_000,
    });
    await expect(
      page.getByRole("heading", { name: /Vaccination schedule/ }),
    ).toBeVisible();

    const pprRow = page.getByRole("row").filter({ hasText: "PPR" });
    await expect(pprRow).toHaveCount(1);
    await expect(pprRow.getByText("DONE", { exact: true })).toBeVisible();
    await expect(page.getByText("OVERDUE", { exact: true }).first()).toBeVisible();
  });
});
