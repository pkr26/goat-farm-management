import { expect, test } from "@playwright/test";

import { createAnimal, pickSelectOption, signIn, uniqueTag } from "./helpers";

test.describe("feeding and finance", () => {
  test("feeding plan shows the 40/20/40 shift split and records a dispense", async ({
    page,
  }) => {
    test.setTimeout(90_000);
    await signIn(page);

    // Make this scenario independent of spec ordering: a fresh, audited
    // quarantine import creates the dry-roughage allocation being dispensed.
    await createAnimal(page, {
      tag: uniqueTag("E2E-FEED"),
      historicalImportReason: "E2E quarantine feeding fixture",
      bucket: "QUARANTINE",
    });

    // Dry roughage is inventory-accounted. Stock the fresh farm through the
    // real inventory workflow before recording its first quarantine dispense.
    await page.goto("/feeding/inventory");
    const stoverRow = page.getByRole("row", { name: /Dry jowar stover/ });
    await expect(stoverRow).toBeVisible();
    await stoverRow.getByRole("button", { name: "Add stock" }).click();
    const stockDialog = page.getByRole("dialog", {
      name: "Add stock — Dry jowar stover",
    });
    await stockDialog.getByLabel(/Quantity/).fill("10");
    await stockDialog.getByRole("button", { name: "Add", exact: true }).click();
    // The toast confirms the balance the API persisted, not the typed input.
    await expect(
      page.getByText(/Stock added — Dry jowar stover is now at [\d,.]+ kg\./),
    ).toBeVisible();
    await expect(stockDialog).toBeHidden();

    await page.goto("/feeding");

    await expect(
      page.getByRole("heading", { name: "Feeding — today" }),
    ).toBeVisible();
    await expect(page.getByText(/split 40 \/ 20 \/ 40/)).toBeVisible();
    await expect(
      page.getByRole("columnheader", { name: "Morning recorded / planned" }),
    ).toBeVisible();
    await expect(
      page.getByRole("columnheader", { name: "Afternoon recorded / planned" }),
    ).toBeVisible();
    await expect(
      page.getByRole("columnheader", { name: "Night recorded / planned" }),
    ).toBeVisible();

    // Record a morning dispense; pick the bucket explicitly so the log row is
    // identifiable. globalSetup gives every run a fresh farm, so the log is
    // empty beforehand and the new row is identifiable by its contents.
    await page.getByRole("button", { name: "Record dispensing" }).click();
    const dialog = page.getByRole("dialog", { name: "Record dispensing" });
    await expect(dialog).toBeVisible();
    await pickSelectOption(dialog, "Bucket", "Quarantine");
    await pickSelectOption(dialog, "Shift", "Morning");
    await pickSelectOption(dialog, "Recipe *", "Dry roughage only");
    await dialog.getByLabel(/Quantity/).fill("3.7");
    await dialog.getByRole("button", { name: "Record", exact: true }).click();
    await expect(page.getByText("Dispensing recorded.")).toBeVisible();
    await expect(dialog).toBeHidden();

    // The same record is intentionally shown in both today's summary and the
    // paginated history. Scope identity/count assertions to today's card.
    const todayLog = page
      .getByText(/^Today's dispensing log \(1\)$/)
      .locator('xpath=ancestor::*[@data-slot="card"][1]');
    const logRow = todayLog.getByRole("row", { name: /Morning\s+Quarantine/ });
    await expect(logRow).toHaveCount(1, { timeout: 15_000 });
    await expect(logRow.getByRole("cell", { name: "3.7", exact: true })).toBeVisible();
  });

  test("adding an expense transaction lists it in the ledger", async ({
    page,
  }) => {
    test.setTimeout(90_000);
    const note = uniqueTag("E2E txn");
    await signIn(page);
    await page.goto("/finance");

    // Stat card: the value <p> sits below the "Total expense" label <p>.
    const expenseValue = page
      .getByText("Total expense", { exact: true })
      .locator("xpath=following-sibling::p[1]");
    await expect(expenseValue).toBeVisible();

    await page.getByRole("button", { name: "New transaction" }).click();
    const dialog = page.getByRole("dialog", { name: "New transaction" });
    await expect(dialog).toBeVisible();
    // Type defaults to EXPENSE.
    await pickSelectOption(dialog, "Category", "Feed");
    await dialog.getByLabel(/Amount/).fill("321.50");
    await dialog.getByLabel("Notes").fill(note);
    await dialog.getByRole("button", { name: "Add transaction" }).click();
    await expect(page.getByText("Transaction saved.")).toBeVisible();
    await expect(dialog).toBeHidden();

    // Row identity, not a total delta (audit 10-H1): the ledger row carrying
    // this run's unique note shows the exact amount.
    const row = page.getByRole("row", { name: new RegExp(note) });
    await expect(row).toBeVisible({ timeout: 15_000 });
    await expect(row.getByText("EXPENSE")).toBeVisible();
    await expect(row.getByText("Feed")).toBeVisible();
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
    await expect(page.getByText("Mortality", { exact: true })).toBeVisible();
    await expect(page.getByText("Could not load the reports.")).toHaveCount(0);
  });
});
