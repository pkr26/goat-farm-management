/**
 * WorkerShell (the shared-device security boundary) unit tests — the shell
 * sat at ~15% coverage with its end-shift queue wipe, offline/queue badges,
 * Telugu-first default, signed-out gate and service-worker registration
 * never executed (2026-09-28 audit, T2). The duty board itself is covered by
 * page.test.tsx; these tests pin the chrome around it.
 */

import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "@/lib/i18n";
import { server } from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";

import WorkerLayout, {
  readTabletFarmId,
  TABLET_FARM_STORAGE_KEY,
  WorkerShell,
  writeTabletFarmId,
} from "./layout";

const {
  pushMock,
  replaceMock,
  signOutMock,
  authState,
  navState,
  wipeQueueMock,
  queueDepthMock,
  startWorkersMock,
  drainQueueMock,
  swRegistration,
  swRegisterMock,
} = vi.hoisted(() => ({
  pushMock: vi.fn(),
  replaceMock: vi.fn(),
  signOutMock: vi.fn<() => Promise<void>>(() => Promise.resolve()),
  authState: {
    user: { id: 7, email: "pin@farm.in", name: "Pin Worker" } as
      | { id: number; email: string; name: string | null }
      | null,
    farmId: 3 as number | null,
    loading: false,
  },
  navState: { pathname: "/worker" },
  wipeQueueMock: vi.fn(),
  queueDepthMock: vi.fn<() => number>(() => 0),
  startWorkersMock: vi.fn(() => vi.fn()),
  drainQueueMock: vi.fn(),
  swRegistration: { update: vi.fn<() => Promise<void>>(() => Promise.resolve()) },
  swRegisterMock: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => navState.pathname,
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

vi.mock("@/lib/auth-context", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useAuth: () => ({
    signIn: vi.fn(),
    signOut: signOutMock,
    user: authState.user,
    farmId: authState.farmId,
    farms: [{ id: 3, name: "Tablet Farm", location: null, timezone: "Asia/Kolkata", role: null }],
    loading: authState.loading,
    selectFarm: vi.fn(),
    refreshFarms: vi.fn(),
    updateUser: vi.fn(),
    getFarms: () => [],
  }),
}));

// The queue itself is covered by offline-queue.test.ts; the shell only needs
// its four entry points observed (and the badge depth driven per test).
vi.mock("@/lib/offline-queue", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  drainOfflineQueue: drainQueueMock,
  offlineQueueDepth: queueDepthMock,
  startOfflineQueueWorkers: startWorkersMock,
  wipeOfflineQueue: wipeQueueMock,
}));

beforeAll(() => {
  swRegisterMock.mockResolvedValue(swRegistration);
  Object.defineProperty(navigator, "serviceWorker", {
    value: { register: swRegisterMock },
    configurable: true,
  });
});

beforeEach(() => {
  pushMock.mockClear();
  replaceMock.mockClear();
  signOutMock.mockClear();
  authState.user = { id: 7, email: "pin@farm.in", name: "Pin Worker" };
  authState.farmId = 3;
  authState.loading = false;
  navState.pathname = "/worker";
  wipeQueueMock.mockClear();
  queueDepthMock.mockReset().mockReturnValue(0);
  startWorkersMock.mockClear();
  drainQueueMock.mockClear();
  swRegistration.update.mockClear();
  swRegisterMock.mockClear();
  window.localStorage.clear();
});

function renderShell(children = <p>duty board</p>) {
  return renderWithProviders(<WorkerShell>{children}</WorkerShell>, createTestQueryClient());
}

describe("WorkerShell chrome", () => {
  it("renders the farm, the worker's identity and the board", async () => {
    // Through the default export (the layout Next mounts), not WorkerShell
    // directly, so the wrapper itself is exercised too.
    renderWithProviders(<WorkerLayout>{<p>duty board</p>}</WorkerLayout>, createTestQueryClient());

    expect(await screen.findByText("Tablet Farm")).toBeInTheDocument();
    expect(screen.getByTestId("worker-identity")).toHaveTextContent("Pin Worker");
    expect(screen.getByText("duty board")).toBeInTheDocument();
  });

  it("starts the drain workers scoped to the signed-in worker and farm", async () => {
    renderShell();

    await screen.findByTestId("end-shift");
    expect(startWorkersMock).toHaveBeenCalled();
    expect(drainQueueMock).toHaveBeenCalledWith({ actorScope: "7", farmScope: "3" });
  });

  it("shows the queue-depth badge only while records wait to send", async () => {
    queueDepthMock.mockReturnValue(2);
    renderShell();

    expect(await screen.findByTestId("worker-queue-depth")).toHaveTextContent(
      "2 saved — will send when online",
    );
  });

  it("shows the offline badge from first paint when the tablet is offline", async () => {
    const onLine = vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    try {
      renderShell();
      expect(await screen.findByText("Offline")).toBeInTheDocument();
    } finally {
      onLine.mockRestore();
    }
  });

  it("flips the offline badge on the browser's offline/online events", async () => {
    renderShell();
    await screen.findByTestId("end-shift");
    expect(screen.queryByText("Offline")).not.toBeInTheDocument();

    fireEvent(window, new Event("offline"));
    expect(await screen.findByText("Offline")).toBeInTheDocument();
    fireEvent(window, new Event("online"));
    await waitFor(() => expect(screen.queryByText("Offline")).not.toBeInTheDocument());
  });

  it("surfaces a permissions failure instead of a silent header", async () => {
    server.use(
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ detail: "permissions unavailable" }, { status: 503 }),
      ),
    );
    renderShell();

    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
  });

  it("gives End shift the worker surface's ≥44px touch floor (W9)", async () => {
    // The default Button height is h-9 (36px), below the documented ≥44px
    // worker-surface floor; jsdom does not apply Tailwind, so pin the class
    // that produces the size (2026-09-28 audit, W9).
    renderShell();

    expect(await screen.findByTestId("end-shift")).toHaveClass("h-11");
  });
});

