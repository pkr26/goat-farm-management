/**
 * AuthProvider — fresh-domain mutation campaign kills (2026-09).
 *
 * Every test here exists to kill a specific surviving or timing-out mutant
 * from the auth campaign (reports/mutation/fresh-auth.json). They run early
 * in the file order so a mutant dies on a direct assertion instead of
 * grinding through the ~2700 tests that transitively import this module
 * (slow mutants were classified as Timeout by the 15 s mutant budget).
 */

import { act, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch } from "@/lib/api-client";
import { AuthProvider, useAuth, type AuthState } from "@/lib/auth-context";
import { server, TEST_ACCESS_TOKEN, TEST_FARMS, TEST_USER } from "@/test/msw-server";
import { createTestQueryClient } from "@/test/render";

const { pushMock, replaceMock, navState } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  replaceMock: vi.fn(),
  navState: { pathname: "/dashboard" },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => navState.pathname,
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const FARM_STORAGE_KEY = "goatfarm.farmId";

/** Exposes the live auth state plus action buttons for the kills below. */
function Probe({ onAuth }: { onAuth?: (auth: AuthState) => void }) {
  const auth = useAuth();
  onAuth?.(auth);
  return (
    <div>
      <span data-testid="loading">{String(auth.loading)}</span>
      <span data-testid="user">{auth.user ? auth.user.email : "none"}</span>
      <span data-testid="user-name">{auth.user?.name ?? "none"}</span>
      <span data-testid="farmId">{auth.farmId === null ? "none" : String(auth.farmId)}</span>
      <span data-testid="farms">{auth.farms.map((f) => f.id).join(",")}</span>
      <span data-testid="farms-ref-count">{auth.getFarms().length}</span>
      <button onClick={() => auth.selectFarm(2)}>select-2</button>
      <button
        onClick={() =>
          void auth
            .signIn(TEST_ACCESS_TOKEN, { id: 1, email: "a@goatfarm.test", name: "User A" })
            .catch(() => undefined)
        }
      >
        sign-in-a
      </button>
      <button
        onClick={() =>
          void auth
            .signIn(TEST_ACCESS_TOKEN, { id: 1, email: "b@goatfarm.test", name: "User B" })
            .catch(() => undefined)
        }
      >
        sign-in-b
      </button>
      <button onClick={() => void auth.signOut()}>sign-out</button>
      <button onClick={() => void auth.refreshFarms().catch(() => undefined)}>
        refresh-farms
      </button>
      <button
        onClick={() => auth.updateUser({ ...TEST_USER, name: "Renamed Owner" })}
      >
        update-user
      </button>
    </div>
  );
}

let queryClient: QueryClient;

function renderProvider() {
  queryClient = createTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <Probe />
      </AuthProvider>
    </QueryClientProvider>,
  );
}

