/**
 * Branch-level tests for AuthProvider: the persisted-farm-id validator, the
 * duck-typed session-changed classifier on the failure path, the mounted /
 * single-flight / forced-logout latches, and the once-only bootstrap under
 * Strict Mode. These are the edges the happy-path suites never reach —
 * corrupted local storage, a rejection value that is not an Error, actions
 * invoked by a tree that has already gone away, and a second auth failure
 * inside one signed-out transition.
 */

import { QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { StrictMode, useEffect, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch, setAccessToken, setCurrentFarmId } from "@/lib/api-client";
import {
  AuthProvider,
  useAuth,
  type SessionUser,
} from "@/lib/auth-context";
import { TEST_ACCESS_TOKEN, TEST_FARMS, TEST_USER, server } from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";

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

const WORKER: SessionUser = {
  id: 9,
  email: "worker@goatfarm.test",
  name: "Worker",
};

/** Reads the auth state without offering any buttons: every test here drives
 *  the actions directly through ActionCapture. */
function Probe() {
  const auth = useAuth();
  return (
    <div>
      <span data-testid="loading">{String(auth.loading)}</span>
      <span data-testid="user">{auth.user ? auth.user.email : "none"}</span>
      <span data-testid="farmId">
        {auth.farmId === null ? "none" : String(auth.farmId)}
      </span>
      <span data-testid="farms">{auth.farms.map((farm) => farm.id).join(",")}</span>
    </div>
  );
}

type AuthActions = Pick<
  ReturnType<typeof useAuth>,
  "selectFarm" | "signIn" | "signOut" | "refreshFarms"
>;

function ActionCapture({ capture }: { capture: (actions: AuthActions) => void }) {
  const { selectFarm, signIn, signOut, refreshFarms } = useAuth();
  useEffect(() => {
    capture({ selectFarm, signIn, signOut, refreshFarms });
  }, [capture, selectFarm, signIn, signOut, refreshFarms]);
  return null;
}

async function expectLoaded() {
  await waitFor(() =>
    expect(screen.getByTestId("loading")).toHaveTextContent("false"),
  );
}

function rejectRefresh() {
  server.use(
    http.post("/api/auth/refresh", () => new HttpResponse(null, { status: 401 })),
  );
}

/** Counts logout POSTs; the handler resolves like the real 204. */
function countLogouts(): () => number {
  let calls = 0;
  server.use(
    http.post("/api/auth/logout", () => {
      calls += 1;
      return new HttpResponse(null, { status: 204 });
    }),
  );
  return () => calls;
}

/** Captures the Authorization/X-Farm-Id a later request would carry, i.e. what
 *  the module-level api client still holds. */
function captureNextRequestScope(): () => { authorization: string | null; farm: string | null } {
  const seen = { authorization: null as string | null, farm: null as string | null };
  server.use(
    http.get("/api/animals", ({ request }) => {
      seen.authorization = request.headers.get("Authorization");
      seen.farm = request.headers.get("X-Farm-Id");
      return HttpResponse.json([]);
    }),
  );
  return () => seen;
}

/** Rejects GET /api/auth/farms at the transport layer with an arbitrary
 *  value. A fetch rejection is not guaranteed to be an Error: an aborted
 *  request rejects with whatever `reason` the aborter passed, and wrappers
 *  installed over `fetch` (extensions, instrumentation) reject with their own
 *  values. The classifier on the failure path must survive those. */
function rejectFarmsFetchWith(reason: unknown): () => void {
  const realFetch = globalThis.fetch;
  vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    if (url.includes("/api/auth/farms")) return Promise.reject(reason);
    return realFetch(input, init);
  });
  return () => vi.unstubAllGlobals();
}

function resetAuthModuleState() {
  pushMock.mockClear();
  replaceMock.mockClear();
  navState.pathname = "/dashboard";
  setAccessToken(null);
  setCurrentFarmId(null);
}

describe("AuthProvider — persisted farm id validation", () => {
  beforeEach(resetAuthModuleState);

  it("ignores a persisted farm id that is not a positive integer", async () => {
    // localStorage is operator-writable and outlives app versions, so the
    // stored value is untrusted input: only a positive safe integer can name
    // a farm. The second membership is a sentinel whose id matches the
    // rejected value — restoring it would prove the validator was skipped,
    // where the contract is to fall back to the first membership.
    localStorage.setItem(FARM_STORAGE_KEY, "0");
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json([
          { id: 5, name: "Farm Five", location: null, role: null },
          { id: 0, name: "Not A Farm", location: null, role: null },
        ]),
      ),
    );

    renderWithProviders(<Probe />);
    await expectLoaded();

    expect(screen.getByTestId("farmId")).toHaveTextContent("5");
    expect(localStorage.getItem(FARM_STORAGE_KEY)).toBe("5");
  });
});

