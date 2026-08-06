import { expect, test, type Page } from "@playwright/test";

import { pickSelectOption, signIn, uniqueTag } from "./helpers";

/** Create a manual CLEANING duty due today (CLEANING requires verification). */
async function createCleaningDuty(page: Page, title: string): Promise<void> {
  await page.goto("/tasks");
  await page.getByRole("button", { name: "New duty" }).click();
  const dialog = page.getByRole("dialog", { name: "New duty" });
  await dialog.getByLabel("Title").fill(title);
  await pickSelectOption(dialog, "Category", "CLEANING");
  await dialog.getByRole("button", { name: "Create duty" }).click();
  await expect(page.getByText("Duty created.")).toBeVisible();
  await expect(dialog).toBeHidden();
}

/** Complete the duty from the Today tab; it lands in Awaiting verification. */
async function completeDuty(page: Page, title: string): Promise<void> {
  await page.getByRole("tab", { name: /Today/ }).click();
  const dutyRow = page.getByRole("row", { name: new RegExp(title) });
  await expect(dutyRow).toBeVisible({ timeout: 15_000 });
  await dutyRow.getByRole("button", { name: "Complete" }).click();
  await expect(page.getByText("Task completed.")).toBeVisible();

  await page.getByRole("tab", { name: /Awaiting verification/ }).click();
  await expect(page.getByRole("row", { name: new RegExp(title) })).toBeVisible({
    timeout: 15_000,
  });
}

test.describe("tasks verification", () => {
  test("owner completes and verifies a cleaning duty — it lands in Completed as VERIFIED", async ({
    page,
  }) => {
    test.setTimeout(90_000);
    const title = uniqueTag("E2E verify");
    await signIn(page);
    await createCleaningDuty(page, title);
    await completeDuty(page, title);

    // Two-person rule: the completer cannot normally verify their own work,
    // but the backend exempts the farm owner (backend/app/api/tasks.py) and
    // demo@goatfarm.in owns Demo Osmanabadi Farm, so self-verify succeeds.
    const awaitingRow = page.getByRole("row", { name: new RegExp(title) });
    await awaitingRow.getByRole("button", { name: "Verify" }).click();
    await expect(page.getByText("Task verified.")).toBeVisible();
    await expect(page.getByRole("row", { name: new RegExp(title) })).toHaveCount(0);

    // The Completed tab shows it as VERIFIED (no longer "awaiting").
    await page.getByRole("tab", { name: "Completed" }).click();
    const doneRow = page.getByRole("row", { name: new RegExp(title) });
    await expect(doneRow).toBeVisible({ timeout: 15_000 });
    await expect(doneRow.getByText("VERIFIED", { exact: true })).toBeVisible();
    await expect(doneRow.getByText("awaiting")).toHaveCount(0);
  });

  test("rejected duty is sent back to pending with the note visible", async ({ page }) => {
    test.setTimeout(90_000);
    const title = uniqueTag("E2E reject");
    const note = "trough still dirty";
    await signIn(page);
    await createCleaningDuty(page, title);
    await completeDuty(page, title);

    // Reject with a reason — the duty leaves Awaiting verification.
    const awaitingRow = page.getByRole("row", { name: new RegExp(title) });
    await awaitingRow.getByPlaceholder("reason (sent back)").fill(note);
    await awaitingRow.getByRole("button", { name: "Reject" }).click();
    await expect(page.getByText("Task sent back.")).toBeVisible();
    await expect(page.getByRole("row", { name: new RegExp(title) })).toHaveCount(0);

    // It is back in Today as PENDING with the sent-back note, completable again.
    await page.getByRole("tab", { name: /Today/ }).click();
    const returnedRow = page.getByRole("row", { name: new RegExp(title) });
    await expect(returnedRow).toBeVisible({ timeout: 15_000 });
    await expect(returnedRow.getByText(`Sent back: ${note}`)).toBeVisible();
    await expect(returnedRow.getByRole("button", { name: "Complete" })).toBeVisible();
  });
});
