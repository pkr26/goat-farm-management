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

import { QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { useEffect, type ReactElement, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setAccessToken, setCurrentFarmId } from "@/lib/api-client";
import { AuthProvider, useAuth } from "@/lib/auth-context";
import { TEST_FARMS, server } from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";

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
      <span data-testid="farmCount">{auth.farms.length}</span>
      <span data-testid="farmId">
        {auth.farmId === null ? "none" : String(auth.farmId)}
      </span>
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
      <button onClick={() => void auth.refreshFarms()}>refresh-farms</button>
      <button onClick={() => void auth.signOut()}>sign-out</button>
    </div>
  );
}

function ActionCapture({
  capture,
}: {
  capture: (actions: {
    signIn: ReturnType<typeof useAuth>["signIn"];
    refreshFarms: () => Promise<void>;
    signOut: () => Promise<void>;
  }) => void;
}) {
  const { signIn, refreshFarms, signOut } = useAuth();
  useEffect(() => {
    capture({ signIn, refreshFarms, signOut });
  }, [capture, signIn, refreshFarms, signOut]);
  return null;
}

function RefreshOnEffect({
  launch,
}: {
  launch: (refreshFarms: () => Promise<void>) => void;
}) {
  const { refreshFarms } = useAuth();
  useEffect(() => launch(refreshFarms), [launch, refreshFarms]);
  return null;
}

function SignInOnEffect({
  launch,
}: {
  launch: (signIn: ReturnType<typeof useAuth>["signIn"]) => void;
}) {
  const { signIn } = useAuth();
  useEffect(() => launch(signIn), [launch, signIn]);
  return null;
}

function renderStrictWithProviders(ui: ReactElement) {
  const queryClient = createTestQueryClient();
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <AuthProvider>{children}</AuthProvider>
      </QueryClientProvider>
    );
  }
  return { queryClient, ...render(ui, { wrapper: Wrapper, reactStrictMode: true }) };
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

