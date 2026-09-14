/**
 * Companion coverage for AuthProvider (see auth-context.test.tsx for the
 * bootstrap/action basics): the second public path, the farm list a
 * signed-out tree renders, the rejection a post-unmount action produces, the
 * "a newer session already owns this failure" escape in establishSession, and
 * the hook-dependency contract — every auth action clears the QueryClient the
 * tree is rendering against NOW, not the one captured when the provider
 * mounted. Same MSW-driven provider and next/navigation mock as the sibling
 * files.
 */

import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { useEffect } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch, setAccessToken, setCurrentFarmId } from "@/lib/api-client";
import { AuthProvider, useAuth } from "@/lib/auth-context";
import { TEST_FARMS, server } from "@/test/msw-server";
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

/** api-client mints an AuthSessionChangedError whenever a newer authenticated
 *  session replaces the one a request was issued under. That rejection is a
 *  transport-independent signal — it can reach a caller before any epoch
 *  bookkeeping the caller does itself is conclusive — so the provider has to
 *  honour the error itself. Everything else stays the real module. */
const { supersededFarmsRead } = vi.hoisted(() => ({
  supersededFarmsRead: { error: null as Error | null },
}));

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return {
    ...actual,
    apiFetch: <T,>(path: string, init?: RequestInit): Promise<T> => {
      if (path === "/api/auth/farms" && supersededFarmsRead.error) {
        return Promise.reject(supersededFarmsRead.error);
      }
      return actual.apiFetch<T>(path, init);
    },
  };
});

type CapturedActions = {
  signIn: ReturnType<typeof useAuth>["signIn"];
  refreshFarms: () => Promise<void>;
  signOut: () => Promise<void>;
};

/** Exposes the auth state and wires the mutating actions to buttons. */
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
      <span data-testid="farmCount">{auth.farms.length}</span>
      <button onClick={() => auth.selectFarm(2)}>select-2</button>
      <button
        onClick={() =>
          void auth
            .signIn("signin-token", {
              id: 9,
              email: "worker@goatfarm.test",
              name: "Worker",
            })
            .catch(() => undefined)
        }
      >
        sign-in
      </button>
      <button onClick={() => void auth.signOut()}>sign-out</button>
      <button onClick={() => void auth.refreshFarms()}>refresh-farms</button>
    </div>
  );
}