/** A deferred response so a test can hold one request on the wire. */
function gate<T>() {
  let release!: (value: T) => void;
  const promise = new Promise<T>((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

/** N deferred farm-list responses, released in request order. Also exposes
 * the claim count so a test can wait for a specific call to be on the wire
 * before starting the concurrent flow it needs to interleave. */
function gatedFarms(count: number) {
  const gates = Array.from({ length: count }, () => gate<typeof TEST_FARMS>());
  let call = 0;
  server.use(
    http.get("/api/auth/farms", () => {
      const gate = gates[call];
      call += 1;
      return gate?.promise.then((f) => HttpResponse.json(f));
    }),
  );
  return { gates, claims: () => call };
}

async function expectIdle() {
  await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));
}

describe("AuthProvider — campaign kills", () => {
  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
    navState.pathname = "/dashboard";
    window.localStorage.clear();
  });

  it("reports no farms from the synchronous ref before the first snapshot lands", async () => {
    const { promise, release } = gate<typeof TEST_FARMS>();
    server.use(http.get("/api/auth/farms", () => promise.then((f) => HttpResponse.json(f))));
    renderProvider();

    // Pre-snapshot: the ref must start genuinely empty, not with placeholder junk.
    expect(screen.getByTestId("farms-ref-count")).toHaveTextContent("0");
    release(TEST_FARMS);
    await expectIdle();
    expect(screen.getByTestId("farms-ref-count")).toHaveTextContent("1");
  });

  it("persists the selected farm under the namespaced storage key", async () => {
    renderProvider();
    await expectIdle();

    expect(window.localStorage.getItem(FARM_STORAGE_KEY)).toBe("1");
    // A stray empty-string key means the constant was mutated.
    expect(window.localStorage.getItem("")).toBeNull();
  });

  it.each(["/login", "/register"])(
    "never redirects a signed-out visitor away from the public path %s",
    async (publicPath) => {
      navState.pathname = publicPath;
      server.use(
        http.post("/api/auth/refresh", () => new HttpResponse(null, { status: 401 })),
      );
      renderProvider();
      await expectIdle();

      expect(screen.getByTestId("user")).toHaveTextContent("none");
      // Give the redirect effect a chance to (wrongly) fire before asserting.
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 20));
      });
      expect(replaceMock).not.toHaveBeenCalled();
    },
  );

  it("restores the session from a valid refresh cookie", async () => {
    renderProvider();
    await expectIdle();

    expect(screen.getByTestId("user")).toHaveTextContent(TEST_USER.email);
    expect(screen.getByTestId("farmId")).toHaveTextContent("1");
  });

  it("selects the persisted farm even when it is not first in the list", async () => {
    window.localStorage.setItem(FARM_STORAGE_KEY, "2");
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json([
          ...TEST_FARMS,
          { id: 2, name: "Second Farm", location: "Solapur", role: null, timezone: "Asia/Kolkata" },
        ]),
      ),
    );
    renderProvider();
    await expectIdle();

    expect(screen.getByTestId("farmId")).toHaveTextContent("2");
  });

  it("stages the access token before the membership read that needs it", async () => {
    server.use(
      http.get("/api/auth/farms", ({ request }) =>
        request.headers.get("Authorization") === `Bearer ${TEST_ACCESS_TOKEN}`
          ? HttpResponse.json(TEST_FARMS)
          : new HttpResponse(null, { status: 401 }),
      ),
    );
    renderProvider();
    await expectIdle();

    // Without the staged bearer the farms read 401s and tears the session down.
    expect(screen.getByTestId("user")).toHaveTextContent(TEST_USER.email);
  });

  it("cancels in-flight queries when a farm is selected", async () => {
    renderProvider();
    await expectIdle();

    const cancelSpy = vi.spyOn(queryClient, "cancelQueries");
    await act(async () => {
      screen.getByRole("button", { name: "select-2" }).click();
    });
    expect(cancelSpy).toHaveBeenCalled();
  });

  it("cancels in-flight queries when the final membership is lost", async () => {
    server.use(http.get("/api/auth/farms", () => HttpResponse.json([])));
    renderProvider();

    const cancelSpy = vi.spyOn(queryClient, "cancelQueries");
    await expectIdle();
    // applyFarmList([]) takes the else branch: farmId cleared, cache aborted.
    expect(screen.getByTestId("farmId")).toHaveTextContent("none");
    expect(cancelSpy).toHaveBeenCalled();
  });

  it("ignores a farm selection delivered after unmount", async () => {
    let captured: AuthState | null = null;
    queryClient = createTestQueryClient();
    const { unmount } = render(
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <Probe onAuth={(auth) => (captured = auth)} />
        </AuthProvider>
      </QueryClientProvider>,
    );
    await expectIdle();
    expect(captured).not.toBeNull();
    unmount();

    await act(async () => {
      captured!.selectFarm(2);
    });
    // The guard must leave every persistence surface untouched.
    expect(window.localStorage.getItem(FARM_STORAGE_KEY)).toBe("1");
  });

  it("keeps a newer establishment's identity when an older one resolves late", async () => {
    renderProvider();
    await expectIdle();
    const { gates } = gatedFarms(2);

    await act(async () => {
      screen.getByRole("button", { name: "sign-in-a" }).click();
    });
    await act(async () => {
      screen.getByRole("button", { name: "sign-in-b" }).click();
    });
    await act(async () => {
      gates[1].release(TEST_FARMS);
    });
    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent("b@goatfarm.test"));
    await act(async () => {
      gates[0].release(TEST_FARMS);
    });
    // The superseded first establishment must not reclaim the session.
    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent("b@goatfarm.test"));
  });

  it("applies an establishment's own list while a newer refresh is merely pending", async () => {
    renderProvider();
    await expectIdle();
    const { gates } = gatedFarms(2);
    const listFive = [
      { id: 5, name: "Fifth Farm", location: "Solapur", role: null, timezone: "Asia/Kolkata" },
    ];
    const listSeven = [
      { id: 7, name: "Seventh Farm", location: "Solapur", role: null, timezone: "Asia/Kolkata" },
    ];

    await act(async () => {
      screen.getByRole("button", { name: "sign-in-a" }).click();
    });
    await act(async () => {
      screen.getByRole("button", { name: "refresh-farms" }).click();
    });
    // The establishment's farms resolve first: only a COMMITTED newer snapshot
    // may suppress it, so its own list must land even though a refresh started.
    await act(async () => {
      gates[0].release(listFive);
    });
    await waitFor(() => expect(screen.getByTestId("farms")).toHaveTextContent("5"));
    await act(async () => {
      gates[1].release(listSeven);
    });
    await waitFor(() => expect(screen.getByTestId("farms")).toHaveTextContent("7"));
  });

  it("clears the synchronous farm ref on sign-out", async () => {
    renderProvider();
    await expectIdle();
    expect(screen.getByTestId("farms-ref-count")).toHaveTextContent("1");

    await act(async () => {
      screen.getByRole("button", { name: "sign-out" }).click();
    });
    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent("none"));
    expect(screen.getByTestId("farms-ref-count")).toHaveTextContent("0");
  });

  it("replaces the in-memory user on updateUser", async () => {
    renderProvider();
    await expectIdle();

    await act(async () => {
      screen.getByRole("button", { name: "update-user" }).click();
    });
    expect(screen.getByTestId("user-name")).toHaveTextContent("Renamed Owner");
  });

  it("tears the session down and redirects when the refresh family dies", async () => {
    renderProvider();
    await expectIdle();

    server.use(
      http.post("/api/auth/refresh", () => new HttpResponse(null, { status: 401 })),
      http.get("/api/things", () => new HttpResponse(null, { status: 401 })),
    );
    await expect(apiFetch("/api/things")).rejects.toThrow();
    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent("none"));
    expect(replaceMock).toHaveBeenCalledWith("/login");
  });

  it("redirects a signed-out visitor on a private path once loading settles", async () => {
    server.use(
      http.post("/api/auth/refresh", () => new HttpResponse(null, { status: 401 })),
    );
    renderProvider();
    await expectIdle();

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
  });

  it("clears the query client the tree renders against NOW across a sign-in and a forced logout", async () => {
    // A rerender can swap the QueryClientProvider's client without remounting
    // AuthProvider; every session transition must then act on the NEW client.
    // This is the fast early kill for the signIn/applyFarmList/clearSession
    // dep-array mutants (the deep session-states suite kills them too, but
    // only after the mutant budget has ground through thousands of tests).
    const previous = createTestQueryClient();
    const current = createTestQueryClient();
    function Harness({ client }: { client: QueryClient }) {
      return (
        <QueryClientProvider client={client}>
          <AuthProvider>
            <Probe />
          </AuthProvider>
        </QueryClientProvider>
      );
    }
    const { rerender } = render(<Harness client={previous} />);
    await expectIdle();
    rerender(<Harness client={current} />);
    previous.setQueryData(["/api/animals"], [{ id: 1 }]);
    current.setQueryData(["/api/animals"], [{ id: 2 }]);

    await act(async () => {
      screen.getByRole("button", { name: "sign-in-a" }).click();
    });
    await waitFor(() =>
      expect(screen.getByTestId("user")).toHaveTextContent("a@goatfarm.test"),
    );
    expect(current.getQueryCache().getAll()).toHaveLength(0);
    expect(previous.getQueryData(["/api/animals"])).toEqual([{ id: 1 }]);

    // Re-seed, then force a logout: the auth-failure path must clear the
    // client the tree renders against now, not the one captured at mount.
    current.setQueryData(["/api/animals"], [{ id: 3 }]);
    server.use(
      http.post("/api/auth/refresh", () => new HttpResponse(null, { status: 401 })),
      http.get("/api/things", () => new HttpResponse(null, { status: 401 })),
    );
    await expect(apiFetch("/api/things")).rejects.toThrow();
    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent("none"));
    expect(current.getQueryCache().getAll()).toHaveLength(0);
  });

  it("releases the loading gate when a sign-in commits over a parked bootstrap", async () => {
    const { gates, claims } = gatedFarms(2);
    renderProvider();
    // The bootstrap parks on its farms read (gate 0): loading must stay true.
    expect(screen.getByTestId("loading")).toHaveTextContent("true");
    // Gate 0 must already belong to the bootstrap before the sign-in's own
    // read can claim gate 1 — request order is what pairs gates to callers.
    await waitFor(() => expect(claims()).toBe(1));

    await act(async () => {
      screen.getByRole("button", { name: "sign-in-a" }).click();
    });
    await waitFor(() => expect(claims()).toBe(2));
    gates[1].release(TEST_FARMS);
    // signIn's own setLoading write must clear the gate even though the
    // bootstrap's finally has not run yet.
    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));
    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent("a@goatfarm.test"));

    gates[0].release(TEST_FARMS);
    // The late bootstrap must not reclaim the newer establishment's session.
    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent("a@goatfarm.test"));
  });
});
