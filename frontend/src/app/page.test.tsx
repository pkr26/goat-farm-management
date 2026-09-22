/**
 * Root page (`/`): a pure redirect hub driven by auth state. It always
 * renders "Loading…" and, once the session bootstrap settles, replaces to
 *   /login        — logged out
 *   /farm-select  — logged in but no farm available/selected
 *   /dashboard    — logged in with an active farm (auto-selected if needed)
 * It must not redirect while the bootstrap is still pending.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { StrictMode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server, TEST_FARMS, TEST_USER } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { useAuth } from "@/lib/auth-context";

import RootPage from "./page";

const { replaceMock } = vi.hoisted(() => ({ replaceMock: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

function RootMountedAfterBootstrap() {
  const { loading } = useAuth();
  return loading ? null : (
    <StrictMode>
      <RootPage />
    </StrictMode>
  );
}

/** The hub plus a way to switch the active farm, which is what drives it
 *  back into its "still deciding" state after it has already redirected. */
function RootWithFarmSwitch({ farmId }: { farmId: number }) {
  const { selectFarm } = useAuth();
  return (
    <>
      <button type="button" onClick={() => selectFarm(farmId)}>
        Switch farm
      </button>
      <RootPage />
    </>
  );
}

describe("RootPage redirect hub", () => {
  beforeEach(() => replaceMock.mockClear());

  it("renders the Loading… placeholder", async () => {
    renderWithProviders(<RootPage />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    // Let the shared refresh promise settle before the next test installs a
    // different refresh handler.
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/dashboard"));
  });

  it("does not redirect while the session bootstrap is still pending", async () => {
    let refreshRequested = false;
    let releaseRefresh: (() => void) | undefined;
    server.use(
      http.post("/api/auth/refresh", () => {
        refreshRequested = true;
        return new Promise<Response>((resolve) => {
          releaseRefresh = () => resolve(new HttpResponse(null, { status: 401 }));
        });
      }),
    );

    renderWithProviders(<RootPage />);
    await screen.findByText("Loading…");

    // Settled signal, not a wall-clock sleep: once the
    // bootstrap's refresh request has fired and is still pending, no effect
    // can have reached the redirect — loading never settles without it.
    await waitFor(() => expect(refreshRequested).toBe(true));
    expect(replaceMock).not.toHaveBeenCalled();
    releaseRefresh?.();
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
  });

  it("redirects to /login when there is no session", async () => {
    server.use(
      http.post("/api/auth/refresh", () => new HttpResponse(null, { status: 401 })),
    );

    renderWithProviders(<RootPage />);

    await waitFor(() => expect(replaceMock).toHaveBeenLastCalledWith("/login"));
  });

  it("redirects to /farm-select when logged in but the user has no farms", async () => {
    server.use(http.get("/api/auth/farms", () => HttpResponse.json([])));

    // Mount the redirect hub only after auth is already settled so its true
    // redirect branch is exercised during Strict Mode's effect replay.
    renderWithProviders(<RootMountedAfterBootstrap />);

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/farm-select"));
    expect(replaceMock).toHaveBeenCalledTimes(1);
  });

  it("redirects to /dashboard when logged in with a farm (auto-selected)", async () => {
    renderWithProviders(<RootPage />);

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/dashboard"));
    expect(localStorage.getItem("goatfarm.farmId")).toBe(String(TEST_FARMS[0].id));
  });

  it("redirects a restricted role to its first permitted module", async () => {
    server.use(permissionsHandler(["health.view"]));

    renderWithProviders(<RootPage />);

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/health"));
    expect(replaceMock).not.toHaveBeenCalledWith("/dashboard");
  });

  it("redirects to /dashboard with multiple farms (first one auto-selected)", async () => {
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json([
          ...TEST_FARMS,
          { id: 2, name: "Second Farm", location: null, role: "Mover" },
        ]),
      ),
    );

    renderWithProviders(<RootPage />);

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/dashboard"));
  });

  it("honours a previously stored farm selection across reloads", async () => {
    localStorage.setItem("goatfarm.farmId", "2");
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json([
          ...TEST_FARMS,
          { id: 2, name: "Second Farm", location: null, role: "Mover" },
        ]),
      ),
    );

    renderWithProviders(<RootPage />);

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/dashboard"));
    expect(localStorage.getItem("goatfarm.farmId")).toBe("2");
  });

  it("sends a revoked stored selection to the farm picker instead of entering an unchosen farm", async () => {
    // RT-O-3: farm 999 is no longer in the membership list. The hub must not
    // silently auto-select list[0]; farmId stays null and the dispatcher
    // routes to /farm-select for an explicit choice.
    localStorage.setItem("goatfarm.farmId", "999");
    server.use(
      http.post("/api/auth/refresh", () =>
        HttpResponse.json({ access_token: "tok", user: TEST_USER }),
      ),
    );

    renderWithProviders(<RootPage />);

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/farm-select"));
    expect(localStorage.getItem("goatfarm.farmId")).toBe("revoked:999");
  });

  it("dispatches no destination other than /login for a signed-out visitor", async () => {
    server.use(
      http.post("/api/auth/refresh", () => new HttpResponse(null, { status: 401 })),
    );

    renderWithProviders(<RootPage />);

    // AuthProvider sends a signed-out session to /login as well, so asserting
    // only the last call cannot tell the hub's own destination apart from the
    // provider's — a hub that redirected somewhere else would hide behind it.
    // Pin every destination the router was handed instead.
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
    expect([...new Set(replaceMock.mock.calls.map(([path]) => path))]).toEqual(["/login"]);
  });

  it("re-decides after a farm switch without dispatching an undecided destination", async () => {
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json([
          ...TEST_FARMS,
          { id: 2, name: "Second Farm", location: null, role: "Mover" },
        ]),
      ),
    );
    const user = userEvent.setup();

    renderWithProviders(<RootWithFarmSwitch farmId={2} />);

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/dashboard"));
    // Selecting a farm clears the query cache, so the new farm's permissions
    // go back to loading and the hub has no destination for a moment. That
    // undecided state must abort the redirect and re-arm it, never be pushed
    // to the router as a destination of its own.
    await user.click(screen.getByRole("button", { name: "Switch farm" }));

    await waitFor(() => expect(replaceMock).toHaveBeenCalledTimes(2));
    expect(replaceMock.mock.calls.map(([path]) => path)).toEqual([
      "/dashboard",
      "/dashboard",
    ]);
  });
});
