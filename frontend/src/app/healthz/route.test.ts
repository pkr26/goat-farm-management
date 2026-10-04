/**
 * The liveness probe renders dynamically and never caches a stale healthy response.
 */

import { describe, expect, it } from "vitest";

import { dynamic, GET } from "./route";

describe("/healthz liveness probe", () => {
  it("answers ok, never cached", async () => {
    const response = GET();

    expect(response.status).toBe(200);
    expect(await response.text()).toBe("ok");
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    expect(dynamic).toBe("force-dynamic");
  });
});
