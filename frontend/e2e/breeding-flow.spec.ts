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
    await signIn(page);

    // Create a breeding-ready doe: female, born on farm, ~14 months old, 24 kg,
    // living in a breeding-ready bucket (FOUNDATION).
    await createAnimal(page, {
      tag: doeTag,
      sex: "F",
      bucket: "FOUNDATION",
      dateOfBirth: monthsAgo(14),
      entryWeightKg: 24,
    });

    // Breed her (creates an active buck first if the farm has none).
    // The ultrasound result is only recordable on/after its planned date
    // (breeding + 32 days), so use a historical but still pending breeding.
    await createBreeding(page, doeTag, daysAgo(40));

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
