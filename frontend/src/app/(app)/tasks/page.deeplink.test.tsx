/**
 * Tasks page deep links and the self-verification guard:
 * /tasks?tab=… selects the initial tab (unknown values fall back to
 * "today"), and Verify/Reject are hidden on duties the current user
 * completed themselves — unless they own the farm (the backend exempts the
 * owner from the two-person rule).
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TaskOut } from "@/api/generated/models";
import { permissionsHandler, server, TEST_USER } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { addDays, farmToday } from "@/lib/format";

import TasksPage from "./page";

const { navState, replaceMock } = vi.hoisted(() => ({
  navState: { search: "" },
  replaceMock: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => "/tasks",
  useSearchParams: () => new URLSearchParams(navState.search),
  useParams: () => ({}),
}));

const TODAY = farmToday();
const THREE_DAYS_AGO = addDays(TODAY, -3);

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

const TODAY_TASK = makeTask({ id: 1, title: "Morning feed count" });
const OVERDUE_TASK = makeTask({ id: 2, title: "Trim hooves", due_date: THREE_DAYS_AGO });

function tasksHandler(tasks: TaskOut[] = [TODAY_TASK, OVERDUE_TASK]) {
  return http.get("/api/tasks", () =>
    HttpResponse.json({
      today: [TODAY_TASK],
      overdue: [OVERDUE_TASK],
      upcoming: [],
      awaiting: tasks.filter((t) => t.status === "DONE"),
      completed: [],
      today_total: 1,
      today_offset: 0,
      overdue_total: 1,
      overdue_offset: 0,
      upcoming_total: 0,
      upcoming_offset: 0,
      awaiting_total: tasks.filter((t) => t.status === "DONE").length,
      awaiting_offset: 0,
      active_limit: 50,
      completed_total: 0,
      completed_limit: 50,
      completed_offset: 0,
    }),
  );
}

/** Row-content queries scope to the table: the below-md card list
 * renders the same titles outside it (see animals page tests). */
function tableScope() {
  return within(screen.getByRole("table"));
}

function rowOf(title: string): HTMLElement {
  const row = tableScope().getByText(title).closest("tr");
  expect(row).not.toBeNull();
  return row as HTMLElement;
}

describe("TasksPage ?tab= deep links (7-1)", () => {
  beforeEach(() => {
    replaceMock.mockClear();
    server.use(tasksHandler());
  });

  it("opens the tab named in ?tab= (the dashboard links to /tasks?tab=overdue)", async () => {
    navState.search = "?tab=overdue";
    renderWithProviders(<TasksPage />);

    // The overdue tab's content is shown, not Today's.
    await within(await screen.findByRole("table")).findByText("Trim hooves");
    expect(tableScope().queryByText("Morning feed count")).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Overdue (1)" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("honours ?tab=today explicitly", async () => {
    navState.search = "?tab=today";
    renderWithProviders(<TasksPage />);

    await within(await screen.findByRole("table")).findByText("Morning feed count");
    expect(tableScope().queryByText("Trim hooves")).not.toBeInTheDocument();
  });

  it("falls back to Today for an unknown tab value", async () => {
    navState.search = "?tab=bogus";
    renderWithProviders(<TasksPage />);

    await within(await screen.findByRole("table")).findByText("Morning feed count");
    expect(screen.getByRole("tab", { name: "Today (1)" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("falls back to Today when the deep-linked tab is not visible (no tasks.verify)", async () => {
    navState.search = "?tab=awaiting";
    server.use(permissionsHandler(["tasks.view"]));
    renderWithProviders(<TasksPage />);

    await within(await screen.findByRole("table")).findByText("Morning feed count");
    expect(
      screen.queryByRole("tab", { name: /Awaiting verification/ }),
    ).not.toBeInTheDocument();
  });

  it("writes tab changes back to the URL so refresh and farm switching restore the page", async () => {
    navState.search = "?tab=today&from=dashboard";
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);
    await within(await screen.findByRole("table")).findByText("Morning feed count");

    await user.click(screen.getByRole("tab", { name: "Overdue (1)" }));

    expect(replaceMock).toHaveBeenCalledWith("/tasks?tab=overdue&from=dashboard");
    expect(screen.getByRole("tablist")).toHaveClass("overflow-x-auto");
  });
});

describe("TasksPage self-verification guard (7-4)", () => {
  const SELF_DONE = makeTask({
    id: 5,
    title: "Deep-clean kidding pen",
    status: "DONE",
    category: "CLEANING",
    needs_verification: true,
    completed_by_id: TEST_USER.id,
    completed_at: "2026-08-05T14:07:00",
  });
  const OTHER_DONE = makeTask({
    id: 6,
    title: "Scrub water troughs",
    status: "DONE",
    category: "CLEANING",
    needs_verification: true,
    completed_by_id: TEST_USER.id + 100,
    completed_at: "2026-08-05T15:00:00",
  });

  beforeEach(() => {
    navState.search = "?tab=awaiting";
    server.use(tasksHandler([SELF_DONE, OTHER_DONE]));
  });

  it("hides Verify/Reject on a duty the current (non-owner) user completed", async () => {
    server.use(permissionsHandler(["tasks.view", "tasks.verify"]));
    renderWithProviders(<TasksPage />);

    await within(await screen.findByRole("table")).findByText("Deep-clean kidding pen");
    const selfRow = rowOf("Deep-clean kidding pen");
    expect(within(selfRow).queryByRole("button", { name: "Verify" })).not.toBeInTheDocument();
    expect(within(selfRow).queryByRole("button", { name: "Reject…" })).not.toBeInTheDocument();

    // …while another worker's completion still offers them.
    const otherRow = rowOf("Scrub water troughs");
    expect(within(otherRow).getByRole("button", { name: "Verify" })).toBeInTheDocument();
    expect(within(otherRow).getByRole("button", { name: "Reject…" })).toBeInTheDocument();
  });

  it("keeps Verify/Reject for the farm owner even on their own completion", async () => {
    server.use(
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ is_owner: true, permissions: ["tasks.view", "tasks.verify"] }),
      ),
    );
    renderWithProviders(<TasksPage />);

    await within(await screen.findByRole("table")).findByText("Deep-clean kidding pen");
    const selfRow = rowOf("Deep-clean kidding pen");
    expect(within(selfRow).getByRole("button", { name: "Verify" })).toBeInTheDocument();
    expect(within(selfRow).getByRole("button", { name: "Reject…" })).toBeInTheDocument();
  });
});
