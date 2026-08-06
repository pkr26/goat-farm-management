import { expect, test } from "@playwright/test";

import { signIn } from "./helpers";

/** Nav label → route → expected page heading (owner sees every item). */
const PAGES: { label: string; href: string; heading: RegExp }[] = [
  { label: "Dashboard", href: "/dashboard", heading: /— Dashboard$/ },
  { label: "Animals", href: "/animals", heading: /^Animals$/ },
  { label: "Buckets", href: "/buckets", heading: /^Buckets$/ },
  { label: "Breeding", href: "/breeding", heading: /^Breeding$/ },
  { label: "Kidding", href: "/kidding", heading: /^Kidding$/ },
  { label: "Health", href: "/health", heading: /^Health$/ },
  { label: "Purchases", href: "/purchases", heading: /^Purchase batches$/ },
  { label: "Feeding", href: "/feeding", heading: /^Feeding — today$/ },
  { label: "Tasks", href: "/tasks", heading: /^Tasks$/ },
  { label: "Finance", href: "/finance", heading: /^Finance$/ },
  { label: "Reports", href: "/reports", heading: /^Reports$/ },
  { label: "Team", href: "/team", heading: /^Team$/ },
];

test.describe("navigation and access control", () => {
  test("every nav item loads its page without errors", async ({ page }) => {
    test.setTimeout(120_000);
    await signIn(page);
    const nav = page.locator("nav");

    for (const { label, href, heading } of PAGES) {
      await nav.getByRole("link", { name: label, exact: true }).click();
      await expect(page).toHaveURL(new RegExp(`${href}$`), { timeout: 15_000 });
      await expect(page.getByRole("heading", { name: heading })).toBeVisible({
        timeout: 15_000,
      });
      // No permission wall, no query error on any page.
      await expect(page.getByText("You don't have access to this page.")).toHaveCount(0);
      await expect(page.getByText(/Could not load/)).toHaveCount(0);
    }
  });

  test("logged-out direct-URL access redirects every app page to /login", async ({ page }) => {
    for (const { href } of PAGES) {
      await page.goto(href);
      await expect(page).toHaveURL(/\/login$/, { timeout: 15_000 });
      await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
    }
  });
});
