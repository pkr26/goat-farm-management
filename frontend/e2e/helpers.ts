import { readFileSync } from "node:fs";

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
      readFileSync(new URL(".e2e-state.json", import.meta.url), "utf8"),
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
  const email = page.getByLabel("Email");
  const password = page.getByLabel("Password");
  // The login form is server-rendered before its React handlers attach, and a
  // slow first hydration (WebKit) resets values typed into that inert markup
  // exactly once. Retrying the fill until the values stick is deterministic;
  // the old `networkidle` wait added a fixed >=500ms idle pause to every test
  // in every browser and would degrade to the full navigation timeout if the
  // page ever gained background polling.
  await expect(async () => {
    await email.fill(E2E_EMAIL);
    await password.fill(E2E_PASSWORD);
    await expect(email).toHaveValue(E2E_EMAIL);
    await expect(password).toHaveValue(E2E_PASSWORD);
  }).toPass({ timeout: 15_000 });
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard$/, { timeout: 20_000 });
  // Farm is auto-selected and nav only renders once permissions load.
  await expect(page.locator("nav").getByRole("link", { name: "Animals" })).toBeVisible({
    timeout: 20_000,
  });
}

/**
 * Human labels for the enum codes the UI renders (worker-first UX): raw
 * codes never appear on screen anymore, so specs that still speak in codes
 * are translated here. Mirrors `src/lib/enum-labels.ts` — the product map is
 * the single source of truth; keep this in sync when a label changes.
 */
const UI_LABELS: Record<string, string> = {
  QUARANTINE: "Quarantine",
  FOUNDATION: "Foundation",
  BREEDING: "Breeding",
  PREGNANCY_EARLY: "Pregnancy A",
  PREGNANCY_LATE: "Pregnancy B",
  DELIVERY: "Delivery",
  RECOVERY: "Recovery",
  RESTING: "Resting",
  MALE_KIDS: "Male kids",
  FEMALE_KIDS: "Female kids",
  CLEANING: "Cleaning",
  FEED: "Feeding",
  MORNING: "Morning",
  AFTERNOON: "Afternoon",
  NIGHT: "Night",
};