describe("AuthProvider — establishment failures that are not Errors", () => {
  beforeEach(resetAuthModuleState);

  /** Signs in against a farms request that rejects with `reason`, and asserts
   *  the staged session was rolled back exactly as an ordinary failure is. */
  async function expectStagedSignInRolledBack(reason: unknown) {
    rejectRefresh(); // start signed out
    const logouts = countLogouts();
    let actions: AuthActions | undefined;
    renderWithProviders(
      <>
        <Probe />
        <ActionCapture
          capture={(captured) => {
            actions = captured;
          }}
        />
      </>,
    );
    await expectLoaded();

    const restoreFetch = rejectFarmsFetchWith(reason);
    try {
      await act(async () => {
        // The caller sees the original failure, not a secondary one raised
        // while classifying it.
        await expect(actions!.signIn("staged-token", WORKER)).rejects.toBe(reason);
      });
      // The session it minted is revoked server-side and locally.
      await waitFor(() => expect(logouts()).toBe(1));
    } finally {
      restoreFetch();
    }
    expect(screen.getByTestId("user")).toHaveTextContent("none");

    const scope = captureNextRequestScope();
    await apiFetch("/api/animals");
    expect(scope().authorization).toBeNull();
  }

  it("rolls back a staged sign-in when farms rejects with a plain string", async () => {
    await expectStagedSignInRolledBack("connection reset");
  });

  it("rolls back a staged sign-in when farms rejects with null", async () => {
    await expectStagedSignInRolledBack(null);
  });
});

describe("AuthProvider — actions from a tree that already unmounted", () => {
  beforeEach(resetAuthModuleState);

  it("ignores a farm selection made after the provider unmounted", async () => {
    let actions: AuthActions | undefined;
    const rendered = renderWithProviders(
      <>
        <Probe />
        <ActionCapture
          capture={(captured) => {
            actions = captured;
          }}
        />
      </>,
    );
    await expectLoaded();
    expect(screen.getByTestId("farmId")).toHaveTextContent("1");
    rendered.queryClient.setQueryData(["/api/animals"], [{ id: 1, tag_number: "A-1" }]);
    rendered.unmount();

    actions!.selectFarm(2);

    // A dead tree owns nothing: not the persisted selection, not the cache it
    // left behind, and above all not the shared client's farm scope — the
    // next tree's requests would otherwise be answered for farm 2.
    expect(localStorage.getItem(FARM_STORAGE_KEY)).toBe("1");
    expect(rendered.queryClient.getQueryData(["/api/animals"])).toEqual([
      { id: 1, tag_number: "A-1" },
    ]);
    const scope = captureNextRequestScope();
    await apiFetch("/api/animals");
    expect(scope().farm).toBe("1");
  });

  it("refuses a sign-in staged after the provider unmounted", async () => {
    let actions: AuthActions | undefined;
    const rendered = renderWithProviders(
      <>
        <Probe />
        <ActionCapture
          capture={(captured) => {
            actions = captured;
          }}
        />
      </>,
    );
    await expectLoaded();
    rendered.unmount();

    let farmsCalls = 0;
    server.use(
      http.get("/api/auth/farms", () => {
        farmsCalls += 1;
        return HttpResponse.json(TEST_FARMS);
      }),
    );

    await expect(actions!.signIn("stale-token", WORKER)).rejects.toMatchObject({
      name: "AbortError",
    });

    // Rejected before anything was staged: no membership read, and no token
    // of its own written into the module-level client that whatever tree
    // comes next will share.
    expect(farmsCalls).toBe(0);
    const scope = captureNextRequestScope();
    await apiFetch("/api/animals");
    expect(scope().authorization).toBe(`Bearer ${TEST_ACCESS_TOKEN}`);
  });
});

