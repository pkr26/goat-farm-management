import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, type Locator, type Page } from "@playwright/test";

/** Credentials of the fresh user+farm provisioned by global-setup.ts for
 *  this run (audit 10-H1 — no hardcoded pre-existing dev account). */
export interface E2ECredentials {
  email: string;
  password: string;
  farmName: string;
}

function loadCredentials(): E2ECredentials {
  try {
    return JSON.parse(
      readFileSync(path.join(__dirname, ".e2e-state.json"), "utf8"),
    ) as E2ECredentials;
  } catch {
    throw new Error(
      "e2e/.e2e-state.json missing — Playwright globalSetup provisions it before the specs run.",
    );
  }
}

const CREDS = loadCredentials();
export const E2E_EMAIL = CREDS.email;
export const E2E_PASSWORD = CREDS.password;
export const E2E_FARM_NAME = CREDS.farmName;

/** Run-unique, regex-safe tag/title suffix (alphanumerics + hyphens only). */
export function uniqueTag(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}${Math.floor(Math.random() * 1296).toString(36)}`;
}

/** Local YYYY-MM-DD for a Date (matches the app's localToday formatting). */
function fmtDate(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

/** YYYY-MM-DD `months` months before today. */
export function monthsAgo(months: number): string {
  const d = new Date();
  d.setMonth(d.getMonth() - months);
  return fmtDate(d);
}

/** YYYY-MM-DD `days` days before today. */
export function daysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return fmtDate(d);
}

/** Sign in through the login page and land on the dashboard. */
export async function signIn(page: Page): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(E2E_EMAIL);
  await page.getByLabel("Password").fill(E2E_PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard$/, { timeout: 20_000 });
  // Farm is auto-selected and nav only renders once permissions load.
  await expect(page.locator("nav").getByRole("link", { name: "Animals" })).toBeVisible({
    timeout: 20_000,
  });
}

/**
 * The shadcn/base-ui Select has no accessible name wiring to its Label, so we
 * scope the trigger to the field wrapper div that contains the label text.
 */
function selectTrigger(scope: Locator, label: string): Locator {
  return scope
    .locator("div.space-y-1\\.5")
    .filter({ has: scope.page().getByText(label, { exact: true }) })
    .locator('[data-slot="select-trigger"]')
    .first();
}

/**
 * Open a base-ui Select inside `scope` identified by its field label and pick
 * an option. String matches by accessible name exactly (a substring match
 * silently picks e.g. "Female" for `"Male"`); pass a RegExp for fuzzy
 * matching. `option` null picks the first available option.
 */
export async function pickSelectOption(
  scope: Locator,
  label: string,
  option: string | RegExp | null,
): Promise<void> {
  const page = scope.page();
  await selectTrigger(scope, label).click();
  if (option === null) {
    await page.getByRole("option").first().click();
  } else if (typeof option === "string") {
    await page.getByRole("option", { name: option, exact: true }).first().click();
  } else {
    await page.getByRole("option").filter({ hasText: option }).first().click();
  }
}

export interface NewAnimal {
  tag: string;
  sex?: "F" | "M";
  /** Display text, e.g. "FOUNDATION". Defaults to the form default (QUARANTINE). */
  bucket?: string;
  dateOfBirth?: string;
  entryWeightKg?: number;
}

/** Create an animal through the Animals page dialog; waits for the success toast. */
export async function createAnimal(page: Page, animal: NewAnimal): Promise<void> {
  await page.goto("/animals");
  await page.getByRole("button", { name: "Add animal" }).click();
  const dialog = page.getByRole("dialog", { name: "Add animal" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Tag number").fill(animal.tag);
  if (animal.sex) {
    await pickSelectOption(dialog, "Sex *", animal.sex === "M" ? "Male" : "Female");
  }
  if (animal.bucket) {
    await pickSelectOption(dialog, "Bucket *", animal.bucket);
  }
  if (animal.dateOfBirth) {
    await dialog.getByLabel("Date of birth").fill(animal.dateOfBirth);
  }
  if (animal.entryWeightKg !== undefined) {
    await dialog.getByLabel("Entry weight (kg)").fill(String(animal.entryWeightKg));
  }
  await dialog.getByRole("button", { name: "Save animal" }).click();
  await expect(page.getByText("Animal added.")).toBeVisible();
  await expect(dialog).toBeHidden();
}

/** Find an animal by tag via the list search and open its profile page. */
export async function openAnimalProfile(page: Page, tag: string): Promise<void> {
  await page.goto("/animals");
  await page.getByPlaceholder("Search tag or name…").fill(tag);
  await page.getByRole("link", { name: tag, exact: true }).click();
  await expect(page).toHaveURL(/\/animals\/\d+$/, { timeout: 15_000 });
  await expect(page.getByRole("heading", { name: new RegExp(tag) })).toBeVisible({
    timeout: 15_000,
  });
}

/** Locator for the <dd> value of a Details-card <dt> on the animal profile. */
export function profileDetail(page: Page, label: string): Locator {
  return page
    .getByText(label, { exact: true })
    .locator("xpath=following-sibling::dd[1]");
}

/**
 * Create a breeding record for `doeTag` through the Breeding page dialog.
 * Ensures an active buck exists first (creates one via the Animals dialog if
 * the farm has none). Breeding date stays at the default (today) unless
 * `breedingDate` (YYYY-MM-DD) is given — e.g. an overdue pregnancy for the
 * kidding flow needs a breeding ~150+ days in the past.
 */
export async function createBreeding(
  page: Page,
  doeTag: string,
  breedingDate?: string,
): Promise<void> {
  await page.goto("/breeding");
  await page.getByRole("button", { name: "Add breeding" }).click();
  let dialog = page.getByRole("dialog", { name: "Add breeding" });
  // The form only renders once the animal pickers have loaded.
  await expect(dialog.getByText("Buck *", { exact: true })).toBeVisible({ timeout: 20_000 });

  if (await dialog.getByText("No active bucks on this farm").isVisible()) {
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await createAnimal(page, { tag: uniqueTag("E2E-BUCK"), sex: "M" });
    await page.goto("/breeding");
    await page.getByRole("button", { name: "Add breeding" }).click();
    dialog = page.getByRole("dialog", { name: "Add breeding" });
    await expect(dialog.getByText("Buck *", { exact: true })).toBeVisible({ timeout: 20_000 });
  }

  await pickSelectOption(dialog, "Doe *", new RegExp(doeTag));
  await pickSelectOption(dialog, "Buck *", null);
  if (breedingDate) {
    await dialog.getByLabel("Breeding date *").fill(breedingDate);
  }
  await dialog.getByRole("button", { name: "Save breeding" }).click();
  await expect(page.getByText("Breeding saved.")).toBeVisible();
  await expect(dialog).toBeHidden();
}

/** Record a pregnant ultrasound (default kid count 2) for the doe's PENDING record. */
export async function recordUltrasoundPregnant(page: Page, doeTag: string): Promise<void> {
  await page.goto("/breeding");
  const row = page.getByRole("row", { name: new RegExp(doeTag) });
  await expect(row).toBeVisible({ timeout: 15_000 });
  await row.getByRole("button", { name: "Ultrasound result" }).click();
  const dialog = page.getByRole("dialog", { name: "Ultrasound result" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("checkbox", { name: "Pregnant — confirmed" })).toBeChecked();
  // Kid count defaults to 2 — assert the default rather than re-picking it.
  await expect(selectTrigger(dialog, "Kid count detected")).toContainText("2");
  await dialog.getByRole("button", { name: "Save result" }).click();
  await expect(page.getByText("Ultrasound result saved.")).toBeVisible();
  await expect(dialog).toBeHidden();
}
