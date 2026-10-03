import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { apiFetch, currentRequestScope, setAccessToken, setCurrentFarmId } from "@/lib/api-client";

beforeEach(() => {
  setAccessToken("old-session", 7); setCurrentFarmId("3");
});
afterEach(() => {
  setAccessToken(null); setCurrentFarmId(null); vi.unstubAllGlobals();
});

it("sends the owning bearer without a cookie lock even when local teardown follows immediately", async () => {
  let resolve!: (response: Response) => void;
  const gate = new Promise<Response>(done => { resolve = done; });
  const requestLock = vi.fn(() => { throw new Error("Exact-family revocation must not acquire the cookie lock"); });
  vi.stubGlobal("navigator", { locks: { request: requestLock }, onLine: true });
  const send = vi.fn<typeof fetch>(() => gate);
  vi.stubGlobal("fetch", send);
  const request = apiFetch("/api/auth/logout-session", { method: "POST" });
  // Match AuthProvider: local identity clears immediately, while the network
  // revokes only the bearer captured by this old session.
  setAccessToken(null); setCurrentFarmId(null);
  expect(send).toHaveBeenCalledTimes(1);
  expect(requestLock).not.toHaveBeenCalled();
  const [, init] = send.mock.calls[0]!;
  expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer old-session");
  expect(init?.credentials).toBe("omit");
  setAccessToken("replacement-session", 8); setCurrentFarmId("4");
  resolve(new Response("{}", { headers: { "Content-Type": "application/json" } }));
  await expect(request).rejects.toHaveProperty("name", "AuthSessionChangedError");
  expect(currentRequestScope()).toMatchObject({ actorScope: "8", farmScope: "4" });
  expect(send).toHaveBeenCalledTimes(1);
});

it.each(["/api/auth/logout-session", "/api/auth/logout-session/", "/api/auth/logout-session?reason=cancel"])("does not refresh or replace authority when an exact revocation is rejected: %s", async (path) => {
  const send = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ detail: "Expired session" }), {
    status: 401, headers: { "Content-Type": "application/json" },
  }));
  vi.stubGlobal("fetch", send);
  await expect(apiFetch(path, { method: "POST" })).rejects.toMatchObject({ status: 401 });
  expect(send).toHaveBeenCalledTimes(1);
  expect(send.mock.calls[0]?.[1]?.credentials).toBe("omit");
  expect(currentRequestScope()).toMatchObject({ actorScope: "7", farmScope: "3" });
});
