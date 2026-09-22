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
import { StrictMode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch, setAccessToken, setCurrentFarmId } from "@/lib/api-client";
import { AuthProvider, useAuth } from "@/lib/auth-context";
import { TEST_ACCESS_TOKEN, TEST_USER, server } from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";
import { QueryClientProvider } from "@tanstack/react-query";
import { settle } from "@/test/settle";

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

describe("AuthProvider bootstrap — valid session", () => {
  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
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

  it("clears the stored selection instead of entering an unchosen farm when the persisted id is revoked", async () => {
    // RT-O-3: the persisted farm (99) is no longer in the membership list.
    // Silently selecting list[0] would land the operator in a farm they never
    // chose; farmId must stay null so the app shell redirects to
    // /farm-select, with the membership list still rendered for the pick.
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
    expect(screen.getByTestId("farmId")).toHaveTextContent("none");
    // The selection is tombstoned (not removed): a removal is the cross-tab
    // signal for session teardown, and a revocation must not read as one.
    expect(localStorage.getItem(FARM_STORAGE_KEY)).toBe("revoked:99");
    expect(screen.getByTestId("farms")).toHaveTextContent("1,2");
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
    replaceMock.mockClear();
    navState.pathname = "/dashboard";
    setAccessToken(null);
    setCurrentFarmId(null);
    rejectRefresh();
  });

  it("stays signed out and redirects to /login on a protected path", async () => {
    const rendered = renderWithProviders(<Probe />);

    await expectLoaded();
    expect(screen.getByTestId("user")).toHaveTextContent("none");
    expect(screen.getByTestId("farmId")).toHaveTextContent("none");
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
    // A parent rerender can change useRouter's wrapper identity without
    // changing the auth decision. Do not dispatch the same transition again.
    rendered.rerender(<Probe />);
    expect(replaceMock).toHaveBeenCalledTimes(1);
  });

  it("re-dispatches login when a different protected path supersedes the first redirect", async () => {
    const rendered = renderWithProviders(<Probe />);
    await expectLoaded();
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
    expect(replaceMock).toHaveBeenCalledTimes(1);

    navState.pathname = "/animals";
    rendered.rerender(<Probe />);

    await waitFor(() => expect(replaceMock).toHaveBeenCalledTimes(2));
    expect(replaceMock).toHaveBeenLastCalledWith("/login");
  });

  it("does not redirect away from the public /login path", async () => {
    navState.pathname = "/login";

    renderWithProviders(<Probe />);

    await expectLoaded();
    expect(screen.getByTestId("user")).toHaveTextContent("none");
    expect(replaceMock).not.toHaveBeenCalled();
  });
});

