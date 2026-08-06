import { expect, test } from "@playwright/test";

import { pickSelectOption, signIn, uniqueTag } from "./helpers";

/** Parse the app's Indian-grouped money format ("₹1,23,456.78") to a number. */
function parseMoney(text: string | null): number {
  return Number((text ?? "").replace(/[^0-9.-]/g, ""));
}

test.describe("feeding and finance", () => {
  test("feeding plan shows the 40/20/40 shift split and records a dispense", async ({
    page,
  }) => {
    test.setTimeout(90_000);
    await signIn(page);
    await page.goto("/feeding");

    await expect(
      page.getByRole("heading", { name: "Feeding — today" }),
    ).toBeVisible();
    await expect(page.getByText(/split 40 \/ 20 \/ 40/)).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "Morning 40%" })).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "Afternoon 20%" })).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "Night 40%" })).toBeVisible();

    // Record a morning dispense; pick the bucket explicitly so the log row is
    // identifiable. Leftover dispenses from earlier runs mean we assert the
    // row count growing by one rather than a unique row.
    const logRows = page.getByRole("row", { name: /MORNING QUARANTINE/ });
    const logCountBefore = await logRows.count();

    await page.getByRole("button", { name: "Record dispensing" }).click();
    const dialog = page.getByRole("dialog", { name: "Record dispensing" });
    await expect(dialog).toBeVisible();
    await pickSelectOption(dialog, "Bucket", "QUARANTINE");
    await pickSelectOption(dialog, "Shift", "MORNING");
    await dialog.getByLabel(/Quantity/).fill("3.7");
    await dialog.getByRole("button", { name: "Record", exact: true }).click();
    await expect(page.getByText("Dispensing recorded.")).toBeVisible();
    await expect(dialog).toBeHidden();

    // Today's dispensing log lists the new record.
    await expect(logRows).toHaveCount(logCountBefore + 1, { timeout: 15_000 });
    await expect(logRows.last().getByRole("cell", { name: "3.7", exact: true })).toBeVisible();
  });

  test("adding an expense transaction updates the totals and the ledger", async ({
    page,
  }) => {
    test.setTimeout(90_000);
    const note = uniqueTag("E2E txn");
    await signIn(page);
    await page.goto("/finance");

    // Stat card: the value sits in the div before the "Total expense" label.
    const expenseValue = page
      .getByText("Total expense", { exact: true })
      .locator("xpath=preceding-sibling::div[1]");
    await expect(expenseValue).toBeVisible();
    const before = parseMoney(await expenseValue.textContent());

    await page.getByRole("button", { name: "New transaction" }).click();
    const dialog = page.getByRole("dialog", { name: "New transaction" });
    await expect(dialog).toBeVisible();
    // Type defaults to EXPENSE.
    await pickSelectOption(dialog, "Category", "FEED");
    await dialog.getByLabel(/Amount/).fill("321.50");
    await dialog.getByLabel("Notes").fill(note);
    await dialog.getByRole("button", { name: "Add transaction" }).click();
    await expect(page.getByText("Transaction saved.")).toBeVisible();
    await expect(dialog).toBeHidden();

    // The all-time expense total grows by exactly the new amount.
    await expect
      .poll(async () => parseMoney(await expenseValue.textContent()), { timeout: 15_000 })
      .toBeCloseTo(before + 321.5, 2);

    // The ledger row shows the transaction.
    const row = page.getByRole("row", { name: new RegExp(note) });
    await expect(row).toBeVisible();
    await expect(row.getByText("EXPENSE")).toBeVisible();
    await expect(row.getByText("FEED")).toBeVisible();
    await expect(row.getByText("₹321.50")).toBeVisible();
  });

  test("reports page renders herd summary, breeding performance and mortality", async ({
    page,
  }) => {
    test.setTimeout(60_000);
    await signIn(page);
    await page.goto("/reports");

    await expect(page.getByRole("heading", { name: "Reports" })).toBeVisible();
    await expect(page.getByText(/Herd summary \(\d+ active\)/)).toBeVisible();
    await expect(page.getByText("Breeding performance")).toBeVisible();
    await expect(page.getByText("Mortality")).toBeVisible();
    await expect(page.getByText("Could not load the reports.")).toHaveCount(0);
  });
});
