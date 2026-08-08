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

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setAccessToken, setCurrentFarmId } from "@/lib/api-client";
import { useAuth } from "@/lib/auth-context";
import { server } from "@/test/msw-server";
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
      <button onClick={() => auth.selectFarm(2)}>select-2</button>
      <button onClick={() => void auth.signOut()}>sign-out</button>
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
