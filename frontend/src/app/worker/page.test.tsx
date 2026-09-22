/**
 * Worker tablet pages (ITEM 2 Phase 2): the PIN-pad login (roster → tap →
 * PIN → worker-login → signIn) and the duty board's offline-aware completion.
 */

import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";
import { OFFLINE_QUEUE_STORAGE_KEY } from "@/lib/offline-queue";

import WorkerLoginPage from "./login/page";
import WorkerBoardPage from "./page";

const { pushMock, replaceMock } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  replaceMock: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => "/worker",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const signIn = vi.fn();

vi.mock("@/lib/auth-context", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useAuth: () => ({
    signIn,
    signOut: vi.fn(),
    user: { id: 7, email: "pin@farm.in", name: "Pin Worker" },
    farmId: 3,
    farms: [{ id: 3, name: "Tablet Farm", location: null, timezone: "Asia/Kolkata", role: null }],
    loading: false,
    selectFarm: vi.fn(),
    refreshFarms: vi.fn(),
    updateUser: vi.fn(),
    getFarms: vi.fn(() => []),
  }),
}));

const ROSTER = {
  items: [
    { membership_id: 11, display_name: "Lakshmi" },
    { membership_id: 12, display_name: "Ravi" },
  ],
};

const BOARD = (id: number, title: string, due: string) => ({
  id,
  title,
  due_date: due,
  category: "OTHER",
  status: "PENDING",
  priority: null,
  notes: null,
  animal_id: null,
  animal_tag: null,
  assigned_role_id: null,
  assigned_user_id: 7,
  assigned_role_name: null,
  breeding_record_id: null,
  purchase_batch_id: null,
  recurring_series_id: null,
  recur_days: null,
  auto_generated: false,
  action_url: null,
  completed_by_id: null,
  completed_at: null,
  verified_by_id: null,
  verified_at: null,
  verification_note: null,
  rejected_by_id: null,
  rejected_at: null,
  skip_reason: null,
  skipped_by_id: null,
  skipped_at: null,
  created_at: "2026-09-20T05:00:00Z",
  updated_at: "2026-09-20T05:00:00Z",
  title_key: null,
  title_args: {},
});

function today() {
  return new Date().toISOString().slice(0, 10);
}

beforeEach(() => {
  pushMock.mockClear();
  replaceMock.mockClear();
  signIn.mockReset();
  window.localStorage.clear();
});

