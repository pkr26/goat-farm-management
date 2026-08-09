import { expect, test } from "@playwright/test";

import { signIn, uniqueTag } from "./helpers";

test.describe("purchases", () => {
  test("a new batch stubs animals into QUARANTINE with the 45-day task schedule", async ({
    page,
  }) => {
    test.setTimeout(90_000);
    const supplier = uniqueTag("E2E-Supplier");
    await signIn(page);
    await page.goto("/purchases");

    // Record a batch of 3 animals; "Create animal stubs in QUARANTINE" stays
    // checked (the default).
    await page.getByRole("button", { name: "New batch" }).click();
    const dialog = page.getByRole("dialog", { name: "New purchase batch" });
    await expect(dialog).toBeVisible();
    await dialog.getByLabel("Supplier").fill(supplier);
    await dialog.getByLabel("Count *").fill("3");
    await dialog.getByLabel("Avg age (months)").fill("12");
    await dialog.getByLabel("Avg weight (kg)").fill("24");
    await dialog.getByLabel("Total price (₹)").fill("24000");
    await dialog.getByRole("button", { name: "Review batch" }).click();
    const review = page.getByRole("dialog", { name: "Review purchase consequences" });
    await expect(review).toBeVisible();
    await expect(review).toContainText("3 female goats");
    await expect(review).toContainText("3 in QUARANTINE");
    await expect(review).toContainText("45-day quarantine schedule");
    await review.getByRole("button", { name: "Confirm and create" }).click();
    await expect(page.getByText("Purchase batch created.")).toBeVisible();
    await expect(review).toBeHidden();

    // The new batch is listed: count 3, 3 animals stubbed, 8 open quarantine
    // protocol tasks (day 1 … day 45).
    const row = page.getByRole("row", { name: new RegExp(supplier) });
    await expect(row).toBeVisible({ timeout: 15_000 });
    await expect(row.getByRole("cell", { name: "3", exact: true })).toHaveCount(2);
    await expect(row.getByRole("cell", { name: "8", exact: true })).toBeVisible();
    const batchId = (
      await row.getByRole("cell", { name: /^#\d+$/ }).textContent()
    )?.slice(1);
    expect(batchId).toBeTruthy();

    // Batch detail: the stubbed animals sit in QUARANTINE and the protocol
    // schedule is listed, ending with the day-45 release to FOUNDATION.
    await row.getByRole("button", { name: "View" }).click();
    const detail = page.getByRole("dialog", { name: `Batch #${batchId}` });
    await expect(detail).toBeVisible();
    await expect(
      detail.getByRole("heading", { name: "Animals created (3)" }),
    ).toBeVisible({ timeout: 15_000 });
    const firstTagPattern = new RegExp(`B${batchId}-[0-9a-f]{12}-0001`);
    const animalRow = detail.getByRole("row", { name: firstTagPattern });
    await expect(animalRow).toBeVisible();
    const firstTag = await animalRow.getByRole("cell").first().innerText();
    await expect(animalRow.getByRole("cell", { name: "QUARANTINE" })).toBeVisible();
    await expect(animalRow.getByRole("cell", { name: "ACTIVE" })).toBeVisible();
    await expect(
      detail.getByRole("heading", { name: "Open quarantine tasks (8)" }),
    ).toBeVisible();
    await expect(
      detail.getByText(/Day 45: 10% zinc sulfate footbath → release to FOUNDATION/),
    ).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(detail).toBeHidden();

    // The herd list's quarantine bucket shows the stubbed animal. (The day-45
    // release is due 44 days out — not automatable without date travel.)
    await page.goto("/animals?bucket=QUARANTINE");
    await page.getByPlaceholder("Search by tag…").fill(firstTag);
    const herdRow = page.getByRole("row", { name: new RegExp(firstTag) });
    await expect(herdRow).toBeVisible({ timeout: 15_000 });
    await expect(herdRow.getByRole("cell", { name: "QUARANTINE" })).toBeVisible();
    await expect(herdRow.getByRole("cell", { name: "ACTIVE" })).toBeVisible();
  });
});
