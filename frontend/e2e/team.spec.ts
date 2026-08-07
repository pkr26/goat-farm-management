import { expect, test } from "@playwright/test";

import { pickSelectOption, signIn, uniqueTag } from "./helpers";

test.describe("team management", () => {
  test("owner adds a worker, changes their role and toggles them inactive/active", async ({
    page,
  }) => {
    test.setTimeout(90_000);
    const email = `${uniqueTag("e2e-worker")}@example.com`;
    await signIn(page);
    await page.goto("/team");

    await expect(page.getByRole("heading", { name: "Team", exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Workers" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Roles" })).toBeVisible();

    // Add a worker (new account, so a password is required) with the
    // Veterinarian preset role.
    await page.getByRole("button", { name: "Add worker" }).click();
    const dialog = page.getByRole("dialog", { name: "Add worker" });
    await expect(dialog).toBeVisible();
    await dialog.getByLabel("Name").fill("E2E Worker");
    await dialog.getByLabel("Email *").fill(email);
    await dialog.getByLabel(/Password/).fill("e2e-password-1");
    await pickSelectOption(dialog, "Role *", "Veterinarian");
    await dialog.getByRole("button", { name: "Add worker" }).click();
    await expect(page.getByText("Worker added.")).toBeVisible();
    await expect(dialog).toBeHidden();

    // The workers table lists the new worker, active, with the chosen role.
    await expect(page.getByRole("columnheader", { name: "Email" })).toBeVisible();
    const row = page.getByRole("row", { name: new RegExp(email) });
    await expect(row).toBeVisible({ timeout: 15_000 });
    await expect(row).toContainText("Veterinarian");
    await expect(row.getByText("Active", { exact: true })).toBeVisible();

    // Reassign the worker to the Feeder role via the row's role select.
    await row.locator('[data-slot="select-trigger"]').click();
    await page.getByRole("option", { name: "Feeder", exact: true }).click();
    await expect(page.getByText("Role updated.")).toBeVisible();
    await expect(row).toContainText("Feeder", { timeout: 15_000 });

    // Deactivate, then reactivate.
    await row.getByRole("button", { name: "Deactivate" }).click();
    await expect(page.getByText("Worker deactivated.")).toBeVisible();
    await expect(row.getByText("Inactive", { exact: true })).toBeVisible({
      timeout: 15_000,
    });
    await expect(row.getByRole("button", { name: "Activate" })).toBeVisible();

    await row.getByRole("button", { name: "Activate" }).click();
    await expect(page.getByText("Worker activated.")).toBeVisible();
    await expect(row.getByText("Active", { exact: true })).toBeVisible({ timeout: 15_000 });
    await expect(row.getByRole("button", { name: "Deactivate" })).toBeVisible();
  });
});
