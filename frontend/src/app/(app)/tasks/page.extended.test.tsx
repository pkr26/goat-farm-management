/**
 * Tasks page (extended beyond page.test.tsx's row guards): tab bar counts and
 * the tasks.verify-gated awaiting/completed tabs, per-row rendering (late
 * badges, recur badges, sent-back notes, assignment and animal cells,
 * completed-tab status), the complete/skip/verify/reject mutation flows with
 * request payloads and error paths, RBAC gating of every action, and the
 * New-duty dialog — zod validation incl. recur_days boundaries, payload
 * mapping, role/worker mutual exclusion from the team payload, and the
 * no-team-access fallback.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

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

type User = ReturnType<typeof userEvent.setup>;

beforeAll(() => {
  // jsdom lacks APIs the Base UI select touches when opening the listbox.
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

/** Farm-calendar fixture dates: operational due dates follow the active farm timezone. */
const TODAY = farmToday();

/** Matches the create form's farm-calendar date default. */
function localTodayISO(): string {
  return farmToday();
}
const THREE_DAYS_AGO = addDays(TODAY, -3);
const NEXT_WEEK = addDays(TODAY, 7);

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
const OVERDUE_TASK = makeTask({
  id: 2,
  title: "Trim hooves",
  due_date: THREE_DAYS_AGO,
  recur_days: 30,
  assigned_role_name: "Mover",
  animal_id: 3,
  animal_tag: "G-003",
});
const SENT_BACK_TASK = makeTask({
  id: 3,
  title: "Scrub water troughs",
  verification_note: "far pen still dirty",
});
const UPCOMING_TASK = makeTask({ id: 4, title: "Rotate buck", due_date: NEXT_WEEK });
const AWAITING_TASK = makeTask({
  id: 5,
  title: "Deep-clean kidding pen",
  status: "DONE",
  category: "CLEANING",
  needs_verification: true,
  completed_at: "2026-08-05T14:07:00",
  assigned_user_name: "Raju",
});
const VERIFIED_TASK = makeTask({
  id: 6,
  title: "Weekly sweep",
  status: "VERIFIED",
  needs_verification: true,
  completed_at: "2026-08-01T09:30:00",
});

const TEAM_PAYLOAD = {
  memberships: [
    {
      id: 1,
      user_id: 9,
      email: "raju@goatfarm.test",
      name: "Raju",
      role_id: 2,
      role_name: "Vet",
      is_active: true,
    },
    {
      id: 2,
      user_id: 10,
      email: "ex@goatfarm.test",
      name: "Ex Worker",
      role_id: 2,
      role_name: "Vet",
      is_active: false,
    },
  ],
  roles: [
    { id: 2, code: "VET", name: "Vet", description: null, permissions: [] },
    { id: 3, code: "MOVER", name: "Mover", description: null, permissions: [] },
  ],
  permission_groups: [],
  permission_labels: {},
};

type TabsPayload = {
  today: TaskOut[];
  overdue: TaskOut[];
  upcoming: TaskOut[];
  awaiting: TaskOut[];
  completed: TaskOut[];
  today_total: number;
  today_offset: number;
  overdue_total: number;
  overdue_offset: number;
  upcoming_total: number;
  upcoming_offset: number;
  awaiting_total: number;
  awaiting_offset: number;
  active_limit: number;
  completed_total: number;
  completed_limit: number;
  completed_offset: number;
};

