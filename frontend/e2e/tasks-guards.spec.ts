import { expect, test } from "@playwright/test";

import {
  createAnimal,
  createBreeding,
  daysAgo,
  monthsAgo,
  pickSelectOption,
  recordUltrasoundPregnant,
  signIn,
  uniqueTag,
} from "./helpers";

test.describe("tasks guards", () => {
  test("auto duties are form-linked; a completed duty lands in awaiting verification", async ({
    page,
  }) => {
    test.setTimeout(180_000);
    const doeTag = uniqueTag("E2E-TDOE");
    const dutyTitle = uniqueTag("E2E duty");
    const breedingDate = daysAgo(40);
    await signIn(page);

    // Set up a breeding so the auto-generated duties exist.
    await createAnimal(page, {
      tag: doeTag,
      historicalImportReason: "E2E task-flow doe fixture",
      sex: "F",
      bucket: "FOUNDATION",
      dateOfBirth: monthsAgo(20),
      entryWeightKg: 24,
      entryWeightDate: breedingDate,
    });
    await createBreeding(page, doeTag, breedingDate);

    // The ultrasound duty (due breeding date + 32 days) is now overdue and is
    // form-linked: an "Open form" link, no raw Complete button.
    await page.goto("/tasks");
    await page.getByRole("tab", { name: /Overdue/ }).click();
    const ultrasoundRow = page.getByRole("row", {
      name: new RegExp(`Pregnancy check: ${doeTag}`),
    });
    await expect(ultrasoundRow).toBeVisible({ timeout: 15_000 });
    await expect(ultrasoundRow.getByRole("link", { name: "Open form" })).toBeVisible();
    await expect(ultrasoundRow.getByRole("button", { name: "Complete" })).toHaveCount(0);

    // Record the pregnancy so the kidding duty is spawned.
    await recordUltrasoundPregnant(page, doeTag);

    // The kidding duty is also form-linked, not a raw Complete button.
    await page.goto("/tasks");
    await page.getByRole("tab", { name: /Upcoming/ }).click();
    const kiddingRow = page.getByRole("row", { name: new RegExp(`Kidding due: ${doeTag}`) });
    await expect(kiddingRow).toBeVisible({ timeout: 15_000 });
    await expect(kiddingRow.getByRole("link", { name: "Open form" })).toBeVisible();
    await expect(kiddingRow.getByRole("button", { name: "Complete" })).toHaveCount(0);

    // Create a manual CLEANING duty due today (CLEANING requires verification).
    await page.getByRole("button", { name: "New duty" }).click();
    const dialog = page.getByRole("dialog", { name: "New duty" });
    await dialog.getByLabel("Title").fill(dutyTitle);
    await pickSelectOption(dialog, "Category", "Cleaning");
    await dialog.getByRole("button", { name: "Create duty" }).click();
    await expect(page.getByText("Duty created.")).toBeVisible();
    await expect(dialog).toBeHidden();

    // It lands in Today and can be completed with a raw button (no form link).
    await page.getByRole("tab", { name: /Today/ }).click();
    const dutyRow = page.getByRole("row", { name: new RegExp(dutyTitle) });
    await expect(dutyRow).toBeVisible({ timeout: 15_000 });
    await dutyRow.getByRole("button", { name: "Complete" }).click();
    await expect(page.getByText("Task completed.")).toBeVisible();

    // The completed duty now awaits verification.
    await page.getByRole("tab", { name: /Awaiting verification/ }).click();
    const awaitingRow = page.getByRole("row", { name: new RegExp(dutyTitle) });
    await expect(awaitingRow).toBeVisible({ timeout: 15_000 });

    // Two-person rule: the completer cannot verify their own work — but the
    // backend exempts the farm owner (backend/app/api/tasks.py:261), and the
    // e2e user provisioned by globalSetup owns this farm. So the UI keeps the
    // Verify button visible and the self-verify succeeds. The 409 path
    // ("Someone else must verify this duty") only applies to non-owner
    // members, which this fresh farm does not have.
    await awaitingRow.getByRole("button", { name: "Verify" }).click();
    await expect(page.getByText("Task verified.")).toBeVisible();
    await expect(page.getByRole("row", { name: new RegExp(dutyTitle) })).toHaveCount(0);
  });
});
