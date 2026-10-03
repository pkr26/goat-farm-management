# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: purchases.spec.ts >> purchases >> a new batch stubs animals into QUARANTINE with the 45-day task schedule
- Location: e2e/purchases.spec.ts:6:3

# Error details

```
Error: page.goto: NS_BINDING_ABORTED
Call log:
  - navigating to "http://localhost:3000/animals?bucket=QUARANTINE", waiting until "load"

```

# Page snapshot

```yaml
- generic [active] [ref=f2e1]:
  - main [ref=f2e2]:
    - status [ref=f2e3]:
      - generic [ref=f2e4]: Herdly
      - status [ref=f2e16]: Loading…
  - region "Notifications alt+T"
  - alert [ref=f2e17]
```

# Test source

```ts
  1  | import { expect, test } from "@playwright/test";
  2  | 
  3  | import { signIn, uniqueTag } from "./helpers";
  4  | 
  5  | test.describe("purchases", () => {
  6  |   test("a new batch stubs animals into QUARANTINE with the 45-day task schedule", async ({
  7  |     page,
  8  |   }) => {
  9  |     test.setTimeout(90_000);
  10 |     const supplier = uniqueTag("E2E-Supplier");
  11 |     await signIn(page);
  12 |     await page.goto("/purchases");
  13 | 
  14 |     // Record a batch of 3 animals; "Create animal stubs in QUARANTINE" stays
  15 |     // checked (the default).
  16 |     await page.getByRole("button", { name: "New batch" }).click();
  17 |     const dialog = page.getByRole("dialog", { name: "New purchase batch" });
  18 |     await expect(dialog).toBeVisible();
  19 |     await dialog.getByLabel("Supplier").fill(supplier);
  20 |     await dialog.getByLabel("Count *").fill("3");
  21 |     await dialog.getByLabel("Avg age (months)").fill("12");
  22 |     await dialog.getByLabel("Avg weight (kg)").fill("24");
  23 |     await dialog.getByLabel("Total price (₹)").fill("24000");
  24 |     await dialog.getByRole("button", { name: "Review batch" }).click();
  25 |     const review = page.getByRole("dialog", { name: "Review purchase consequences" });
  26 |     await expect(review).toBeVisible();
  27 |     await expect(review).toContainText("3 female goats");
  28 |     await expect(review).toContainText("3 in QUARANTINE");
  29 |     await expect(review).toContainText("45-day quarantine schedule");
  30 |     await review.getByRole("button", { name: "Confirm and create" }).click();
  31 |     await expect(page.getByText("Purchase batch created.")).toBeVisible();
  32 |     await expect(review).toBeHidden();
  33 | 
  34 |     // The new batch is listed: count 3, 3 animals stubbed, 11 open quarantine
  35 |     // protocol tasks (the 45-day protocol: days 1, 1, 4, 5, 10, 13, 20, 30,
  36 |     // 30, 40 and 45).
  37 |     const row = page.getByRole("row", { name: new RegExp(supplier) });
  38 |     await expect(row).toBeVisible({ timeout: 15_000 });
  39 |     await expect(row.getByRole("cell", { name: "3", exact: true })).toHaveCount(2);
  40 |     await expect(row.getByRole("cell", { name: "11", exact: true })).toBeVisible();
  41 |     const batchId = (
  42 |       await row.getByRole("cell", { name: /^#\d+$/ }).textContent()
  43 |     )?.slice(1);
  44 |     expect(batchId).toBeTruthy();
  45 | 
  46 |     // Batch detail: the stubbed animals sit in QUARANTINE and the protocol
  47 |     // schedule is listed, ending with the day-45 release to FOUNDATION.
  48 |     await row.getByRole("button", { name: "View" }).click();
  49 |     const detail = page.getByRole("dialog", { name: `Batch #${batchId}` });
  50 |     await expect(detail).toBeVisible();
  51 |     await expect(
  52 |       detail.getByRole("heading", { name: "Animals created (3)" }),
  53 |     ).toBeVisible({ timeout: 15_000 });
  54 |     const firstTagPattern = new RegExp(`B${batchId}-[0-9a-f]{12}-0001`);
  55 |     const animalRow = detail.getByRole("row", { name: firstTagPattern });
  56 |     await expect(animalRow).toBeVisible();
  57 |     const firstTag = await animalRow.getByRole("cell").first().innerText();
  58 |     await expect(animalRow.getByRole("cell", { name: "QUARANTINE" })).toBeVisible();
  59 |     await expect(animalRow.getByRole("cell", { name: "ACTIVE" })).toBeVisible();
  60 |     await expect(
  61 |       detail.getByRole("heading", { name: "Open quarantine tasks (11)" }),
  62 |     ).toBeVisible();
  63 |     await expect(
  64 |       detail.getByText(/Day 45: 10% zinc sulfate footbath → release to FOUNDATION/),
  65 |     ).toBeVisible();
  66 |     await page.keyboard.press("Escape");
  67 |     await expect(detail).toBeHidden();
  68 | 
  69 |     // The herd list's quarantine bucket shows the stubbed animal. (The day-45
  70 |     // release is due 44 days out — not automatable without date travel.)
> 71 |     await page.goto("/animals?bucket=QUARANTINE");
     |                ^ Error: page.goto: NS_BINDING_ABORTED
  72 |     await page.getByPlaceholder("Search by tag…").fill(firstTag);
  73 |     const herdRow = page.getByRole("row", { name: new RegExp(firstTag) });
  74 |     await expect(herdRow).toBeVisible({ timeout: 15_000 });
  75 |     await expect(herdRow.getByRole("cell", { name: "QUARANTINE" })).toBeVisible();
  76 |     await expect(herdRow.getByRole("cell", { name: "ACTIVE" })).toBeVisible();
  77 |   });
  78 | });
  79 | 
```