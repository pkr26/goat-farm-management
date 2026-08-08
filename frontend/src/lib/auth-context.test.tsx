/**
 * Tests for AuthProvider/useAuth: the silent-refresh bootstrap, farm
 * selection + localStorage persistence, X-Farm-Id propagation, protected
 * route redirect, signIn/signOut/selectFarm/refreshFarms, and the
 * auth-failure handler registered with the api client. Drives the real
 * provider through MSW with next/navigation mocked.
 */

import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch, setAccessToken, setCurrentFarmId } from "@/lib/api-client";
import { useAuth } from "@/lib/auth-context";
import { TEST_ACCESS_TOKEN, TEST_USER, server } from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";
import { QueryClientProvider } from "@tanstack/react-query";

const { pushMock, navState } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  navState: { pathname: "/dashboard" },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => navState.pathname,
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const FARM_STORAGE_KEY = "goatfarm.farmId";

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
      <span data-testid="farms">{auth.farms.map((f) => f.id).join(",")}</span>
      <button onClick={() => auth.selectFarm(2)}>select-2</button>
      <button
        onClick={() =>
          void auth.signIn("signin-token", {
            id: 9,
            email: "worker@goatfarm.test",
            name: "Worker",
          })
        }
      >
        sign-in
      </button>
      <button onClick={() => void auth.signOut()}>sign-out</button>
      <button onClick={() => void auth.refreshFarms()}>refresh-farms</button>
    </div>
  );
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

describe("AuthProvider bootstrap — valid session", () => {
  beforeEach(() => {
    pushMock.mockClear();
    navState.pathname = "/dashboard";
    setAccessToken(null);
    setCurrentFarmId(null);
  });

  it("finishes loading with the refreshed user and auto-selects the only farm", async () => {
    renderWithProviders(<Probe />);

    await expectLoaded();
    expect(screen.getByTestId("user")).toHaveTextContent(TEST_USER.email);
    expect(screen.getByTestId("farmId")).toHaveTextContent("1");
    expect(screen.getByTestId("farms")).toHaveTextContent("1");
  });

  it("persists the selected farm id to localStorage", async () => {
    renderWithProviders(<Probe />);

    await expectLoaded();
    expect(localStorage.getItem(FARM_STORAGE_KEY)).toBe("1");
  });

  it("never puts the access token in localStorage or sessionStorage", async () => {
    // The api-client's core XSS mitigation: the access token lives in memory
    // only. A future refactor that starts persisting it silently regresses
    // that invariant — this test locks it in.
    renderWithProviders(<Probe />);
    await expectLoaded();

    const tokenNeedles = [TEST_ACCESS_TOKEN, "Bearer", "access_token"];
    const allValues = [
      ...Object.keys(localStorage).flatMap((k) => [k, localStorage.getItem(k) ?? ""]),
      ...Object.keys(sessionStorage).flatMap((k) => [k, sessionStorage.getItem(k) ?? ""]),
    ];
    for (const v of allValues) {
      for (const needle of tokenNeedles) {
        expect(v).not.toContain(needle);
      }
    }
  });

  it("sends the selected farm as X-Farm-Id on subsequent API calls", async () => {
    let farmHeader: string | null = null;
    server.use(
      http.get("/api/animals", ({ request }) => {
        farmHeader = request.headers.get("X-Farm-Id");
        return HttpResponse.json([]);
      }),
    );

    renderWithProviders(<Probe />);
    await expectLoaded();

    await apiFetch("/api/animals");
    expect(farmHeader).toBe("1");
  });

  it("restores the stored farm when it still exists in the farm list", async () => {
    localStorage.setItem(FARM_STORAGE_KEY, "2");
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json([
          { id: 1, name: "Farm One", location: null, role: null },
          { id: 2, name: "Farm Two", location: null, role: null },
        ]),
      ),
    );

    renderWithProviders(<Probe />);

    await expectLoaded();
    expect(screen.getByTestId("farmId")).toHaveTextContent("2");
    expect(localStorage.getItem(FARM_STORAGE_KEY)).toBe("2");
  });

  it("falls back to the first farm when the stored id is no longer valid", async () => {
    localStorage.setItem(FARM_STORAGE_KEY, "99");
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json([
          { id: 1, name: "Farm One", location: null, role: null },
          { id: 2, name: "Farm Two", location: null, role: null },
        ]),
      ),
    );

    renderWithProviders(<Probe />);

    await expectLoaded();
    expect(screen.getByTestId("farmId")).toHaveTextContent("1");
    expect(localStorage.getItem(FARM_STORAGE_KEY)).toBe("1");
  });

  it("leaves farmId null when the user has no farms", async () => {
    server.use(http.get("/api/auth/farms", () => HttpResponse.json([])));

    renderWithProviders(<Probe />);

    await expectLoaded();
    expect(screen.getByTestId("user")).toHaveTextContent(TEST_USER.email);
    expect(screen.getByTestId("farmId")).toHaveTextContent("none");
    expect(localStorage.getItem(FARM_STORAGE_KEY)).toBeNull();
  });
});

