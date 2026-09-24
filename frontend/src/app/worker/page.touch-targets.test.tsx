/**
 * Touch-target floor (2026-09-23 verification plan, category 15): every
 * interactive control on the worker tablet's duty board must carry a
 * ≥44 px hit area (min-h-11 = 2.75rem = 44px in the Tailwind scale). The
 * tablet is operated with fingers in a shed; a 32 px control is a
 * mis-tap generator. jsdom does not apply Tailwind, so the assertion pins
 * the classes that produce the size.
 */

import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createTestQueryClient, renderWithProviders } from "@/test/render";
import { server } from "@/test/msw-server";

import WorkerBoardPage from "./page";

const { pushMock, replaceMock, signOutMock, selectFarmMock, getFarmsMock } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  replaceMock: vi.fn(),
  signOutMock: vi.fn(),
  selectFarmMock: vi.fn(),
  getFarmsMock: vi.fn<() => { id: number; name: string; location: null; timezone: string; role: null }[]>(() => []),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => "/worker",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

vi.mock("@/lib/auth-context", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useAuth: () => ({
    signIn: vi.fn(),
    signOut: signOutMock,
    user: { id: 7, email: "pin@farm.in", name: "Pin Worker" },
    farmId: 3,
    farms: [{ id: 3, name: "Tablet Farm", location: null, timezone: "Asia/Kolkata", role: null }],
    loading: false,
    selectFarm: selectFarmMock,
    refreshFarms: vi.fn(),
    updateUser: vi.fn(),
    getFarms: getFarmsMock,
  }),
}));

const BOARD = (id: number, title: string) => ({
  id,
  title,
  due_date: new Date().toISOString().slice(0, 10),
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

beforeEach(() => {
  window.localStorage.clear();
  server.use(
    http.get("/api/tasks", () =>
      HttpResponse.json({
        today: [BOARD(1, "Feed the bucks"), BOARD(2, "Water trough check")],
        overdue: [BOARD(3, "Yesterday's spray round")],
        upcoming: [],
        awaiting: [],
        completed: [],
      }),
    ),
  );
});

const FORTY_FOUR_PX = /(^|\s)(min-h-11|min-h-\[(?:4[4-9]|[5-9][0-9])px\]|h-11)(\s|$)/;

describe("worker duty board touch targets", () => {
  it("gives every interactive control a >=44px hit area", async () => {
    renderWithProviders(<WorkerBoardPage />, createTestQueryClient());
    const buttons = await screen.findAllByRole("button");
    expect(buttons.length).toBeGreaterThan(0);
    const undersized = buttons.filter(
      (button) => !FORTY_FOUR_PX.test(button.className ?? ""),
    );
    expect(
      undersized.map((b) => `${b.textContent ?? b.getAttribute("data-testid")}:${b.className}`),
      "buttons without a >=44px class (min-h-11): ",
    ).toEqual([]);
  });
});
