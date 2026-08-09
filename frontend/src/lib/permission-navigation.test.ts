import { describe, expect, it } from "vitest";

import {
  firstPermittedPathFromList,
  permittedAppPathFromList,
  withReturnTo,
} from "@/lib/permission-navigation";

describe("firstPermittedPathFromList", () => {
  it("uses the first permitted module instead of assuming dashboard access", () => {
    expect(firstPermittedPathFromList(["health.view", "finance.view"])).toBe("/health");
    expect(firstPermittedPathFromList(["tasks.view"])).toBe("/tasks");
  });

  it("uses the safe no-access page for an empty custom role", () => {
    expect(firstPermittedPathFromList([])).toBe("/no-access");
  });
});

describe("permittedAppPathFromList", () => {
  it("preserves a permitted internal detail path with its page state", () => {
    expect(
      permittedAppPathFromList("/tasks?tab=overdue#task-7", ["tasks.view"]),
    ).toBe("/tasks?tab=overdue#task-7");
    expect(permittedAppPathFromList("/animals/7?returnTo=%2Fdashboard", ["animals.view"]))
      .toBe("/animals/7?returnTo=%2Fdashboard");
  });

  it("fails closed for external, traversal, unknown, and unpermitted destinations", () => {
    for (const path of [
      "https://evil.test/tasks",
      "//evil.test/tasks",
      "/tasks/../finance",
      "/tasks/./overdue",
      "/tasks/%2e%2e/finance",
      "/%74asks",
      "/unknown",
      "/tasks\\evil",
    ]) {
      expect(permittedAppPathFromList(path, ["tasks.view", "finance.view"])).toBeNull();
    }
    expect(permittedAppPathFromList("/health", ["tasks.view"])).toBeNull();
    expect(permittedAppPathFromList("/health/new", ["health.view"])).toBeNull();
  });
});

describe("withReturnTo", () => {
  it("adds encoded return state without discarding an existing query", () => {
    expect(withReturnTo("/health/new?task_id=8", "/tasks?tab=overdue")).toBe(
      "/health/new?task_id=8&returnTo=%2Ftasks%3Ftab%3Doverdue",
    );
  });
});
