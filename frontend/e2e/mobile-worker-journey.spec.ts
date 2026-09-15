import { expect, test, type Locator, type Page } from "@playwright/test";

import {
  createAnimal,
  createBreeding,
  daysAgo,
  monthsAgo,
  pickSelectOption,
  signIn,
  uniqueTag,
} from "./helpers";

/**
 * Phone worker journey (Mobile Chrome project — Pixel 7 emulation). The
 * worker's device is a phone, so below md the duty board renders the
 * md:hidden card list instead of the 720px table, and every action on a
 * card must meet the 44px touch-target floor.
 *
 * login → tasks board (card layout) → complete a duty → open a linked form.
 */

/** The below-md card list on the tasks board (the app shell has no other
 * md:hidden surface). */
function cardList(page: Page): Locator {
  return page.locator(".md\\:hidden");
}

/** The desktop table wrapper (hidden below md). */
function desktopTableWrapper(page: Page): Locator {
  return page.locator(".md\\:block");
}

/** Assert the element's rendered height meets the 44px touch-target floor. */
async function expectTouchTarget(locator: Locator): Promise<void> {
  const box = await locator.boundingBox();
  expect(box, "touch target must be measurable").not.toBeNull();
  expect(box!.height).toBeGreaterThanOrEqual(44);
}

test.describe("mobile worker journey", () => {
  test("login → tasks board cards → complete a duty → open the linked form", async ({
    page,
  }) => {
    test.setTimeout(180_000);
    const doeTag = uniqueTag("E2E-MDOE");
    const dutyTitle = uniqueTag("E2E mobile duty");
    const breedingDate = daysAgo(40);
    await signIn(page);

    // A breeding 40 days back leaves the ultrasound duty overdue and
    // form-linked (Open form, no raw Complete button).
    await createAnimal(page, {
      tag: doeTag,
      historicalImportReason: "E2E mobile worker doe fixture",
      sex: "F",
      bucket: "FOUNDATION",
      dateOfBirth: monthsAgo(20),
      entryWeightKg: 24,
      entryWeightDate: breedingDate,
    });
    await createBreeding(page, doeTag, breedingDate);

    // A manual CLEANING duty due today, completable from the card.
    await page.goto("/tasks");
    await page.getByRole("button", { name: "New duty" }).click();
    const dialog = page.getByRole("dialog", { name: "New duty" });
    await dialog.getByLabel("Title").fill(dutyTitle);
    await pickSelectOption(dialog, "Category", "Cleaning");
    await dialog.getByRole("button", { name: "Create duty" }).click();
    await expect(page.getByText("Duty created.")).toBeVisible();
    await expect(dialog).toBeHidden();

    // ---- Today tab: the board is the md:hidden card list on a phone. ----
    await page.getByRole("tab", { name: /Today/ }).click();
    const cards = cardList(page);
    const dutyCard = cards.locator("> div", { hasText: dutyTitle });
    await expect(dutyCard).toBeVisible({ timeout: 15_000 });
    // The 720px desktop table is hidden at this viewport.
    await expect(desktopTableWrapper(page)).toBeHidden();

    // Card actions are ≥44px touch targets.
    const completeButton = dutyCard.getByRole("button", { name: "Complete" });
    const skipButton = dutyCard.getByRole("button", { name: "Skip" });
    await expectTouchTarget(completeButton);
    await expectTouchTarget(skipButton);

    // ---- Complete the duty from the card. ----
    await completeButton.click();
    await expect(page.getByText("Task completed.")).toBeVisible();

    // ---- Overdue tab: open the duty's linked form from the card. ----
    await page.getByRole("tab", { name: /Overdue/ }).click();
    const ultrasoundCard = cardList(page).locator("> div", {
      hasText: `Pregnancy check: ${doeTag}`,
    });
    await expect(ultrasoundCard).toBeVisible({ timeout: 15_000 });
    const openForm = ultrasoundCard.getByRole("link", { name: "Open form" });
    await expectTouchTarget(openForm);
    await openForm.click();

    // The linked form is the ultrasound-result dialog on /breeding.
    await expect(page).toHaveURL(/\/breeding\?.*ultrasound_id=\d+/);
    await expect(
      page.getByRole("dialog", { name: "Ultrasound result" }),
    ).toBeVisible({ timeout: 15_000 });
  });
});
