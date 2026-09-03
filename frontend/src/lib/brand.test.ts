import { describe, expect, it, vi } from "vitest";

import { APP_NAME } from "./brand";

describe("APP_NAME", () => {
  it("pins the product wordmark exactly", () => {
    expect(APP_NAME).toBe("Herdly");
  });

  it("pins the wordmark on a fresh module evaluation too", async () => {
    // Re-evaluate the module inside the test so module-scope mutants are
    // attributed to (and exercised by) this test, not only by whatever test
    // first imported the module in the worker.
    vi.resetModules();
    const fresh = await import("./brand");
    expect(fresh.APP_NAME).toBe("Herdly");
  });
});
