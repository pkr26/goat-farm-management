import { act, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, it, vi } from "vitest";

import type { TaskOut, TaskTabsOut } from "@/api/generated/models";
import { getListTasksApiTasksGetQueryKey, type listTasksApiTasksGetResponseSuccess } from "@/api/generated/endpoints";
import { setAccessToken, setCurrentFarmId } from "@/lib/api-client";
import { LanguageProvider } from "@/lib/i18n";
import { server, TEST_USER } from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";
import WorkerBoardPage from "./page";

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), push: vi.fn() }), usePathname: () => "/worker" }));

const duty: TaskOut = {
    id: 41, title: "Verified manual duty", title_key: null, title_args: {}, due_date: "2026-10-07",
    status: "PENDING", category: "OTHER", auto_generated: false, action_url: null,
    animal_id: null, purchase_batch_id: null, breeding_record_id: null,
    assigned_role_id: null, assigned_user_id: TEST_USER.id, recur_days: null, recurring_series_id: null,
    completed_by_id: null, completed_at: null, verified_by_id: null, verified_at: null, verification_note: null,
    skipped_by_id: null, skipped_at: null, skip_reason: null, rejected_by_id: null, rejected_at: null,
    created_at: "2026-10-07T00:00:00Z", created_by_id: null,
  };
const otherDuty: TaskOut = { ...duty, id: 42, title: "Another verified duty" };
const board: TaskTabsOut = {
  today: [duty], overdue: [otherDuty], upcoming: [], awaiting: [], completed: [],
  today_total: 1, overdue_total: 1, upcoming_total: 0, awaiting_total: 0, completed_total: 0,
  today_offset: 0, overdue_offset: 0, upcoming_offset: 0, awaiting_offset: 0,
  active_limit: 200, completed_limit: 200, completed_offset: 0,
};
const params = { active_limit: 200, today_offset: 0, overdue_offset: 0, view: "worker" } as const;

const unknownMessage = "The full duty count is unavailable — ask a manager for the full board.";
const cases = [
  { label: "empty valid lists and zero totals", todayTotal: 0, overdueTotal: 0, empty: true, expected: null },
  { label: "all known duties visible", todayTotal: 1, overdueTotal: 1, expected: null },
  { label: "one known remaining duty", todayTotal: 2, overdueTotal: 1, expected: "1 more duty not shown — ask a manager for the full board." },
  { label: "known remaining duties", todayTotal: 5, overdueTotal: 1, expected: "4 more duties not shown — ask a manager for the full board." },
  { label: "unknown overdue beside known remaining today", todayTotal: 5, overdueTotal: Number.NaN, expected: "At least 4 more duties not shown — ask a manager for the full board." },
  { label: "unknown today beside known remaining overdue", todayTotal: Number.NaN, overdueTotal: 5, expected: "At least 4 more duties not shown — ask a manager for the full board." },
  { label: "unknown overdue beside one known remaining today", todayTotal: 2, overdueTotal: Number.NaN, expected: "At least 1 more duty not shown — ask a manager for the full board." },
  { label: "both unknown totals", todayTotal: Number.NaN, overdueTotal: Number.NaN, expected: unknownMessage },
  { label: "one unknown total with all other duties visible", todayTotal: 1, overdueTotal: Number.NaN, expected: unknownMessage },
  { label: "infinite overdue total", todayTotal: 5, overdueTotal: Number.POSITIVE_INFINITY, expected: "At least 4 more duties not shown — ask a manager for the full board." },
  { label: "fractional today total", todayTotal: 0.5, overdueTotal: 5, expected: "At least 4 more duties not shown — ask a manager for the full board." },
  { label: "negative overdue total", todayTotal: 5, overdueTotal: -1, expected: "At least 4 more duties not shown — ask a manager for the full board." },
  { label: "empty cached lists with unknown totals", todayTotal: Number.NaN, overdueTotal: Number.NaN, empty: true, expected: unknownMessage },
  { label: "Telugu partial remaining count", todayTotal: 5, overdueTotal: Number.NaN, language: "te", expected: "కనీసం ఇంకా 4 విధులు చూపబడలేదు — పూర్తి జాబితా కోసం మేనేజర్‌ను అడగండి." },
  { label: "Telugu unknown count", todayTotal: Number.NaN, overdueTotal: Number.NaN, language: "te", expected: "పూర్తి విధుల సంఖ్యను నిర్ధారించలేము — పూర్తి జాబితా కోసం మేనేజర్‌ను అడగండి." },
  { label: "oversized finite overdue total", todayTotal: 5, overdueTotal: Number.MAX_VALUE, expected: "At least 4 more duties not shown — ask a manager for the full board." },
  { label: "oversized finite today total", todayTotal: Number.MAX_VALUE, overdueTotal: 5, expected: "At least 4 more duties not shown — ask a manager for the full board." },
  { label: "two overflowing finite totals", todayTotal: Number.MAX_VALUE, overdueTotal: Number.MAX_VALUE, expected: unknownMessage },
  { label: "unsafe integer total", todayTotal: Number.MAX_SAFE_INTEGER + 1, overdueTotal: 5, expected: "At least 4 more duties not shown — ask a manager for the full board." },
  { label: "two safe totals with an unsafe sum", todayTotal: Number.MAX_SAFE_INTEGER, overdueTotal: Number.MAX_SAFE_INTEGER, expected: "At least 9007199254740990 more duties not shown — ask a manager for the full board." },
] as const;

