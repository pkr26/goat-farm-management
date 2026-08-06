import { expect, test } from "@playwright/test";

import {
  createAnimal,
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

    // Create via the Animals page dialog (defaults: female, born on farm, QUARANTINE).
    await createAnimal(page, { tag });

    // The new animal shows up in the list (search to be robust to existing data).
    await page.getByPlaceholder("Search tag or name…").fill(tag);
    await expect(page.getByRole("link", { name: tag, exact: true })).toBeVisible();

    // Open its profile.
    await page.getByRole("link", { name: tag, exact: true }).click();
    await expect(page).toHaveURL(/\/animals\/\d+$/, { timeout: 15_000 });
    await expect(page.getByRole("heading", { name: new RegExp(tag) })).toBeVisible({
      timeout: 15_000,
    });
    await expect(profileDetail(page, "Bucket")).toHaveText("QUARANTINE");

    // Record a weight.
    await page.getByRole("button", { name: "Record weight" }).click();
    const weightDialog = page.getByRole("dialog", { name: "Record weight" });
    await weightDialog.getByLabel(/Weight \(kg\)/).fill("26.5");
    await weightDialog.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Weight recorded.")).toBeVisible();
    await expect(weightDialog).toBeHidden();

    // Profile reflects the weight.
    await expect(page.getByText("Weight history (1)")).toBeVisible();
    await expect(profileDetail(page, "Latest weight")).toHaveText("26.5 kg");

    // Move bucket (target differs from the current QUARANTINE).
    await page.getByRole("button", { name: "Move bucket" }).click();
    const moveDialog = page.getByRole("dialog", { name: "Move bucket" });
    await pickSelectOption(moveDialog, "To bucket *", "FOUNDATION");
    await moveDialog.getByRole("button", { name: "Move" }).click();
    await expect(page.getByText("Animal moved.")).toBeVisible();
    await expect(moveDialog).toBeHidden();

    // Profile reflects the move (creation itself logs the initial move into
    // QUARANTINE, so assert the moves-table row rather than a hardcoded count).
    await expect(profileDetail(page, "Bucket")).toHaveText("FOUNDATION");
    await expect(
      page.getByRole("row", { name: /QUARANTINE\s+FOUNDATION/ }),
    ).toBeVisible();

    // And the list shows the new bucket too.
    await openAnimalProfile(page, tag);
    await expect(profileDetail(page, "Bucket")).toHaveText("FOUNDATION");
  });
});
