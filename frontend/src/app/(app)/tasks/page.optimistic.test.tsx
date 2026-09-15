/**
 * Optimistic task completion (2026-09 felt-speed win): tapping Complete
 * strikes the duty through before the POST settles, and a failed completion
 * rolls the board cache back so the row reads pending again with the error.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TaskOut } from "@/api/generated/models";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/msw-server";
import { farmToday } from "@/lib/format";

import TasksPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/tasks",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const TODAY = farmToday();

function makeTask(overrides: Partial<TaskOut>): TaskOut {
  return {
    id: 1,
    title: "Task",
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
    action_url: null,
    ...overrides,
  };
}

const DUTY = makeTask({ id: 7, title: "Clean water troughs" });
const DONE_DUTY = makeTask({ id: 7, title: "Clean water troughs", status: "DONE" });

function board({ today = [] as TaskOut[], completed = [] as TaskOut[] }) {
  return {
    today,
    overdue: [],
    upcoming: [],
    awaiting: [],
    completed,
    today_total: today.length,
    today_offset: 0,
    overdue_total: 0,
    overdue_offset: 0,
    upcoming_total: 0,
    upcoming_offset: 0,
    awaiting_total: 0,
    awaiting_offset: 0,
    active_limit: 50,
    completed_total: completed.length,
    completed_limit: 50,
    completed_offset: 0,
  };
}

/** Row content is scoped to the desktop table: the below-md card list
 * renders the same duty a second time. */
function titleCell(title: string): HTMLElement {
  return within(screen.getByRole("table")).getByText(title);
}

describe("TasksPage — optimistic completion", () => {
  beforeEach(() => {
    vi.mocked(toast.success).mockClear();
    vi.mocked(toast.error).mockClear();
  });

  it("strikes the duty through before the POST settles, then keeps it struck", async () => {
    let completed = false;
    let release: () => void = () => {};
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json(completed ? board({ completed: [DONE_DUTY] }) : board({ today: [DUTY] })),
      ),
      http.post(
        "/api/tasks/7/complete",
        () =>
          new Promise<Response>((resolve) => {
            release = () => {
              completed = true;
              resolve(HttpResponse.json(DONE_DUTY));
            };
          }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);

    const completeButtons = await screen.findAllByRole("button", { name: "Complete" });
    await user.click(completeButtons[0]);

    // Before the response lands the cached row already reads DONE.
    await waitFor(() =>
      expect(titleCell("Clean water troughs")).toHaveClass("line-through"),
    );
    // The row instance whose Complete was tapped keeps the action mounted
    // but locked while the optimistic row waits (its desktop twin never fired
    // and drops the action once the optimistic DONE lands).
    await waitFor(() => {
      const completes = screen.getAllByRole("button", { name: "Complete" });
      expect(completes.length).toBeGreaterThan(0);
      expect(completes.every((button) => button.hasAttribute("disabled"))).toBe(true);
    });

    release();
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Task completed."));
    // The refetched board agrees: the duty left the Today tab (empty board
    // renders the empty state, no table).
    await screen.findByText("No tasks for today.");
  });

  it("rolls the row back to pending when the completion fails", async () => {
    let release: () => void = () => {};
    server.use(
      http.get("/api/tasks", () => HttpResponse.json(board({ today: [DUTY] }))),
      http.post(
        "/api/tasks/7/complete",
        () =>
          new Promise<Response>((resolve) => {
            release = () =>
              resolve(
                HttpResponse.json(
                  { detail: "Already completed by another worker" },
                  { status: 409 },
                ),
              );
          }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);

    const completeButtons = await screen.findAllByRole("button", { name: "Complete" });
    await user.click(completeButtons[0]);

    // The optimistic strike appears while the request is in flight…
    await waitFor(() =>
      expect(titleCell("Clean water troughs")).toHaveClass("line-through"),
    );
    // …then the failed completion rolls it back and offers the retry.
    release();
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    await waitFor(() =>
      expect(titleCell("Clean water troughs")).not.toHaveClass("line-through"),
    );
    // The retry label lives on the row whose Complete was tapped (the mobile
    // card instance owns that action state); the desktop twin still reads
    // "Complete" because it never fired.
    expect(
      screen.getAllByRole("button", { name: "Retry complete" }).length,
    ).toBeGreaterThan(0);
  });
});
