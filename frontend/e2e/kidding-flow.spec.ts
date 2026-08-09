import { expect, test } from "@playwright/test";

import {
  createAnimal,
  createBreeding,
  daysAgo,
  monthsAgo,
  openAnimalProfile,
  profileDetail,
  recordUltrasoundPregnant,
  signIn,
  uniqueTag,
} from "./helpers";

test.describe("kidding flow", () => {
  test("overdue pregnancy is kidded with twins — kids become animals, doe moves to RECOVERY", async ({
    page,
  }) => {
    test.setTimeout(180_000);
    const doeTag = uniqueTag("E2E-KDOE");
    const kid1 = uniqueTag("E2E-KID-A");
    const kid2 = uniqueTag("E2E-KID-B");
    const breedingDate = daysAgo(155);
    await signIn(page);

    // Breeding-ready doe, then breed her 155 days ago (gestation is 150 days)
    // so the confirmed pregnancy is already overdue and recordable.
    await createAnimal(page, {
      tag: doeTag,
      historicalImportReason: "E2E kidding-flow doe fixture",
      sex: "F",
      bucket: "FOUNDATION",
      dateOfBirth: monthsAgo(20),
      entryWeightKg: 24,
      entryWeightDate: breedingDate,
    });
    await createBreeding(page, doeTag, breedingDate);
    await recordUltrasoundPregnant(page, doeTag);

    // The overdue card on the Kidding page offers the Record kidding action.
    await page.goto("/kidding");
    const overdueRow = page.getByRole("row", { name: new RegExp(doeTag) });
    await expect(overdueRow).toBeVisible({ timeout: 15_000 });
    await expect(overdueRow.getByText(/\d+d late/)).toBeVisible();
    await overdueRow.getByRole("button", { name: "Record kidding" }).click();

    // The dialog opens with two kid rows (twins are the norm) — tag both.
    const dialog = page.getByRole("dialog", { name: "Record kidding" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText(/2 detected/)).toBeVisible();
    const tagInputs = dialog.getByPlaceholder("auto");
    await expect(tagInputs).toHaveCount(2);
    await tagInputs.nth(0).fill(kid1);
    await tagInputs.nth(1).fill(kid2);
    const weightInputs = dialog.locator('input[type="number"]');
    await weightInputs.nth(0).fill("2.5");
    await weightInputs.nth(1).fill("2.3");
    await dialog.getByRole("button", { name: "Save kidding" }).click();
    await expect(page.getByText("Kidding recorded.")).toBeVisible();
    await expect(dialog).toBeHidden();

    // Recent kiddings lists the doe with both kids linked to their profiles.
    const recordRow = page.getByRole("row", { name: new RegExp(doeTag) });
    await expect(recordRow).toBeVisible({ timeout: 15_000 });
    await expect(recordRow.getByRole("link", { name: kid1, exact: true })).toBeVisible();
    await expect(recordRow.getByRole("link", { name: kid2, exact: true })).toBeVisible();
    await expect(recordRow.getByText(/F, alive/)).toHaveCount(2);

    // Both kids are now animals (auto-created, source BORN, RECOVERY bucket).
    await page.goto("/animals");
    await page.getByPlaceholder("Search by tag…").fill(kid1);
    await expect(page.getByRole("link", { name: kid1, exact: true })).toBeVisible();
    await page.getByPlaceholder("Search by tag…").fill(kid2);
    await expect(page.getByRole("link", { name: kid2, exact: true })).toBeVisible();
    await openAnimalProfile(page, kid1);
    await expect(profileDetail(page, "Bucket")).toHaveText("RECOVERY");

    // The doe left PREGNANCY_EARLY for RECOVERY.
    await openAnimalProfile(page, doeTag);
    await expect(profileDetail(page, "Bucket")).toHaveText("RECOVERY");
    await expect(page.getByText("Kids (2)")).toBeVisible();
  });
});
