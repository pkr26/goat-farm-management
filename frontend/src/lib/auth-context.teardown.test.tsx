/**
 * Behavioural coverage for the two AuthProvider teardown paths that reach
 * OUTSIDE React state: clearSession (sign-out / forced logout) and
 * applyFarmList's "no memberships left" branch. Both have to reset the two
 * module-level singletons the rest of the app reads without a hook — the api
 * client's X-Farm-Id scope and format.ts's active farm timezone — and
 * signOut's returned promise has to stay pending until the server-side
 * revocation actually settles, so a failed revocation stays retryable.
 */

import { useQuery } from "@tanstack/react-query";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { useEffect } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch, setAccessToken, setCurrentFarmId } from "@/lib/api-client";
import { useAuth } from "@/lib/auth-context";
import { farmToday } from "@/lib/format";
import { server } from "@/test/msw-server";
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
      <span data-testid="farmId">
        {auth.farmId === null ? "none" : String(auth.farmId)}
      </span>
      <button onClick={() => auth.selectFarm(2)}>select-2</button>
      <button onClick={() => void auth.signOut()}>sign-out</button>
      <button onClick={() => void auth.refreshFarms()}>refresh-farms</button>
    </div>
  );
}

/** A page-style consumer: URL-only cache key, farm scope carried only by the
 *  X-Farm-Id header the api client stamps on, and re-rendered by the farm
 *  selection like every real page. Every value it has ever rendered is
 *  recorded, so a one-frame flash of the previous farm's rows is a failure
 *  and not merely a final-state mismatch. */
function AnimalsPanel({ rendered }: { rendered: string[] }) {
  const { farmId } = useAuth();
  const { data } = useQuery({
    queryKey: ["/api/animals"],
    queryFn: ({ signal }) =>
      apiFetch<{ id: number; tag_number: string }[]>("/api/animals", { signal }),
  });
  const tags = (data ?? []).map((animal) => animal.tag_number).join(",");
  rendered.push(tags);
  return (
    <span data-testid="animals" data-farm={farmId ?? "none"}>
      {tags}
    </span>
  );
}

/** Hands the raw signOut promise to the test: the button swallows it, and
 *  whether that promise is still pending is exactly what is under test. */
function SignOutCapture({ capture }: { capture: (signOut: () => Promise<void>) => void }) {
  const { signOut } = useAuth();
  useEffect(() => capture(signOut), [capture, signOut]);
  return null;
}

/** A farm in a zone whose calendar date differs from the product default
 *  (Asia/Kolkata) for the instant used below — the only way a timezone reset
 *  is observable at all. */
const DESERT_FARM = [
  {
    id: 7,
    name: "Desert Farm",
    location: "Phoenix",
    timezone: "America/Phoenix",
    role: null,
  },
];
/** 07:30 on the 10th in Kolkata, still 19:00 on the 9th in Phoenix. */
const INSTANT = new Date("2026-08-10T02:00:00Z");

async function expectLoaded() {
  await waitFor(() =>
    expect(screen.getByTestId("loading")).toHaveTextContent("false"),
  );
}

/** Records the X-Farm-Id every later request carries; "unset" means the
 *  request never happened. */
function captureFarmScope(): () => string | null {
  let farmHeader: string | null = "unset";
  server.use(
    http.get("/api/animals", ({ request }) => {
      farmHeader = request.headers.get("X-Farm-Id");
      return HttpResponse.json([]);
    }),
  );
  return () => farmHeader;
}

function acceptLogout(): () => number {
  let calls = 0;
  server.use(
    http.post("/api/auth/logout", () => {
      calls += 1;
      return new HttpResponse(null, { status: 204 });
    }),
  );
  return () => calls;
}

