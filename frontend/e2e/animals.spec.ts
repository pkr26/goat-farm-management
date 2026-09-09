import { expect, test } from "@playwright/test";

import {
  createAnimal,
  monthsAgo,
  openAnimalProfile,
  pickSelectOption,
  profileDetail,
  signIn,
  uniqueTag,
} from "./helpers";

test.describe("animals", () => {
  test("create animal, record a weight, move bucket — profile reflects all", async ({
    page,
  }) => {
    test.setTimeout(90_000);
    const tag = uniqueTag("E2E-ANIM");
    await signIn(page);

    // Create a mature, breeding-ready doe so this test exercises a legal
    // manual lifecycle edge rather than forging a quarantine release.
    await createAnimal(page, {
      tag,
      historicalImportReason: "E2E mature doe lifecycle fixture",
      sex: "F",
      bucket: "FOUNDATION",
      dateOfBirth: monthsAgo(14),
      entryWeightKg: 24,
    });

    // The new animal shows up in the list (search to be robust to existing data).
    await page.getByPlaceholder("Search by tag…").fill(tag);
    await expect(page.getByRole("link", { name: tag, exact: true })).toBeVisible();

    // Open its profile.
    await page.getByRole("link", { name: tag, exact: true }).click();
    await expect(page).toHaveURL(/\/animals\/\d+$/, { timeout: 15_000 });
    await expect(page.getByRole("heading", { name: new RegExp(tag) })).toBeVisible({
      timeout: 15_000,
    });
    await expect(profileDetail(page, "Bucket")).toHaveText("Foundation");

    // Record a weight.
    await page.getByRole("button", { name: "Record weight" }).click();
    const weightDialog = page.getByRole("dialog", { name: "Record weight" });
    await weightDialog.getByLabel(/Weight \(kg\)/).fill("26.5");
    await weightDialog.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Weight recorded.")).toBeVisible();
    await expect(weightDialog).toBeHidden();

    // Profile reflects the weight.
    await expect(page.getByText("Weight history (2)")).toBeVisible();
    await expect(profileDetail(page, "Latest weight")).toHaveText("26.5 kg");

    // Foundation → Breeding is a legal manual transition for this eligible doe.
    await page.getByRole("button", { name: "Move bucket" }).click();
    const moveDialog = page.getByRole("dialog", { name: "Move bucket" });
    await pickSelectOption(moveDialog, "To bucket *", "Breeding");
    await moveDialog.getByRole("button", { name: "Move" }).click();
    await expect(page.getByText("Animal moved.")).toBeVisible();
    await expect(moveDialog).toBeHidden();

    // Profile reflects the legal transition; creation itself logs the initial
    // bucket, so assert the transition row rather than a hardcoded count.
    await expect(profileDetail(page, "Bucket")).toHaveText("Breeding");
    await expect(
      page.getByRole("row", { name: /Foundation\s+Breeding/ }),
    ).toBeVisible();

    // And the list shows the new bucket too.
    await openAnimalProfile(page, tag);
    await expect(profileDetail(page, "Bucket")).toHaveText("Breeding");
  });
});