function fullPayload(): TabsPayload {
  return {
    today: [TODAY_TASK, SENT_BACK_TASK],
    overdue: [OVERDUE_TASK],
    upcoming: [UPCOMING_TASK],
    awaiting: [AWAITING_TASK],
    completed: [AWAITING_TASK, VERIFIED_TASK],
    today_total: 2,
    today_offset: 0,
    overdue_total: 1,
    overdue_offset: 0,
    upcoming_total: 1,
    upcoming_offset: 0,
    awaiting_total: 1,
    awaiting_offset: 0,
    active_limit: 50,
    completed_total: 2,
    completed_limit: 50,
    completed_offset: 0,
  };
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

async function pickOption(user: User, trigger: HTMLElement, name: string | RegExp) {
  await user.click(trigger);
  await user.click(await screen.findByRole("option", { name }));
}

describe("TasksPage (extended)", () => {
  let listCalls: number;
  let teamCalls: number;
  let payload: TabsPayload;
  let actionCalls: { action: string; taskId: string; body: unknown }[];
  let createBody: Record<string, unknown> | null;

  beforeEach(() => {
    listCalls = 0;
    teamCalls = 0;
    payload = fullPayload();
    actionCalls = [];
    createBody = null;
    server.use(
      http.get("/api/tasks", () => {
        listCalls += 1;
        return HttpResponse.json(payload);
      }),
      http.get("/api/team", () => {
        teamCalls += 1;
        return HttpResponse.json(TEAM_PAYLOAD);
      }),
      http.get("/api/animals", () =>
        HttpResponse.json({
          animals: [{ id: 7, tag_number: "G-007", name: "Radha" }],
          total: 1,
        }),
      ),
      http.post("/api/tasks", async ({ request }) => {
        createBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(makeTask({ id: 50 }), { status: 201 });
      }),
      http.post("/api/tasks/:taskId/:action", async ({ request, params }) => {
        let body: unknown = null;
        try {
          body = await request.json();
        } catch {
          /* bodyless actions */
        }
        actionCalls.push({
          action: String(params.action),
          taskId: String(params.taskId),
          body,
        });
        return HttpResponse.json(makeTask({ id: Number(params.taskId) }));
      }),
    );
  });

  async function renderLoaded() {
    renderWithProviders(<TasksPage />);
    await screen.findByRole("tab", { name: "Today (2)" });
  }

  // ---------- tabs & rendering ----------

  it("renders the tab bar with counts for each bucket", async () => {
    await renderLoaded();
    expect(screen.getByRole("tab", { name: "Today (2)" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Overdue (1)" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Upcoming (1)" })).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: "Awaiting verification (1)" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Completed (2)" })).toBeInTheDocument();
  });

  it("hides the verification tabs without tasks.verify", async () => {
    server.use(permissionsHandler(["tasks.view", "tasks.create", "tasks.complete"]));
    await renderLoaded();
    expect(
      screen.queryByRole("tab", { name: /Awaiting verification/ }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Completed (2)" })).toBeInTheDocument();
  });

  it("switches tabs to show their tasks", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    expect(tableScope().getByText("Morning feed count")).toBeInTheDocument();
    expect(tableScope().queryByText("Rotate buck")).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Upcoming (1)" }));
    expect(await within(await screen.findByRole("table")).findByText("Rotate buck")).toBeInTheDocument();
    expect(tableScope().queryByText("Morning feed count")).not.toBeInTheDocument();
  });

  it("shows the per-tab empty message", async () => {
    payload.today = [];
    payload.today_total = 0;
    renderWithProviders(<TasksPage />);
    await screen.findByRole("tab", { name: "Today (0)" });
    expect(screen.getByText("No tasks for today.")).toBeInTheDocument();
  });

  it("marks a pending overdue task with the days-late badge and recur badge", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("tab", { name: "Overdue (1)" }));
    const row = rowOf("Trim hooves");
    expect(within(row).getByText(/\(3d late\)/)).toBeInTheDocument();
    expect(within(row).getByText("every 30d")).toBeInTheDocument();
  });

  it("shows the sent-back note under a rejected pending task", async () => {
    await renderLoaded();
    expect(tableScope().getByText("Sent back: far pen still dirty")).toBeInTheDocument();
  });

  it("renders the assignment and animal cells", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const todayRow = rowOf("Morning feed count");
    expect(within(todayRow).getAllByText("—")).toHaveLength(2); // assignee + animal

    await user.click(screen.getByRole("tab", { name: "Overdue (1)" }));
    const row = rowOf("Trim hooves");
    expect(within(row).getByText("Mover")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "G-003" })).toHaveAttribute(
      "href",
      "/animals/3?returnTo=%2Ftasks%3Ftab%3Doverdue",
    );
  });

  it("completed tab shows status, awaiting marker and formatted completion time", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("tab", { name: "Completed (2)" }));

    const awaitingRow = rowOf("Deep-clean kidding pen");
    expect(within(awaitingRow).getByText("DONE")).toBeInTheDocument();
    expect(within(awaitingRow).getByText("awaiting")).toBeInTheDocument();
    // completed_at is naive UTC rendered in the active farm's timezone
    // (Asia/Kolkata, +05:30), so 14:07 UTC must display as 19:37 — asserted as
    // a literal so dropping the UTC normalisation in formatFarmDateTime fails.
    expect(within(awaitingRow).getByText("5 Aug 2026, 7:37 pm")).toBeInTheDocument();

    const verifiedRow = rowOf("Weekly sweep");
    expect(within(verifiedRow).getByText("VERIFIED")).toBeInTheDocument();
    expect(within(verifiedRow).queryByText("awaiting")).not.toBeInTheDocument();
  });

  it("shows skipped timestamps and reasons in completed history", async () => {
    payload.completed = [
      makeTask({
        id: 20,
        title: "Evening ration check",
        status: "SKIPPED",
        skipped_at: "2026-08-05T13:00:00",
        skip_reason: "No animals in pen",
        rejected_by_id: null,
        rejected_at: null,
      }),
    ];
    payload.completed_total = 1;
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("tab", { name: "Completed (1)" }));

    const row = rowOf("Evening ration check");
    expect(within(row).getByText("SKIPPED")).toBeInTheDocument();
    expect(within(row).getByText("Reason: No animals in pen")).toBeInTheDocument();
    // 13:00 naive UTC → 18:30 in the farm's Asia/Kolkata day.
    expect(within(row).getByText("5 Aug 2026, 6:30 pm")).toBeInTheDocument();
  });

  it("pages completed history using the backend total", async () => {
    let requestedOffset = "";
    server.use(
      http.get("/api/tasks", ({ request }) => {
        listCalls += 1;
        requestedOffset = new URL(request.url).searchParams.get("completed_offset") ?? "";
        return HttpResponse.json({
          ...payload,
          completed_total: 120,
          completed_limit: 50,
          completed_offset: Number(requestedOffset || 0),
        });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("tab", { name: "Completed (120)" }));
    await user.click(screen.getByRole("button", { name: "Next" }));

    await waitFor(() => expect(requestedOffset).toBe("50"));
  });

  it("shows the server error detail when tasks fail to load", async () => {
    let attempts = 0;
    server.use(
      http.get("/api/tasks", () => {
        attempts += 1;
        return attempts === 1
          ? HttpResponse.json({ detail: "tasks engine down" }, { status: 500 })
          : HttpResponse.json(payload);
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("tasks engine down");
    await user.click(screen.getByRole("button", { name: "Retry tasks" }));
    expect(await screen.findByRole("tab", { name: "Today (2)" })).toBeInTheDocument();
    expect(attempts).toBe(2);
  });

  it("blocks the page without tasks.view", async () => {
    server.use(permissionsHandler(["tasks.complete"]));
    renderWithProviders(<TasksPage />);
    expect(
      await screen.findByText("You don't have access to this page."),
    ).toBeInTheDocument();
  });

  it("shows an error state, not 'no access', when the permissions call fails", async () => {
    server.use(
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    renderWithProviders(<TasksPage />);
    expect(
      await screen.findByText(/Could not load your permissions/),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("You don't have access to this page."),
    ).not.toBeInTheDocument();
  });

  // ---------- complete / skip flows ----------

  it("completes a pending manual task and refetches", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(within(rowOf("Morning feed count")).getByRole("button", { name: "Complete" }));

    await waitFor(() =>
      expect(actionCalls).toContainEqual({ action: "complete", taskId: "1", body: null }),
    );
    await waitFor(() => expect(listCalls).toBeGreaterThanOrEqual(2));
  });

  it("locks the other transition while a task completion is in flight", async () => {
    let releaseComplete!: () => void;
    server.use(
      http.post(
        "/api/tasks/1/complete",
        () =>
          new Promise<Response>((resolve) => {
            releaseComplete = () => resolve(HttpResponse.json(TODAY_TASK));
          }),
      ),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const row = rowOf("Morning feed count");

    await user.click(within(row).getByRole("button", { name: "Complete" }));
    await waitFor(() => expect(releaseComplete).toBeTypeOf("function"));

    expect(within(row).getByRole("button", { name: "Skip" })).toBeDisabled();
    releaseComplete();
    await waitFor(() => expect(listCalls).toBeGreaterThanOrEqual(2));
  });

  it("does not refetch when completing fails on the server", async () => {
    let failed = 0;
    server.use(
      http.post("/api/tasks/:taskId/complete", () => {
        failed += 1;
        return HttpResponse.json({ detail: "duty is locked until its due date" }, { status: 409 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(within(rowOf("Morning feed count")).getByRole("button", { name: "Complete" }));

    await waitFor(() => expect(failed).toBe(1));
    expect(listCalls).toBe(1);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "duty is locked until its due date",
    );
    expect(screen.getByRole("button", { name: "Retry complete" })).toBeInTheDocument();
  });

  it("skips a pending task and refetches", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(within(rowOf("Morning feed count")).getByRole("button", { name: "Skip" }));
    const dialog = await screen.findByRole("dialog", { name: "Skip this task?" });
    await user.type(within(dialog).getByLabelText("Reason"), "not needed");
    await user.click(within(dialog).getByRole("button", { name: "Skip task" }));

    await waitFor(() =>
      expect(actionCalls).toContainEqual({
        action: "skip",
        taskId: "1",
        body: { reason: "not needed" },
      }),
    );
    await waitFor(() => expect(listCalls).toBeGreaterThanOrEqual(2));
  });

  it("dismisses the skip dialog before any transition starts", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(within(rowOf("Morning feed count")).getByRole("button", { name: "Skip" }));
    await screen.findByRole("dialog", { name: "Skip this task?" });

    await user.keyboard("{Escape}");

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(actionCalls).toHaveLength(0);
  });

  it("records a trimmed skip reason in the task audit history", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(within(rowOf("Morning feed count")).getByRole("button", { name: "Skip" }));
    const dialog = await screen.findByRole("dialog", { name: "Skip this task?" });
    await user.type(within(dialog).getByLabelText("Reason"), "  feed already issued  ");
    await user.click(within(dialog).getByRole("button", { name: "Skip task" }));

    await waitFor(() =>
      expect(actionCalls).toContainEqual({
        action: "skip",
        taskId: "1",
        body: { reason: "feed already issued" },
      }),
    );
  });

  it("does not refetch when skipping fails on the server", async () => {
    let failed = 0;
    server.use(
      http.post("/api/tasks/:taskId/skip", () => {
        failed += 1;
        return HttpResponse.json({ detail: "only assignees can skip" }, { status: 403 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(within(rowOf("Morning feed count")).getByRole("button", { name: "Skip" }));
    const skipDialog = await screen.findByRole("dialog", { name: "Skip this task?" });
    await user.type(within(skipDialog).getByLabelText("Reason"), "not needed");
    await user.click(within(skipDialog).getByRole("button", { name: "Skip task" }));

    await waitFor(() => expect(failed).toBe(1));
    expect(listCalls).toBe(1);
    expect(await screen.findByRole("alert")).toHaveTextContent("only assignees can skip");
    expect(screen.getByRole("button", { name: "Retry skip" })).toBeInTheDocument();
  });

  it("hides Complete and Skip without tasks.complete", async () => {
    server.use(permissionsHandler(["tasks.view", "tasks.verify"]));
    await renderLoaded();
    expect(screen.queryByRole("button", { name: "Complete" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Skip" })).not.toBeInTheDocument();
  });

  // ---------- verify / reject flows ----------

  async function openAwaiting() {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("tab", { name: "Awaiting verification (1)" }));
    await within(await screen.findByRole("table")).findByText("Deep-clean kidding pen");
    return user;
  }

  it("verifies an awaiting task and refetches", async () => {
    const user = await openAwaiting();
    await user.click(within(rowOf("Deep-clean kidding pen")).getByRole("button", { name: "Verify" }));

    await waitFor(() =>
      expect(actionCalls).toContainEqual({ action: "verify", taskId: "5", body: null }),
    );
    await waitFor(() => expect(listCalls).toBeGreaterThanOrEqual(2));
  });

  it("rejects with a typed note, trims it, and reopens with a blank reason", async () => {
    const user = await openAwaiting();
    const row = rowOf("Deep-clean kidding pen");
    await user.click(within(row).getByRole("button", { name: "Reject…" }));
    const dialog = await screen.findByRole("dialog", { name: "Reject duty" });
    await user.type(within(dialog).getByLabelText("Reason *"), "  pen still wet ");
    await user.click(within(dialog).getByRole("button", { name: "Reject duty" }));

    await waitFor(() =>
      expect(actionCalls).toContainEqual({
        action: "reject",
        taskId: "5",
        body: { note: "pen still wet" },
      }),
    );
    await waitFor(() =>
      expect(within(rowOf("Deep-clean kidding pen")).getByRole("button", { name: "Reject…" })),
    );
    await waitFor(() => expect(listCalls).toBeGreaterThanOrEqual(2));

    // A fresh opening starts clean: no stale reason from the last rejection.
    await user.click(
      within(rowOf("Deep-clean kidding pen")).getByRole("button", { name: "Reject…" }),
    );
    const reopened = await screen.findByRole("dialog", { name: "Reject duty" });
    expect(within(reopened).getByLabelText("Reason *")).toHaveValue("");
    expect(within(reopened).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("blocks rejection until a reason is typed (no noteless POST)", async () => {
    const user = await openAwaiting();
    const row = rowOf("Deep-clean kidding pen");
    await user.click(within(row).getByRole("button", { name: "Reject…" }));
    const dialog = await screen.findByRole("dialog", { name: "Reject duty" });

    await user.click(within(dialog).getByRole("button", { name: "Reject duty" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Reason is required.",
    );
    expect(actionCalls).toHaveLength(0);
    // Typing clears the flag; the same submit then goes through.
    await user.type(within(dialog).getByLabelText("Reason *"), "pen still wet");
    await user.click(within(dialog).getByRole("button", { name: "Reject duty" }));
    await waitFor(() =>
      expect(actionCalls).toContainEqual({
        action: "reject",
        taskId: "5",
        body: { note: "pen still wet" },
      }),
    );
  });

  it("does not refetch when verifying fails on the server", async () => {
    let failed = 0;
    server.use(
      http.post("/api/tasks/:taskId/verify", () => {
        failed += 1;
        return HttpResponse.json({ detail: "already sent back" }, { status: 400 });
      }),
    );
    const user = await openAwaiting();
    await user.click(within(rowOf("Deep-clean kidding pen")).getByRole("button", { name: "Verify" }));

    await waitFor(() => expect(failed).toBe(1));
    expect(listCalls).toBe(1);
    expect(await screen.findByRole("alert")).toHaveTextContent("already sent back");
    expect(screen.getByRole("button", { name: "Retry verify" })).toBeInTheDocument();
  });

  it("does not refetch and retains the reason when rejection fails", async () => {
    let failed = 0;
    server.use(
      http.post("/api/tasks/:taskId/reject", () => {
        failed += 1;
        return HttpResponse.json({ detail: "verification already changed" }, { status: 409 });
      }),
    );
    const user = await openAwaiting();
    const row = rowOf("Deep-clean kidding pen");
    await user.click(within(row).getByRole("button", { name: "Reject…" }));
    const dialog = await screen.findByRole("dialog", { name: "Reject duty" });
    const reason = within(dialog).getByLabelText("Reason *");
    await user.type(reason, "Still wet");
    await user.click(within(dialog).getByRole("button", { name: "Reject duty" }));

    await waitFor(() => expect(failed).toBe(1));
    expect(listCalls).toBe(1);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "verification already changed",
    );
    expect(within(dialog).getByRole("button", { name: "Retry reject" })).toBeInTheDocument();
    expect(reason).toHaveValue("Still wet");
  });

  it("shows no Verify/Reject controls without tasks.verify", async () => {
    server.use(permissionsHandler(["tasks.view", "tasks.complete"]));
    await renderLoaded();
    // The awaiting tab is hidden, so the controls are unreachable.
    expect(screen.queryByRole("button", { name: "Verify" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject…" })).not.toBeInTheDocument();
  });

  // ---------- New-duty dialog ----------

  async function openDialog() {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "New duty" }));
    return { user, dialog: await screen.findByRole("dialog") };
  }

  it("dismisses a new-duty draft before submission", async () => {
    const { user } = await openDialog();

    await user.keyboard("{Escape}");

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(createBody).toBeNull();
  });

  it("requires a title before creating", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("button", { name: "Create duty" }));
    expect(await within(dialog).findByText("Title is required")).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/title/i)).toHaveFocus();
    expect(within(dialog).getByLabelText(/title/i)).toHaveAccessibleDescription(
      "Title is required",
    );
    expect(createBody).toBeNull();
  });

  it("requires a due date before creating", async () => {
    const { user, dialog } = await openDialog();
    fireEvent.change(within(dialog).getByLabelText(/due date/i), {
      target: { value: "" },
    });
    await user.type(within(dialog).getByLabelText(/title/i), "Check fences");
    await user.click(within(dialog).getByRole("button", { name: "Create duty" }));
    expect(await within(dialog).findByText("Due date is required")).toBeInTheDocument();
    expect(createBody).toBeNull();
  });

  it.each(["0", "abc", "3651", "3.5"])(
    "rejects the invalid recurrence %s",
    async (recur) => {
      const { user, dialog } = await openDialog();
      await user.type(within(dialog).getByLabelText(/title/i), "Check fences");
      fireEvent.change(within(dialog).getByLabelText(/repeats every/i), {
        target: { value: recur },
      });
      await user.click(within(dialog).getByRole("button", { name: "Create duty" }));
      expect(
        await within(dialog).findByText("Must be a whole number of days (1–3650)"),
      ).toBeInTheDocument();
      expect(createBody).toBeNull();
    },
  );

  it("creates a duty with trimmed title, defaults and null optionals", async () => {
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText(/title/i), "  Check fences  ");
    await user.click(within(dialog).getByRole("button", { name: "Create duty" }));

    await waitFor(() => expect(createBody).not.toBeNull());
    expect(createBody).toEqual({
      title: "Check fences",
      due_date: localTodayISO(),
      category: "OTHER",
      recur_days: null,
      animal_id: null,
      assigned_role_id: null,
      assigned_user_id: null,
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(listCalls).toBeGreaterThanOrEqual(2));
  });

  it("does not dismiss the create dialog while its write is in flight", async () => {
    let releaseCreate!: () => void;
    let markCreateStarted!: () => void;
    const createGate = new Promise<void>((resolve) => {
      releaseCreate = resolve;
    });
    const createStarted = new Promise<void>((resolve) => {
      markCreateStarted = resolve;
    });
    server.use(
      http.post("/api/tasks", async ({ request }) => {
        createBody = (await request.json()) as Record<string, unknown>;
        markCreateStarted();
        await createGate;
        return HttpResponse.json(makeTask({ id: 50 }), { status: 201 });
      }),
    );
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText(/title/i), "Check fences");
    await user.click(within(dialog).getByRole("button", { name: "Create duty" }));
    await createStarted;

    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Creating…" })).toBeDisabled();
    expect(within(dialog).getByLabelText(/title/i)).toBeDisabled();

    releaseCreate();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("creates at most one duty across same-render duplicate form events", async () => {
    let calls = 0;
    let releaseCreate!: () => void;
    const createGate = new Promise<void>((resolve) => {
      releaseCreate = resolve;
    });
    server.use(
      http.post("/api/tasks", async ({ request }) => {
        calls += 1;
        createBody = (await request.json()) as Record<string, unknown>;
        await createGate;
        return HttpResponse.json(makeTask({ id: 50 }), { status: 201 });
      }),
    );
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText(/title/i), "Check fences");
    const form = within(dialog).getByRole("button", { name: "Create duty" }).closest("form")!;

    fireEvent.submit(form);
    fireEvent.submit(form);
    await waitFor(() => expect(calls).toBeGreaterThan(0));
    releaseCreate();

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(calls).toBe(1);
  });

  it("links a manually created duty to an authorised animal", async () => {
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText(/title/i), "Check Radha");
    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Animal (optional)" }),
      "G-007 — Radha",
    );
    await user.click(within(dialog).getByRole("button", { name: "Create duty" }));

    await waitFor(() => expect(createBody).not.toBeNull());
    expect(createBody).toMatchObject({ animal_id: 7 });
  });

  it("explains why the animal selector is unavailable without animals.view", async () => {
    server.use(permissionsHandler(["tasks.view", "tasks.create", "tasks.complete"]));
    const { dialog } = await openDialog();

    expect(within(dialog).queryByLabelText("Animal (optional)")).not.toBeInTheDocument();
    expect(
      within(dialog).getByText(/don't have animal access.*without an animal link/i),
    ).toBeInTheDocument();
  });

  it("accepts the boundary recurrence of 3650 days", async () => {
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText(/title/i), "Decennial check");
    fireEvent.change(within(dialog).getByLabelText(/repeats every/i), {
      target: { value: "3650" },
    });
    await user.click(within(dialog).getByRole("button", { name: "Create duty" }));

    await waitFor(() => expect(createBody).not.toBeNull());
    expect(createBody!.recur_days).toBe(3650);
  });

  it("rejects a recurring due date that cannot produce a representable successor", async () => {
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText(/title/i), "Far-future inspection");
    fireEvent.change(within(dialog).getByLabelText(/due date/i), {
      target: { value: "2100-12-31" },
    });
    fireEvent.change(within(dialog).getByLabelText(/repeats every/i), {
      target: { value: "1" },
    });
    await user.click(within(dialog).getByRole("button", { name: "Create duty" }));

    expect(
      await within(dialog).findByText(
        "Recurring due date is too late to schedule its next occurrence",
      ),
    ).toBeInTheDocument();
    expect(createBody).toBeNull();

    fireEvent.change(within(dialog).getByLabelText(/due date/i), {
      target: { value: "2100-12-30" },
    });
    await user.click(within(dialog).getByRole("button", { name: "Create duty" }));

    await waitFor(() => expect(createBody).not.toBeNull());
    expect(createBody).toMatchObject({ due_date: "2100-12-30", recur_days: 1 });
  });

  it("assigns to a role, clearing any worker, and posts the role id", async () => {
    const { user, dialog } = await openDialog();
    expect(teamCalls).toBeGreaterThanOrEqual(1);
    await user.type(within(dialog).getByLabelText(/title/i), "Herd check");

    const combos = () => within(dialog).getAllByRole("combobox");
    // Order: category, animal, role, worker. Pick a worker first, then a role — the
    // role selection clears the worker.
    await pickOption(user, combos()[3], /Raju \(Vet\)/);
    await pickOption(user, combos()[2], "Vet");

    // Closed triggers show labels, not raw ids; picking a role cleared the worker.
    expect(combos()[2]).toHaveTextContent("Vet");
    expect(combos()[3]).toHaveTextContent("— none —");

    await user.click(within(dialog).getByRole("button", { name: "Create duty" }));
    await waitFor(() => expect(createBody).not.toBeNull());
    expect(createBody).toMatchObject({ assigned_role_id: 2, assigned_user_id: null });
  });

  it("lists only active workers in the worker select", async () => {
    const { user, dialog } = await openDialog();
    const workerSelect = within(dialog).getAllByRole("combobox")[3];
    await user.click(workerSelect);
    const options = await screen.findAllByRole("option");
    const names = options.map((o) => o.textContent ?? "");
    expect(names.some((n) => n.includes("Raju"))).toBe(true);
    expect(names.some((n) => n.includes("Ex Worker"))).toBe(false);
  });

  it("keeps an active worker's label selected and posts only that worker id", async () => {
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText(/title/i), "Check isolation pen");
    const combos = () => within(dialog).getAllByRole("combobox");

    await pickOption(user, combos()[3], /Raju \(Vet\)/);
    expect(combos()[3]).toHaveTextContent("Raju (Vet)");
    expect(combos()[2]).toHaveTextContent("— none —");
    await user.click(within(dialog).getByRole("button", { name: "Create duty" }));

    await waitFor(() => expect(createBody).not.toBeNull());
    expect(createBody).toMatchObject({ assigned_role_id: null, assigned_user_id: 9 });
  });

  it("keeps the dialog open when creating fails on the server", async () => {
    server.use(
      http.post("/api/tasks", () =>
        HttpResponse.json({ detail: "role not on this farm" }, { status: 422 }),
      ),
    );
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText(/title/i), "Check fences");
    await user.click(within(dialog).getByRole("button", { name: "Create duty" }));

    await waitFor(() => expect(listCalls).toBe(1));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(within(dialog).getByRole("alert")).toHaveTextContent("role not on this farm");
    expect(within(dialog).getByRole("button", { name: "Retry create" })).toBeInTheDocument();
  });

  it("announces assignment lookup failures without blocking unassigned creation", async () => {
    let attempts = 0;
    server.use(
      http.get("/api/team", () => {
        attempts += 1;
        return attempts === 1
          ? HttpResponse.json({ detail: "team directory unavailable" }, { status: 503 })
          : HttpResponse.json(TEAM_PAYLOAD);
      }),
    );
    const { user, dialog } = await openDialog();

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "team directory unavailable",
    );
    // Assignment is optional: with no role/worker chosen the duty submits
    // unassigned, so a broken team directory must not block the button.
    expect(within(dialog).getByRole("button", { name: "Create duty" })).toBeEnabled();
    await user.click(within(dialog).getByRole("button", { name: "Retry assignments" }));

    await waitFor(() => expect(attempts).toBe(2));
    expect(await within(dialog).findByLabelText("Assign to role")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Create duty" })).toBeEnabled();
  });

  it("shows the unassigned note and skips the team fetch without team.manage", async () => {
    server.use(permissionsHandler(["tasks.view", "tasks.create", "tasks.complete"]));
    const { dialog } = await openDialog();
    expect(
      within(dialog).getByText(/the duty will be created unassigned/),
    ).toBeInTheDocument();
    expect(teamCalls).toBe(0);
  });

  it("hides the New duty button without tasks.create", async () => {
    server.use(permissionsHandler(["tasks.view", "tasks.complete"]));
    await renderLoaded();
    expect(screen.queryByRole("button", { name: "New duty" })).not.toBeInTheDocument();
  });

  // REGRESSION — the due-date default was captured once at page mount and a
  // bare reset() restored that snapshot, so a tab left open across the farm's
  // midnight created duties dated yesterday, i.e. already Overdue.
  it("prefills the due date with the farm's current date, not the mount-time date", async () => {
    vi.setSystemTime(new Date("2026-08-09T12:30:00Z")); // 18:00 IST
    try {
      const user = userEvent.setup();
      await renderLoaded();

      vi.setSystemTime(new Date("2026-08-10T12:30:00Z")); // 18:00 IST, next day
      await user.click(screen.getByRole("button", { name: "New duty" }));
      const dialog = await screen.findByRole("dialog");

      expect(within(dialog).getByLabelText("Due date *")).toHaveValue("2026-08-10");
    } finally {
      vi.useRealTimers();
    }
  });
});
