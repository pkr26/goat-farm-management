/**
 * Tasks board — generated duty titles (H1): a task whose payload carries
 * title_key/title_args renders through the taskGen catalog in the worker's
 * language; unkeyed duties render their payload title verbatim.
 */

import { screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import type { TaskOut } from "@/api/generated/models";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { farmToday } from "@/lib/format";
import { LanguageProvider, LANGUAGE_STORAGE_KEY } from "@/lib/i18n";

import TasksPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/tasks",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const TODAY = farmToday();

function makeTask(overrides: Partial<TaskOut>): TaskOut {
  return {
    id: 1,
    title: "Task",
    title_key: null,
    title_args: {},
    due_date: TODAY,
    status: "PENDING",
    category: "OTHER",
    auto_generated: false,
    animal_id: null,
    purchase_batch_id: null,
    breeding_record_id: null,
    assigned_role_id: null,
    assigned_user_id: null,
    recur_days: null,
    recurring_series_id: null,
    completed_by_id: null,
    completed_at: null,
    verified_by_id: null,
    verified_at: null,
    verification_note: null,
    skipped_by_id: null,
    skipped_at: null,
    skip_reason: null,
    rejected_by_id: null,
    rejected_at: null,
    created_at: "2026-01-01T00:00:00Z",
    created_by_id: null,
    action_url: null,
    ...overrides,
  };
}

function board(tasks: TaskOut[]) {
  return {
    today: tasks,
    overdue: [],
    upcoming: [],
    awaiting: [],
    completed: [],
    today_total: tasks.length,
    today_offset: 0,
    overdue_total: 0,
    overdue_offset: 0,
    upcoming_total: 0,
    upcoming_offset: 0,
    awaiting_total: 0,
    awaiting_offset: 0,
    active_limit: 50,
    completed_total: 0,
    completed_limit: 50,
    completed_offset: 0,
  };
}

describe("TasksPage — localized generated titles", () => {
  it("renders the kidding-watch duty in Telugu and the manual duty verbatim", async () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    try {
      server.use(
        http.get("/api/tasks", () =>
          HttpResponse.json(
            board([
              makeTask({
                id: 3,
                title: "Kidding watch: G-101 (due 20-09) — check udder fill",
                auto_generated: true,
                category: "KIDDING_WATCH",
                title_key: "kidding_watch",
                title_args: { tag: "G-101", kidding_date: "2026-09-20", days_before: 3 },
              } as Partial<TaskOut>),
              makeTask({ id: 4, title: "Clean water troughs" }),
            ]),
          ),
        ),
      );
      renderWithProviders(
        <LanguageProvider>
          <TasksPage />
        </LanguageProvider>,
      );

      await waitFor(() =>
        expect(
          screen.getAllByText(/పిల్లల గమనింపు: G-101 \(.+ సెప్టెం 2026 నాటికి\)/).length,
        ).toBeGreaterThan(0),
      );
      // The raw English payload title never leaks for the keyed duty.
      expect(screen.queryByText(/Kidding watch: G-101/)).not.toBeInTheDocument();
      // The unkeyed manual duty renders its own title.
      expect(screen.getAllByText("Clean water troughs").length).toBeGreaterThan(0);
    } finally {
      window.localStorage.clear();
      document.documentElement.lang = "en";
    }
  });
});