/** Translate an enum code to the label the UI shows (identity if unknown). */
export function uiLabel(code: string): string {
  return UI_LABELS[code] ?? code;
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

/**
 * Open a server-backed RemotePicker by its accessible combobox label, search
 * the bounded result set when requested, and choose an option from the linked
 * picker dialog. Unlike the static Select helper above, this follows the
 * trigger's aria-controls relationship and exercises the user-visible search.
 */
export async function pickRemoteOption(
  scope: Locator,
  label: string,
  option: string | RegExp | null,
  query?: string,
): Promise<void> {
  const page = scope.page();
  const trigger = scope.getByRole("combobox", { name: label, exact: true });
  await expect(trigger).toBeVisible();
  const dialogId = await trigger.getAttribute("aria-controls");
  if (!dialogId) throw new Error(`Remote picker "${label}" has no aria-controls target.`);

  await trigger.click();
  const picker = page.locator(`[id="${dialogId}"]`);
  await expect(picker).toHaveRole("dialog");
  await expect(picker).toBeVisible();
  if (query !== undefined) {
    await picker.getByRole("searchbox").fill(query);
  }

  const options = picker.getByRole("option");
  if (option === null) {
    await expect(options.first()).toBeVisible({ timeout: 20_000 });
    await options.first().click();
  } else if (typeof option === "string") {
    const match = picker.getByRole("option", { name: option, exact: true });
    await expect(match).toBeVisible({ timeout: 20_000 });
    await match.click();
  } else {
    const match = picker.getByRole("option", { name: option }).first();
    await expect(match).toBeVisible({ timeout: 20_000 });
    await match.click();
  }
  await expect(picker).toBeHidden();
}

export interface NewAnimal {
  tag: string;
  /** Owner audit reason for this direct historical born-on-farm import. */
  historicalImportReason: string;
  sex?: "F" | "M";
  /** Display text, e.g. "FOUNDATION". Defaults to the form default (QUARANTINE). */
  bucket?: string;
  dateOfBirth?: string;
  entryWeightKg?: number;
  /** Date of the imported entry weight, required for as-of breeding fixtures. */
  entryWeightDate?: string;
}

/** Create an animal through the Animals page dialog; waits for the success toast. */
export async function createAnimal(page: Page, animal: NewAnimal): Promise<void> {
  if (animal.entryWeightDate !== undefined && animal.entryWeightKg === undefined) {
    throw new Error("entryWeightDate requires entryWeightKg");
  }

  await page.goto("/animals");
  await page.getByRole("button", { name: "Add animal" }).click();
  const dialog = page.getByRole("dialog", { name: "Add animal" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Tag number").fill(animal.tag);
  if (animal.sex) {
    await pickSelectOption(dialog, "Sex *", animal.sex === "M" ? "Male" : "Female");
  }
  await pickSelectOption(
    dialog,
    "Source *",
    "Historical born-on-farm import",
  );
  if (animal.bucket) {
    await pickSelectOption(dialog, "Bucket *", uiLabel(animal.bucket));
  }
  if (animal.dateOfBirth) {
    await dialog.getByLabel("Date of birth").fill(animal.dateOfBirth);
  }
  if (animal.entryWeightKg !== undefined) {
    await dialog.getByLabel("Entry weight (kg)").fill(String(animal.entryWeightKg));
  }
  if (animal.entryWeightDate !== undefined) {
    await dialog.getByLabel("Entry weight date").fill(animal.entryWeightDate);
  }
  await dialog
    .getByLabel("Historical import reason *")
    .fill(animal.historicalImportReason);
  await dialog.getByRole("button", { name: "Save animal" }).click();
  await expect(page.getByText("Animal added.")).toBeVisible();
  await expect(dialog).toBeHidden();
}

/** Find an animal by tag via the list search and open its profile page. */
export async function openAnimalProfile(page: Page, tag: string): Promise<void> {
  await page.goto("/animals");
  await page.getByPlaceholder("Search by tag…").fill(tag);
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
  // A buck that is eligible today may have no weight (or insufficient age) as
  // of a backdated breeding. Create and select a date-correct buck explicitly
  // so serial specs cannot accidentally reuse a later-only fixture.
  let preferredBuckTag: string | undefined;
  if (breedingDate !== undefined) {
    preferredBuckTag = uniqueTag("E2E-BUCK");
    await createAnimal(page, {
      tag: preferredBuckTag,
      historicalImportReason: "E2E backdated breeding buck fixture",
      sex: "M",
      bucket: "BREEDING",
      dateOfBirth: monthsAgo(20),
      entryWeightKg: 30,
      entryWeightDate: breedingDate,
    });
  }

  await page.goto("/breeding");
  await page.getByRole("button", { name: "Add breeding" }).first().click();
  let dialog = page.getByRole("dialog", { name: "Add breeding" });
  const noEligibleBuck = dialog.getByText(/No eligible bucks are available/);
  let buckPicker = dialog.getByRole("combobox", { name: "Buck *", exact: true });
  // Buck eligibility is checked against the canonical scoped endpoint. Wait
  // until it either enables the picker or reports a truthful empty state.
  await expect
    .poll(
      async () => (await buckPicker.isEnabled()) || (await noEligibleBuck.isVisible()),
      { timeout: 20_000 },
    )
    .toBe(true);

  if (await noEligibleBuck.isVisible()) {
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    preferredBuckTag = uniqueTag("E2E-BUCK");
    await createAnimal(page, {
      tag: preferredBuckTag,
      historicalImportReason: "E2E breeding buck fixture",
      sex: "M",
      bucket: "BREEDING",
      dateOfBirth: monthsAgo(20),
      entryWeightKg: 30,
      entryWeightDate: breedingDate,
    });
    await page.goto("/breeding");
    await page.getByRole("button", { name: "Add breeding" }).first().click();
    dialog = page.getByRole("dialog", { name: "Add breeding" });
    buckPicker = dialog.getByRole("combobox", { name: "Buck *", exact: true });
    await expect(buckPicker).toBeEnabled({ timeout: 20_000 });
  }

  await pickRemoteOption(dialog, "Doe *", new RegExp(doeTag), doeTag);
  if (preferredBuckTag !== undefined) {
    await pickRemoteOption(
      dialog,
      "Buck *",
      new RegExp(preferredBuckTag),
      preferredBuckTag,
    );
  } else {
    await pickRemoteOption(dialog, "Buck *", null);
  }
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
  const pregnantCheckbox = dialog.getByRole("checkbox", { name: "Pregnant — confirmed" });
  await expect(pregnantCheckbox).not.toBeChecked();
  await pregnantCheckbox.click();
  await expect(pregnantCheckbox).toBeChecked();
  // Kid count defaults to 2 — assert the default rather than re-picking it.
  await expect(selectTrigger(dialog, "Kid count detected")).toContainText("2");
  await dialog.getByRole("button", { name: "Save result" }).click();
  await expect(page.getByText("Ultrasound result saved.")).toBeVisible();
  await expect(dialog).toBeHidden();
}
