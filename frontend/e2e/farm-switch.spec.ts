import { expect, test } from "@playwright/test";

import { createAnimal, signIn, uniqueTag } from "./helpers";

/** Owner nav, mirrored from auth.spec — the demo user owns both farms. */
const OWNER_NAV = [
  "Dashboard",
  "Animals",
  "Buckets",
  "Breeding",
  "Kidding",
  "Health",
  "Purchases",
  "Feeding",
  "Tasks",
  "Finance",
  "Reports",
  "Team",
];

test.describe("farm switching", () => {
  test("switching farms renders none of the previous farm's cached data", async ({
    page,
  }) => {
    test.setTimeout(120_000);

    // Farm A: the seeded demo farm, plus one uniquely tagged animal.
    await signIn(page);
    await expect(page.getByText("Demo Osmanabadi Farm", { exact: true })).toBeVisible({
      timeout: 20_000,
    });
    const tagA = uniqueTag("E2E-A");
    await createAnimal(page, { tag: tagA });

    // Create farm B through the picker; creating selects it immediately.
    const farmB = uniqueTag("E2E Farm B");
    await page.getByRole("link", { name: "switch farm" }).click();
    await expect(page).toHaveURL(/\/farm-select$/, { timeout: 15_000 });
    await page.getByLabel("Farm name").fill(farmB);
    await page.getByRole("button", { name: "Create farm" }).click();

    // Farm B's dashboard: its own name, nothing from farm A anywhere.
    await expect(page).toHaveURL(/\/dashboard$/, { timeout: 20_000 });
    await expect(
      page.getByRole("heading", { name: `${farmB} — Dashboard` }),
    ).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("Demo Osmanabadi Farm", { exact: true })).toHaveCount(0);

    // Nav reflects farm B's freshly loaded permissions (owner → full nav).
    const nav = page.locator("nav");
    for (const label of OWNER_NAV) {
      await expect(nav.getByRole("link", { name: label, exact: true })).toBeVisible();
    }

    // Farm B's herd is empty; farm A's animal must not leak from the cache.
    await nav.getByRole("link", { name: "Animals", exact: true }).click();
    await expect(page).toHaveURL(/\/animals$/, { timeout: 15_000 });
    await expect(page.getByText("No animals match these filters.")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByRole("link", { name: tagA, exact: true })).toHaveCount(0);

    // Switching back refetches farm A's data — the animal is still there.
    await page.getByRole("link", { name: "switch farm" }).click();
    await expect(page).toHaveURL(/\/farm-select$/, { timeout: 15_000 });
    await page.getByRole("button", { name: /Demo Osmanabadi Farm/ }).click();
    await expect(page).toHaveURL(/\/dashboard$/, { timeout: 20_000 });
    await expect(
      page.getByRole("heading", { name: "Demo Osmanabadi Farm — Dashboard" }),
    ).toBeVisible({ timeout: 20_000 });
    await nav.getByRole("link", { name: "Animals", exact: true }).click();
    await page.getByPlaceholder("Search tag or name…").fill(tagA);
    await expect(page.getByRole("link", { name: tagA, exact: true })).toBeVisible({
      timeout: 15_000,
    });
  });
});
