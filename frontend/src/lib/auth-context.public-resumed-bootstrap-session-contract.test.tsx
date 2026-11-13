import { QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { IDBFactory } from "fake-indexeddb";
import { HttpResponse, http } from "msw";
import { Activity, useEffect } from "react";
import { flushSync } from "react-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { AuthProvider, useAuth, type AuthState } from "@/lib/auth-context";
import { setAccessToken, setCurrentFarmId } from "@/lib/api-client";
import { TEST_FARMS, TEST_USER, server } from "@/test/msw-server";
import { createTestQueryClient } from "@/test/render";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/login",
}));

function signal() {
  let resolve!: () => void;
  const promise = new Promise<void>(done => { resolve = done; });
  return { promise, resolve };
}
beforeEach(() => {
  localStorage.clear(); sessionStorage.clear();
  setAccessToken(null); setCurrentFarmId(null);
  vi.stubGlobal("indexedDB", new IDBFactory());
});
afterEach(() => {
  vi.unstubAllGlobals(); setAccessToken(null); setCurrentFarmId(null);
  localStorage.clear(); sessionStorage.clear();
});

it(
  "keeps a completed bootstrap gate ready when Activity resumes into explicit membership discovery",
  async () => {
    const delay = 5;
    const arrived = signal(), release = signal(), farmsRelease = signal(), resumed = signal();
    const farmAuthorizations: Array<string | null> = [];
    const transportErrors: unknown[] = [];
    const signInErrors: unknown[] = [];
    server.use(
      http.post("/api/auth/refresh", async () => {
        arrived.resolve(); await release.promise;
        return HttpResponse.json({ access_token: "earlier-bootstrap-grant", user: TEST_USER });
      }),
      http.get("/api/auth/farms", async ({ request }) => {
        farmAuthorizations.push(request.headers.get("Authorization"));
        await farmsRelease.promise; return HttpResponse.json(TEST_FARMS);
      }),
    );
    let auth: AuthState | undefined;
    function Consumer() {
      const current = useAuth();
      useEffect(() => { auth = current; }, [current]);
      return <>
        <output aria-label="Session gate">{current.loading ? "Checking" : "Ready"}</output>
        <output aria-label="Identity">{current.user?.name ?? "Signed out"}</output>
      </>;
    }
    const client = createTestQueryClient();
    const tree = (mode: "visible" | "hidden") => <QueryClientProvider client={client}>
      <Activity mode={mode}><AuthProvider><Consumer /></AuthProvider></Activity>
    </QueryClientProvider>;
    let view: ReturnType<typeof render> | undefined;
    let publication: Promise<void> | undefined;
    const resume = () => {
      flushSync(() => { view!.rerender(tree("visible")); });
      publication = auth!.signIn("new-public-grant", { ...TEST_USER, name: "New operator" }).catch(error => { signInErrors.push(error); });
      resumed.resolve();
    };
    const nativeFetch = globalThis.fetch;
    const observer = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const response = await nativeFetch(input, init);
      const request = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (new URL(request, location.href).pathname === "/api/auth/refresh") {
        const actualJSON = response.json.bind(response);
        response.json = () => {
          const body = actualJSON();
          void body.then(() => {
            const enqueue = (remaining: number) => {
              if (remaining > 0) queueMicrotask(() => { enqueue(remaining - 1); });
              else resume();
            };
            // Public UI work follows actual native body decoding without replacing its promise.
            enqueue(delay);
          }, error => { transportErrors.push(error); resumed.resolve(); });
          return body;
        };
      }
      return response;
    });
    try {
      view = render(tree("visible"));
      await act(async () => { await arrived.promise; });
      view.rerender(tree("hidden"));
      await act(async () => { release.resolve(); await resumed.promise; });
      expect(screen.getByLabelText("Session gate")).toHaveTextContent("Ready");
      expect(transportErrors).toEqual([]);
      await act(async () => { farmsRelease.resolve(); await publication; });
      expect(signInErrors).toEqual([]);
      expect(screen.getByLabelText("Identity")).toHaveTextContent("New operator");
      expect(screen.getByLabelText("Session gate")).toHaveTextContent("Ready");
      expect(farmAuthorizations).toEqual(["Bearer new-public-grant"]);
    } finally {
      release.resolve(); farmsRelease.resolve();
      await act(async () => { await publication?.catch(error => { transportErrors.push(error); }); });
      view?.unmount(); client.clear(); observer.mockRestore();
    }
  },
);