describe("AuthProvider teardown — module-level scopes reset with the session", () => {
  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
    setAccessToken(null);
    setCurrentFarmId(null);
  });

  it("stops stamping the signed-out farm on later requests", async () => {
    // X-Farm-Id lives in the api client, not React. A request fired after
    // sign-out (a background refetch, or the next operator on a shared
    // terminal) must not be scoped to the previous session's tenant.
    acceptLogout();
    const farmScope = captureFarmScope();

    const user = userEvent.setup();
    renderWithProviders(<Probe />);
    await expectLoaded();
    expect(screen.getByTestId("farmId")).toHaveTextContent("1");
    await apiFetch("/api/animals");
    expect(farmScope()).toBe("1");

    await user.click(screen.getByRole("button", { name: "sign-out" }));
    await waitFor(() =>
      expect(screen.getByTestId("user")).toHaveTextContent("none"),
    );

    await apiFetch("/api/animals");
    expect(farmScope()).toBeNull();
  });

  it("returns date-only business rules to the default zone on sign-out", async () => {
    // Schemas and non-React helpers read the active farm timezone straight
    // out of format.ts. Leaving the signed-out farm's zone installed would
    // date the next session's "today" by the previous tenant's calendar.
    acceptLogout();
    server.use(http.get("/api/auth/farms", () => HttpResponse.json(DESERT_FARM)));

    const user = userEvent.setup();
    renderWithProviders(<Probe />);
    await expectLoaded();
    expect(screen.getByTestId("farmId")).toHaveTextContent("7");
    expect(farmToday(INSTANT)).toBe("2026-08-09");

    await user.click(screen.getByRole("button", { name: "sign-out" }));
    await waitFor(() =>
      expect(screen.getByTestId("user")).toHaveTextContent("none"),
    );

    expect(farmToday(INSTANT)).toBe("2026-08-10");
  });

  it("stops stamping a farm once the last membership disappears", async () => {
    const farmScope = captureFarmScope();

    const user = userEvent.setup();
    renderWithProviders(<Probe />);
    await expectLoaded();
    await apiFetch("/api/animals");
    expect(farmScope()).toBe("1");

    // Removed from the farm server-side while the tab stayed open.
    server.use(http.get("/api/auth/farms", () => HttpResponse.json([])));
    await user.click(screen.getByRole("button", { name: "refresh-farms" }));
    await waitFor(() =>
      expect(screen.getByTestId("farmId")).toHaveTextContent("none"),
    );

    await apiFetch("/api/animals");
    expect(farmScope()).toBeNull();
  });

  it("returns date-only business rules to the default zone when the last membership disappears", async () => {
    server.use(http.get("/api/auth/farms", () => HttpResponse.json(DESERT_FARM)));

    const user = userEvent.setup();
    renderWithProviders(<Probe />);
    await expectLoaded();
    expect(farmToday(INSTANT)).toBe("2026-08-09");

    server.use(http.get("/api/auth/farms", () => HttpResponse.json([])));
    await user.click(screen.getByRole("button", { name: "refresh-farms" }));
    await waitFor(() =>
      expect(screen.getByTestId("farmId")).toHaveTextContent("none"),
    );

    expect(farmToday(INSTANT)).toBe("2026-08-10");
  });
});

