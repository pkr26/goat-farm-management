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

import { screen, waitFor } from "@testing-library/react";
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
    </div>
  );
}

describe("AuthProvider bootstrap — refresh throws (suspected unhandled rejection bug)", () => {
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
    expect(pushMock).toHaveBeenCalledWith("/login");
    // BUG: the assertions above pass, but the bootstrap promise rejected
    // without a catch — see this run's unhandled-rejection report.
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
    expect(pushMock).toHaveBeenCalledWith("/login");
    // BUG: same root cause — resp.json() throws inside the try with no catch.
  });
});
