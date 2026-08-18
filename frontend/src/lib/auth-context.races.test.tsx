// REGRESSION TESTS — bugs fixed; these tests pin the fixes.
//
// Both concern AuthProvider's one-shot bootstrap racing a real sign-in, and
// they had to be fixed together: bounding the bootstrap without the epoch
// guard would have converted a stuck spinner into a silent logout.
//
// Bug A — pinned shell. `loading` was written in exactly one place: the
// bootstrap IIFE's finally. That IIFE awaits /api/auth/farms, which has no
// timeout, so a black-holed farms call left `loading` true forever. The app
// shell gates on it ((app)/layout.tsx), and nothing in signIn touched it — so
// a user who signed in successfully sat behind a permanent "Loading…". The
// bounded variant needs no wedged socket at all: another tab holding the
// refresh Web Lock parks this one for up to 12s.
//
// Bug B — superseded teardown. establishSession's catch only re-threw
// AuthSessionChangedError, which api-client mints in its post-await epoch
// asserts. A TRANSPORT failure rejects before any of those asserts run, so it
// arrived as a bare TypeError, skipped the escape, and ran clearSession() —
// wiping the token, user, farms, query cache and stored farm id of whichever
// session was current, which after a racing sign-in is the NEW one. The
// bootstrap is always the epoch loser, because performRefresh installs its
// token directly without bumping the epoch.

import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setAccessToken, setCurrentFarmId } from "@/lib/api-client";
import { useAuth } from "@/lib/auth-context";
import { TEST_FARMS, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

const { pushMock, replaceMock } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  replaceMock: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock, prefetch: vi.fn() }),
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

/** Parks the FIRST /api/auth/farms call (the bootstrap's) until released, and
 *  answers every later one (the sign-in's) normally. */
function parkFirstFarmsCall(outcome: "hang" | "networkError") {
  let release: (() => void) | undefined;
  const parked = new Promise<void>((resolve) => {
    release = resolve;
  });
  let calls = 0;
  server.use(
    http.get("/api/auth/farms", async () => {
      calls += 1;
      if (calls === 1) {
        await parked;
        if (outcome === "networkError") return HttpResponse.error();
      }
      return HttpResponse.json(TEST_FARMS);
    }),
  );
  return {
    release: () => release?.(),
    firstCallStarted: () => waitFor(() => expect(calls).toBeGreaterThan(0)),
  };
}

describe("AuthProvider bootstrap racing a sign-in", () => {
  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
    setAccessToken(null);
    setCurrentFarmId(null);
  });

  it("releases the loading gate on sign-in while the bootstrap farms call is still parked", async () => {
    const bootstrap = parkFirstFarmsCall("hang");
    const user = userEvent.setup();
    renderWithProviders(<Probe />);

    await bootstrap.firstCallStarted();
    // The bootstrap owns `loading` and is going nowhere.
    expect(screen.getByTestId("loading")).toHaveTextContent("true");

    await user.click(screen.getByText("sign-in"));

    // A committed session outranks a parked bootstrap.
    await waitFor(() =>
      expect(screen.getByTestId("loading")).toHaveTextContent("false"),
    );
    expect(screen.getByTestId("user")).toHaveTextContent("worker@goatfarm.test");

    await act(async () => {
      bootstrap.release();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  });

  it("keeps the signed-in session when the superseded bootstrap's farms call fails at the transport layer", async () => {
    const bootstrap = parkFirstFarmsCall("networkError");
    const user = userEvent.setup();
    renderWithProviders(<Probe />);

    await bootstrap.firstCallStarted();
    await user.click(screen.getByText("sign-in"));
    await waitFor(() =>
      expect(screen.getByTestId("user")).toHaveTextContent("worker@goatfarm.test"),
    );

    // Now let the superseded bootstrap fail. It must not clear the session it
    // no longer owns.
    await act(async () => {
      bootstrap.release();
      await new Promise((resolve) => setTimeout(resolve, 50));
    });

    expect(screen.getByTestId("user")).toHaveTextContent("worker@goatfarm.test");
    expect(screen.getByTestId("loading")).toHaveTextContent("false");
  });
});