describe("AuthProvider actions", () => {
  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
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

  it("rolls back the staged token and user when farm discovery fails", async () => {
    rejectRefresh();
    const logouts = countLogouts();
    let farmCalls = 0;
    let laterAuthorization: string | null = "unset";
    server.use(
      http.get("/api/auth/farms", () => {
        farmCalls += 1;
        return HttpResponse.json({ detail: "farms unavailable" }, { status: 503 });
      }),
      http.get("/api/animals", ({ request }) => {
        laterAuthorization = request.headers.get("Authorization");
        return HttpResponse.json([]);
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<Probe />);
    await expectLoaded();
    await user.click(screen.getByRole("button", { name: "sign-in" }));
    await waitFor(() => expect(farmCalls).toBe(1));
    await waitFor(() => expect(logouts()).toBe(1));

    expect(screen.getByTestId("user")).toHaveTextContent("none");
    expect(screen.getByTestId("farmId")).toHaveTextContent("none");
    await apiFetch("/api/animals");
    expect(laterAuthorization).toBeNull();
  });

  it("signIn revokes the session it just minted when farm discovery fails", async () => {
    // The user is watching an error message here, so the half-established
    // login must not survive as a reloadable server session.
    rejectRefresh();
    const logouts = countLogouts();
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json({ detail: "farms unavailable" }, { status: 503 }),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<Probe />);
    await expectLoaded();
    await user.click(screen.getByRole("button", { name: "sign-in" }));

    await waitFor(() => expect(logouts()).toBe(1));
    expect(screen.getByTestId("user")).toHaveTextContent("none");
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
    // The teardown no longer waits on the network, so the revocation may still
    // be in flight when local state is already gone.
    await waitFor(() => expect(logoutCalled).toBe(true));
    expect(screen.getByTestId("farmId")).toHaveTextContent("none");
    expect(localStorage.getItem(FARM_STORAGE_KEY)).toBeNull();
    expect(replaceMock).toHaveBeenCalledWith("/login");
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
    expect(replaceMock).toHaveBeenCalledWith("/login");
  });

  it("signOut clears the session even when the logout request never settles", async () => {
    // A black-holed connection (server accepts the TCP connection, never
    // answers) used to leave a shared terminal fully signed in: the token,
    // farm and whole query cache stayed live behind the await.
    let releaseLogout!: () => void;
    const parkedLogout = new Promise<void>((resolve) => {
      releaseLogout = resolve;
    });
    server.use(
      http.post("/api/auth/logout", async () => {
        await parkedLogout;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    const user = userEvent.setup();
    const { queryClient } = renderWithProviders(<Probe />);
    await expectLoaded();
    queryClient.setQueryData(["/api/animals"], [{ id: 1, tag_number: "A-1" }]);

    await user.click(screen.getByRole("button", { name: "sign-out" }));

    await waitFor(() =>
      expect(screen.getByTestId("user")).toHaveTextContent("none"),
    );
    expect(screen.getByTestId("farmId")).toHaveTextContent("none");
    expect(localStorage.getItem(FARM_STORAGE_KEY)).toBeNull();
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
    expect(replaceMock).toHaveBeenCalledWith("/login");
    // Release test infrastructure after proving teardown did not wait. In
    // production the request timeout releases the shared cookie lock; leaving
    // this synthetic never-response parked would poison later tests instead.
    await act(async () => {
      releaseLogout();
      await settle(0);
    });
  });

  it("refreshFarms re-fetches the list while the selected farm stays valid", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Probe />);
    await expectLoaded();
    expect(screen.getByTestId("farmId")).toHaveTextContent("1");

    // Membership changed server-side: farm 3 joined, farm 1 is still valid.
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json([
          { id: 3, name: "New Farm", location: null, role: null },
          { id: 1, name: "Farm One", location: null, role: null },
        ]),
      ),
    );

    await user.click(screen.getByRole("button", { name: "refresh-farms" }));

    await waitFor(() => expect(screen.getByTestId("farms")).toHaveTextContent("3,1"));
    expect(screen.getByTestId("farmId")).toHaveTextContent("1");
    expect(localStorage.getItem(FARM_STORAGE_KEY)).toBe("1");
  });

  it("refreshFarms clears the revoked selection instead of silently switching farms", async () => {
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
      expect(screen.getByTestId("farmId")).toHaveTextContent("none"),
    );
    // The list itself survives so /farm-select offers the explicit choice.
    expect(screen.getByTestId("farms")).toHaveTextContent("3");
    expect(localStorage.getItem(FARM_STORAGE_KEY)).toBe("revoked:1");
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
    expect(replaceMock).toHaveBeenCalledWith("/login");
  });

  it("clears the displayed actor instead of replaying a mutation as another account", async () => {
    let mutationCalls = 0;
    server.use(
      http.post("/api/tasks", () => {
        mutationCalls += 1;
        return HttpResponse.json({ detail: "Expired" }, { status: 401 });
      }),
    );

    renderWithProviders(<Probe />);
    await expectLoaded();
    expect(screen.getByTestId("user")).toHaveTextContent(TEST_USER.email);

    server.use(
      http.post("/api/auth/refresh", () =>
        HttpResponse.json({
          access_token: "actor-two-token",
          user: { id: 2, email: "actor-two@goatfarm.test", name: "Actor Two" },
        }),
      ),
    );
    pushMock.mockClear();
    replaceMock.mockClear();
    await act(async () => {
      await apiFetch("/api/tasks", {
        method: "POST",
        body: JSON.stringify({ title: "Must stay actor one" }),
      }).catch(() => undefined);
    });

    expect(mutationCalls).toBe(1);
    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent("none"));
    expect(replaceMock).toHaveBeenCalledWith("/login");
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
    replaceMock.mockClear();
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
      expect(replaceMock).toHaveBeenCalledTimes(1);
      expect(replaceMock).toHaveBeenCalledWith("/login");
    } finally {
      navState.pathname = previousPath;
    }
  });
});

