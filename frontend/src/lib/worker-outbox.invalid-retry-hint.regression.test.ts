import { IDBFactory } from "fake-indexeddb";
import { HttpResponse, http } from "msw";
import { expect, it, vi } from "vitest";

import { ApiError, apiFetch, currentRequestScope, setAccessToken, setCurrentFarmId } from "@/lib/api-client";
import { server } from "@/test/msw-server";

import { clearWorkerOutboxBackoff, drainWorkerOutbox, persistWorkerOperation, readWorkerOutbox, type WorkerOperation } from "./worker-outbox";

it.each([
  { hint: Number.NaN, delay: 30, mode: "adapter", name: "an unknown cached numeric hint" },
  { hint: Number.POSITIVE_INFINITY, delay: 30, mode: "adapter", name: "a nonfinite cached numeric hint" },
  { hint: 45, delay: 45, mode: "adapter", name: "a finite longer server hint" },
  { hint: 0, delay: 30, mode: "adapter", name: "a known zero hint" },
  { hint: Number.MAX_VALUE, delay: 3600, mode: "adapter", name: "a finite hint that would overflow the backoff epoch" },
  { hint: Number.MAX_VALUE, delay: 3600, mode: "HTTP header", name: "the same oversized hint through actual HTTP parsing" },
])("retains the duty and respects a finite retry cadence for $name", async ({ hint, delay, mode }) => {
  vi.stubGlobal("indexedDB", new IDBFactory());
  vi.useFakeTimers({ toFake: ["Date"] });
  const started = new Date("2026-10-08T00:00:00Z"); vi.setSystemTime(started);
  localStorage.clear(); clearWorkerOutboxBackoff();
  setAccessToken("retry-adapter-worker", 7); setCurrentFarmId("3");
  const own = { actorScope: "7", farmScope: "3" };
  const wire: Array<string | null> = [];
  server.use(http.post("/api/tasks/41/complete", ({ request }) => {
    wire.push(request.headers.get("Idempotency-Key"));
    return wire.length === 1
      ? HttpResponse.json({ detail: "Try again later" }, { status: 429, headers: mode === "HTTP header" ? { "Retry-After": String(hint) } : {} })
      : new HttpResponse(null, { status: 204 });
  }));
  // The public SDK transport adapter delegates actual HTTP and exposes a
  // typed numeric hint through the exported ApiError constructor. Nonfinite
  // hints model malformed in-memory adapter/cache data. Oversized finite
  // hints use the same bounded policy as actual HTTP parsing; its independent
  // wire control verifies that contract. Normal apiFetch
  // header parsing is already defensive and never emits these values.
  const send = async (operation: WorkerOperation, scope: ReturnType<typeof currentRequestScope>) => {
    if (scope === null) throw new DOMException("No worker session.", "AbortError");
    try {
      return await apiFetch(operation.path, {
        method: operation.method, body: operation.body ?? undefined,
        headers: { "Idempotency-Key": operation.idempotencyKey },
      }, scope);
    } catch (error) {
      if (mode === "adapter" && error instanceof ApiError && error.status === 429) {
        throw new ApiError(error.status, error.detail, error.validationIssues, error.code, hint);
      }
      throw error;
    }
  };
  try {
    const saved = await persistWorkerOperation("/api/tasks/41/complete", undefined, own);
    await expect(drainWorkerOutbox(own, currentRequestScope, send)).resolves.toEqual({ replayed: 0, rejected: 0, remaining: 1 });
    expect(wire).toEqual([saved.idempotencyKey]);
    vi.setSystemTime(started.getTime() + delay * 1000 - 1);
    await expect(drainWorkerOutbox(own, currentRequestScope, send)).resolves.toEqual({ replayed: 0, rejected: 0, remaining: 1 });
    expect(wire).toEqual([saved.idempotencyKey]);
    await expect(readWorkerOutbox(own)).resolves.toMatchObject([{ id: saved.id, state: "pending" }]);
    vi.setSystemTime(started.getTime() + delay * 1000);
    await expect(drainWorkerOutbox(own, currentRequestScope, send)).resolves.toEqual({ replayed: 1, rejected: 0, remaining: 0 });
    expect(wire).toEqual([saved.idempotencyKey, saved.idempotencyKey]);
    await expect(readWorkerOutbox(own)).resolves.toMatchObject([{ id: saved.id, state: "sent" }]);
  } finally {
    clearWorkerOutboxBackoff(); setAccessToken(null); setCurrentFarmId(null);
    localStorage.clear(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals();
  }
});
