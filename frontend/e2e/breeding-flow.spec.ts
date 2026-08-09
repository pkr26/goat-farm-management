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

test.describe("breeding flow", () => {
  test("doe is bred, confirmed pregnant via ultrasound, moves to PREGNANCY_EARLY", async ({
    page,
  }) => {
    test.setTimeout(120_000);
    const doeTag = uniqueTag("E2E-DOE");
    const breedingDate = daysAgo(40);
    await signIn(page);

    // The breeding is backdated, so its eligibility facts must exist as of
    // that date rather than only today.
    await createAnimal(page, {
      tag: doeTag,
      historicalImportReason: "E2E breeding-flow doe fixture",
      sex: "F",
      bucket: "FOUNDATION",
      dateOfBirth: monthsAgo(20),
      entryWeightKg: 24,
      entryWeightDate: breedingDate,
    });

    // Breed her (creates an active buck first if the farm has none).
    // The ultrasound result is only recordable on/after its planned date
    // (breeding + 32 days), so use a historical but still pending breeding.
    await createBreeding(page, doeTag, breedingDate);

    // The record shows as PENDING on the breeding list.
    const row = page.getByRole("row", { name: new RegExp(doeTag) });
    await expect(row.getByText("PENDING")).toBeVisible();

    // Record the ultrasound: pregnant, 2 kids (the dialog defaults).
    await recordUltrasoundPregnant(page, doeTag);

    // Outcome flips to CONFIRMED PREGNANT.
    await expect(
      page.getByRole("row", { name: new RegExp(doeTag) }).getByText("CONFIRMED PREGNANT"),
    ).toBeVisible({ timeout: 15_000 });

    // The doe's profile shows she was moved to PREGNANCY_EARLY.
    await openAnimalProfile(page, doeTag);
    await expect(profileDetail(page, "Bucket")).toHaveText("PREGNANCY EARLY");
  });
});
