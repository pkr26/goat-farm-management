import { describe, expect, it } from "vitest";

import { firstPermittedPathFromList } from "@/lib/permission-navigation";

describe("firstPermittedPathFromList", () => {
  it("uses the first permitted module instead of assuming dashboard access", () => {
    expect(firstPermittedPathFromList(["health.view", "finance.view"])).toBe("/health");
    expect(firstPermittedPathFromList(["tasks.view"])).toBe("/tasks");
  });

  it("uses the safe no-access page for an empty custom role", () => {
    expect(firstPermittedPathFromList([])).toBe("/no-access");
  });
});