describe("WorkerLoginPage", () => {
  it("pins the tablet farm, lists names, and signs in by PIN", async () => {
    window.localStorage.setItem("herdly.tabletFarm", "3");
    server.use(
      http.get("/api/auth/worker-roster", () => HttpResponse.json(ROSTER)),
    );
    server.use(
      http.post("/api/auth/worker-login", async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        expect(body).toEqual({ farm_id: 3, membership_id: 11, pin: "4321" });
        return HttpResponse.json({
          access_token: "tablet-token",
          token_type: "bearer",
          user: { id: 7, email: "pin@farm.in", name: "Lakshmi", must_change_password: false },
        });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<WorkerLoginPage />, createTestQueryClient());

    await user.click(await screen.findByRole("button", { name: "Lakshmi" }));
    for (const digit of ["4", "3", "2", "1"]) {
      await user.click(screen.getByRole("button", { name: new RegExp(`pin ${digit}`, "i") }));
    }
    await user.click(screen.getByTestId("pin-sign-in"));

    await waitFor(() => expect(signIn).toHaveBeenCalled());
    expect(replaceMock).toHaveBeenCalledWith("/worker");
  });

  it("shows the no-farm setup state when the tablet is unpinned", async () => {
    renderWithProviders(<WorkerLoginPage />, createTestQueryClient());
    expect(
      await screen.findByText("No farm on this tablet"),
    ).toBeInTheDocument();
  });

  it("answers a wrong PIN with its own message", async () => {
    window.localStorage.setItem("herdly.tabletFarm", "3");
    server.use(
      http.get("/api/auth/worker-roster", () => HttpResponse.json(ROSTER)),
      http.post("/api/auth/worker-login", () =>
        HttpResponse.json({ detail: "Invalid PIN." }, { status: 401 }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<WorkerLoginPage />, createTestQueryClient());
    await user.click(await screen.findByRole("button", { name: "Ravi" }));
    for (const digit of ["1", "2", "3", "4"]) {
      fireEvent.click(
        screen.getByRole("button", { name: new RegExp(`pin ${digit}`, "i") }),
      );
    }
    await user.click(screen.getByTestId("pin-sign-in"));
    expect(await screen.findByText("Wrong PIN — try again.")).toBeInTheDocument();
    expect(signIn).not.toHaveBeenCalled();
  });
});

describe("WorkerBoardPage", () => {
  function renderBoard() {
    return renderWithProviders(<WorkerBoardPage />, createTestQueryClient());
  }

  it("renders overdue and today duties as large cards", async () => {
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json({
          today: [BOARD(1, "Feed the bucks", today())],
          overdue: [BOARD(2, "Clean water trough", "2026-09-01")],
          upcoming: [],
          awaiting: [],
          completed: [],
          totals: { today: 1, overdue: 1, upcoming: 0, awaiting: 0, completed: 0 },
        }),
      ),
    );
    renderBoard();

    expect(await screen.findByText("Feed the bucks")).toBeInTheDocument();
    expect(screen.getByText("Clean water trough")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Overdue \(1\)/ })).toBeInTheDocument();
    expect(screen.getByTestId("complete-1")).toBeInTheDocument();
  });

  it("completes a duty and refetches", async () => {
    let completed = 0;
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json({
          today: [BOARD(1, "Feed the bucks", today())],
          overdue: [],
          upcoming: [],
          awaiting: [],
          completed: [],
          totals: { today: 1, overdue: 0, upcoming: 0, awaiting: 0, completed: 0 },
        }),
      ),
      http.post("/api/tasks/1/complete", ({ request }) => {
        completed += 1;
        expect(request.headers.get("Idempotency-Key")).toMatch(/.+/);
        return HttpResponse.json(BOARD(1, "Feed the bucks", today()));
      }),
    );
    const user = userEvent.setup();
    renderBoard();

    await user.click(await screen.findByTestId("complete-1"));
    await waitFor(() => expect(completed).toBe(1));
  });

  it("queues the completion when the network fails, with the same key", async () => {
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json({
          today: [BOARD(1, "Feed the bucks", today())],
          overdue: [],
          upcoming: [],
          awaiting: [],
          completed: [],
          totals: { today: 1, overdue: 0, upcoming: 0, awaiting: 0, completed: 0 },
        }),
      ),
      // HttpResponse.error() is the MSW-native network failure (a thrown
      // TypeError inside a handler would surface as a 500 RESPONSE).
      http.post("/api/tasks/1/complete", () => HttpResponse.error()),
    );
    const user = userEvent.setup();
    renderBoard();

    await user.click(await screen.findByTestId("complete-1"));
    await waitFor(() =>
      expect(
        JSON.parse(window.localStorage.getItem(OFFLINE_QUEUE_STORAGE_KEY) ?? "[]"),
      ).toHaveLength(1),
    );
    const record = JSON.parse(
      window.localStorage.getItem(OFFLINE_QUEUE_STORAGE_KEY) ?? "[]",
    )[0];
    expect(record.path).toBe("/api/tasks/1/complete");
    expect(record.headers["Idempotency-Key"]).toMatch(/.+/);
    expect(record.actorScope).toBe("7");
    expect(record.farmScope).toBe("3");
  });

  it("shows the empty state for a clear board", async () => {
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json({
          today: [],
          overdue: [],
          upcoming: [],
          awaiting: [],
          completed: [],
          totals: { today: 0, overdue: 0, upcoming: 0, awaiting: 0, completed: 0 },
        }),
      ),
    );
    renderBoard();
    expect(await screen.findByText("No duties right now")).toBeInTheDocument();
  });
});