describe("AuthProvider cross-tab storage signals", () => {
  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
    navState.pathname = "/dashboard";
    setAccessToken(null);
    setCurrentFarmId(null);
  });

  /** Simulates the storage event the browser fires in THIS tab when another
   *  tab writes/removes the shared key. */
  function dispatchStorage(key: string, newValue: string | null) {
    window.dispatchEvent(
      new StorageEvent("storage", {
        key,
        newValue,
        oldValue: null,
        storageArea: window.localStorage,
      }),
    );
  }

  function useTwoFarmList() {
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json([
          { id: 1, name: "Farm One", location: null, role: null },
          { id: 2, name: "Farm Two", location: null, role: null },
        ]),
      ),
    );
  }

  it("a farm-revoked tombstone from another tab drops only the selection, never the session", async () => {
    // B1 (2026-09-21 audit): revoking a membership used to read as a session
    // teardown in every other tab. The tombstone value distinguishes the two
    // intents: this tab keeps its valid session and lands on /farm-select.
    useTwoFarmList();
    localStorage.setItem(FARM_STORAGE_KEY, "1");
    const { queryClient } = renderWithProviders(<Probe />);
    await expectLoaded();
    expect(screen.getByTestId("farmId")).toHaveTextContent("1");
    queryClient.setQueryData(["/api/animals"], [{ id: 1, tag_number: "A-1" }]);

    await act(async () => {
      dispatchStorage(FARM_STORAGE_KEY, "revoked:1");
    });

    await waitFor(() =>
      expect(screen.getByTestId("farmId")).toHaveTextContent("none"),
    );
    expect(screen.getByTestId("user")).toHaveTextContent(TEST_USER.email);
    expect(replaceMock).not.toHaveBeenCalledWith("/login");
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
    // The stale membership entry no longer offers the revoked farm.
    await waitFor(() =>
      expect(screen.getByTestId("farms")).toHaveTextContent("2"),
    );
  });

  it("a farm-revoked tombstone for a farm this tab is not on changes nothing", async () => {
    useTwoFarmList();
    renderWithProviders(<Probe />);
    await expectLoaded();
    expect(screen.getByTestId("farmId")).toHaveTextContent("1");

    await act(async () => {
      dispatchStorage(FARM_STORAGE_KEY, "revoked:2");
    });

    expect(screen.getByTestId("farmId")).toHaveTextContent("1");
    expect(screen.getByTestId("farms")).toHaveTextContent("1,2");
    expect(screen.getByTestId("user")).toHaveTextContent(TEST_USER.email);
  });

  it("a key removal (sign-out in another tab) still tears this session down", async () => {
    renderWithProviders(<Probe />);
    await expectLoaded();
    expect(screen.getByTestId("user")).toHaveTextContent(TEST_USER.email);

    await act(async () => {
      dispatchStorage(FARM_STORAGE_KEY, null);
    });

    await waitFor(() =>
      expect(screen.getByTestId("user")).toHaveTextContent("none"),
    );
    expect(localStorage.getItem(FARM_STORAGE_KEY)).toBeNull();
    expect(replaceMock).toHaveBeenCalledWith("/login");
  });

  it("a farm switch in another tab updates this tab's selection", async () => {
    useTwoFarmList();
    renderWithProviders(<Probe />);
    await expectLoaded();
    expect(screen.getByTestId("farmId")).toHaveTextContent("1");

    await act(async () => {
      dispatchStorage(FARM_STORAGE_KEY, "2");
    });

    await waitFor(() =>
      expect(screen.getByTestId("farmId")).toHaveTextContent("2"),
    );
    expect(localStorage.getItem(FARM_STORAGE_KEY)).toBe("2");
  });

  it("a tombstoned persisted selection boots as no selection at all", async () => {
    // readStoredFarmId must treat "revoked:1" exactly like a missing key: the
    // bootstrap neither selects the dead farm nor sees a session teardown.
    localStorage.setItem(FARM_STORAGE_KEY, "revoked:1");
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json([{ id: 2, name: "Farm Two", location: null, role: null }]),
      ),
    );

    renderWithProviders(<Probe />);

    await expectLoaded();
    expect(screen.getByTestId("farmId")).toHaveTextContent("2");
    expect(screen.getByTestId("user")).toHaveTextContent(TEST_USER.email);
  });
});

