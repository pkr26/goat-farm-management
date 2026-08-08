/**
 * Root page (`/`): a pure redirect hub driven by auth state. It always
 * renders "Loading…" and, once the session bootstrap settles, replaces to
 *   /login        — logged out
 *   /farm-select  — logged in but no farm available/selected
 *   /dashboard    — logged in with an active farm (auto-selected if needed)
 * It must not redirect while the bootstrap is still pending.
 */

import { screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { permissionsHandler, server, TEST_FARMS, TEST_USER } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import RootPage from "./page";

const { replaceMock } = vi.hoisted(() => ({ replaceMock: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

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

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
  });

  it("redirects to /farm-select when logged in but the user has no farms", async () => {
    server.use(http.get("/api/auth/farms", () => HttpResponse.json([])));

    renderWithProviders(<RootPage />);

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/farm-select"));
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

  it("falls back to the first farm when the stored selection no longer exists", async () => {
    localStorage.setItem("goatfarm.farmId", "999");
    server.use(
      http.post("/api/auth/refresh", () =>
        HttpResponse.json({ access_token: "tok", user: TEST_USER }),
      ),
    );

    renderWithProviders(<RootPage />);

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/dashboard"));
    expect(localStorage.getItem("goatfarm.farmId")).toBe(String(TEST_FARMS[0].id));
  });
});
