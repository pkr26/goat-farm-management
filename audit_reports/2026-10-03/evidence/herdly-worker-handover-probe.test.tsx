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
  clearBackoffMock,
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
  clearBackoffMock: vi.fn(),
  queueDepthMock: vi.fn<
    (scopes?: { actorScope: string; farmScope: string }) => number
  >(() => 0),
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
  clearOfflineQueueDrainBackoff: clearBackoffMock,
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
  clearBackoffMock.mockClear();
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


it("audit: a newly queued write is erased before the badge tick without confirmation", () => {
  queueDepthMock.mockReturnValue(0);
  renderShell();
  expect(screen.queryByTestId("worker-queue-depth")).toBeNull();
  // Another component enqueued after this shell's last depth read.
  queueDepthMock.mockReturnValue(1);
  fireEvent.click(screen.getByTestId("end-shift"));
  expect(wipeQueueMock).toHaveBeenCalledTimes(1);
  expect(signOutMock).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("alertdialog")).toBeNull();
});