describe("AuthProvider — sign-out single flight release", () => {
  beforeEach(resetAuthModuleState);

  it("revokes again when a settled sign-out is followed by another", async () => {
    // Coalescing is scoped to one live flight. Once the revocation settled,
    // the next sign-out is a new transition and must reach the server again;
    // returning the finished task would silently skip both the POST and the
    // navigation.
    const logouts = countLogouts();
    let actions: AuthActions | undefined;
    renderWithProviders(
      <>
        <Probe />
        <ActionCapture
          capture={(captured) => {
            actions = captured;
          }}
        />
      </>,
    );
    await expectLoaded();

    await act(async () => {
      await actions!.signOut();
    });
    expect(logouts()).toBe(1);

    await act(async () => {
      await actions!.signOut();
    });

    expect(logouts()).toBe(2);
    expect(replaceMock).toHaveBeenCalledTimes(2);
  });

  it("keeps coalescing the live sign-out after an older one settles", async () => {
    // The release must only retire the flight it belongs to. If a previous
    // session's slow revocation clears the current registration, the sign-out
    // the operator is already waiting on stops coalescing and the tab runs a
    // second teardown + navigation for one click.
    let markFirstStarted!: () => void;
    let releaseFirst!: () => void;
    let releaseSecond!: () => void;
    const firstStarted = new Promise<void>((resolve) => {
      markFirstStarted = resolve;
    });
    const firstGate = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });
    const secondGate = new Promise<void>((resolve) => {
      releaseSecond = resolve;
    });
    let logoutCalls = 0;
    server.use(
      http.post("/api/auth/logout", async () => {
        logoutCalls += 1;
        if (logoutCalls === 1) {
          markFirstStarted();
          await firstGate;
        } else {
          await secondGate;
        }
        return new HttpResponse(null, { status: 204 });
      }),
    );
    let actions: AuthActions | undefined;
    renderWithProviders(
      <>
        <Probe />
        <ActionCapture
          capture={(captured) => {
            actions = captured;
          }}
        />
      </>,
    );
    await expectLoaded();

    let firstSignOut!: Promise<void>;
    await act(async () => {
      firstSignOut = actions!.signOut();
      await firstStarted;
    });
    await act(async () => {
      await actions!.signIn("signin-token", WORKER);
    });
    expect(screen.getByTestId("user")).toHaveTextContent(WORKER.email);

    // A distinct session: this sign-out owns a new epoch and its own flight,
    // queued behind the previous session's parked revocation.
    await act(async () => {
      void actions!.signOut();
    });
    expect(screen.getByTestId("user")).toHaveTextContent("none");

    await act(async () => {
      releaseFirst();
      await firstSignOut;
    });

    // The second flight is still on the wire, so this repeat coalesces into
    // it instead of tearing down and navigating a second time.
    await act(async () => {
      void actions!.signOut();
    });

    expect(replaceMock).toHaveBeenCalledTimes(2);

    await act(async () => {
      releaseSecond();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(logoutCalls).toBe(2);
  });
});

describe("AuthProvider — forced logout latch", () => {
  beforeEach(resetAuthModuleState);

  /** Makes the next protected call fail auth with no refresh cookie left. */
  function expireSession() {
    rejectRefresh();
    server.use(
      http.get("/api/animals", () =>
        HttpResponse.json({ detail: "Expired" }, { status: 401 }),
      ),
    );
  }

  it("redirects exactly once when a live session is force-logged-out", async () => {
    // On a protected path the signed-out redirect effect would fire too. The
    // forced-logout latch is what keeps one expiry to one navigation instead
    // of pushing /login twice.
    renderWithProviders(<Probe />);
    await expectLoaded();
    expect(screen.getByTestId("user")).toHaveTextContent(TEST_USER.email);
    replaceMock.mockClear();

    expireSession();
    await act(async () => {
      await apiFetch("/api/animals").catch(() => undefined);
    });

    await waitFor(() =>
      expect(screen.getByTestId("user")).toHaveTextContent("none"),
    );
    expect(replaceMock).toHaveBeenCalledTimes(1);
    expect(replaceMock).toHaveBeenCalledWith("/login");
  });

  it("ignores a second auth failure inside the same signed-out transition", async () => {
    // Every queued request of the dead session reports its own 401. The
    // cleanup and the navigation belong to the first one only.
    renderWithProviders(<Probe />);
    await expectLoaded();
    replaceMock.mockClear();

    expireSession();
    await act(async () => {
      await apiFetch("/api/animals").catch(() => undefined);
    });
    await waitFor(() =>
      expect(screen.getByTestId("user")).toHaveTextContent("none"),
    );

    await act(async () => {
      await apiFetch("/api/animals").catch(() => undefined);
    });

    expect(replaceMock).toHaveBeenCalledTimes(1);
  });

  it("re-arms the forced-logout path for a session established afterwards", async () => {
    // The latch survives as a ref across the whole provider lifetime, so a
    // sign-in that follows a logout must clear it. Otherwise the new
    // session's first expiry would be swallowed and the operator would keep
    // staring at a signed-in shell that can no longer talk to the server.
    rejectRefresh();
    let actions: AuthActions | undefined;
    renderWithProviders(
      <>
        <Probe />
        <ActionCapture
          capture={(captured) => {
            actions = captured;
          }}
        />
      </>,
    );
    await expectLoaded();
    expect(screen.getByTestId("user")).toHaveTextContent("none");

    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json([
          { id: 5, name: "Worker Farm", location: null, role: "worker" },
        ]),
      ),
    );
    await act(async () => {
      await actions!.signIn("worker-token", WORKER);
    });
    expect(screen.getByTestId("user")).toHaveTextContent(WORKER.email);
    replaceMock.mockClear();

    expireSession();
    await act(async () => {
      await apiFetch("/api/animals").catch(() => undefined);
    });

    await waitFor(() =>
      expect(screen.getByTestId("user")).toHaveTextContent("none"),
    );
    expect(replaceMock).toHaveBeenCalledWith("/login");
  });
});

