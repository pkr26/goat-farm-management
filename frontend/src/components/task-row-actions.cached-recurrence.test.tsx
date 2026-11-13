import { act, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, it, vi } from "vitest";
import type { TaskOut, TaskTabsOut } from "@/api/generated/models";
import { setActiveFarmTimezone } from "@/lib/format";
import { setAccessToken, setCurrentFarmId } from "@/lib/api-client";
import { MAX_RECUR_DAYS } from "@/lib/backend-caps";
import { LanguageProvider } from "@/lib/i18n";
import { server } from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";
import TasksPage from "@/app/(app)/tasks/page";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/tasks", useSearchParams: () => new URLSearchParams(), useParams: () => ({}),
}));
const TODAY = "2026-10-08";
const task: TaskOut = {
  id: 7, title: "Clean the water trough", title_key: null, title_args: {},
  due_date: TODAY, status: "PENDING", category: "OTHER", auto_generated: false,
  animal_id: null, purchase_batch_id: null, breeding_record_id: null,
  assigned_role_id: null, assigned_user_id: null, recur_days: 2,
  recurring_series_id: null, completed_by_id: null, completed_at: null,
  verified_by_id: null, verified_at: null, verification_note: null,
  skipped_by_id: null, skipped_at: null, skip_reason: null,
  rejected_by_id: null, rejected_at: null, created_at: `${TODAY}T12:00:00Z`,
  created_by_id: null, action_url: null,
};

type BoardResponse = { status: number; data: TaskTabsOut };
const cases: Array<{ name: string; days: number | null; valid: boolean; date?: string }> = [
  { name: "nonfinite NaN", days: Number.NaN, valid: false },
  { name: "nonfinite infinity", days: Number.POSITIVE_INFINITY, valid: false },
  { name: "fractional days", days: 1.5, valid: false },
  { name: "zero days", days: 0, valid: false },
  { name: "negative days", days: -1, valid: false },
  { name: "above the declared recurrence cap", days: MAX_RECUR_DAYS + 1, valid: false },
  { name: "unsafe integer", days: Number.MAX_SAFE_INTEGER + 1, valid: false },
  { name: "minimum one day", days: 1, valid: true, date: "9 Oct 2026" },
  { name: "healthy two days", days: 2, valid: true, date: "10 Oct 2026" },
  { name: "maximum declared days", days: MAX_RECUR_DAYS, valid: true, date: "5 Oct 2036" },
  { name: "one-off duty", days: null, valid: true },
];
it.each(cases)("keeps the real duty board coherent after a typed cache update with $name", async ({ days, valid, date }) => {
  vi.useFakeTimers({ toFake: ["Date"] }); vi.setSystemTime(new Date("2026-10-08T12:00:00Z"));
  localStorage.clear(); localStorage.setItem("herdly.language", "en");
  const board: TaskTabsOut = { today: [task], overdue: [], upcoming: [], awaiting: [], completed: [],
    today_total: 1, today_offset: 0, overdue_total: 0, overdue_offset: 0, upcoming_total: 0, upcoming_offset: 0,
    awaiting_total: 0, awaiting_offset: 0, active_limit: 50, completed_total: 0, completed_limit: 50, completed_offset: 0 };
  const wire: string[] = [];
  server.use(http.get("/api/tasks", () => HttpResponse.json(board)),
    http.post("/api/tasks/:id/complete", ({ params }) => {
      wire.push(String(params.id)); board.today[0] = { ...task, status: "DONE" };
      return HttpResponse.json(board.today[0]);
    }));
  const client = createTestQueryClient();
  const view = renderWithProviders(<LanguageProvider><TasksPage /></LanguageProvider>, client);
  try {
    await view.waitForAuthIdle();
    const table = await screen.findByRole("table");
    const title = await within(table).findByText(task.title);
    const row = title.closest("tr"); expect(row).not.toBeNull();
    const cachedBoards = client.getQueriesData<BoardResponse>({ queryKey: ["/api/tasks"] });
    expect(cachedBoards).toHaveLength(1);
    const [key, loaded] = cachedBoards[0];
    expect(loaded?.status).toBe(200); expect(loaded?.data.today[0].recur_days).toBe(2);
    // Real Auth and the generated hook first load a finite server DTO. This
    // public typed QueryClient producer changes the actual existing cache;
    // no hook, RowActions prop or private callback is substituted.
    await act(async () => {
      client.setQueryData<BoardResponse>(key, (old) => old ? {
        ...old, data: { ...old.data, today: old.data.today.map((item) => item.id === task.id
          ? { ...item, recur_days: days } : item) },
      } : old);
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
    });
    expect(Object.is(client.getQueryData<BoardResponse>(key)?.data.today[0].recur_days, days)).toBe(true);
    expect(within(row!).getByRole("button", { name: "Skip" })).toBeEnabled();
    const complete = within(row!).getByRole("button", { name: "Complete" }); expect(complete).toBeEnabled();
    act(() => complete.click());
    if (days === null) {
      expect(screen.queryByRole("dialog")).toBeNull();
    } else {
      const dialog = screen.getByRole("dialog");
      const confirm = within(dialog).getByRole("button", { name: "Complete duty" });
      expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeEnabled();
      if (!valid) {
        expect(confirm).toBeDisabled();
        const alert = within(dialog).queryByRole("alert");
        expect(alert).not.toBeNull();
        expect(alert).toBeVisible();
        expect(dialog).not.toHaveTextContent(/NaN|Infinity|schedules the next occurrence for/);
        act(() => confirm.click()); expect(wire).toEqual([]);
        // Genuine cache refresh restores the valid server interval while the
        // same confirmation stays open and becomes usable again.
        await act(async () => {
          await client.invalidateQueries({ queryKey: ["/api/tasks"] });
          await new Promise<void>((resolve) => setTimeout(resolve, 0));
        });
        await waitFor(() => expect(confirm).toBeEnabled());
        expect(within(dialog).queryByRole("alert")).toBeNull();
        expect(dialog).toHaveTextContent("This duty repeats every 2 days. Completing it now schedules the next occurrence for 10 Oct 2026.");
      } else {
        expect(confirm).toBeEnabled(); expect(within(dialog).queryByRole("alert")).toBeNull();
        expect(dialog).toHaveTextContent(`This duty repeats every ${days} days. Completing it now schedules the next occurrence for ${date}.`);
      }
      act(() => confirm.click());
    }
    await waitFor(() => expect(wire).toEqual([String(task.id)]));
    await waitFor(() => expect(client.getQueryData<BoardResponse>(key)?.data.today[0].status).toBe("DONE"));
  } finally {
    view.unmount(); client.clear(); setAccessToken(null); setCurrentFarmId(null); setActiveFarmTimezone(null);
    localStorage.clear(); sessionStorage.clear(); vi.useRealTimers();
  }
});
