/**
 * /healthz liveness probe: a stale "ok" must never mask a dead Next server,
 * so the handler stays force-dynamic and answers no-store (2026-09-28 audit,
 * T1 — one of the files the aggregate floors let sit at 0%).
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
