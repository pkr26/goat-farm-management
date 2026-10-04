/** Return-path validation rejects alternate encodings and preserves supported query state. */

import { describe, expect, it } from "vitest";

import { permittedAppPathFromList } from "./permission-navigation";

describe("permittedAppPathFromList — contracts", () => {
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
