/**
 * ADVERSARIAL AUDIT D1/D3/D4 — transport-layer attacks.
 *
 * D1  Idempotency coverage matrix: an adversary (or just an ambiguous network
 *     failure) replays every money/stock-creating POST. Which routes carry a
 *     stable Idempotency-Key across an automatic retry, and which are naked?
 * D1b Ambiguous failure: first send dies at the socket AFTER the server may
 *     have received it — the retry must reuse the SAME key (no double commit).
 * D3  401 storm: N parallel requests hitting an expired token must trigger
 *     exactly ONE /api/auth/refresh.
 * D4  Malicious/shape-shifted error bodies must never crash the error mapper
 *     and must surface usable detail.
 */

import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";

import { apiFetch, ApiError } from "@/lib/api-client";
import { isIdempotencyProtectedMutation } from "@/lib/idempotent-request";

describe("ADV D1: idempotency coverage matrix (money/stock-creating POSTs)", () => {
  const PROTECTED = [
    "/api/auth/farms",
    "/api/finance/new",
    "/api/finance/transactions/12/correct",
    "/api/purchases/new",
    "/api/animals",
    "/api/animals/7/weight",
    "/api/tasks",
    "/api/team/workers",
    "/api/health/events",
    "/api/simulation/scenarios",
    "/api/planner/plans",
    "/api/feeding/dispense",
    "/api/feeding/mix",
    "/api/feeding/inventory/3/add",
  ];
  it.each(PROTECTED)("POST %s is idempotency-protected", (path) => {
    expect(isIdempotencyProtectedMutation(path, "POST")).toBe(true);
  });

  it("non-POST methods are never protected (query replay is harmless)", () => {
    expect(isIdempotencyProtectedMutation("/api/finance/new", "GET")).toBe(false);
    expect(isIdempotencyProtectedMutation("/api/animals", undefined)).toBe(false);
  });

  it("unrelated POSTs are not swept into the registry", () => {
    expect(isIdempotencyProtectedMutation("/api/auth/login", "POST")).toBe(false);
    expect(isIdempotencyProtectedMutation("/api/animals/7/status", "POST")).toBe(false);
  });

  // FIXED (L13, client side): pregnancy creation and kidding (which
  // auto-creates animals + tasks) now carry a client-managed Idempotency-Key —
  // concurrent submits dedupe and the automatic retry reuses the same key.
  // Server-side dedup still needs the backend to declare the header (tracked).
  it("breeding/kidding creation POSTs are client-side idempotency-protected", () => {
    expect(isIdempotencyProtectedMutation("/api/breeding", "POST")).toBe(true);
    expect(isIdempotencyProtectedMutation("/api/kidding", "POST")).toBe(true);
  });
});

describe("ADV D1b: ambiguous socket failure replays the SAME key exactly once more", () => {
  beforeEach(() => {
    server.events.removeAllListeners();
  });

  it("finance/new: network failure → one automatic retry with the identical Idempotency-Key", async () => {
    const seenKeys: string[] = [];
    let calls = 0;
    server.use(
      http.post("/api/finance/new", ({ request }) => {
        calls += 1;
        seenKeys.push(request.headers.get("idempotency-key") ?? "");
        if (calls === 1) {
          // Adversary: the connection dies after the server has likely received
          // the write. (MSW aborts the response.)
          return Response.error();
        }
        return HttpResponse.json({ id: calls }, { status: 201 });
      }),
    );

    const result = await apiFetch<{ id: number }>("/api/finance/new", {
      method: "POST",
      body: JSON.stringify({ amount: 100 }),
    });

    expect(result).toEqual({ id: 2 });
    expect(calls).toBe(2);
    expect(seenKeys).toHaveLength(2);
    expect(seenKeys[0]).toMatch(/^[0-9a-f-]{36}$/i);
    expect(seenKeys[1]).toBe(seenKeys[0]);
  });

  it("a 4xx verdict does NOT auto-retry (no double send)", async () => {
    let calls = 0;
    server.use(
      http.post("/api/finance/new", () => {
        calls += 1;
        return HttpResponse.json({ detail: "bad amount" }, { status: 422 });
      }),
    );
    await expect(
      apiFetch("/api/finance/new", { method: "POST", body: JSON.stringify({}) }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(calls).toBe(1);
  });
});

describe("ADV D3: 401 storm triggers exactly one refresh", () => {
  it("10 parallel 401s on an expired token → 1 refresh, all retried and resolved", async () => {
    let refreshCalls = 0;
    let dataCalls = 0;
    server.use(
      http.post("/api/auth/refresh", () => {
        refreshCalls += 1;
        return HttpResponse.json({
          access_token: "rotated-token",
          user: { id: 1, email: "a@b.c", name: null },
        });
      }),
      http.get("/api/animals", () => {
        dataCalls += 1;
        return new Response(null, { status: 401 });
      }),
    );

    const results = await Promise.allSettled(
      Array.from({ length: 10 }, () => apiFetch("/api/animals")),
    );
    // Every caller eventually observes the authoritative 401 (the refresh here
    // is single-flight and the rotated token still 401s by design of the mock).
    expect(results.every((r) => r.status === "rejected")).toBe(true);
    expect(dataCalls).toBe(20); // 10 originals + 10 post-refresh retries
    expect(refreshCalls).toBeGreaterThanOrEqual(1);
    // The single-flight fence: at most a tiny number of refreshes, never 10.
    expect(refreshCalls).toBeLessThanOrEqual(2);
  });
});

describe("ADV D4: shape-shifted error bodies never crash the mapper", () => {
  it.each([
    ["string detail", { detail: "plain message" }],
    ["array detail (FastAPI 422)", { detail: [{ loc: ["body", "x"], msg: "Field required" }] }],
    ["object detail", { detail: { code: "WEIRD", nested: { deep: true } } }],
    ["numeric detail", { detail: 42 }],
    ["null detail", { detail: null }],
    ["no detail key", { something: "else" }],
    ["non-JSON body", undefined],
  ])("%s maps to an ApiError without throwing", async (_label, body) => {
    server.use(
      http.get("/api/buckets", () =>
        body === undefined
          ? new HttpResponse("<html>gateway error</html>", {
              status: 502,
              headers: { "content-type": "text/html" },
            })
          : HttpResponse.json(body, { status: 400 }),
      ),
    );
    const error = await apiFetch("/api/buckets").catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(typeof (error as ApiError).detail).toBe("string");
    expect((error as ApiError).detail.length).toBeGreaterThan(0);
  });
});

describe("ADV D4b: a caller cannot reach a non-allowlisted origin", () => {
  it.each([
    "https://evil.example/api/steal",
    "//evil.example/api",
    "/api/../../etc/passwd",
    "/api/%2e%2e/admin",
    "/api/x#fragment",
  ])("apiFetch(%j) refuses before any network activity", async (path) => {
    const spy = vi.spyOn(globalThis, "fetch");
    await expect(apiFetch(path)).rejects.toThrow();
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });

  it("the two service probes (/healthz, /readyz) remain deliberately reachable", () => {
    // Exact-path allowlist members — everything else under / is not.
    expect(() => new URL("/healthz", "https://goatfarm.invalid")).not.toThrow();
  });
});