describe("AuthProvider teardown — previous-farm data never reaches the new scope", () => {
  /** Answers /api/animals with rows tagged by the X-Farm-Id that was on the
   *  request. One call is parked on demand so a response can still be on the
   *  wire when the farm transition happens; that one is tagged STALE. */
  function farmScopedAnimals(state: {
    park: boolean;
    started: number;
    aborted: boolean;
    release: () => void;
    parked: Promise<void>;
  }) {
    server.use(
      http.get("/api/animals", async ({ request }) => {
        const farm = request.headers.get("X-Farm-Id");
        if (!state.park) {
          return HttpResponse.json([{ id: 1, tag_number: `LIVE-${farm}` }]);
        }
        state.park = false;
        state.started += 1;
        request.signal.addEventListener("abort", () => {
          state.aborted = true;
        });
        await state.parked;
        return HttpResponse.json([{ id: 2, tag_number: `STALE-${farm}` }]);
      }),
    );
  }

  function parkState() {
    let release!: () => void;
    const parked = new Promise<void>((resolve) => {
      release = resolve;
    });
    return { park: false, started: 0, aborted: false, release, parked };
  }

  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
    setAccessToken(null);
    setCurrentFarmId(null);
  });

  it("aborts a farm-1 request still on the wire when the operator switches to farm 2", async () => {
    // Cache keys carry no farm id, so a response that left with the OLD
    // X-Farm-Id would otherwise settle into the fresh cache and render
    // another tenant's animals under the new farm's heading.
    const state = parkState();
    server.use(
      http.get("/api/auth/farms", () =>
        HttpResponse.json([
          { id: 1, name: "Farm One", location: null, role: null },
          { id: 2, name: "Farm Two", location: null, role: null },
        ]),
      ),
    );
    farmScopedAnimals(state);

    const rendered: string[] = [];
    const user = userEvent.setup();
    const { queryClient } = renderWithProviders(
      <>
        <Probe />
        <AnimalsPanel rendered={rendered} />
      </>,
    );
    await expectLoaded();
    await waitFor(() =>
      expect(screen.getByTestId("animals")).toHaveTextContent("LIVE-1"),
    );

    state.park = true;
    void queryClient.refetchQueries({ queryKey: ["/api/animals"] });
    await waitFor(() => expect(state.started).toBe(1));
    rendered.length = 0;

    await user.click(screen.getByRole("button", { name: "select-2" }));
    await act(async () => {
      state.release();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    expect(state.aborted).toBe(true);
    await waitFor(() =>
      expect(screen.getByTestId("animals")).toHaveTextContent("LIVE-2"),
    );
    expect(rendered.filter((tags) => tags.startsWith("STALE"))).toEqual([]);
  });

  it("aborts a request still on the wire when the last membership disappears", async () => {
    // Losing the final farm is a transition too: the parked response would
    // otherwise repopulate a cache the operator no longer has access to.
    const state = parkState();
    farmScopedAnimals(state);

    const rendered: string[] = [];
    const user = userEvent.setup();
    const { queryClient } = renderWithProviders(
      <>
        <Probe />
        <AnimalsPanel rendered={rendered} />
      </>,
    );
    await expectLoaded();
    await waitFor(() =>
      expect(screen.getByTestId("animals")).toHaveTextContent("LIVE-1"),
    );

    state.park = true;
    void queryClient.refetchQueries({ queryKey: ["/api/animals"] });
    await waitFor(() => expect(state.started).toBe(1));
    rendered.length = 0;

    server.use(http.get("/api/auth/farms", () => HttpResponse.json([])));
    await user.click(screen.getByRole("button", { name: "refresh-farms" }));
    await waitFor(() =>
      expect(screen.getByTestId("farmId")).toHaveTextContent("none"),
    );
    await act(async () => {
      state.release();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    expect(state.aborted).toBe(true);
    expect(rendered.filter((tags) => tags.startsWith("STALE"))).toEqual([]);
  });
});

describe("AuthProvider signOut — the returned promise tracks the revocation", () => {
  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
    setAccessToken(null);
    setCurrentFarmId(null);
  });

  it("stays pending until the server revocation settles", async () => {
    // Local teardown deliberately does not wait on the network, but callers
    // that await signOut() (the account dialog, e2e flows) are told the
    // server session is gone. Resolving early would make that a lie.
    let releaseLogout!: () => void;
    const parkedLogout = new Promise<void>((resolve) => {
      releaseLogout = resolve;
    });
    const order: string[] = [];
    let logoutStarted = false;
    server.use(
      http.post("/api/auth/logout", async () => {
        logoutStarted = true;
        await parkedLogout;
        order.push("revoked");
        return new HttpResponse(null, { status: 204 });
      }),
    );

    let signOut!: () => Promise<void>;
    renderWithProviders(
      <>
        <Probe />
        <SignOutCapture
          capture={(fn) => {
            signOut = fn;
          }}
        />
      </>,
    );
    await expectLoaded();

    let flight!: Promise<void>;
    await act(async () => {
      flight = signOut();
      void flight.then(() => order.push("settled"));
    });
    await waitFor(() => expect(logoutStarted).toBe(true));
    // Local state is already gone while the revocation is still on the wire.
    expect(screen.getByTestId("user")).toHaveTextContent("none");
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(order).toEqual([]);

    await act(async () => {
      releaseLogout();
      await flight;
    });
    expect(order).toEqual(["revoked", "settled"]);
  });

  it("lets the operator retry a sign-out whose revocation never reached the server", async () => {
    // The revocation is coalesced only while its flight is live. Once a
    // failed attempt has settled, a second click has to reach the server
    // again — otherwise the refresh cookie the first attempt failed to revoke
    // stays valid for the rest of its life with no way to try again.
    let logoutCalls = 0;
    server.use(
      http.post("/api/auth/logout", () => {
        logoutCalls += 1;
        return HttpResponse.error();
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<Probe />);
    await expectLoaded();

    await user.click(screen.getByRole("button", { name: "sign-out" }));
    await waitFor(() => expect(logoutCalls).toBe(1));
    await waitFor(() =>
      expect(screen.getByTestId("user")).toHaveTextContent("none"),
    );
    // Let the failed flight settle and release its coalescing slot.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    replaceMock.mockClear();

    await user.click(screen.getByRole("button", { name: "sign-out" }));

    await waitFor(() => expect(logoutCalls).toBe(2));
    expect(replaceMock).toHaveBeenCalledWith("/login");
  });
});