it.each(cases)("preserves truthful remaining-duty guidance for $label", async (sample) => {
  localStorage.clear(); sessionStorage.clear();
  localStorage.setItem("herdly.language", "language" in sample ? sample.language : "en");
  setAccessToken(null); setCurrentFarmId(null);
  server.use(http.get("/api/tasks", () => HttpResponse.json(board)));
  const client = createTestQueryClient();
  const view = renderWithProviders(<LanguageProvider><WorkerBoardPage /></LanguageProvider>, client);
  try {
    await view.waitForAuthIdle();
    expect(await screen.findByText(duty.title)).toBeVisible();
    expect(screen.getByText(otherDuty.title)).toBeVisible();
    const key = getListTasksApiTasksGetQueryKey(params);
    const verified = client.getQueryData<listTasksApiTasksGetResponseSuccess>(key);
    expect(verified).toBeDefined(); expect(verified?.status).toBe(200);
    // Restore through the public SDK cache after genuine successful HTTP.
    // Unknown numeric cache values retain a complete declared DTO shape.
    const restored: listTasksApiTasksGetResponseSuccess = {
      ...verified!, data: { ...verified!.data,
        today: "empty" in sample ? [] : verified!.data.today,
        overdue: "empty" in sample ? [] : verified!.data.overdue,
        today_total: sample.todayTotal, overdue_total: sample.overdueTotal,
      },
    };
    await act(async () => { client.setQueryData(key, restored); });
    if ("empty" in sample) {
      await waitFor(() => expect(screen.queryByText(duty.title)).toBeNull());
      expect(screen.queryByText(otherDuty.title)).toBeNull();
    }
    if (sample.expected === null) {
      expect(screen.queryByText(/more dut(?:y|ies) not shown/)).toBeNull();
      expect(screen.queryByText(unknownMessage)).toBeNull();
    }
    else expect(await screen.findByText(sample.expected)).toBeVisible();
    if (!("empty" in sample)) {
      expect(screen.getByText(duty.title)).toBeVisible();
      expect(screen.getByText(otherDuty.title)).toBeVisible();
    }
    expect(screen.queryByText(/NaN|Infinity/)).toBeNull();
  } finally {
    view.unmount(); client.clear(); setAccessToken(null); setCurrentFarmId(null);
    localStorage.clear(); sessionStorage.clear();
  }
});

it("removes uncertainty when a later valid cache result recovers the full count", async () => {
  localStorage.clear(); sessionStorage.clear(); localStorage.setItem("herdly.language", "en");
  setAccessToken(null); setCurrentFarmId(null);
  server.use(http.get("/api/tasks", () => HttpResponse.json(board)));
  const client = createTestQueryClient();
  const view = renderWithProviders(<LanguageProvider><WorkerBoardPage /></LanguageProvider>, client);
  try {
    await view.waitForAuthIdle(); expect(await screen.findByText(duty.title)).toBeVisible();
    const key = getListTasksApiTasksGetQueryKey(params);
    const verified = client.getQueryData<listTasksApiTasksGetResponseSuccess>(key);
    expect(verified).toBeDefined(); expect(verified?.status).toBe(200);
    const unknown: listTasksApiTasksGetResponseSuccess = {
      ...verified!, data: { ...verified!.data, today_total: Number.NaN, overdue_total: Number.NaN },
    };
    await act(async () => { client.setQueryData(key, unknown); });
    expect(await screen.findByText(unknownMessage)).toBeVisible();
    const recovered: listTasksApiTasksGetResponseSuccess = {
      ...verified!, data: { ...verified!.data, today_total: 5, overdue_total: 1 },
    };
    await act(async () => { client.setQueryData(key, recovered); });
    expect(await screen.findByText("4 more duties not shown — ask a manager for the full board.")).toBeVisible();
    expect(screen.queryByText(unknownMessage)).toBeNull();
    expect(screen.queryByText(/At least/)).toBeNull();
  } finally {
    view.unmount(); client.clear(); setAccessToken(null); setCurrentFarmId(null);
    localStorage.clear(); sessionStorage.clear();
  }
});
