import { http, HttpResponse } from "msw";
import { afterEach, expect, it } from "vitest";

import { server } from "../test/msw-server";
import { clearIdempotencyRequestState, clearPersistedIdempotencyRequestState, runIdempotencyProtectedRequest } from "./idempotent-request";

const url = "http://localhost/api/finance/new";

function requestOptions(intermediateMethod?: string): RequestInit {
  if (intermediateMethod === undefined) return { method: "POST", headers: { "Content-Type": "application/json" }, body: '{"amount":7}' };
  let method = "POST";
  return {
    get method() { return method; },
    get headers() { method = intermediateMethod; return { "Content-Type": "application/json" }; },
    get body() { method = "POST"; return '{"amount":7}'; },
  };
}

function transport() {
  const committed = new Map<string, { id: number }>();
  const observed: { method: string; body: string }[] = [];
  let writes = 0;
  let loseFirstReply = false;
  server.use(http.all(url, async ({ request }) => {
    const body = await request.text();
    observed.push({ method: request.method, body });
    if (request.method !== "POST" || body !== '{"amount":7}') return HttpResponse.json({ detail: "Invalid action envelope" }, { status: 400 });
    const key = request.headers.get("Idempotency-Key");
    if (!key) return HttpResponse.json({ detail: "Missing protection key" }, { status: 400 });
    let result = committed.get(key);
    if (!result) { result = { id: 41 + writes }; writes += 1; committed.set(key, result); }
    if (loseFirstReply) { loseFirstReply = false; return HttpResponse.json({ detail: "Ambiguous reply" }, { status: 503 }); }
    return HttpResponse.json(result);
  }));
  const execute = async (init: RequestInit) => {
    const response = await fetch(new Request(url, init));
    if (!response.ok) throw Object.assign(new Error("Server rejected the reply"), { status: response.status });
    return response.json() as Promise<{ id: number }>;
  };
  return { execute, observed, get writes() { return writes; }, loseReply() { loseFirstReply = true; } };
}

afterEach(() => { clearPersistedIdempotencyRequestState(); window.sessionStorage.clear(); });

it.each([{ kind: "stable data properties", first: undefined, second: undefined }, { kind: "public changing accessors", first: "", second: "GET" }])(
  "preserves one committed action for identical pending HTTP requests from $kind", async ({ first, second }) => {
    clearPersistedIdempotencyRequestState();
    const actual = transport();
    const submit = (init: RequestInit) => runIdempotencyProtectedRequest({ url: "/api/finance/new", init, farmScope: "17", sessionScope: 4, actorScope: null, execute: actual.execute });
    const pending = [submit(requestOptions(first)), submit(requestOptions(second))];
    const [left, right] = await Promise.all(pending);
    expect(actual.observed.every(request => request.method === "POST" && request.body === '{"amount":7}')).toBe(true);
    expect(right).toEqual(left);
    expect(actual.writes).toBe(1);
  },
);

it("preserves inherited and nonenumerable native request members", async () => {
  clearPersistedIdempotencyRequestState();
  const actual = transport();
  const init: RequestInit = {};
  Object.setPrototypeOf(init, { method: "POST", headers: { "Content-Type": "application/json" } });
  Object.defineProperty(init, "body", { value: '{"amount":7}', enumerable: false });
  await expect(runIdempotencyProtectedRequest({ url: "/api/finance/new", init, farmScope: "17", sessionScope: 4, actorScope: null, execute: actual.execute })).resolves.toEqual({ id: 41 });
  expect(actual.observed).toEqual([{ method: "POST", body: '{"amount":7}' }]);
  expect(actual.writes).toBe(1);
});

it("preserves inherited native cancellation before an action can commit", async () => {
  clearPersistedIdempotencyRequestState();
  const actual = transport();
  const controller = new AbortController();controller.abort();
  const init: RequestInit = {};
  Object.setPrototypeOf(init, { ...requestOptions(), signal: controller.signal });
  await expect(runIdempotencyProtectedRequest({ url: "/api/finance/new", init, farmScope: "17", sessionScope: 4, actorScope: null, execute: actual.execute })).rejects.toMatchObject({ name: "AbortError" });
  expect(actual.observed).toEqual([]);
  expect(actual.writes).toBe(0);
});

it("delegates an unprotected request unchanged to its public transport", async () => {
  const init: RequestInit = { method: "GET", get body(): BodyInit { throw new Error("An unprotected consumer owns this body"); } };
  const result = await runIdempotencyProtectedRequest({ url: "/api/finance/new", init, farmScope: "17", sessionScope: 4, actorScope: null, execute: async received => received });
  expect(result).toBe(init);
});

it.each([{ kind: "stable data properties", first: undefined, second: undefined }, { kind: "public changing accessors", first: "", second: "GET" }])(
  "recovers the committed action after an ambiguous reply and reload with $kind", async ({ first, second }) => {
    clearPersistedIdempotencyRequestState();
    const actual = transport();actual.loseReply();
    const submit = (init: RequestInit) => runIdempotencyProtectedRequest({ url: "/api/finance/new", init, farmScope: "17", sessionScope: 4, actorScope: "101", execute: actual.execute });
    await expect(submit(requestOptions(first))).rejects.toMatchObject({ status: 503 });
    // This exported realm-only operation is documented for same-tab reload
    // simulation. Native session storage and real protection keys survive.
    clearIdempotencyRequestState();
    const recovered = await submit(requestOptions(second));
    expect(actual.observed).toEqual([{ method: "POST", body: '{"amount":7}' }, { method: "POST", body: '{"amount":7}' }]);
    expect(recovered).toEqual({ id: 41 });
    expect(actual.writes).toBe(1);
  },
);
