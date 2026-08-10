/**
 * Tasks page row guards (RowActions): a PENDING task linked to a form shows
 * an "Open form" link instead of a Complete button; a plain PENDING manual
 * task shows Complete; a future auto-generated duty cannot be completed early;
 * and a future recurring duty cannot be completed or skipped early.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
    rejected_by_id: null,
    rejected_at: null,
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

// REGRESSION TESTS — actions the backend deterministically 409s are no longer
// offered: Skip on quarantine-gate/weaning duties (api/tasks.py::skip) and the
// "Open form" link on future VACCINE/DEWORMING duties whose linked health
// write rejects any event before the due date (api/health.py).

const QUARANTINE_GATE_TASK = makeTask({
  id: 6,
  title: "Day 20: vaccinate ET + Tetanus",
  category: "VACCINE",
  auto_generated: true,
  purchase_batch_id: 42,
  action_url: "/health/new?task_id=6&purchase_batch_id=42",
});
const RECOVERY_WEANING_TASK = makeTask({
  id: 7,
  title: "Wean kids of G-012",
  category: "WEANING",
  auto_generated: true,
  animal_id: 12,
});
const FUTURE_FORM_TASK = makeTask({
  id: 8,
  title: "Pre-kidding booster",
  category: "VACCINE",
  auto_generated: true,
  animal_id: 9,
  due_date: TOMORROW,
  action_url: "/health/new?task_id=8&animal_id=9",
});

describe("TasksPage deterministic 409 guards", () => {
  beforeEach(() => {
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json({
          today: [QUARANTINE_GATE_TASK, RECOVERY_WEANING_TASK, FUTURE_FORM_TASK],
          overdue: [],
          upcoming: [],
          awaiting: [],
          completed: [],
          today_total: 3,
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

  it("hides Skip on a batch-linked quarantine duty but keeps its form workflow", async () => {
    renderWithProviders(<TasksPage />);
    await screen.findByText("Day 20: vaccinate ET + Tetanus");

    const row = rowOf("Day 20: vaccinate ET + Tetanus");
    expect(within(row).getByRole("link", { name: "Open form" })).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "Skip" })).not.toBeInTheDocument();
  });

  it("hides Skip on an animal-linked weaning duty but keeps Complete", async () => {
    renderWithProviders(<TasksPage />);
    await screen.findByText("Wean kids of G-012");

    const row = rowOf("Wean kids of G-012");
    expect(within(row).getByRole("button", { name: "Complete" })).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "Skip" })).not.toBeInTheDocument();
  });

  it("replaces the Open form link of a future health duty with a not-due hint", async () => {
    renderWithProviders(<TasksPage />);
    await screen.findByText("Pre-kidding booster");

    const row = rowOf("Pre-kidding booster");
    expect(within(row).queryByRole("link", { name: "Open form" })).not.toBeInTheDocument();
    expect(within(row).getByText(/Not due yet/)).toBeInTheDocument();
    // A one-off generated duty may still be skipped early (only recurring
    // duties are future-locked for skip).
    expect(within(row).getByRole("button", { name: "Skip" })).toBeInTheDocument();
  });
});

// REGRESSION TESTS — the Create-duty button used to be disabled whenever the
// (optional) team directory was loading or erroring, so a team.manage holder
// could not create even an intentionally unassigned duty during a /api/team
// outage while a less-privileged user could. The load state now only gates a
// submit that actually chose an assignment.

describe("TasksPage create-duty gating during a team outage", () => {
  let createBody: Record<string, unknown> | null;

  beforeEach(() => {
    createBody = null;
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json({
          today: [],
          overdue: [],
          upcoming: [],
          awaiting: [],
          completed: [],
          today_total: 0,
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
      http.get("/api/team", () =>
        HttpResponse.json({ detail: "team directory unavailable" }, { status: 503 }),
      ),
      http.get("/api/animals", () =>
        HttpResponse.json({ animals: [], total: 0 }),
      ),
      http.post("/api/tasks", async ({ request }) => {
        createBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(makeTask({ id: 50 }), { status: 201 });
      }),
    );
  });

  it("still creates an unassigned duty while /api/team is erroring", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);
    await screen.findByRole("tab", { name: "Today (0)" });

    await user.click(screen.getByRole("button", { name: "New duty" }));
    const dialog = await screen.findByRole("dialog");
    // The assignment section still announces the failure for users who DO
    // want to assign — but it must not gate an unassigned submit.
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "team directory unavailable",
    );

    const createButton = within(dialog).getByRole("button", { name: "Create duty" });
    expect(createButton).toBeEnabled();
    await user.type(within(dialog).getByLabelText(/title/i), "Clean water troughs");
    await user.click(createButton);

    await waitFor(() => expect(createBody).not.toBeNull());
    expect(createBody).toMatchObject({
      title: "Clean water troughs",
      assigned_role_id: null,
      assigned_user_id: null,
    });
  });
});