describe("WorkerShell end shift", () => {
  it("wipes the queued writes, signs out and returns to the PIN pad", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(await screen.findByTestId("end-shift"));

    // Shared-device handover: the departing worker's queued writes must not
    // leak into the next session, and the tablet goes back to the PIN pad
    // (never the manager's /login form).
    await waitFor(() => expect(signOutMock).toHaveBeenCalled());
    expect(wipeQueueMock).toHaveBeenCalled();
    expect(wipeQueueMock.mock.invocationCallOrder[0]).toBeLessThan(
      signOutMock.mock.invocationCallOrder[0],
    );
    expect(replaceMock).toHaveBeenCalledWith("/worker/login");
  });
});

describe("WorkerShell language default", () => {
  function renderShellWithLanguage() {
    return renderWithProviders(
      <LanguageProvider>
        <WorkerShell>{null}</WorkerShell>
      </LanguageProvider>,
      createTestQueryClient(),
    );
  }

  it("defaults to Telugu on first mount when no language was ever chosen", async () => {
    renderShellWithLanguage();

    // Telugu-first: field workers are the primary audience of this surface.
    await waitFor(() =>
      expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("te"),
    );
    await waitFor(() => expect(document.documentElement.lang).toBe("te"));
  });

  it("keeps a manager's explicit language choice", async () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "en");
    renderShellWithLanguage();

    await screen.findByTestId("end-shift");
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("en");
    expect(document.documentElement.lang).toBe("en");
  });
});

describe("WorkerShell session gate", () => {
  it("sends a signed-out session to the /worker/login PIN pad (W1)", async () => {
    authState.user = null;
    authState.farmId = null;
    renderShell();

    // PIN-only workers hold no password, so the gate aims at the PIN pad,
    // never the manager's email/password form (2026-09-28 audit, W1).
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/worker/login"));
    // The login page renders inside the shell too — the chrome just hides.
    expect(screen.getByText("duty board")).toBeInTheDocument();
    expect(screen.queryByTestId("end-shift")).not.toBeInTheDocument();
  });

  it("does not redirect away from /worker/login itself", async () => {
    authState.user = null;
    authState.farmId = null;
    navState.pathname = "/worker/login";
    renderShell();

    await screen.findByText("duty board");
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("holds the gate while the session bootstrap is pending", async () => {
    authState.loading = true;
    renderShell();

    expect(await screen.findByRole("status")).toHaveTextContent("Loading…");
    expect(replaceMock).not.toHaveBeenCalled();
  });
});

describe("WorkerShell service worker", () => {
  it("registers /sw.js and forces an update check on every mount (H1)", async () => {
    renderShell();

    await screen.findByTestId("end-shift");
    expect(swRegisterMock).toHaveBeenCalledWith("/sw.js");
    // The browser otherwise throttles sw.js revalidation to ~once per 24h,
    // which pinned tablets to their install-time build (2026-09-28 audit, H1).
    await waitFor(() => expect(swRegistration.update).toHaveBeenCalled());
  });

  it("keeps running when the update check fails (offline tablet)", async () => {
    swRegistration.update.mockRejectedValueOnce(new Error("offline"));
    renderShell();

    await screen.findByTestId("end-shift");
    await waitFor(() => expect(swRegistration.update).toHaveBeenCalled());
    expect(screen.getByTestId("end-shift")).toBeInTheDocument();
  });
});

describe("tablet farm pin storage", () => {
  it("reads a pinned farm id and rejects missing, corrupt or non-positive values", () => {
    expect(readTabletFarmId()).toBeNull();
    window.localStorage.setItem(TABLET_FARM_STORAGE_KEY, "3");
    expect(readTabletFarmId()).toBe(3);
    window.localStorage.setItem(TABLET_FARM_STORAGE_KEY, "not-a-number");
    expect(readTabletFarmId()).toBeNull();
    window.localStorage.setItem(TABLET_FARM_STORAGE_KEY, "-2");
    expect(readTabletFarmId()).toBeNull();
  });

  it("a blocked store reads as unpinned and never throws on write", () => {
    const getter = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage blocked");
    });
    try {
      expect(readTabletFarmId()).toBeNull();
    } finally {
      getter.mockRestore();
    }
    const setter = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("storage blocked");
    });
    try {
      expect(() => writeTabletFarmId(3)).not.toThrow();
    } finally {
      setter.mockRestore();
    }
  });
});
