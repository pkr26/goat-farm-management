/**
 * WorkerShell (the shared-device security boundary) unit tests — the shell
 * sat at ~15% coverage with its end-shift queue wipe, offline/queue badges,
 * Telugu-first default, signed-out gate and service-worker registration
 * never executed (2026-09-28 audit, T2). The duty board itself is covered by
 * page.test.tsx; these tests pin the chrome around it.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "@/lib/i18n";
import { currentRequestScope, setAccessToken, setCurrentFarmId } from "@/lib/api-client";
import type { WorkerOperation } from "@/lib/worker-outbox";
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
  clearBackoffMock,
  queueDepthMock,
  startWorkersMock,
  drainQueueMock,
  readOutboxMock,
  clearAcceptedMock,
  toastSuccessMock,
  toastErrorMock,
  swRegistration,
  swRegisterMock,
} = vi.hoisted(() => ({
  pushMock: vi.fn(),
  replaceMock: vi.fn(),
  signOutMock: vi.fn<() => Promise<void>>(() => Promise.resolve()),
  authState: {
    user: { id: 7, email: "pin@farm.in", name: "Pin Worker" } as
      | { id: number; email: string; name: string | null; must_change_password?: boolean }
      | null,
    farmId: 3 as number | null,
    loading: false,
  },
  navState: { pathname: "/worker" },
  wipeQueueMock: vi.fn(),
  clearBackoffMock: vi.fn(),
  queueDepthMock: vi.fn<
    (scopes?: { actorScope: string; farmScope: string }) => number
  >(() => 0),
  startWorkersMock: vi.fn(),
  drainQueueMock: vi.fn(),
  readOutboxMock: vi.fn(),
  clearAcceptedMock: vi.fn(),
  toastSuccessMock: vi.fn(),
  toastErrorMock: vi.fn(),
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
  clearOfflineQueueDrainBackoff: clearBackoffMock,
}));
vi.mock("@/lib/worker-outbox", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  startWorkerOutbox: startWorkersMock,
  readWorkerOutbox: readOutboxMock,
  clearAcceptedWorkerReceipts: clearAcceptedMock,
}));
vi.mock("sonner", async (importOriginal) => {
  const original = await importOriginal<typeof import("sonner")>();
  return { ...original, toast: { ...original.toast, success: toastSuccessMock, error: toastErrorMock } };
});

function pendingRecords(count: number): WorkerOperation[] {
  return Array.from({ length: count }, (_, index) => ({
    id: String(index), path: `/api/tasks/${index + 1}/complete`, method: "POST", body: null,
    idempotencyKey: `key-${index}`, queuedAt: Date.now(), state: "pending", actorScope: "7", farmScope: "3",
  }));
}

beforeAll(() => {
  swRegisterMock.mockResolvedValue(swRegistration);
  Object.defineProperty(navigator, "serviceWorker", {
    value: { register: swRegisterMock },
    configurable: true,
  });
});

beforeEach(() => {
  setAccessToken("worker-test-token", 7); setCurrentFarmId("3");
  server.use(
    http.post("/api/auth/refresh", () => HttpResponse.json({ access_token: "worker-test-token", user: { id: 7, email: "pin@farm.in", name: "Pin Worker", must_change_password: false } })),
    http.get("/api/auth/farms", () => HttpResponse.json([{ id: 3, name: "Tablet Farm", location: null, timezone: "Asia/Kolkata", role: null }])),
  );
  pushMock.mockClear();
  replaceMock.mockClear();
  signOutMock.mockClear();
  authState.user = { id: 7, email: "pin@farm.in", name: "Pin Worker" };
  authState.farmId = 3;
  authState.loading = false;
  navState.pathname = "/worker";
  wipeQueueMock.mockClear();
  clearBackoffMock.mockClear();
  queueDepthMock.mockReset().mockReturnValue(0);
  startWorkersMock.mockClear();
  startWorkersMock.mockImplementation((getScopes: () => { actorScope: string; farmScope: string } | null, onChange: (records: WorkerOperation[]) => void) => {
    const scope = getScopes();
    if (scope) onChange(pendingRecords(queueDepthMock({ actorScope: scope.actorScope, farmScope: scope.farmScope })));
    return vi.fn();
  });
  readOutboxMock.mockReset().mockImplementation(async () => pendingRecords(queueDepthMock()));
  clearAcceptedMock.mockReset().mockResolvedValue(2);
  toastSuccessMock.mockClear(); toastErrorMock.mockClear();
  drainQueueMock.mockClear();
  swRegistration.update.mockClear();
  swRegisterMock.mockClear();
  window.localStorage.clear();
});

function renderShell(children = <p>duty board</p>) {
  return renderWithProviders(<WorkerShell>{children}</WorkerShell>, createTestQueryClient());
}

function acceptedReceipts(): WorkerOperation[] {
  return pendingRecords(2).map((record, index) => ({ ...record, id: `accepted-${index}`, state: "sent" }));
}

function showAcceptedReceipts(): WorkerOperation[] {
  const records = [pendingRecords(1)[0], ...acceptedReceipts()];
  startWorkersMock.mockImplementation((_getScope: unknown, onChange: (items: WorkerOperation[]) => void) => {
    onChange(records); return vi.fn();
  });
  readOutboxMock.mockResolvedValue(records);
  return records;
}

describe("WorkerShell accepted receipt cleanup", () => {
  it("confirms the exact accepted count and clears once only after explicit consent", async () => {
    const records = showAcceptedReceipts();
    let complete!: (count: number) => void;
    clearAcceptedMock.mockImplementation(() => new Promise<number>((resolve) => { complete = resolve; }));
    const user = userEvent.setup(); renderShell();
    await user.click(await screen.findByText("Saved duties and delivery receipts"));
    await user.click(screen.getByTestId("worker-clear-accepted"));
    const dialog = await screen.findByRole("alertdialog", { name: "Clear accepted receipts?" });
    expect(within(dialog).getByText(/Remove 2 accepted receipts/)).toHaveTextContent("Pending duties and duties needing review will stay saved.");
    expect(clearAcceptedMock).not.toHaveBeenCalled();
    await user.click(within(dialog).getByTestId("worker-clear-accepted-cancel"));
    expect(clearAcceptedMock).not.toHaveBeenCalled();
    await user.click(screen.getByTestId("worker-clear-accepted"));
    const confirm = await screen.findByTestId("worker-clear-accepted-confirm");
    fireEvent.click(confirm); fireEvent.click(confirm);
    expect(clearAcceptedMock).toHaveBeenCalledTimes(1);
    expect(clearAcceptedMock).toHaveBeenCalledWith(currentRequestScope(), ["accepted-0", "accepted-1"]);
    expect(confirm).toBeDisabled();
    expect(toastSuccessMock).not.toHaveBeenCalled();
    complete(2);
    await waitFor(() => expect(toastSuccessMock).toHaveBeenCalledWith("2 accepted receipts cleared."));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.queryByTestId("worker-clear-accepted")).not.toBeInTheDocument();
    expect(screen.getByTestId("worker-receipts")).toHaveTextContent("Waiting to send");
    expect(records[0].state).toBe("pending");
  });

  it("keeps receipts and the confirmation when cleanup storage fails", async () => {
    showAcceptedReceipts(); clearAcceptedMock.mockRejectedValue(new Error("transaction aborted"));
    const user = userEvent.setup(); renderShell();
    await user.click(await screen.findByText("Saved duties and delivery receipts"));
    await user.click(screen.getByTestId("worker-clear-accepted"));
    await user.click(await screen.findByTestId("worker-clear-accepted-confirm"));
    await waitFor(() => expect(toastErrorMock).toHaveBeenCalledWith("Receipts could not be cleared. Your saved duties are still on this tablet."));
    expect(toastSuccessMock).not.toHaveBeenCalled();
    expect(screen.getByRole("alertdialog", { name: "Clear accepted receipts?" })).toBeInTheDocument();
    expect(screen.getByTestId("worker-receipts")).toHaveTextContent("Accepted by the server");
    expect(screen.getByTestId("worker-receipts")).toHaveTextContent("Waiting to send");
  });

  it.each(["new identity", "same farm leave and return"])("an old confirmation cannot clear receipts after %s", async (change) => {
    showAcceptedReceipts(); const user = userEvent.setup(); renderShell();
    await user.click(await screen.findByText("Saved duties and delivery receipts"));
    await user.click(screen.getByTestId("worker-clear-accepted"));
    const confirm = await screen.findByTestId("worker-clear-accepted-confirm");
    if (change === "new identity") setAccessToken("new-worker", 8);
    else { setCurrentFarmId("4"); setCurrentFarmId("3"); }
    fireEvent.click(confirm);
    expect(clearAcceptedMock).not.toHaveBeenCalled();
    expect(toastSuccessMock).not.toHaveBeenCalled();
  });

  it("does not open an old worker's confirmation when its pending receipt read resolves after handover", async () => {
    const records = showAcceptedReceipts();
    let complete!: (items: WorkerOperation[]) => void;
    readOutboxMock.mockImplementation(() => new Promise<WorkerOperation[]>((resolve) => { complete = resolve; }));
    const user = userEvent.setup(); renderShell();
    await user.click(await screen.findByText("Saved duties and delivery receipts"));
    const button = screen.getByTestId("worker-clear-accepted");
    fireEvent.click(button); fireEvent.click(button);
    expect(readOutboxMock).toHaveBeenCalledTimes(1);
    setAccessToken("replacement-worker", 8);
    complete(records);
    await waitFor(() => expect(button).not.toBeDisabled());
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(clearAcceptedMock).not.toHaveBeenCalled();
  });

  it("does not apply an old cleanup result or success toast to a newer identity", async () => {
    showAcceptedReceipts();
    let complete!: (count: number) => void;
    clearAcceptedMock.mockImplementation(() => new Promise<number>((resolve) => { complete = resolve; }));
    const user = userEvent.setup(); renderShell();
    await user.click(await screen.findByText("Saved duties and delivery receipts"));
    await user.click(screen.getByTestId("worker-clear-accepted"));
    const confirm = await screen.findByTestId("worker-clear-accepted-confirm");
    fireEvent.click(confirm);
    setAccessToken("replacement-worker", 8); complete(2);
    await waitFor(() => expect(confirm).not.toBeDisabled());
    expect(toastSuccessMock).not.toHaveBeenCalled();
    expect(toastErrorMock).not.toHaveBeenCalled();
  });
});

describe("WorkerShell chrome", () => {
  it("keeps account password actions unavailable for ordinary PIN workers", async () => {
    renderShell();
    expect(await screen.findByTestId("worker-identity")).toHaveTextContent("Pin Worker");
    expect(screen.queryByTestId("worker-change-password")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Account/ })).not.toBeInTheDocument();
  });

  it("lets a password worker rotate a temporary password from a 44px header action", async () => {
    authState.user = { id: 7, email: "worker@farm.in", name: "Password Worker", must_change_password: true };
    const passwordChanges: unknown[] = [];
    server.use(http.post("/api/auth/change-password", async ({ request }) => {
      passwordChanges.push(await request.json());
      return HttpResponse.json({ access_token: "rotated-worker-token" });
    }));
    const user = userEvent.setup();
    renderShell();
    const trigger = await screen.findByTestId("worker-change-password");
    expect(trigger).toHaveAccessibleName("Change password");
    expect(trigger).toHaveStyle({ minHeight: "44px" });
    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "Account & password" });
    expect(within(dialog).queryByRole("button", { name: /export|delete account|enable two-factor/i })).not.toBeInTheDocument();
    await user.type(within(dialog).getByLabelText("Current password for password change"), "temporary-password");
    await user.type(within(dialog).getByLabelText("New password"), "replacement-password");
    await user.type(within(dialog).getByLabelText("Confirm new password"), "replacement-password");
    await user.click(within(dialog).getByRole("button", { name: "Change password" }));
    await waitFor(() => expect(passwordChanges).toEqual([{
      current_password: "temporary-password", new_password: "replacement-password",
    }]));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(signOutMock).not.toHaveBeenCalled();
  });

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
    // The third argument is the live-session probe: a slow drain that
    // outlives its login stops instead of replaying under the next actor
    // (2026-10-02 audit) — so the shell must hand over its scopes reader.
    const probe = startWorkersMock.mock.calls[0]?.[0] as () => unknown;
    expect(probe()).toMatchObject({ actorScope: "7", farmScope: "3" });
    setAccessToken("replacement-worker", 8);
    expect(probe()).toBeNull();
  });

  it("keeps pending-duty replay gated until required password rotation finishes", async () => {
    authState.user = { id: 7, email: "worker@farm.in", name: "Password Worker", must_change_password: true };
    const view = renderShell();
    await screen.findByTestId("worker-change-password");
    const lockedGate = startWorkersMock.mock.calls.at(-1)?.[3] as () => boolean;
    expect(lockedGate()).toBe(false);
    authState.user = { ...authState.user, must_change_password: false };
    view.rerender(<WorkerShell><p>duty board</p></WorkerShell>);
    await waitFor(() => {
      const releasedGate = startWorkersMock.mock.calls.at(-1)?.[3] as () => boolean;
      expect(releasedGate()).toBe(true);
    });
  });

  it("shows the queue-depth badge only while records wait to send", async () => {
    queueDepthMock.mockReturnValue(2);
    renderShell();

    expect(await screen.findByTestId("worker-queue-depth")).toHaveTextContent(
      "2 saved — will send when online",
    );
  });

  it("counts the badge with the same scope filter the drain uses (2026-10-01 audit, 07-L3)", async () => {
    // A foreign actor's or farm's residue must not inflate the badge (or the
    // end-shift confirm, which reads the same depth): the shell passes the
    // session's scopes into offlineQueueDepth instead of counting the whole
    // store.
    queueDepthMock.mockReturnValue(1);
    renderShell();

    await screen.findByTestId("end-shift");
    expect(queueDepthMock).toHaveBeenCalledWith({
      actorScope: "7",
      farmScope: "3",
    });
    expect(await screen.findByTestId("worker-queue-depth")).toHaveTextContent(
      "1 saved — will send when online",
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
  it("an old confirmation cannot end a new worker's session", async () => {
    const user = userEvent.setup();
    queueDepthMock.mockReturnValue(1);
    renderShell();
    await user.click(await screen.findByTestId("end-shift"));
    const confirm = await screen.findByTestId("end-shift-confirm");
    setAccessToken("new-worker-token", 8);
    fireEvent.click(confirm);
    expect(signOutMock).not.toHaveBeenCalled();
  });
  it("reads the current committed queue at click time even when the badge was empty", async () => {
    const user = userEvent.setup();
    renderShell();
    await screen.findByTestId("end-shift");
    expect(screen.queryByTestId("worker-queue-depth")).not.toBeInTheDocument();
    queueDepthMock.mockReturnValue(1);
    await user.click(screen.getByTestId("end-shift"));
    expect(await screen.findByText("Unsent duties")).toBeInTheDocument();
    expect(signOutMock).not.toHaveBeenCalled();
    expect(wipeQueueMock).not.toHaveBeenCalled();
  });
  it("preserves queued writes, signs out and returns to the PIN pad", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(await screen.findByTestId("end-shift"));

    // Shared-device handover: the departing worker's queued writes must not
    // leak into the next session, and the tablet goes back to the PIN pad
    // (never the manager's /login form).
    await waitFor(() => expect(signOutMock).toHaveBeenCalled());
    expect(wipeQueueMock).not.toHaveBeenCalled();
    expect(replaceMock).toHaveBeenCalledWith("/worker/login");
  });

  it("clears the 429 drain backoff on end shift (M1: next actor starts un-gated)", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(await screen.findByTestId("end-shift"));

    await waitFor(() => expect(signOutMock).toHaveBeenCalled());
    expect(readOutboxMock).toHaveBeenCalledWith(currentRequestScope());
  });

  it("explains retained pending work before handover", async () => {
    const user = userEvent.setup();
    queueDepthMock.mockReturnValue(2);
    renderShell();

    await user.click(await screen.findByTestId("end-shift"));

    // The confirm opens with the queued count; nothing is wiped or signed
    // out until the worker explicitly chooses to discard.
    expect(await screen.findByText("Unsent duties")).toBeInTheDocument();
    expect(
      screen.getByText("2 saved duties have not been sent yet. They will stay on this tablet for you to send or review after signing in again."),
    ).toBeInTheDocument();
    expect(signOutMock).not.toHaveBeenCalled();
    expect(wipeQueueMock).not.toHaveBeenCalled();

    await user.click(await screen.findByTestId("end-shift-confirm"));

    await waitFor(() => expect(signOutMock).toHaveBeenCalled());
    expect(wipeQueueMock).not.toHaveBeenCalled();
    expect(replaceMock).toHaveBeenCalledWith("/worker/login");
  });

  it("keeps the queue and session when the worker cancels the discard", async () => {
    const user = userEvent.setup();
    queueDepthMock.mockReturnValue(1);
    renderShell();

    await user.click(await screen.findByTestId("end-shift"));

    // Singular copy for exactly one queued record (L7).
    expect(
      await screen.findByText("1 saved duty has not been sent yet. It will stay on this tablet for you to send or review after signing in again."),
    ).toBeInTheDocument();
    await user.click(await screen.findByTestId("end-shift-cancel"));

    await waitFor(() =>
      expect(screen.queryByText("Unsent duties")).not.toBeInTheDocument(),
    );
    expect(signOutMock).not.toHaveBeenCalled();
    expect(wipeQueueMock).not.toHaveBeenCalled();
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
  it.each(["/worker/login", "/worker/offline"])("keeps %s children mounted across session transitions without worker chrome", async (pathname) => {
    navState.pathname = pathname;
    authState.user = null;
    const children = <label>Setup continuity<input defaultValue="manager@farm.in" /></label>;
    const view = renderShell(children);
    const input = await screen.findByLabelText("Setup continuity");
    authState.user = { id: 7, email: "manager@farm.in", name: "Manager" };
    view.rerender(<WorkerShell>{children}</WorkerShell>);
    expect(screen.getByLabelText("Setup continuity")).toBe(input);
    expect(input).toHaveValue("manager@farm.in");
    expect(screen.queryByTestId("worker-identity")).not.toBeInTheDocument();
    expect(screen.queryByTestId("end-shift")).not.toBeInTheDocument();
    expect(signOutMock).not.toHaveBeenCalled();
  });

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
