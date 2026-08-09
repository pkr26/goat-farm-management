/**
 * Tasks page row guards (RowActions): a PENDING task linked to a form shows
 * an "Open form" link instead of a Complete button; a plain PENDING manual
 * task shows Complete; a future auto-generated duty cannot be completed early;
 * and a future recurring duty cannot be completed or skipped early.
 */

import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TaskOut } from "@/api/generated/models";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { addDays, farmToday } from "@/lib/format";

import TasksPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/tasks",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

/** UTC-relative fixture dates: the page compares against utcToday()
 *, so browser-local fixtures drift a day near midnight. */
const TODAY = farmToday();
const TOMORROW = addDays(TODAY, 1);

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
    action_url: null,
    ...overrides,
  };
}

const FORM_TASK = makeTask({
  id: 1,
  title: "Vaccinate goats",
  category: "VACCINE",
  animal_id: 7,
  animal_tag: "G-007",
  action_url: "/health/new?task=1",
});
const MANUAL_TASK = makeTask({ id: 2, title: "Clean water troughs" });
const FUTURE_AUTO_TASK = makeTask({
  id: 3,
  title: "Weigh batch kids",
  category: "WEANING",
  auto_generated: true,
  due_date: TOMORROW,
});
const FUTURE_RECURRING_TASK = makeTask({
  id: 4,
  title: "Inspect perimeter fence",
  due_date: TOMORROW,
  recur_days: 7,
  recurring_series_id: "00000000-0000-4000-8000-000000000004",
});
const FUTURE_MANUAL_TASK = makeTask({
  id: 5,
  title: "Prepare kidding supplies",
  due_date: TOMORROW,
});

function rowOf(title: string): HTMLElement {
  const row = screen.getByText(title).closest("tr");
  expect(row).not.toBeNull();
  return row as HTMLElement;
}

describe("TasksPage row guards", () => {
  beforeEach(() => {
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json({
          today: [
            FORM_TASK,
            MANUAL_TASK,
            FUTURE_AUTO_TASK,
            FUTURE_RECURRING_TASK,
            FUTURE_MANUAL_TASK,
          ],
          overdue: [],
          upcoming: [],
          awaiting: [],
          completed: [],
          today_total: 5,
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
        }),
      ),
    );
  });

  it("form-linked task shows an Open form link, not a Complete button", async () => {
    renderWithProviders(<TasksPage />);
    await screen.findByText("Vaccinate goats");

    const row = rowOf("Vaccinate goats");
    expect(within(row).getByRole("link", { name: "Open form" })).toHaveAttribute(
      "href",
      "/health/new?task=1&returnTo=%2Ftasks%3Ftab%3Dtoday",
    );
    expect(within(row).queryByRole("button", { name: "Complete" })).not.toBeInTheDocument();
    // Skip stays available on this non-recurring pending row.
    expect(within(row).getByRole("button", { name: "Skip" })).toBeInTheDocument();
  });

  it("pending manual task shows a Complete button", async () => {
    renderWithProviders(<TasksPage />);
    await screen.findByText("Clean water troughs");

    const row = rowOf("Clean water troughs");
    expect(within(row).getByRole("button", { name: "Complete" })).toBeInTheDocument();
    expect(within(row).queryByRole("link", { name: "Open form" })).not.toBeInTheDocument();
  });

  it("future auto-generated duty is locked: no Complete button until due", async () => {
    renderWithProviders(<TasksPage />);
    await screen.findByText("Weigh batch kids");

    const row = rowOf("Weigh batch kids");
    expect(within(row).queryByRole("button", { name: "Complete" })).not.toBeInTheDocument();
    expect(within(row).queryByRole("link", { name: "Open form" })).not.toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Skip" })).toBeInTheDocument();
  });

  it("future recurring duty exposes neither Complete nor Skip until due", async () => {
    renderWithProviders(<TasksPage />);
    await screen.findByText("Inspect perimeter fence");

    const row = rowOf("Inspect perimeter fence");
    expect(within(row).queryByRole("button", { name: "Complete" })).not.toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "Skip" })).not.toBeInTheDocument();
  });

  it("future one-off manual duty remains available for early completion", async () => {
    renderWithProviders(<TasksPage />);
    await screen.findByText("Prepare kidding supplies");

    const row = rowOf("Prepare kidding supplies");
    expect(within(row).getByRole("button", { name: "Complete" })).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Skip" })).toBeInTheDocument();
  });

  it("does not link a form or animal that the task worker cannot access", async () => {
    server.use(permissionsHandler(["tasks.view", "tasks.complete"]));
    renderWithProviders(<TasksPage />);
    await screen.findByText("Vaccinate goats");

    const row = rowOf("Vaccinate goats");
    expect(within(row).getByText("G-007")).toBeInTheDocument();
    expect(within(row).queryByRole("link", { name: "G-007" })).not.toBeInTheDocument();
    expect(within(row).queryByRole("link", { name: "Open form" })).not.toBeInTheDocument();
    expect(within(row).getByText(/Linked form unavailable/)).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Skip" })).toBeInTheDocument();
  });
});
