import type { TFn } from "@/lib/i18n";

/** Inputs checked before an operational simulation is submitted. */
export type DailyOpsHerdRow = {
  tag: string;
  sex: "F" | "M";
  bucket: string;
  ageMonths: number;
  daysInBucket: number;
  bredDaysAgo: string;
};

const MAX_START_HEAD = 500;
// Per-animal bounds from AnimalStartSpec in shared/openapi.json; the backend
// re-validates (422), but blocking the run client-side avoids a lost round trip.
const MAX_AGE_MONTHS = 240;
const MAX_DAYS_IN_BUCKET = 3650;
const MAX_BRED_DAYS = 150;

export function validateDailyOpsHerd(rows: readonly DailyOpsHerdRow[], t: TFn): string[] {
  const errors: string[] = [];
  const tags = rows.map((row) => row.tag.trim());
  if (tags.some((tag) => tag === "")) errors.push(t("opsSim.validation.needTag"));
  const duplicates = tags.filter((tag, index) => tag !== "" && tags.indexOf(tag) !== index);
  if (duplicates.length > 0) {
    errors.push(
      t("opsSim.validation.duplicateTags", { tags: [...new Set(duplicates)].join(", ") }),
    );
  }
  if (rows.length === 0) errors.push(t("opsSim.validation.needAnimal"));
  if (rows.length > MAX_START_HEAD) {
    errors.push(t("opsSim.validation.maxHead", { max: MAX_START_HEAD }));
  }
  for (const row of rows) {
    const label = row.tag.trim() || t("opsSim.validation.unnamed");
    if (row.ageMonths < 0 || row.ageMonths > MAX_AGE_MONTHS) {
      errors.push(t("opsSim.validation.ageRange", { label, max: MAX_AGE_MONTHS }));
    }
    if (row.daysInBucket < 0 || row.daysInBucket > MAX_DAYS_IN_BUCKET) {
      errors.push(t("opsSim.validation.daysRange", { label, max: MAX_DAYS_IN_BUCKET }));
    }
    if (row.bredDaysAgo.trim() !== "") {
      const bred = Number(row.bredDaysAgo);
      if (!Number.isInteger(bred) || bred < 0 || bred > MAX_BRED_DAYS) {
        errors.push(t("opsSim.validation.bredRange", { label, max: MAX_BRED_DAYS }));
      }
    }
    // Coherence mirrors of the server's DailyOpsInput._check_herd. Structural rules
    // only — the gestation-day windows stay server-side where the species profile
    // lives.
    if (row.bucket === "MALE_KIDS" && row.sex !== "M") {
      errors.push(t("opsSim.validation.maleKidsOnly", { label }));
    }
    if (
      (row.bucket === "PREGNANCY_EARLY" ||
        row.bucket === "PREGNANCY_LATE" ||
        row.bucket === "DELIVERY") &&
      row.sex !== "F"
    ) {
      errors.push(t("opsSim.validation.doeOnly", { label }));
    }
    if (
      (row.bucket === "PREGNANCY_EARLY" ||
        row.bucket === "PREGNANCY_LATE" ||
        row.bucket === "DELIVERY") &&
      row.bredDaysAgo.trim() === ""
    ) {
      errors.push(
        t("opsSim.validation.needsBred", { label, bucket: row.bucket.toLowerCase() }),
      );
    }
    if (row.bucket === "QUARANTINE" && row.daysInBucket > 44) {
      errors.push(t("opsSim.validation.quarantineLimit", { label }));
    }
    if (row.bredDaysAgo.trim() !== "" && row.sex !== "F") {
      errors.push(t("opsSim.validation.bredDoesOnly", { label }));
    }
  }
  return errors;
}
