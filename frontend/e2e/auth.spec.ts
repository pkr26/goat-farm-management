import { expect, test } from "@playwright/test";

import { e2eCredentials, signIn, uniqueTag } from "./helpers";

const OWNER_NAV = [
  "Dashboard",
  "Animals",
  "Buckets",
  "Breeding",
  "Kidding",
  "Health",
  "Feeding",
  "Purchases",
  "Tasks",
  "Finance",
  "Reports",
  "Team",
];

test.describe("auth", () => {
  test("owner signs in, sees the full nav, and logout returns to /login", async ({
    page,
  }) => {
    const credentials = e2eCredentials();
    await page.goto("/login");
    await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();

    await page.getByLabel("Email").fill(credentials.email);
    await page.getByLabel("Password").fill(credentials.password);
    await page.getByRole("button", { name: "Sign in" }).click();

    // Lands on the dashboard with the farm auto-selected.
    await expect(page).toHaveURL(/\/dashboard$/, { timeout: 20_000 });
    await expect(page.getByText(credentials.farmName, { exact: true })).toBeVisible({ timeout: 20_000 });

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
    const credentials = e2eCredentials();
    await page.goto("/login");
    await page.getByLabel("Email").fill(credentials.email);
    await page.getByLabel("Password").fill("definitely-wrong");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByText("Invalid email or password.")).toBeVisible();
    await expect(page).toHaveURL(/\/login$/);
  });

  // Keep signIn referenced even if tests above change; it is the shared entry point.
  test("signIn helper lands on the dashboard", async ({ page }) => {
    const credentials = e2eCredentials();
    await signIn(page);
    await expect(page.getByText(credentials.farmName, { exact: true })).toBeVisible();
  });

  test("authenticated shell fits supported narrow widths and hands off navigation focus", async ({
    page,
  }) => {
    await signIn(page);

    for (const width of [320, 360, 768]) {
      await page.setViewportSize({ width, height: 900 });
      await expect.poll(() => page.evaluate(() => ({
        client: document.documentElement.clientWidth,
        scroll: document.documentElement.scrollWidth,
      }))).toEqual({ client: width, scroll: width });

      const switcherBox = await page.getByRole("link", { name: /Switch farm/ }).boundingBox();
      expect(switcherBox?.width).toBeGreaterThanOrEqual(96);
    }

    // Phone-priority controls move into the account surface instead of
    // disappearing or forcing the farm identity off-screen.
    await page.setViewportSize({ width: 320, height: 900 });
    await page.getByRole("button", { name: /^Account — / }).click();
    const account = page.getByRole("dialog", { name: "Account & password" });
    await expect(account.getByRole("group", { name: "Language / భాష" })).toBeVisible();
    await expect(account.getByRole("button", { name: "Logout" })).toBeVisible();
    await page.keyboard.press("Escape");

    await page.setViewportSize({ width: 768, height: 900 });
    const animals = page.getByRole("navigation").getByRole("link", {
      name: "Animals",
      exact: true,
    });
    await animals.focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/animals$/);
    await expect(page.getByRole("main", { name: "Animals" })).toBeFocused();
  });

  test("sign-out propagates between farmless tabs without a farm storage key", async ({
    page,
  }) => {
    test.setTimeout(60_000);
    const email = `${uniqueTag("farmless-tabs")}@goatfarm.test`;
    await page.goto("/register");
    await page.getByLabel("Name (optional)").fill("Farmless tabs");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill("e2e-password-1");
    await page.getByRole("button", { name: "Create account" }).click();
    await expect(page).toHaveURL(/\/farm-select$/, { timeout: 20_000 });
    await expect(page.getByText("No farms yet — create your first one below.")).toBeVisible();
    expect(await page.evaluate(() => localStorage.getItem("goatfarm.farmId"))).toBeNull();

    const second = await page.context().newPage();
    try {
      await second.goto("/farm-select");
      await expect(second.getByText("No farms yet — create your first one below.")).toBeVisible({
        timeout: 20_000,
      });
      expect(await second.evaluate(() => localStorage.getItem("goatfarm.farmId"))).toBeNull();

      await page.getByRole("button", { name: "Sign out" }).click();
      await expect(page).toHaveURL(/\/login$/, { timeout: 15_000 });
      await expect(second).toHaveURL(/\/login$/, { timeout: 15_000 });
    } finally {
      await second.close();
    }
  });
});
