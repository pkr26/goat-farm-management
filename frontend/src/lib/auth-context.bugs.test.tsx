// REGRESSION TESTS — bug fixed; these tests pin the fix.
//
// AuthProvider's bootstrap IIFE (auth-context.tsx useEffect) used to wrap the
// silent /api/auth/refresh in try/finally WITHOUT a catch. When the refresh
// fetch itself rejected (backend down / network error) or a 200 response
// carried a non-JSON body (resp.json() throws), the promise rejected with no
// handler → an UNHANDLED PROMISE REJECTION on every app load for a
// signed-out user on a flaky network. The redirect itself still worked
// (finally ran, user stayed null, /login push happened), so the bug
// manifested only as the unhandled rejection vitest reports against this
// file. The bootstrap now has a catch; these tests confirm both the
// functional path and the absence of the rejection.
//
// Second bug pinned below: cross-tenant cache leak. React Query cache keys
// are URL-only (no farm id) and selectFarm/signOut never touched the
// QueryClient, so switching farms kept rendering the previous farm's cached
// data (permissions included) and a sign-out → sign-in as another user
// leaked the previous user's data. Both actions now call
// queryClient.clear(); the tests seed the cache and assert it is emptied.
//
// Third bug pinned below: AccountDialog installed the post-password-change
// access token via the PUBLIC setAccessToken, which bumps authSessionEpoch —
// the "different session signed in" boundary — so every concurrent in-flight
// apiFetch rejected with AuthSessionChangedError even though the actor never
// changed. The dialog now rotates through refreshSession(), the same
// non-epoch-bumping path a 401 retry uses; the test holds a request in
// flight across the password change and asserts it still resolves.
//
// Fourth bug pinned below: establishSession's catch handler unconditionally
// called clearSession() (and, with revokeOnFailure, POSTed
// /api/auth/logout) for ANY rejection, including AuthSessionChangedError —
// the signal that a NEWER session already superseded this call's epoch. A
// slow bootstrap /api/auth/farms fetch that resolved after a sign-in raced
// it used to wipe out the session sign-in had just established. The catch
// now re-throws AuthSessionChangedError untouched instead of tearing down
// state it doesn't own.

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccountDialog } from "@/components/account-dialog";
import { apiFetch, setAccessToken, setCurrentFarmId } from "@/lib/api-client";
import { useAuth } from "@/lib/auth-context";
import { farmToday } from "@/lib/format";
import { TEST_USER, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

const { pushMock } = vi.hoisted(() => ({ pushMock: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

function Probe() {
  const auth = useAuth();
  return (
    <div>
      <span data-testid="loading">{String(auth.loading)}</span>
      <span data-testid="user">{auth.user ? auth.user.email : "none"}</span>
      <span data-testid="farmId">
        {auth.farmId === null ? "none" : String(auth.farmId)}
      </span>
      <button onClick={() => auth.selectFarm(2)}>select-2</button>
      <button onClick={() => auth.selectFarm(99, "America/Phoenix")}>
        select-unlisted-phoenix
      </button>
      <button onClick={() => void auth.signOut()}>sign-out</button>
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
    </div>
  );
}

describe("AuthProvider bootstrap — refresh failure handling", () => {
  beforeEach(() => {
    pushMock.mockClear();
    setAccessToken(null);
    setCurrentFarmId(null);
  });

  it("network error during silent refresh still settles loading and redirects", async () => {
    server.use(http.post("/api/auth/refresh", () => HttpResponse.error()));

    renderWithProviders(<Probe />);

    await waitFor(() =>
      expect(screen.getByTestId("loading")).toHaveTextContent("false"),
    );
    expect(screen.getByTestId("user")).toHaveTextContent("none");
    // The redirect is a separate effect that fires the render AFTER loading
    // flips — an immediate assertion races it (flaky).
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/login"));
  });

  it("non-JSON 200 body during silent refresh still settles loading and redirects", async () => {
    server.use(
      http.post(
        "/api/auth/refresh",
        () =>
          new HttpResponse("<html>proxy error</html>", {
            status: 200,
            headers: { "Content-Type": "text/html" },
          }),
      ),
    );

    renderWithProviders(<Probe />);

    await waitFor(() =>
      expect(screen.getByTestId("loading")).toHaveTextContent("false"),
    );
    expect(screen.getByTestId("user")).toHaveTextContent("none");
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/login"));
  });

  it("a farms failure after a valid refresh does not partially commit the user", async () => {
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json({ detail: "temporarily unavailable" }, { status: 503 }),
      ),
    );

    renderWithProviders(<Probe />);

    await waitFor(() =>
      expect(screen.getByTestId("loading")).toHaveTextContent("false"),
    );
    expect(screen.getByTestId("user")).toHaveTextContent("none");
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/login"));
  });
});

describe("AuthProvider — query cache cleared on farm switch / sign-out", () => {
  beforeEach(() => {
    pushMock.mockClear();
    setAccessToken(null);
    setCurrentFarmId(null);
  });

  it("selectFarm drops every cached query from the previous farm", async () => {
    const { queryClient } = renderWithProviders(<Probe />);
    await waitFor(() =>
      expect(screen.getByTestId("loading")).toHaveTextContent("false"),
    );

    // Stale entries keyed by bare URL, exactly like the real pages leave them.
    queryClient.setQueryData(["/api/animals"], [{ id: 1, tag_number: "A-1" }]);
    queryClient.setQueryData(["/api/auth/permissions"], {
      is_owner: true,
      permissions: [],
    });
    expect(queryClient.getQueryCache().getAll()).not.toHaveLength(0);

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "select-2" }));

    expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
  });

  it("uses caller-provided timezone data when the selected farm is not in the cached list", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Probe />);
    await waitFor(() =>
      expect(screen.getByTestId("loading")).toHaveTextContent("false"),
    );
    const instant = new Date("2026-08-10T02:00:00Z");
    expect(farmToday(instant)).toBe("2026-08-10");

    await user.click(
      screen.getByRole("button", { name: "select-unlisted-phoenix" }),
    );

    expect(farmToday(instant)).toBe("2026-08-09");
  });

  it("signOut leaves the query cache empty for the next user", async () => {
    server.use(
      http.post("/api/auth/logout", () => HttpResponse.json({ ok: true })),
    );
    const { queryClient } = renderWithProviders(<Probe />);
    await waitFor(() =>
      expect(screen.getByTestId("user")).toHaveTextContent(
        "owner@goatfarm.test",
      ),
    );

    queryClient.setQueryData(["/api/animals"], [{ id: 1, tag_number: "A-1" }]);
    expect(queryClient.getQueryCache().getAll()).not.toHaveLength(0);

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "sign-out" }));

    await waitFor(() =>
      expect(screen.getByTestId("user")).toHaveTextContent("none"),
    );
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
  });
});

