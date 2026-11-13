import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { server } from "@/test/msw-server";
import { apiFetch, setAccessToken, setCurrentFarmId } from "./api-client";

const cases = (["headers", "body", "signal", "mode"] as const).flatMap((slot) =>
  (["replacement", "teardown", "stable"] as const).map((change) => ({ slot, change })));
it.each(cases)("checks ownership after public $slot preparation performs $change", async ({ slot, change }) => {
  setAccessToken("header-owner", 7); setCurrentFarmId("3");
  const wire: Array<{ bearer: string | null; farm: string | null; marker: string | null }> = [];
  server.use(http.get("/api/animals", ({ request }) => {
    wire.push({ bearer: request.headers.get("Authorization"), farm: request.headers.get("X-Farm-Id"), marker: request.headers.get("X-Public-Header") });
    return HttpResponse.json([]);
  }));
  const headers: HeadersInit = { "X-Public-Header": "public-header" };
  const callerSignal = new AbortController().signal;
  const values: Pick<RequestInit, "headers" | "body" | "signal" | "mode"> = {
    headers, body: undefined, signal: callerSignal, mode: "same-origin",
  };
  const init: RequestInit = { headers };
  Object.defineProperty(init, slot, { enumerable: true, get() {
    if (change === "replacement") {
      setAccessToken("header-replacement", 8); setCurrentFarmId("4");
    } else if (change === "teardown") {
      setAccessToken(null); setCurrentFarmId(null);
    }
    return values[slot];
  } });
  try {
    const outcome = await apiFetch("/api/animals", init).then(
      () => ({ kind: "resolved" as const }),
      (error: unknown) => ({ kind: "rejected" as const, error }),
    );
    if (change === "stable") expect(outcome.kind).toBe("resolved");
    else expect(outcome).toMatchObject({ kind: "rejected", error: { name: "AuthSessionChangedError" } });
    expect(wire).toEqual(change === "stable"
      ? [{ bearer: "Bearer header-owner", farm: "3", marker: "public-header" }]
      : []);
  } finally { setAccessToken(null); setCurrentFarmId(null); }
});