describe("AuthProvider Strict Mode ownership fences", () => {
  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
    setAccessToken(null);
    setCurrentFarmId(null);
  });

  it("keeps a post-rehearsal farm refresh when the pre-cleanup response arrives last", async () => {
    let releaseFirst!: () => void;
    const firstGate = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });
    let farmCalls = 0;
    server.use(
      http.post("/api/auth/refresh", () => new HttpResponse(null, { status: 401 })),
      http.get("/api/auth/farms", async () => {
        farmCalls += 1;
        if (farmCalls === 1) {
          await firstGate;
          return HttpResponse.json([
            {
              id: 2,
              name: "First rehearsed snapshot",
              location: null,
              timezone: "Asia/Kolkata",
              role: null,
            },
          ]);
        }
        return HttpResponse.json([
          {
            id: 3,
            name: "Second rehearsed snapshot",
            location: null,
            timezone: "America/Phoenix",
            role: null,
          },
        ]);
      }),
    );
    let actions:
      | {
          signIn: ReturnType<typeof useAuth>["signIn"];
          refreshFarms: () => Promise<void>;
          signOut: () => Promise<void>;
        }
      | undefined;
    let rehearsals = 0;
    let preCleanupRefresh!: Promise<void>;
    const launch = (refreshFarms: () => Promise<void>) => {
      rehearsals += 1;
      if (rehearsals === 1) preCleanupRefresh = refreshFarms();
    };

    renderStrictWithProviders(
      <>
        <Probe />
        <RefreshOnEffect launch={launch} />
        <ActionCapture capture={(captured) => { actions = captured; }} />
      </>,
    );
    await waitFor(() => expect(farmCalls).toBe(1));
    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));
    await act(async () => {
      await actions!.refreshFarms();
    });
    expect(farmCalls).toBe(2);
    expect(screen.getByTestId("farms")).toHaveTextContent("3");

    await act(async () => {
      releaseFirst();
      await preCleanupRefresh;
    });
    expect(screen.getByTestId("farms")).toHaveTextContent("3");
  });

  it("keeps a post-rehearsal identity when the pre-cleanup establishment arrives last", async () => {
    let releaseFirst!: () => void;
    const firstGate = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });
    let farmCalls = 0;
    server.use(
      http.post("/api/auth/refresh", () => new HttpResponse(null, { status: 401 })),
      http.get("/api/auth/farms", async () => {
        farmCalls += 1;
        if (farmCalls === 1) {
          await firstGate;
          return HttpResponse.json([
            {
              id: 2,
              name: "First rehearsed membership",
              location: null,
              timezone: "Asia/Kolkata",
              role: null,
            },
          ]);
        }
        return HttpResponse.json([
          {
            id: 3,
            name: "Second rehearsed membership",
            location: null,
            timezone: "America/Phoenix",
            role: null,
          },
        ]);
      }),
    );
    let actions:
      | {
          signIn: ReturnType<typeof useAuth>["signIn"];
          refreshFarms: () => Promise<void>;
          signOut: () => Promise<void>;
        }
      | undefined;
    let rehearsals = 0;
    let preCleanupEstablishment!: Promise<void>;
    const launch = (signIn: ReturnType<typeof useAuth>["signIn"]) => {
      rehearsals += 1;
      if (rehearsals === 1) {
        preCleanupEstablishment = signIn(
          "strict-shared-token",
          { id: 8, email: "strict-old@goatfarm.test", name: "Strict old" },
        );
      }
    };

    renderStrictWithProviders(
      <>
        <Probe />
        <SignInOnEffect launch={launch} />
        <ActionCapture capture={(captured) => { actions = captured; }} />
      </>,
    );
    await waitFor(() => expect(farmCalls).toBe(1));
    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));
    await act(async () => {
      await actions!.signIn("strict-shared-token", {
        id: 9,
        email: "strict-new@goatfarm.test",
        name: "Strict new",
      });
    });
    expect(farmCalls).toBe(2);
    expect(screen.getByTestId("user")).toHaveTextContent("strict-new@goatfarm.test");
    expect(screen.getByTestId("farms")).toHaveTextContent("3");

    await act(async () => {
      releaseFirst();
      await preCleanupEstablishment;
    });
    expect(screen.getByTestId("user")).toHaveTextContent("strict-new@goatfarm.test");
    expect(screen.getByTestId("farms")).toHaveTextContent("3");
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

  it("keeps the newest same-session establishment when the older response arrives last", async () => {
    let actions:
      | {
          signIn: ReturnType<typeof useAuth>["signIn"];
          refreshFarms: () => Promise<void>;
          signOut: () => Promise<void>;
        }
      | undefined;
    renderWithProviders(
      <>
        <Probe />
        <ActionCapture capture={(captured) => { actions = captured; }} />
      </>,
    );
    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));

    let releaseFirst!: () => void;
    let markFirstStarted!: () => void;
    const firstStarted = new Promise<void>((resolve) => {
      markFirstStarted = resolve;
    });
    const firstGate = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });
    let calls = 0;
    server.use(
      http.get("/api/auth/farms", async () => {
        calls += 1;
        if (calls === 1) {
          markFirstStarted();
          await firstGate;
          return HttpResponse.json([
            {
              id: 2,
              name: "Older sign-in snapshot",
              location: null,
              timezone: "Asia/Kolkata",
              role: null,
            },
          ]);
        }
        return HttpResponse.json([
          {
            id: 3,
            name: "Newest sign-in snapshot",
            location: null,
            timezone: "America/Phoenix",
            role: null,
          },
        ]);
      }),
    );

    const olderActor = { id: 8, email: "older@goatfarm.test", name: "Older" };
    const newestActor = { id: 9, email: "newest@goatfarm.test", name: "Newest" };
    let older!: Promise<void>;
    await act(async () => {
      older = actions!.signIn("shared-signin-token", olderActor);
      await firstStarted;
      await actions!.signIn("shared-signin-token", newestActor);
    });
    expect(screen.getByTestId("user")).toHaveTextContent("newest@goatfarm.test");
    expect(screen.getByTestId("farms")).toHaveTextContent("3");

    await act(async () => {
      releaseFirst();
      await older;
    });
    expect(screen.getByTestId("user")).toHaveTextContent("newest@goatfarm.test");
    expect(screen.getByTestId("farms")).toHaveTextContent("3");
  });

  it("keeps a newer same-token session when the older establishment fails last", async () => {
    let actions:
      | {
          signIn: ReturnType<typeof useAuth>["signIn"];
          refreshFarms: () => Promise<void>;
          signOut: () => Promise<void>;
        }
      | undefined;
    renderWithProviders(
      <>
        <Probe />
        <ActionCapture capture={(captured) => { actions = captured; }} />
      </>,
    );
    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));

    let releaseOlder!: () => void;
    let markOlderStarted!: () => void;
    const olderStarted = new Promise<void>((resolve) => {
      markOlderStarted = resolve;
    });
    const olderGate = new Promise<void>((resolve) => {
      releaseOlder = resolve;
    });
    let calls = 0;
    server.use(
      http.get("/api/auth/farms", async () => {
        calls += 1;
        if (calls === 1) {
          markOlderStarted();
          await olderGate;
          return HttpResponse.error();
        }
        return HttpResponse.json([
          {
            id: 3,
            name: "New owner membership",
            location: null,
            timezone: "America/Phoenix",
            role: null,
          },
        ]);
      }),
    );

    let older!: Promise<void>;
    await act(async () => {
      older = actions!.signIn("shared-failure-token", {
        id: 8,
        email: "older@goatfarm.test",
        name: "Older",
      });
      await olderStarted;
      await actions!.signIn("shared-failure-token", {
        id: 9,
        email: "newest@goatfarm.test",
        name: "Newest",
      });
    });
    expect(screen.getByTestId("user")).toHaveTextContent("newest@goatfarm.test");
    expect(screen.getByTestId("farms")).toHaveTextContent("3");

    await act(async () => {
      releaseOlder();
      await expect(older).rejects.toThrow();
    });
    expect(screen.getByTestId("user")).toHaveTextContent("newest@goatfarm.test");
    expect(screen.getByTestId("farms")).toHaveTextContent("3");
  });

  it("tears down the currently owned session when establishment fails", async () => {
    let actions:
      | {
          signIn: ReturnType<typeof useAuth>["signIn"];
          refreshFarms: () => Promise<void>;
          signOut: () => Promise<void>;
        }
      | undefined;
    let logoutCalls = 0;
    server.use(
      http.get("/api/auth/farms", () => HttpResponse.error()),
      http.post("/api/auth/logout", () => {
        logoutCalls += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const rendered = renderWithProviders(
      <>
        <Probe />
        <ActionCapture capture={(captured) => { actions = captured; }} />
      </>,
    );
    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));
    rendered.queryClient.setQueryData(["owned-session-data"], { stale: true });

    await act(async () => {
      await expect(
        actions!.signIn("failed-current-token", {
          id: 10,
          email: "failed@goatfarm.test",
          name: "Failed",
        }),
      ).rejects.toThrow();
    });

    expect(screen.getByTestId("user")).toHaveTextContent("none");
    expect(screen.getByTestId("farms")).toBeEmptyDOMElement();
    expect(screen.getByTestId("farmId")).toHaveTextContent("none");
    expect(rendered.queryClient.getQueryData(["owned-session-data"])).toBeUndefined();
    await waitFor(() => expect(logoutCalls).toBe(1));
  });

  it("still commits a staged user when a newer explicit farm refresh owns the list", async () => {
    let actions:
      | {
          signIn: ReturnType<typeof useAuth>["signIn"];
          refreshFarms: () => Promise<void>;
          signOut: () => Promise<void>;
        }
      | undefined;
    renderWithProviders(
      <>
        <Probe />
        <ActionCapture capture={(captured) => { actions = captured; }} />
      </>,
    );
    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));

    let releaseSignIn!: () => void;
    let markSignInStarted!: () => void;
    const signInStarted = new Promise<void>((resolve) => {
      markSignInStarted = resolve;
    });
    const signInGate = new Promise<void>((resolve) => {
      releaseSignIn = resolve;
    });
    let calls = 0;
    server.use(
      http.get("/api/auth/farms", async () => {
        calls += 1;
        if (calls === 1) {
          markSignInStarted();
          await signInGate;
          return HttpResponse.json([
            {
              id: 2,
              name: "Sign-in membership snapshot",
              location: null,
              timezone: "Asia/Kolkata",
              role: null,
            },
          ]);
        }
        return HttpResponse.json([
          {
            id: 3,
            name: "Explicit refresh snapshot",
            location: null,
            timezone: "America/Phoenix",
            role: null,
          },
        ]);
      }),
    );

    const actor = { id: 9, email: "worker@goatfarm.test", name: "Worker" };
    let establishing!: Promise<void>;
    await act(async () => {
      establishing = actions!.signIn("replacement-token", actor);
      await signInStarted;
      await actions!.refreshFarms();
    });
    expect(screen.getByTestId("farms")).toHaveTextContent("3");

    await act(async () => {
      releaseSignIn();
      await establishing;
    });
    expect(screen.getByTestId("user")).toHaveTextContent("worker@goatfarm.test");
    expect(screen.getByTestId("farms")).toHaveTextContent("3");
  });

  it("uses the establishment list when a newer explicit farm refresh fails", async () => {
    let actions:
      | {
          signIn: ReturnType<typeof useAuth>["signIn"];
          refreshFarms: () => Promise<void>;
          signOut: () => Promise<void>;
        }
      | undefined;
    renderWithProviders(
      <>
        <Probe />
        <ActionCapture capture={(captured) => { actions = captured; }} />
      </>,
    );
    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));

    let releaseSignIn!: () => void;
    let markSignInStarted!: () => void;
    const signInStarted = new Promise<void>((resolve) => {
      markSignInStarted = resolve;
    });
    const signInGate = new Promise<void>((resolve) => {
      releaseSignIn = resolve;
    });
    let calls = 0;
    server.use(
      http.get("/api/auth/farms", async () => {
        calls += 1;
        if (calls === 1) {
          markSignInStarted();
          await signInGate;
          return HttpResponse.json([
            {
              id: 42,
              name: "Valid establishment snapshot",
              location: null,
              timezone: "Asia/Kolkata",
              role: null,
            },
          ]);
        }
        return HttpResponse.error();
      }),
    );

    const actor = { id: 9, email: "worker@goatfarm.test", name: "Worker" };
    let establishing!: Promise<void>;
    await act(async () => {
      establishing = actions!.signIn("replacement-token", actor);
      await signInStarted;
      await expect(actions!.refreshFarms()).rejects.toThrow();
    });

    await act(async () => {
      releaseSignIn();
      await establishing;
    });
    expect(screen.getByTestId("user")).toHaveTextContent("worker@goatfarm.test");
    expect(screen.getByTestId("farms")).toHaveTextContent("42");
  });

  it("preserves a live farm choice across refresh when localStorage is blocked", async () => {
    let calls = 0;
    server.use(
      http.get("/api/auth/farms", () => {
        calls += 1;
        return HttpResponse.json([
          {
            id: 1,
            name: "First farm",
            location: null,
            timezone: "Asia/Kolkata",
            role: null,
          },
          {
            id: 2,
            name: "Chosen farm",
            location: null,
            timezone: "America/Phoenix",
            role: null,
          },
          ...(calls > 1
            ? [{
                id: 3,
                name: "New membership",
                location: null,
                timezone: "Europe/London",
                role: null,
              }]
            : []),
        ]);
      }),
    );
    const blockedStorage = vi
      .spyOn(window, "localStorage", "get")
      .mockImplementation(() => {
        throw new DOMException("The operation is insecure.", "SecurityError");
      });
    try {
      const user = userEvent.setup();
      renderWithProviders(<Probe />);
      await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));

      await user.click(screen.getByRole("button", { name: "select-2" }));
      expect(screen.getByTestId("farmId")).toHaveTextContent("2");
      await user.click(screen.getByRole("button", { name: "refresh-farms" }));

      await waitFor(() => expect(screen.getByTestId("farms")).toHaveTextContent("1,2,3"));
      expect(screen.getByTestId("farmId")).toHaveTextContent("2");
    } finally {
      blockedStorage.mockRestore();
    }
  });

  it("clears cached farm data immediately when the operator selects another farm", async () => {
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json([
          ...TEST_FARMS,
          {
            id: 2,
            name: "Second farm",
            location: null,
            timezone: "America/Phoenix",
            role: null,
          },
        ]),
      ),
    );
    const user = userEvent.setup();
    const rendered = renderWithProviders(<Probe />);
    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));
    expect(screen.getByTestId("farmId")).toHaveTextContent("1");
    rendered.queryClient.setQueryData(["farm-scoped-row"], { farmId: 1 });

    await user.click(screen.getByRole("button", { name: "select-2" }));

    expect(screen.getByTestId("farmId")).toHaveTextContent("2");
    expect(rendered.queryClient.getQueryData(["farm-scoped-row"])).toBeUndefined();
  });

  it("fully tears down the active farm when the final membership disappears", async () => {
    let actions:
      | {
          signIn: ReturnType<typeof useAuth>["signIn"];
          refreshFarms: () => Promise<void>;
          signOut: () => Promise<void>;
        }
      | undefined;
    const rendered = renderWithProviders(
      <>
        <Probe />
        <ActionCapture capture={(captured) => { actions = captured; }} />
      </>,
    );
    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));
    rendered.queryClient.setQueryData(["removed-farm-row"], { farmId: 1 });
    server.use(http.get("/api/auth/farms", () => HttpResponse.json([])));

    await act(async () => {
      await actions!.refreshFarms();
    });

    expect(screen.getByTestId("farms")).toBeEmptyDOMElement();
    expect(screen.getByTestId("farmId")).toHaveTextContent("none");
    expect(rendered.queryClient.getQueryData(["removed-farm-row"])).toBeUndefined();
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

  it("suppresses a refresh error owned by the session before logout and replacement", async () => {
    let actions:
      | {
          signIn: ReturnType<typeof useAuth>["signIn"];
          refreshFarms: () => Promise<void>;
          signOut: () => Promise<void>;
        }
      | undefined;
    renderWithProviders(
      <>
        <Probe />
        <ActionCapture capture={(captured) => { actions = captured; }} />
      </>,
    );
    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));

    let releaseOldRefresh!: () => void;
    let markOldRefreshStarted!: () => void;
    const oldRefreshStarted = new Promise<void>((resolve) => {
      markOldRefreshStarted = resolve;
    });
    const oldRefreshGate = new Promise<void>((resolve) => {
      releaseOldRefresh = resolve;
    });
    let farmCalls = 0;
    server.use(
      http.get("/api/auth/farms", async () => {
        farmCalls += 1;
        if (farmCalls === 1) {
          markOldRefreshStarted();
          await oldRefreshGate;
          return HttpResponse.error();
        }
        return HttpResponse.json([
          {
            id: 4,
            name: "Replacement farm",
            location: null,
            timezone: "America/Phoenix",
            role: null,
          },
        ]);
      }),
      http.post("/api/auth/logout", () => new HttpResponse(null, { status: 204 })),
    );

    let staleRefresh!: Promise<void>;
    await act(async () => {
      staleRefresh = actions!.refreshFarms();
      await oldRefreshStarted;
      await actions!.signOut();
      await actions!.signIn("replacement-after-logout", {
        id: 11,
        email: "replacement@goatfarm.test",
        name: "Replacement",
      });
    });
    expect(screen.getByTestId("user")).toHaveTextContent("replacement@goatfarm.test");
    expect(screen.getByTestId("farms")).toHaveTextContent("4");

    await act(async () => {
      releaseOldRefresh();
      await expect(staleRefresh).resolves.toBeUndefined();
    });
    expect(screen.getByTestId("user")).toHaveTextContent("replacement@goatfarm.test");
    expect(screen.getByTestId("farms")).toHaveTextContent("4");
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

  it("rejects a staged sign-in when its provider unmounts before farms settle", async () => {
    let actions:
      | {
          signIn: ReturnType<typeof useAuth>["signIn"];
          refreshFarms: () => Promise<void>;
          signOut: () => Promise<void>;
        }
      | undefined;
    const rendered = renderWithProviders(
      <>
        <Probe />
        <ActionCapture capture={(captured) => { actions = captured; }} />
      </>,
    );
    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));

    let releaseFarms!: () => void;
    let markFarmsStarted!: () => void;
    const farmsStarted = new Promise<void>((resolve) => {
      markFarmsStarted = resolve;
    });
    const farmsGate = new Promise<void>((resolve) => {
      releaseFarms = resolve;
    });
    server.use(
      http.get("/api/auth/farms", async () => {
        markFarmsStarted();
        await farmsGate;
        return HttpResponse.json(TEST_FARMS);
      }),
    );

    let staged!: Promise<void>;
    await act(async () => {
      staged = actions!.signIn("unmounted-token", {
        id: 12,
        email: "unmounted@goatfarm.test",
        name: "Unmounted",
      });
      await farmsStarted;
    });
    rendered.unmount();

    releaseFarms();
    await expect(staged).rejects.toMatchObject({ name: "AbortError" });
  });

  it("coalesces same-tick logout requests into one revocation", async () => {
    let logoutCalls = 0;
    server.use(
      http.post("/api/auth/logout", () => {
        logoutCalls += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const rendered = renderWithProviders(<Probe />);
    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));
    rendered.queryClient.setQueryData(["signed-in-cache"], { farmId: 1 });

    const signOut = screen.getByRole("button", { name: "sign-out" });
    fireEvent.click(signOut);
    fireEvent.click(signOut);

    await waitFor(() => expect(logoutCalls).toBe(1));
    expect(replaceMock).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("farms")).toBeEmptyDOMElement();
    expect(screen.getByTestId("farmCount")).toHaveTextContent("0");
    expect(screen.getByTestId("farmId")).toHaveTextContent("none");
    expect(rendered.queryClient.getQueryData(["signed-in-cache"])).toBeUndefined();
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
