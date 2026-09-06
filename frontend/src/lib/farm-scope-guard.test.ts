import { afterEach, describe, expect, it } from "vitest";

import { setCurrentFarmId } from "@/lib/api-client";

import { captureFarmScope } from "./farm-scope-guard";

afterEach(() => {
  setCurrentFarmId(null);
});

describe("captureFarmScope", () => {
  it("reports stillCurrent while the farm is unchanged", () => {
    setCurrentFarmId("7");
    const stillCurrent = captureFarmScope();
    setCurrentFarmId("7"); // same value: no epoch bump
    expect(stillCurrent()).toBe(true);
  });

  it("reports false after any farm switch", () => {
    setCurrentFarmId("7");
    const stillCurrent = captureFarmScope();
    setCurrentFarmId("9");
    expect(stillCurrent()).toBe(false);
    // A switch back does not revive the superseded continuation.
    setCurrentFarmId("7");
    expect(stillCurrent()).toBe(false);
  });

  it("reports false after the farm selection is cleared", () => {
    setCurrentFarmId("7");
    const stillCurrent = captureFarmScope();
    setCurrentFarmId(null);
    expect(stillCurrent()).toBe(false);
  });
});
