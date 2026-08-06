import { expect, test } from "@playwright/test";

import { DEV_EMAIL, DEV_PASSWORD, signIn } from "./helpers";

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

test.describe("auth", () => {
  test("owner signs in, sees the full nav, and logout returns to /login", async ({
    page,
  }) => {
    await page.goto("/login");
    await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();

    await page.getByLabel("Email").fill(DEV_EMAIL);
    await page.getByLabel("Password").fill(DEV_PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();

    // Lands on the dashboard with the farm auto-selected.
    await expect(page).toHaveURL(/\/dashboard$/, { timeout: 20_000 });
    await expect(page.getByText("Demo Osmanabadi Farm", { exact: true })).toBeVisible({ timeout: 20_000 });

    // The owner sees every nav item.
    const nav = page.locator("nav");
    for (const label of OWNER_NAV) {
      await expect(nav.getByRole("link", { name: label, exact: true })).toBeVisible();
    }

    await page.getByRole("button", { name: "Logout" }).click();
    await expect(page).toHaveURL(/\/login$/, { timeout: 15_000 });
    await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  });

  test("unauthenticated visit to an app page redirects to /login", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/login$/, { timeout: 15_000 });
  });

  test("wrong password shows an error and stays on /login", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("Email").fill(DEV_EMAIL);
    await page.getByLabel("Password").fill("definitely-wrong");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByText("Invalid email or password.")).toBeVisible();
    await expect(page).toHaveURL(/\/login$/);
  });

  // Keep signIn referenced even if tests above change; it is the shared entry point.
  test("signIn helper lands on the dashboard", async ({ page }) => {
    await signIn(page);
    await expect(page.getByText("Demo Osmanabadi Farm", { exact: true })).toBeVisible();
  });
});