describe("AuthProvider — a superseded establishment never runs the teardown", () => {
  beforeEach(resetAuthModuleState);

  it("leaves the teardown to the failed refresh that ended the session", async () => {
    // Two provider trees briefly overlap while a root is replaced; the newest
    // owns the auth-failure handler. When the staged sign-in's farms read
    // 401s and the refresh cookie is rejected, the api client itself ends the
    // session and that handler runs the cleanup. The losing establishment is
    // no longer the owner: revoking again, or clearing this tree's cache,
    // would destroy state that now belongs to somebody else.
    rejectRefresh();
    const logouts = countLogouts();
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json({ detail: "Expired" }, { status: 401 }),
      ),
    );

    let actions: AuthActions | undefined;
    const rendered = renderWithProviders(
      <>
        <Probe />
        <ActionCapture
          capture={(captured) => {
            actions = captured;
          }}
        />
      </>,
    );
    await expectLoaded();

    const replacementClient = createTestQueryClient();
    function Replacement({ children }: { children: ReactNode }) {
      return (
        <QueryClientProvider client={replacementClient}>
          <AuthProvider>{children}</AuthProvider>
        </QueryClientProvider>
      );
    }
    const replacement = render(<span data-testid="replacement" />, {
      wrapper: Replacement,
    });
    await waitFor(() =>
      expect(replacement.getByTestId("replacement")).toBeInTheDocument(),
    );

    rendered.queryClient.setQueryData(["/api/animals"], [{ id: 1, tag_number: "A-1" }]);
    await act(async () => {
      await expect(
        actions!.signIn("staged-token", WORKER),
      ).rejects.toMatchObject({ status: 401 });
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    expect(logouts()).toBe(0);
    expect(rendered.queryClient.getQueryData(["/api/animals"])).toEqual([
      { id: 1, tag_number: "A-1" },
    ]);
  });
});

describe("AuthProvider — Strict Mode bootstrap", () => {
  beforeEach(resetAuthModuleState);

  it("spends the one-time refresh token once when the mount effect replays", async () => {
    // Refresh tokens rotate: a second /api/auth/refresh would 401 and its
    // teardown would race the first call's farm selection. Strict Mode's
    // rehearsed mount must therefore not start a second bootstrap.
    let refreshCalls = 0;
    let farmsCalls = 0;
    server.use(
      http.post("/api/auth/refresh", () => {
        refreshCalls += 1;
        return HttpResponse.json({
          access_token: TEST_ACCESS_TOKEN,
          user: TEST_USER,
        });
      }),
      http.get("/api/auth/farms", () => {
        farmsCalls += 1;
        return HttpResponse.json(TEST_FARMS);
      }),
    );

    const queryClient = createTestQueryClient();
    render(
      <StrictMode>
        <QueryClientProvider client={queryClient}>
          <AuthProvider>
            <Probe />
          </AuthProvider>
        </QueryClientProvider>
      </StrictMode>,
    );
    await expectLoaded();

    expect(screen.getByTestId("user")).toHaveTextContent(TEST_USER.email);
    expect(screen.getByTestId("farmId")).toHaveTextContent("1");
    expect(refreshCalls).toBe(1);
    expect(farmsCalls).toBe(1);
  });
});
