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
// refresh-cookie Web Lock parks this one behind the current bounded mutation.
//
// Bug B — superseded teardown. establishSession's catch only re-threw
// AuthSessionChangedError, which api-client mints in its post-await epoch
// asserts. A TRANSPORT failure rejects before any of those asserts run, so it
// arrived as a bare TypeError, skipped the escape, and ran clearSession() —
// wiping the token, user, farms, query cache and stored farm id of whichever
// session was current, which after a racing sign-in is the NEW one. The
// bootstrap is always the epoch loser, because performRefresh installs its
// token directly without bumping the epoch.

import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { useEffect } from "react";
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
      <span data-testid="farms">{auth.farms.map((farm) => farm.id).join(",")}</span>
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
      <button onClick={() => void auth.refreshFarms()}>refresh-farms</button>
      <button onClick={() => void auth.signOut()}>sign-out</button>
    </div>
  );
}

function ActionCapture({
  capture,
}: {
  capture: (actions: {
    refreshFarms: () => Promise<void>;
    signOut: () => Promise<void>;
  }) => void;
}) {
  const { refreshFarms, signOut } = useAuth();
  useEffect(() => {
    capture({ refreshFarms, signOut });
  }, [capture, refreshFarms, signOut]);
  return null;
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

  it("waits beyond 12 seconds for a legitimate auth-cookie lock holder", async () => {
    let grantLock: (() => void) | undefined;
    const lockRequest = vi.fn(
      (
        _name: string,
        options: LockOptions,
        callback: () => Promise<unknown>,
      ) =>
        new Promise<unknown>((resolve, reject) => {
          options.signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
          grantLock = () => {
            grantLock = undefined;
            void callback().then(resolve, reject);
          };
        }),
    );
    vi.stubGlobal("navigator", { locks: { request: lockRequest } });
    vi.useFakeTimers();
    try {
      renderWithProviders(<Probe />);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(lockRequest).toHaveBeenCalledTimes(1);

      // This was the old lock-wait deadline. A normal cookie mutation can own
      // the same lock for up to 60 seconds, so bootstrap must remain pending
      // here instead of concluding that the user has no session.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(13_000);
      });
      expect(screen.getByTestId("loading")).toHaveTextContent("true");
      expect(replaceMock).not.toHaveBeenCalled();

      act(() => grantLock?.());
      vi.useRealTimers();
      await waitFor(() =>
        expect(screen.getByTestId("loading")).toHaveTextContent("false"),
      );
      expect(screen.getByTestId("user")).toHaveTextContent("owner@goatfarm.test");
    } finally {
      grantLock?.();
      vi.useRealTimers();
      vi.unstubAllGlobals();
    }
  });
});

