/**
 * permission-navigation — fresh-domain mutation campaign kills (2026-09):
 * percent-encoded and dot-segment return destinations must be rejected as
 * written, and a root module path must keep its query state through the
 * farm-switch validator.
 */

import { describe, expect, it } from "vitest";

import { permittedAppPathFromList } from "./permission-navigation";

describe("permittedAppPathFromList — campaign kills", () => {
  it("rejects a percent-encoded path spelling outright", () => {
    // A path that would resolve to the animals module is still refused: only
    // the already-canonical spelling may name a module.
    expect(permittedAppPathFromList("/animals/%41", ["animals.view"])).toBeNull();
  });

  it("rejects a path that only normalizes into a permitted module", () => {
    expect(permittedAppPathFromList("/tasks/../animals", ["animals.view"])).toBeNull();
  });

  it("preserves a root module path's query state", () => {
    expect(permittedAppPathFromList("/animals?tab=2", ["animals.view"])).toBe("/animals?tab=2");
  });
});
