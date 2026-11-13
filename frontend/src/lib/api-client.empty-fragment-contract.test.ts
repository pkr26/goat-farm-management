import { http, HttpResponse } from "msw";
import { afterEach, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { apiFetch, currentRequestScope, setAccessToken, setCurrentFarmId } from "./api-client";

afterEach(() => { setAccessToken(null); setCurrentFarmId(null); });

it.each([
  ["/healthz#", "GET"],
  ["/api/auth/worker-login#", "POST"],
  ["/api/auth/farms#", "POST"],
])("rejects the empty fragment in %s before sending credentials or changing the session", async (path, method) => {
  setAccessToken("existing-worker-grant", 7); setCurrentFarmId("3");
  const requests: Array<{ url: string; farm: string | null; actor: string | null }> = [];
  server.use(http.all("*", ({ request }) => {
    requests.push({ url: request.url, farm: request.headers.get("X-Farm-Id"), actor: request.headers.get("Authorization") });
    return HttpResponse.json({ detail: "Invalid PIN." }, { status: 401 });
  }));
  const outcome = await apiFetch(path, { method }).then(
    () => ({ kind: "resolved" }),
    (error: Error) => ({ kind: "rejected", name: error.name }),
  );
  const scope = currentRequestScope();
  expect({ outcome, requests, actorScope: scope?.actorScope, farmScope: scope?.farmScope }).toEqual({
    outcome: { kind: "rejected", name: "UnsafeApiPathError" }, requests: [], actorScope: "7", farmScope: "3",
  });
});

it("preserves canonical worker PIN errors with queries and never attempts a refresh", async () => {
  setAccessToken("existing-worker-grant", 7); setCurrentFarmId("3");
  const refresh = vi.fn();
  server.use(
    http.post("/api/auth/worker-login", () => HttpResponse.json({ detail: "Invalid PIN." }, { status: 401 })),
    http.post("/api/auth/refresh", () => { refresh(); return HttpResponse.json({}, { status: 401 }); }),
  );
  await expect(apiFetch("/api/auth/worker-login?device=tablet", { method: "POST" })).rejects.toMatchObject({ status: 401, message: "Invalid PIN." });
  expect(refresh).not.toHaveBeenCalled();
  expect(currentRequestScope()).toMatchObject({ actorScope: "7", farmScope: "3" });
});

it("continues to allow the generated root service probe with an ordinary query", async () => {
  server.use(http.get("/healthz", () => HttpResponse.json({ status: "ok" })));
  await expect(apiFetch("/healthz?probe=tablet")).resolves.toEqual({ status: "ok" });
});
