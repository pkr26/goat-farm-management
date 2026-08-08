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
import { addDays, utcToday } from "@/lib/format";

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

/** UTC-relative fixture dates: the page compares against utcToday()
 * (audit 7-5), so browser-local fixtures drift a day near midnight. */
const TODAY = utcToday();

/** Browser-local today — matches the create form's write-side date default
 * (the backend accepts one day of headroom, so writes stay local). */
function localTodayISO(): string {
  const d = new Date();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
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
    completed_by_id: null,
    completed_at: null,
    verified_by_id: null,
    verified_at: null,
    verification_note: null,
    skipped_by_id: null,
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
};

function fullPayload(): TabsPayload {
  return {
    today: [TODAY_TASK, SENT_BACK_TASK],
    overdue: [OVERDUE_TASK],
    upcoming: [UPCOMING_TASK],
    awaiting: [AWAITING_TASK],
    completed: [AWAITING_TASK, VERIFIED_TASK],
  };
}

function rowOf(title: string): HTMLElement {
  const row = screen.getByText(title).closest("tr");
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
    expect(screen.getByRole("tab", { name: "Completed" })).toBeInTheDocument();
  });

  it("hides the verification tabs without tasks.verify", async () => {
    server.use(permissionsHandler(["tasks.view", "tasks.create", "tasks.complete"]));
    await renderLoaded();
    expect(
      screen.queryByRole("tab", { name: /Awaiting verification/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Completed" })).not.toBeInTheDocument();
  });

  it("switches tabs to show their tasks", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    expect(screen.getByText("Morning feed count")).toBeInTheDocument();
    expect(screen.queryByText("Rotate buck")).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Upcoming (1)" }));
    expect(await screen.findByText("Rotate buck")).toBeInTheDocument();
    expect(screen.queryByText("Morning feed count")).not.toBeInTheDocument();
  });

  it("shows the per-tab empty message", async () => {
    payload.today = [];
    renderWithProviders(<TasksPage />);
    await screen.findByRole("tab", { name: "Today (0)" });
    expect(screen.getByText("No today tasks.")).toBeInTheDocument();
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
    expect(screen.getByText("Sent back: far pen still dirty")).toBeInTheDocument();
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
      "/animals/3",
    );
  });

  it("completed tab shows status, awaiting marker and formatted completion time", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("tab", { name: "Completed" }));

    const awaitingRow = rowOf("Deep-clean kidding pen");
    expect(within(awaitingRow).getByText("DONE")).toBeInTheDocument();
    expect(within(awaitingRow).getByText("awaiting")).toBeInTheDocument();
    // completed_at is naive UTC: the cell renders that instant in local time
    // (was: the naive string passed off as browser-local).
    const expectedCompletedAt = (() => {
      const d = new Date("2026-08-05T14:07:00Z");
      const p = (n: number) => String(n).padStart(2, "0");
      return `${p(d.getDate())}-${p(d.getMonth() + 1)} ${p(d.getHours())}:${p(d.getMinutes())}`;
    })();
    expect(within(awaitingRow).getByText(expectedCompletedAt)).toBeInTheDocument();

    const verifiedRow = rowOf("Weekly sweep");
    expect(within(verifiedRow).getByText("VERIFIED")).toBeInTheDocument();
    expect(within(verifiedRow).queryByText("awaiting")).not.toBeInTheDocument();
  });

  it("shows the server error detail when tasks fail to load", async () => {
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json({ detail: "tasks engine down" }, { status: 500 }),
      ),
    );
    renderWithProviders(<TasksPage />);
    expect(await screen.findByText("tasks engine down")).toBeInTheDocument();
  });

  it("blocks the page without tasks.view", async () => {
    server.use(permissionsHandler(["tasks.complete"]));
    renderWithProviders(<TasksPage />);
    expect(
      await screen.findByText("You don't have access to this page."),
    ).toBeInTheDocument();
  });

  it("shows an error state, not 'no access', when the permissions call fails (audit 7-6)", async () => {
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
  });

  it("skips a pending task and refetches", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(within(rowOf("Morning feed count")).getByRole("button", { name: "Skip" }));

    await waitFor(() =>
      expect(actionCalls).toContainEqual({ action: "skip", taskId: "1", body: null }),
    );
    await waitFor(() => expect(listCalls).toBeGreaterThanOrEqual(2));
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

    await waitFor(() => expect(failed).toBe(1));
    expect(listCalls).toBe(1);
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
    await screen.findByText("Deep-clean kidding pen");
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

  it("rejects with a typed note, sends it, and clears the note box", async () => {
    const user = await openAwaiting();
    const row = rowOf("Deep-clean kidding pen");
    await user.type(within(row).getByPlaceholderText("reason (sent back)"), "  pen still wet ");
    await user.click(within(row).getByRole("button", { name: "Reject" }));

    await waitFor(() =>
      expect(actionCalls).toContainEqual({
        action: "reject",
        taskId: "5",
        body: { note: "pen still wet" },
      }),
    );
    await waitFor(() =>
      expect(within(rowOf("Deep-clean kidding pen")).getByPlaceholderText("reason (sent back)")),
    );
    expect(
      within(rowOf("Deep-clean kidding pen")).getByPlaceholderText("reason (sent back)"),
    ).toHaveValue("");
    await waitFor(() => expect(listCalls).toBeGreaterThanOrEqual(2));
  });

  it("rejects without a note as note: null (SPEC expects a note; backend allows none)", async () => {
    const user = await openAwaiting();
    await user.click(
      within(rowOf("Deep-clean kidding pen")).getByRole("button", { name: "Reject" }),
    );
    await waitFor(() =>
      expect(actionCalls).toContainEqual({
        action: "reject",
        taskId: "5",
        body: { note: null },
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
  });

  it("shows no Verify/Reject controls without tasks.verify", async () => {
    server.use(permissionsHandler(["tasks.view", "tasks.complete"]));
    await renderLoaded();
    // The awaiting tab is hidden, so the controls are unreachable.
    expect(screen.queryByRole("button", { name: "Verify" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });

  // ---------- New-duty dialog ----------

  async function openDialog() {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "New duty" }));
    return { user, dialog: await screen.findByRole("dialog") };
  }

  it("requires a title before creating", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("button", { name: "Create duty" }));
    expect(await within(dialog).findByText("Title is required")).toBeInTheDocument();
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
      assigned_role_id: null,
      assigned_user_id: null,
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(listCalls).toBeGreaterThanOrEqual(2));
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

  it("assigns to a role, clearing any worker, and posts the role id", async () => {
    const { user, dialog } = await openDialog();
    expect(teamCalls).toBeGreaterThanOrEqual(1);
    await user.type(within(dialog).getByLabelText(/title/i), "Herd check");

    const combos = () => within(dialog).getAllByRole("combobox");
    // Order: category, role, worker. Pick a worker first, then a role — the
    // role selection clears the worker.
    await pickOption(user, combos()[2], /Raju \(Vet\)/);
    await pickOption(user, combos()[1], "Vet");

    // Closed triggers show labels, not raw ids; picking a role cleared the worker.
    expect(combos()[1]).toHaveTextContent("Vet");
    expect(combos()[2]).toHaveTextContent("— none —");

    await user.click(within(dialog).getByRole("button", { name: "Create duty" }));
    await waitFor(() => expect(createBody).not.toBeNull());
    expect(createBody).toMatchObject({ assigned_role_id: 2, assigned_user_id: null });
  });

  it("lists only active workers in the worker select", async () => {
    const { user, dialog } = await openDialog();
    const workerSelect = within(dialog).getAllByRole("combobox")[2];
    await user.click(workerSelect);
    const options = await screen.findAllByRole("option");
    const names = options.map((o) => o.textContent ?? "");
    expect(names.some((n) => n.includes("Raju"))).toBe(true);
    expect(names.some((n) => n.includes("Ex Worker"))).toBe(false);
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
});
