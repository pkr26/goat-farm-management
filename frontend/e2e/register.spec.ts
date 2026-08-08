import { expect, test } from "@playwright/test";

import { uniqueTag } from "./helpers";

test.describe("registration", () => {
  test("a new user registers, creates a farm and reaches its dashboard", async ({
    page,
  }) => {
    test.setTimeout(120_000);
    const email = `${uniqueTag("e2e-user")}@goatfarm.test`;
    const farmName = uniqueTag("E2E Farm");

    await page.goto("/register");
    await page.getByLabel("Name (optional)").fill("E2E User");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill("e2e-password-1");
    await page.getByRole("button", { name: "Create account" }).click();

    // New accounts are signed in and land on the farm picker with no farms.
    await expect(page).toHaveURL(/\/farm-select$/, { timeout: 20_000 });
    await expect(
      page.getByText("No farms yet — create your first one below."),
    ).toBeVisible({ timeout: 20_000 });

    await page.getByLabel("Farm name").fill(farmName);
    await page.getByRole("button", { name: "Create farm" }).click();

    // The new farm's dashboard loads.
    await expect(page).toHaveURL(/\/dashboard$/, { timeout: 20_000 });
    await expect(
      page.getByRole("heading", { name: `${farmName} — Dashboard` }),
    ).toBeVisible({ timeout: 20_000 });
    await expect(
      page.locator("nav").getByRole("link", { name: "Animals", exact: true }),
    ).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("Active animals")).toBeVisible();

    // The empty-state CTA exercises the /animals/new → /animals?new=1 shim.
    // The animals page strips ?new=1 as soon as the dialog opens, so accept
    // either form of the URL rather than racing the strip.
    await page.getByRole("link", { name: "Add your first animal" }).click();
    await expect(page).toHaveURL(/\/animals(\?new=1)?$/, { timeout: 15_000 });
    await expect(page.getByRole("dialog", { name: "Add animal" })).toBeVisible({
      timeout: 15_000,
    });
  });
});
