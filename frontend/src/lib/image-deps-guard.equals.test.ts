/**
 * Version comparator equal-input contract: two identical dotted versions compare
 * equal — and the loop terminates. A `<` → `<=` loop bound keeps iterating past both
 * arrays forever on equal inputs (undefined ?? 0 on both sides, delta always 0).
 */

import { describe, expect, it } from "vitest";

import { compareDottedVersions } from "./image-deps-guard";

describe("compareDottedVersions equal inputs", () => {
  it("returns 0 for identical versions and terminates", () => {
    expect(compareDottedVersions("1.2.3", "1.2.3")).toBe(0);
    expect(compareDottedVersions("15.0.0", "15.0.0")).toBe(0);
    // Different lengths keep their ordering semantics.
    expect(compareDottedVersions("1.2", "1.2.0")).toBe(0);
    expect(compareDottedVersions("1.2", "1.2.1")).toBeLessThan(0);
  });
});