describe("AuthProvider bootstrap — no session", () => {
  beforeEach(() => {
    pushMock.mockClear();
    navState.pathname = "/dashboard";
    setAccessToken(null);
    setCurrentFarmId(null);
    rejectRefresh();
  });

  it("stays signed out and redirects to /login on a protected path", async () => {
    renderWithProviders(<Probe />);

    await expectLoaded();
    expect(screen.getByTestId("user")).toHaveTextContent("none");
    expect(screen.getByTestId("farmId")).toHaveTextContent("none");
    expect(pushMock).toHaveBeenCalledWith("/login");
  });

  it("does not redirect away from the public /login path", async () => {
    navState.pathname = "/login";

    renderWithProviders(<Probe />);

    await expectLoaded();
    expect(screen.getByTestId("user")).toHaveTextContent("none");
    expect(pushMock).not.toHaveBeenCalled();
  });
});

describe("AuthProvider actions", () => {
  beforeEach(() => {
    pushMock.mockClear();
    navState.pathname = "/dashboard";
    setAccessToken(null);
    setCurrentFarmId(null);
  });

  it("selectFarm updates the active farm and persists it", async () => {
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json([
          { id: 1, name: "Farm One", location: null, role: null },
          { id: 2, name: "Farm Two", location: null, role: null },
        ]),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<Probe />);
    await expectLoaded();
    expect(screen.getByTestId("farmId")).toHaveTextContent("1");

    await user.click(screen.getByRole("button", { name: "select-2" }));

    expect(screen.getByTestId("farmId")).toHaveTextContent("2");
    expect(localStorage.getItem(FARM_STORAGE_KEY)).toBe("2");
  });

  it("signIn stores the token, sets the user and loads farms with the token", async () => {
    rejectRefresh(); // start signed out
    let farmsAuthorization: string | null = null;
    server.use(
      http.get("/api/auth/farms", ({ request }) => {
        farmsAuthorization = request.headers.get("Authorization");
        return HttpResponse.json([
          { id: 5, name: "Worker Farm", location: null, role: "worker" },
        ]);
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<Probe />);
    await expectLoaded();
    expect(screen.getByTestId("user")).toHaveTextContent("none");

    await user.click(screen.getByRole("button", { name: "sign-in" }));

    await waitFor(() =>
      expect(screen.getByTestId("user")).toHaveTextContent(
        "worker@goatfarm.test",
      ),
    );
    expect(screen.getByTestId("farmId")).toHaveTextContent("5");
    expect(farmsAuthorization).toBe("Bearer signin-token");
  });

  it("signOut posts to /api/auth/logout, clears state and navigates to /login", async () => {
    let logoutCalled = false;
    server.use(
      http.post("/api/auth/logout", () => {
        logoutCalled = true;
        return HttpResponse.json({ ok: true });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<Probe />);
    await expectLoaded();
    expect(screen.getByTestId("user")).toHaveTextContent(TEST_USER.email);

    await user.click(screen.getByRole("button", { name: "sign-out" }));

    await waitFor(() =>
      expect(screen.getByTestId("user")).toHaveTextContent("none"),
    );
    expect(logoutCalled).toBe(true);
    expect(screen.getByTestId("farmId")).toHaveTextContent("none");
    expect(localStorage.getItem(FARM_STORAGE_KEY)).toBeNull();
    expect(pushMock).toHaveBeenCalledWith("/login");
  });

  it("signOut still clears local state when the logout request fails", async () => {
    server.use(
      http.post("/api/auth/logout", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<Probe />);
    await expectLoaded();

    await user.click(screen.getByRole("button", { name: "sign-out" }));

    await waitFor(() =>
      expect(screen.getByTestId("user")).toHaveTextContent("none"),
    );
    expect(localStorage.getItem(FARM_STORAGE_KEY)).toBeNull();
    expect(pushMock).toHaveBeenCalledWith("/login");
  });

  it("refreshFarms re-fetches the list and applies membership changes", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Probe />);
    await expectLoaded();
    expect(screen.getByTestId("farmId")).toHaveTextContent("1");

    // Membership changed server-side: farm 1 is gone, farm 3 is the only one.
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json([
          { id: 3, name: "New Farm", location: null, role: null },
        ]),
      ),
    );

    await user.click(screen.getByRole("button", { name: "refresh-farms" }));

    await waitFor(() =>
      expect(screen.getByTestId("farmId")).toHaveTextContent("3"),
    );
    expect(screen.getByTestId("farms")).toHaveTextContent("3");
    expect(localStorage.getItem(FARM_STORAGE_KEY)).toBe("3");
  });

  it("clears the token and redirects when a later API call fails auth", async () => {
    let laterAuthorization: string | null = "unset";
    server.use(
      http.get("/api/animals", ({ request }) => {
        laterAuthorization = request.headers.get("Authorization");
        return HttpResponse.json({ detail: "Expired" }, { status: 401 });
      }),
    );

    renderWithProviders(<Probe />);
    await expectLoaded();
    expect(screen.getByTestId("user")).toHaveTextContent(TEST_USER.email);

    // The session token expires; the refresh cookie is gone too.
    rejectRefresh();
    await act(async () => {
      await apiFetch("/api/animals").catch(() => undefined);
    });

    // The 401 carried the bootstrap token; the failure cleared the session.
    expect(laterAuthorization).toBe(`Bearer ${TEST_ACCESS_TOKEN}`);
    await waitFor(() =>
      expect(screen.getByTestId("user")).toHaveTextContent("none"),
    );
    expect(pushMock).toHaveBeenCalledWith("/login");
  });

  it("forced logout runs the same cleanup as signOut, once per transition", async () => {
    server.use(
      http.get("/api/animals", () =>
        HttpResponse.json({ detail: "Expired" }, { status: 401 }),
      ),
    );

    // On a public path the no-user redirect effect stays out of the way, so
    // every /login push below comes from the auth-failure handler itself.
    const previousPath = navState.pathname;
    navState.pathname = "/login";
    try {
      const { queryClient } = renderWithProviders(<Probe />);
      await expectLoaded();
      expect(screen.getByTestId("farmId")).toHaveTextContent("1");
      queryClient.setQueryData(["/api/animals"], [{ id: 1, tag_number: "A-1" }]);

      // The refresh cookie is rejected (expired/revoked/reused token); two
      // concurrent 401s must share ONE forced logout, not two.
      rejectRefresh();
      pushMock.mockClear();
      await act(async () => {
        await Promise.all([
          apiFetch("/api/animals").catch(() => undefined),
          apiFetch("/api/animals").catch(() => undefined),
        ]);
      });

      // Same cleanup as signOut: user, farms, farm id, localStorage key and
      // the TanStack cache are all gone.
      await waitFor(() =>
        expect(screen.getByTestId("user")).toHaveTextContent("none"),
      );
      expect(screen.getByTestId("farms")).toHaveTextContent("");
      expect(screen.getByTestId("farmId")).toHaveTextContent("none");
      expect(localStorage.getItem(FARM_STORAGE_KEY)).toBeNull();
      expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
      expect(pushMock).toHaveBeenCalledTimes(1);
      expect(pushMock).toHaveBeenCalledWith("/login");
    } finally {
      navState.pathname = previousPath;
    }
  });
});

describe("useAuth", () => {
  it("throws when used outside an AuthProvider", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    function Bare() {
      useAuth();
      return null;
    }
    expect(() =>
      render(
        <QueryClientProvider client={createTestQueryClient()}>
          <Bare />
        </QueryClientProvider>,
      ),
    ).toThrow("useAuth must be used inside AuthProvider");
    consoleError.mockRestore();
  });
});