it(
  "keeps an exhausted bootstrap gate ready when Activity resumes into explicit membership discovery",
  async () => {
    const delay = 6;
    const arrived = signal(), release = signal(), farmsRelease = signal(), resumed = signal();
    let refreshRequests = 0;
    const farmAuthorizations: Array<string | null> = [];
    const transportErrors: unknown[] = [];
    const signInErrors: unknown[] = [];
    server.use(
      http.post("/api/auth/refresh", async () => {
        refreshRequests += 1;
        if (refreshRequests === 3) { arrived.resolve(); await release.promise; }
        return new HttpResponse(null, { status: 503 });
      }),
      http.get("/api/auth/farms", async ({ request }) => {
        farmAuthorizations.push(request.headers.get("Authorization"));
        await farmsRelease.promise; return HttpResponse.json(TEST_FARMS);
      }),
    );
    let auth: AuthState | undefined;
    function Consumer() {
      const current = useAuth();
      useEffect(() => { auth = current; }, [current]);
      return <>
        <output aria-label="Session gate">{current.loading ? "Checking" : "Ready"}</output>
        <output aria-label="Identity">{current.user?.name ?? "Signed out"}</output>
      </>;
    }
    const client = createTestQueryClient();
    const tree = (mode: "visible" | "hidden") => <QueryClientProvider client={client}>
      <Activity mode={mode}><AuthProvider><Consumer /></AuthProvider></Activity>
    </QueryClientProvider>;
    let view: ReturnType<typeof render> | undefined;
    let publication: Promise<void> | undefined;
    const resume = () => {
      flushSync(() => { view!.rerender(tree("visible")); });
      publication = auth!.signIn("new-public-grant", { ...TEST_USER, name: "New operator" }).catch(error => { signInErrors.push(error); });
      resumed.resolve();
    };
    const nativeFetch = globalThis.fetch;
    let observedRefreshResponses = 0;
    const observer = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const response = await nativeFetch(input, init);
      const request = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (new URL(request, location.href).pathname === "/api/auth/refresh") {
        observedRefreshResponses += 1;
        if (observedRefreshResponses === 3) {
          const enqueue = (remaining: number) => {
            if (remaining > 0) queueMicrotask(() => { enqueue(remaining - 1); });
            else resume();
          };
          enqueue(delay);
        }
      }
      return response;
    });
    try {
      view = render(tree("visible"));
      await act(async () => { await arrived.promise; });
      view.rerender(tree("hidden"));
      await act(async () => { release.resolve(); await resumed.promise; });
      expect(screen.getByLabelText("Session gate")).toHaveTextContent("Ready");
      expect(transportErrors).toEqual([]);
      expect(refreshRequests).toBe(3);
      await act(async () => { farmsRelease.resolve(); await publication; });
      expect(signInErrors).toEqual([]);
      expect(screen.getByLabelText("Identity")).toHaveTextContent("New operator");
      expect(screen.getByLabelText("Session gate")).toHaveTextContent("Ready");
      expect(farmAuthorizations).toEqual(["Bearer new-public-grant"]);
    } finally {
      release.resolve(); farmsRelease.resolve();
      await act(async () => { await publication?.catch(error => { transportErrors.push(error); }); });
      view?.unmount(); client.clear(); observer.mockRestore();
    }
  },
);