describe("AuthProvider — silent bootstrap must not destroy a valid session", () => {
  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
    navState.pathname = "/dashboard";
    setAccessToken(null);
    setCurrentFarmId(null);
  });

  it("leaves the just-rotated refresh session alone when farms fails on load", async () => {
    // /api/auth/refresh succeeded and rotated the cookie; a transient 500 on
    // the following read used to POST /api/auth/logout, which revokes the
    // whole refresh family server-side — forcing a password re-entry where a
    // plain reload would have recovered.
    const logouts = countLogouts();
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json({ detail: "temporarily unavailable" }, { status: 500 }),
      ),
    );

    renderWithProviders(<Probe />);

    await expectLoaded();
    expect(screen.getByTestId("user")).toHaveTextContent("none");
    expect(logouts()).toBe(0);
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
  });

  it("establishes the session with site storage blocked, without revoking it", async () => {
    // Chrome/Safari "block all site data" makes even reading
    // window.localStorage throw SecurityError. That used to escape into
    // establishSession's catch, revoke the server session, and then throw
    // again from clearSession, leaving the provider half torn down.
    const blockedStorage = vi
      .spyOn(window, "localStorage", "get")
      .mockImplementation(() => {
        throw new DOMException("The operation is insecure.", "SecurityError");
      });
    const logouts = countLogouts();

    try {
      const user = userEvent.setup();
      renderWithProviders(<Probe />);

      await expectLoaded();
      expect(screen.getByTestId("user")).toHaveTextContent(TEST_USER.email);
      expect(screen.getByTestId("farmId")).toHaveTextContent("1");
      expect(logouts()).toBe(0);

      // Teardown must complete too, even though it cannot clear the store.
      await user.click(screen.getByRole("button", { name: "sign-out" }));
      await waitFor(() =>
        expect(screen.getByTestId("user")).toHaveTextContent("none"),
      );
      expect(screen.getByTestId("farmId")).toHaveTextContent("none");
    } finally {
      blockedStorage.mockRestore();
    }
  });

  it("deregisters the auth-failure handler on unmount, Strict Mode included", async () => {
    // Strict Mode invokes the mount effect twice. The registration used to
    // share an effect with the once-only bootstrap and sat above its early
    // return, so the second registration got no cleanup and the module-level
    // handler outlived the provider.
    const queryClient = createTestQueryClient();
    const { unmount } = render(
      <StrictMode>
        <QueryClientProvider client={queryClient}>
          <AuthProvider>
            <Probe />
          </AuthProvider>
        </QueryClientProvider>
      </StrictMode>,
    );
    await expectLoaded();

    unmount();

    server.use(
      http.get("/api/animals", () =>
        HttpResponse.json({ detail: "Expired" }, { status: 401 }),
      ),
    );
    rejectRefresh();
    pushMock.mockClear();
    replaceMock.mockClear();
    await act(async () => {
      await apiFetch("/api/animals").catch(() => undefined);
    });

    expect(replaceMock).not.toHaveBeenCalled();
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
