import { describe, expect, it } from "vitest";

import { validateDailyOpsHerd, type DailyOpsHerdRow } from "@/lib/daily-ops-input";
import { translate, type TFn } from "@/lib/i18n";

const t: TFn = (key, values) => translate("en", key, values);
const row = (patch: Partial<DailyOpsHerdRow> = {}): DailyOpsHerdRow => ({
  tag: "D1", sex: "F", bucket: "BREEDING", ageMonths: 18, daysInBucket: 0, bredDaysAgo: "", ...patch,
});
const validate = (...rows: DailyOpsHerdRow[]) => validateDailyOpsHerd(rows, t);

describe("operational simulation herd validation", () => {
  it("accepts exactly 500 distinct animals and rejects a 501st animal", () => {
    const herd = Array.from({ length: 500 }, (_, index) => row({ tag: `GOAT-${index}` }));
    expect(validateDailyOpsHerd(herd, t)).toEqual([]);
    expect(validateDailyOpsHerd([...herd, row({ tag: "GOAT-500" })], t)).toEqual(["At most 500 head per run."]);
  });

  it("requires a nonempty herd and nonblank trimmed tags", () => {
    expect(validate()).toEqual([t("opsSim.validation.needAnimal")]);
    expect(validate(row({ tag: "  " }))).toEqual([t("opsSim.validation.needTag")]);
    expect(validate(row({ tag: " D1 " }))).toEqual([]);
  });

  it("detects trimmed duplicates and lists each duplicate only once", () => {
    expect(validate(row(), row({ tag: " D1 " }), row(), row({ tag: "D2" }))).toEqual([
      t("opsSim.validation.duplicateTags", { tags: "D1" }),
    ]);
    expect(validate(row({ tag: "D1" }), row({ tag: "D2" }))).toEqual([]);
    expect(validate(row({ tag: "" }), row({ tag: "" }))).toEqual([t("opsSim.validation.needTag")]);
  });

  for (const [key, maximum, message] of [
    ["ageMonths", 240, "ageRange"], ["daysInBucket", 3650, "daysRange"],
  ] as const) {
    it.each([0, maximum])(`accepts inclusive ${key} boundary %s`, (value) => {
      expect(validate(row({ [key]: value }))).toEqual([]);
    });
    it.each([-1, maximum + 1])(`rejects ${key} outside its bounds: %s`, (value) => {
      expect(validate(row({ [key]: value }))).toEqual([t(`opsSim.validation.${message}`, { label: "D1", max: maximum })]);
    });
  }

  it.each(["", " ", "0", "150", " 150 "])("accepts optional/inclusive gestation value %j", (bredDaysAgo) => {
    expect(validate(row({ bredDaysAgo }))).toEqual([]);
  });
  it.each(["-1", "151", "1.5", "NaN", "Infinity", "not-a-number"])("rejects invalid gestation value %j", (bredDaysAgo) => {
    expect(validate(row({ bredDaysAgo }))).toEqual([t("opsSim.validation.bredRange", { label: "D1", max: 150 })]);
  });

  it("accepts age/day fractions for the page to round when submitting", () => {
    expect(validate(row({ ageMonths: 18.4, daysInBucket: 10.4 }))).toEqual([]);
  });

  it("accepts 44 quarantine days and rejects day 45", () => {
    expect(validate(row({ bucket: "QUARANTINE", daysInBucket: 44 }))).toEqual([]);
    expect(validate(row({ bucket: "QUARANTINE", daysInBucket: 45 }))).toEqual([
      t("opsSim.validation.quarantineLimit", { label: "D1" }),
    ]);
    expect(validate(row({ bucket: "BREEDING", daysInBucket: 45 }))).toEqual([]);
  });

  it("permits only males in the male-kids bucket", () => {
    expect(validate(row({ bucket: "MALE_KIDS", sex: "M" }))).toEqual([]);
    expect(validate(row({ bucket: "MALE_KIDS", sex: "F" }))).toEqual([t("opsSim.validation.maleKidsOnly", { label: "D1" })]);
  });

  it.each(["PREGNANCY_EARLY", "PREGNANCY_LATE", "DELIVERY"])("requires a bred doe in %s", (bucket) => {
    expect(validate(row({ bucket, bredDaysAgo: "0" }))).toEqual([]);
    expect(validate(row({ bucket, bredDaysAgo: " " }))).toEqual([
      t("opsSim.validation.needsBred", { label: "D1", bucket: bucket.toLowerCase() }),
    ]);
    expect(validate(row({ bucket, sex: "M", bredDaysAgo: "0" }))).toEqual([
      t("opsSim.validation.doeOnly", { label: "D1" }), t("opsSim.validation.bredDoesOnly", { label: "D1" }),
    ]);
  });

  it("permits unbred males and blocks gestation values on males outside pregnancy buckets", () => {
    expect(validate(row({ sex: "M", bredDaysAgo: "" }))).toEqual([]);
    expect(validate(row({ sex: "M", bredDaysAgo: "0" }))).toEqual([t("opsSim.validation.bredDoesOnly", { label: "D1" })]);
  });

  it("uses the unnamed-row label for additional errors without changing the inputs", () => {
    const animal = Object.freeze(row({ tag: " ", ageMonths: -1 }));
    const herd = Object.freeze([animal]);
    expect(validateDailyOpsHerd(herd, t)).toEqual([
      t("opsSim.validation.needTag"), t("opsSim.validation.ageRange", { label: t("opsSim.validation.unnamed"), max: 240 }),
    ]);
    expect(animal.tag).toBe(" ");
  });
});
