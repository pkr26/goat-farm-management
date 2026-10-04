/**
 * Sex-restricted bucket options match the backend animal constraint.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { BUCKET_REQUIRED_SEX } from "./bucket-sex";

const constraintSource = (() => {
  const source = readFileSync(
    join(process.cwd(), "..", "backend", "app", "models", "animals.py"),
    "utf8",
  );
  const match = source.match(/CheckConstraint\(([^]+?),\s*name="ck_animals_bucket_sex"/);
  if (!match) {
    throw new Error("ck_animals_bucket_sex not found in backend animals.py — update this test's anchor");
  }
  // The constraint is written as adjacent Python string literals; join them
  // into one logical expression before parsing.
  return match[1].replace(/"\s*"/g, "");
})();

/** Every bucket the constraint names, with the sex it requires. */
const backendRequiredSex: Record<string, string> = (() => {
  const out: Record<string, string> = {};
  // Single-bucket clauses: (current_bucket <> 'X' OR sex = 'M'|'F')
  for (const m of constraintSource.matchAll(
    /current_bucket\s*<>\s*'([A-Z_]+)'\s*OR\s*sex\s*=\s*'([MF])'/g,
  )) {
    out[m[1]] = m[2];
  }
  // The NOT IN list: (current_bucket NOT IN ('A','B',…) OR sex = 'M'|'F')
  for (const m of constraintSource.matchAll(
    /current_bucket\s*NOT IN\s*\(([^)]+)\)\s*OR\s*sex\s*=\s*'([MF])'/g,
  )) {
    for (const bucket of m[1].matchAll(/'([A-Z_]+)'/g)) {
      out[bucket[1]] = m[2];
    }
  }
  return out;
})();

describe("BUCKET_REQUIRED_SEX mirrors ck_animals_bucket_sex", () => {
  it("the backend constraint was actually parsed (non-empty, sex-valued)", () => {
    expect(Object.keys(backendRequiredSex).length).toBeGreaterThanOrEqual(6);
    for (const sex of Object.values(backendRequiredSex)) {
      expect(["M", "F"]).toContain(sex);
    }
  });

  it("every restricted backend bucket is restricted here with the same sex", () => {
    expect(BUCKET_REQUIRED_SEX).toEqual(backendRequiredSex);
  });
});
