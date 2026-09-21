/**
 * Pins every frontend mirror of a backend constant to the backend's actual
 * value, by reading the backend source. A drifted mirror used to let deep
 * links pass the client clamp and 422 the API with no self-heal
 * (MAX_PAGE_OFFSET stayed 1_000_000 after the backend lowered it to
 * 10_000 — 2026-09-20 audit P2-16); this test fails the suite the moment
 * either side moves without the other.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { MAX_PAGE_OFFSET } from "./use-url-state";
import { farmVocabulary } from "./farm-vocabulary";

const backendConstant = (file: string, name: string): number => {
  const source = readFileSync(
    join(process.cwd(), "..", "backend", "app", ...file.split("/")),
    "utf8",
  );
  // Indifferent to indentation (species-profile kwargs) and to Python's
  // digit separators (10_000), anchored to the constant's own name.
  const match = source.match(new RegExp(`^\\s*${name}\\s*=\\s*([\\d_]+)`, "m"));
  if (!match) {
    throw new Error(`${name} not found in backend ${file} — update this test's anchor`);
  }
  return Number(match[1].replace(/_/g, ""));
};

describe("frontend mirrors of backend constants", () => {
  it("MAX_PAGE_OFFSET matches backend schemas/common.py", () => {
    expect(MAX_PAGE_OFFSET).toBe(backendConstant("schemas/common.py", "MAX_PAGE_OFFSET"));
  });

  it("the minimum breeding age matches the species profile", () => {
    // P2-17: the client gate and operator copy said 10 months while the
    // backend enforces 12 — a 10-11-month doe passed client validation and
    // 422'd on submit.
    expect(farmVocabulary.facts.minBreedingAgeMonths).toBe(
      backendConstant("models/species.py", "min_breeding_age_months"),
    );
  });
});