function ActionCapture({
  capture,
}: {
  capture: (actions: CapturedActions) => void;
}) {
  const { signIn, refreshFarms, signOut } = useAuth();
  useEffect(() => {
    capture({ signIn, refreshFarms, signOut });
  }, [capture, signIn, refreshFarms, signOut]);
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

function resetSession() {
  pushMock.mockClear();
  replaceMock.mockClear();
  navState.pathname = "/dashboard";
  setAccessToken(null);
  setCurrentFarmId(null);
  supersededFarmsRead.error = null;
}

describe("AuthProvider redirect — public paths", () => {
  beforeEach(resetSession);

  it("does not redirect away from the public /register path", async () => {
    // Signing up is reachable without a session; bouncing the visitor to
    // /login the moment the silent refresh fails would make registration
    // unreachable for exactly the people who need it.
    navState.pathname = "/register";
    rejectRefresh();

    renderWithProviders(<Probe />);

    await expectLoaded();
    expect(screen.getByTestId("user")).toHaveTextContent("none");
    // Give the redirect effect a full flush before concluding it stayed put.
    await act(async () => {
      await Promise.resolve();
    });
    expect(replaceMock).not.toHaveBeenCalled();
  });
});

describe("AuthProvider — signed-out state", () => {
  beforeEach(resetSession);

  it("renders an empty farm list while no session has been established", async () => {
    // Consumers branch on farms.length (the farm switcher renders nothing at
    // 0, /farm-select gates on it): the pre-session value must be empty, not
    // merely free of usable ids.
    rejectRefresh();

    renderWithProviders(<Probe />);

    await expectLoaded();
    expect(screen.getByTestId("user")).toHaveTextContent("none");
    expect(screen.getByTestId("farmCount")).toHaveTextContent("0");
    expect(screen.getByTestId("farms")).toBeEmptyDOMElement();
    expect(screen.getByTestId("farmId")).toHaveTextContent("none");
  });
});

describe("AuthProvider — actions dispatched after unmount", () => {
  beforeEach(resetSession);

  it("rejects a post-unmount sign-in with an abort naming the dead provider", async () => {
    // A dialog that unmounts the tree while its submit handler is still
    // awaiting signIn must be able to tell "this provider is gone" from a
    // credential failure — it reports the latter to the operator.
    let actions: CapturedActions | undefined;
    const rendered = renderWithProviders(
      <>
        <Probe />
        <ActionCapture capture={(captured) => { actions = captured; }} />
      </>,
    );
    await expectLoaded();
    expect(actions).toBeDefined();
    rendered.unmount();

    let farmsRequests = 0;
    server.use(
      http.get("/api/auth/farms", () => {
        farmsRequests += 1;
        return HttpResponse.json(TEST_FARMS);
      }),
    );

    const staged = actions!.signIn("post-unmount-token", {
      id: 12,
      email: "unmounted@goatfarm.test",
      name: "Unmounted",
    });

    await expect(staged).rejects.toThrow(
      "The authentication provider was unmounted.",
    );
    await expect(staged).rejects.toMatchObject({ name: "AbortError" });
    // The staged token was never installed, so nothing went on the wire.
    expect(farmsRequests).toBe(0);
  });
});

describe("AuthProvider — establishSession must not tear down a superseded call", () => {
  beforeEach(resetSession);

  it("leaves the staged token alone when the farms read says a newer session took over", async () => {
    // The mirror image of "rolls back the staged token and user when farm
    // discovery fails": that failure belongs to this call, so it revokes.
    // AuthSessionChangedError means the state now belongs to somebody else —
    // tearing down (and revoking!) here would destroy the newer session.
    rejectRefresh();
    const logouts = countLogouts();
    let laterAuthorization: string | null = "unset";
    server.use(
      http.get("/api/animals", ({ request }) => {
        laterAuthorization = request.headers.get("Authorization");
        return HttpResponse.json([]);
      }),
    );

    let actions: CapturedActions | undefined;
    renderWithProviders(
      <>
        <Probe />
        <ActionCapture capture={(captured) => { actions = captured; }} />
      </>,
    );
    await expectLoaded();

    const superseded = new Error(
      "Your authenticated session changed while this request was in progress. The request was not replayed.",
    );
    superseded.name = "AuthSessionChangedError";
    supersededFarmsRead.error = superseded;

    await act(async () => {
      await expect(
        actions!.signIn("staged-token", {
          id: 9,
          email: "worker@goatfarm.test",
          name: "Worker",
        }),
      ).rejects.toMatchObject({ name: "AuthSessionChangedError" });
    });

    // No teardown ran: the token this call staged is still installed and the
    // server session behind it was never revoked.
    await apiFetch("/api/animals");
    expect(laterAuthorization).toBe("Bearer staged-token");
    expect(logouts()).toBe(0);
    expect(screen.getByTestId("user")).toHaveTextContent("none");
  });
});

/** Renders the provider under `first`, lets the bootstrap settle, then swaps
 *  the context to `second` WITHOUT remounting AuthProvider — the shape a tree
 *  that re-creates its QueryClientProvider (a per-account cache reset is a
 *  documented React Query pattern) presents to the provider below it. Both
 *  clients are seeded afterwards so a wrongly captured one is visible. */
async function renderThenSwapQueryClient(): Promise<{
  previous: QueryClient;
  current: QueryClient;
}> {
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
  await expectLoaded();
  expect(screen.getByTestId("farmId")).toHaveTextContent("1");

  rerender(<Harness client={current} />);
  previous.setQueryData(["/api/animals"], [{ id: 1, tag_number: "A-1" }]);
  current.setQueryData(["/api/animals"], [{ id: 2, tag_number: "A-2" }]);
  return { previous, current };
}

function expectOnlyCurrentClientCleared(clients: {
  previous: QueryClient;
  current: QueryClient;
}) {
  expect(clients.current.getQueryCache().getAll()).toHaveLength(0);
  expect(clients.previous.getQueryData(["/api/animals"])).toEqual([
    { id: 1, tag_number: "A-1" },
  ]);
}

describe("AuthProvider — auth actions act on the QueryClient in context", () => {
  beforeEach(resetSession);

  it("selectFarm clears the client the tree renders against now", async () => {
    const clients = await renderThenSwapQueryClient();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "select-2" }));

    expect(screen.getByTestId("farmId")).toHaveTextContent("2");
    expectOnlyCurrentClientCleared(clients);
  });

  it("signOut clears the client the tree renders against now", async () => {
    server.use(
      http.post("/api/auth/logout", () => new HttpResponse(null, { status: 204 })),
    );
    const clients = await renderThenSwapQueryClient();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "sign-out" }));

    await waitFor(() =>
      expect(screen.getByTestId("user")).toHaveTextContent("none"),
    );
    expectOnlyCurrentClientCleared(clients);
  });

  it("refreshFarms clears the client the tree renders against now", async () => {
    const clients = await renderThenSwapQueryClient();
    // The refreshed snapshot keeps farm 1 valid and adds farm 3: the
    // selection survives a membership refresh, and the cache clear runs on
    // the client the tree renders against now. (A snapshot that REVOKES the
    // selection clears identically — see the refreshFarms suite in
    // auth-context.test.tsx.)
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json([
          { id: 3, name: "New Farm", location: null, role: null },
          { id: 1, name: "Farm One", location: null, role: null },
        ]),
      ),
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "refresh-farms" }));

    await waitFor(() => expect(screen.getByTestId("farms")).toHaveTextContent("3,1"));
    expect(screen.getByTestId("farmId")).toHaveTextContent("1");
    expectOnlyCurrentClientCleared(clients);
  });

  it("signIn clears the client the tree renders against now", async () => {
    const clients = await renderThenSwapQueryClient();
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json([
          { id: 5, name: "Worker Farm", location: null, role: "worker" },
        ]),
      ),
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "sign-in" }));

    await waitFor(() =>
      expect(screen.getByTestId("user")).toHaveTextContent(
        "worker@goatfarm.test",
      ),
    );
    // The previous session's farm (1) is not one of the new user's
    // memberships; the selection is cleared rather than silently landing the
    // new user in list[0], and the clear still hits the current client.
    expect(screen.getByTestId("farmId")).toHaveTextContent("none");
    expectOnlyCurrentClientCleared(clients);
  });

  it("forced logout clears the client the tree renders against now", async () => {
    const clients = await renderThenSwapQueryClient();
    server.use(
      http.get("/api/animals", () =>
        HttpResponse.json({ detail: "Expired" }, { status: 401 }),
      ),
    );
    // The refresh cookie is rejected too: the api client's auth-failure
    // handler owns the cleanup from here.
    rejectRefresh();

    await act(async () => {
      await apiFetch("/api/animals").catch(() => undefined);
    });

    await waitFor(() =>
      expect(screen.getByTestId("user")).toHaveTextContent("none"),
    );
    expect(replaceMock).toHaveBeenCalledWith("/login");
    expectOnlyCurrentClientCleared(clients);
  });
});