describe("AuthProvider stale async completions", () => {
  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
    setAccessToken(null);
    setCurrentFarmId(null);
  });

  it("keeps the newest farm refresh when an older response arrives last", async () => {
    renderWithProviders(<Probe />);
    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));

    let releaseFirst!: () => void;
    const firstGate = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });
    let calls = 0;
    server.use(
      http.get("/api/auth/farms", async () => {
        calls += 1;
        if (calls === 1) {
          await firstGate;
          return HttpResponse.json([
            {
              id: 2,
              name: "Older membership snapshot",
              location: null,
              timezone: "Asia/Kolkata",
              role: null,
            },
          ]);
        }
        return HttpResponse.json([
          {
            id: 3,
            name: "Newest membership snapshot",
            location: null,
            timezone: "America/Phoenix",
            role: null,
          },
        ]);
      }),
    );

    const refresh = screen.getByRole("button", { name: "refresh-farms" });
    fireEvent.click(refresh);
    fireEvent.click(refresh);
    await waitFor(() => expect(screen.getByTestId("farms")).toHaveTextContent("3"));

    await act(async () => {
      releaseFirst();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(screen.getByTestId("farms")).toHaveTextContent("3");
  });

  it("suppresses an older refresh error after a newer refresh succeeds", async () => {
    let actions:
      | { refreshFarms: () => Promise<void>; signOut: () => Promise<void> }
      | undefined;
    renderWithProviders(
      <>
        <Probe />
        <ActionCapture capture={(captured) => { actions = captured; }} />
      </>,
    );
    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));

    let releaseFirst!: () => void;
    const firstGate = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });
    let calls = 0;
    server.use(
      http.get("/api/auth/farms", async () => {
        calls += 1;
        if (calls === 1) {
          await firstGate;
          return HttpResponse.error();
        }
        return HttpResponse.json([
          {
            id: 3,
            name: "Newest membership snapshot",
            location: null,
            timezone: "America/Phoenix",
            role: null,
          },
        ]);
      }),
    );

    let older!: Promise<void>;
    await act(async () => {
      older = actions!.refreshFarms();
      await actions!.refreshFarms();
    });
    await waitFor(() => expect(screen.getByTestId("farms")).toHaveTextContent("3"));
    await act(async () => {
      releaseFirst();
      await expect(older).resolves.toBeUndefined();
    });
    expect(screen.getByTestId("farms")).toHaveTextContent("3");
  });

  it("does not let an unmounted bootstrap clear a later tree's query cache", async () => {
    let releaseFarms!: () => void;
    let farmsStarted!: () => void;
    const started = new Promise<void>((resolve) => {
      farmsStarted = resolve;
    });
    const gate = new Promise<void>((resolve) => {
      releaseFarms = resolve;
    });
    server.use(
      http.get("/api/auth/farms", async () => {
        farmsStarted();
        await gate;
        return HttpResponse.json(TEST_FARMS);
      }),
    );

    const rendered = renderWithProviders(<Probe />);
    await started;
    rendered.unmount();
    rendered.queryClient.setQueryData(["new-tree-data"], { safe: true });

    await act(async () => {
      releaseFarms();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(rendered.queryClient.getQueryData(["new-tree-data"])).toEqual({
      safe: true,
    });
  });

  it("coalesces same-tick logout requests into one revocation", async () => {
    let logoutCalls = 0;
    server.use(
      http.post("/api/auth/logout", () => {
        logoutCalls += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithProviders(<Probe />);
    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));

    const signOut = screen.getByRole("button", { name: "sign-out" });
    fireEvent.click(signOut);
    fireEvent.click(signOut);

    await waitFor(() => expect(logoutCalls).toBe(1));
    expect(replaceMock).toHaveBeenCalledTimes(1);
  });

  it("does not let a prior session's slow logout suppress the new session's logout", async () => {
    let releaseFirst!: () => void;
    let markFirstStarted!: () => void;
    const firstStarted = new Promise<void>((resolve) => {
      markFirstStarted = resolve;
    });
    const firstGate = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });
    let logoutCalls = 0;
    server.use(
      http.post("/api/auth/logout", async () => {
        logoutCalls += 1;
        if (logoutCalls === 1) {
          markFirstStarted();
          await firstGate;
        }
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<Probe />);
    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));

    fireEvent.click(screen.getByRole("button", { name: "sign-out" }));
    await firstStarted;
    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent("none"));

    // Establish a distinct session while the old session's revocation is
    // still parked. Its sign-out owns a new epoch and must dispatch its own
    // revocation plus local teardown immediately.
    await user.click(screen.getByRole("button", { name: "sign-in" }));
    await waitFor(() =>
      expect(screen.getByTestId("user")).toHaveTextContent("worker@goatfarm.test"),
    );
    fireEvent.click(screen.getByRole("button", { name: "sign-out" }));

    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent("none"));
    expect(replaceMock).toHaveBeenCalledTimes(2);
    // Cookie-mutating responses are FIFO: the new session's revocation is
    // queued, not suppressed, until the old response has deleted its cookie.
    expect(logoutCalls).toBe(1);

    await act(async () => {
      releaseFirst();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitFor(() => expect(logoutCalls).toBe(2));
  });

  it("ignores stale auth actions invoked after their provider unmounted", async () => {
    let actions:
      | { refreshFarms: () => Promise<void>; signOut: () => Promise<void> }
      | undefined;
    const rendered = renderWithProviders(
      <>
        <Probe />
        <ActionCapture capture={(captured) => { actions = captured; }} />
      </>,
    );
    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));
    expect(actions).toBeDefined();
    rendered.unmount();

    let staleRequests = 0;
    server.use(
      http.get("/api/auth/farms", () => {
        staleRequests += 1;
        return HttpResponse.json(TEST_FARMS);
      }),
      http.post("/api/auth/logout", () => {
        staleRequests += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    replaceMock.mockClear();

    await actions!.refreshFarms();
    await actions!.signOut();

    expect(staleRequests).toBe(0);
    expect(replaceMock).not.toHaveBeenCalled();
  });
});