describe("AccountDialog — password change keeps the session's in-flight requests alive", () => {
  beforeEach(() => {
    pushMock.mockClear();
    setAccessToken(null);
    setCurrentFarmId(null);
  });

  it("a request in flight across a password change still resolves", async () => {
    let releaseAnimals: (() => void) | undefined;
    const animalsGate = new Promise<void>((resolve) => {
      releaseAnimals = resolve;
    });
    server.use(
      http.post("/api/auth/change-password", () =>
        HttpResponse.json({
          access_token: "rotated-access-token",
          token_type: "bearer",
          user: TEST_USER,
        }),
      ),
      http.get("/api/animals", async () => {
        await animalsGate;
        return HttpResponse.json([{ id: 1, tag_number: "G-001" }]);
      }),
    );

    renderWithProviders(
      <>
        <Probe />
        <AccountDialog name="Test Owner" email="owner@goatfarm.test" />
      </>,
    );
    // The bootstrap's setAccessToken marks a genuine session boundary; the
    // held request below must be issued under the settled session.
    await waitFor(() =>
      expect(screen.getByTestId("user")).toHaveTextContent(
        "owner@goatfarm.test",
      ),
    );

    // A background query, still on the wire while the password changes.
    const inFlight = apiFetch<Array<{ id: number; tag_number: string }>>(
      "/api/animals",
    ).then(
      (data) => ({ outcome: "resolved" as const, data }),
      (error: unknown) => ({
        outcome: "rejected" as const,
        name: error instanceof Error ? error.name : "unknown",
      }),
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Account" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(
      within(dialog).getByLabelText("Current password for password change"),
      "old-password-123",
    );
    await user.type(
      within(dialog).getByLabelText("New password"),
      "correct-horse-battery",
    );
    await user.type(
      within(dialog).getByLabelText("Confirm new password"),
      "correct-horse-battery",
    );
    await user.click(within(dialog).getByRole("button", { name: "Change password" }));
    // The dialog closes only after the mutation resolved and the rotated
    // token was installed — the moment the epoch bump used to happen.
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );

    releaseAnimals?.();
    // Previously: {outcome: "rejected", name: "AuthSessionChangedError"} —
    // the epoch bump aborted a same-actor request mid-flight.
    await expect(inFlight).resolves.toEqual({
      outcome: "resolved",
      data: [{ id: 1, tag_number: "G-001" }],
    });
  });
});

describe("AuthProvider — establishSession must not tear down a newer session on a stale-request race", () => {
  beforeEach(() => {
    pushMock.mockClear();
    setAccessToken(null);
    setCurrentFarmId(null);
  });

  it("a stale bootstrap farms response arriving after sign-in leaves the new session intact", async () => {
    let releaseBootstrapFarms: (() => void) | undefined;
    const bootstrapFarmsGate = new Promise<void>((resolve) => {
      releaseBootstrapFarms = resolve;
    });
    let bootstrapFarmsRequested = false;

    server.use(
      // Only the bootstrap's own call is held; the sign-in call that races
      // it must resolve immediately, exactly like the real slow-GET race.
      http.get("/api/auth/farms", async () => {
        if (!bootstrapFarmsRequested) {
          bootstrapFarmsRequested = true;
          await bootstrapFarmsGate;
        }
        return HttpResponse.json([
          { id: 5, name: "Worker Farm", location: null, role: "worker" },
        ]);
      }),
    );

    renderWithProviders(<Probe />);
    await waitFor(() => expect(bootstrapFarmsRequested).toBe(true));

    // Sign in while the bootstrap's farms fetch is still held: this bumps
    // the auth epoch and commits the second session via its own farms call.
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "sign-in" }));
    await waitFor(() =>
      expect(screen.getByTestId("user")).toHaveTextContent(
        "worker@goatfarm.test",
      ),
    );
    expect(screen.getByTestId("farmId")).toHaveTextContent("5");

    // The stale bootstrap response now lands under a superseded epoch and
    // must not clear the session sign-in just established.
    releaseBootstrapFarms?.();
    await waitFor(() =>
      expect(screen.getByTestId("loading")).toHaveTextContent("false"),
    );

    expect(screen.getByTestId("user")).toHaveTextContent(
      "worker@goatfarm.test",
    );
    expect(screen.getByTestId("farmId")).toHaveTextContent("5");
  });
});
