/**
 * Mutation-hardening tests for the tasks page: URL state (tab deep links,
 * offset canonicalization), board-settling banner, row guards and hints,
 * enum labels, verification/skip annotations, create-duty payload mapping
 * and assignment selects, dialog close guards and error branches.
 */

import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { TaskOut } from "@/api/generated/models";
import { permissionsHandler, server, TEST_USER } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { addDays, farmToday } from "@/lib/format";

import TasksPage from "./page";

const { navState, replaceMock, pushMock } = vi.hoisted(() => ({
  navState: { search: "" },
  replaceMock: vi.fn(),
  pushMock: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: pushMock,
    replace: replaceMock,
    prefetch: vi.fn(),
  }),
  usePathname: () => "/tasks",
  useSearchParams: () => new URLSearchParams(navState.search),
  useParams: () => ({}),
}));

beforeAll(() => {
  Object.assign(window.HTMLElement.prototype, {
    hasPointerCapture: () => false,
    setPointerCapture: () => {},
    releasePointerCapture: () => {},
    scrollIntoView: () => {},
  });
});

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

const TASKS_URLS: string[] = [];

function tasksHandler(payload: Record<string, unknown>) {
  return http.get("/api/tasks", ({ request }) => {
    TASKS_URLS.push(new URL(request.url).search);
    return HttpResponse.json(payload);
  });
}

function emptyBoard(extra: Record<string, unknown> = {}) {
  return {
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
    ...extra,
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

beforeEach(() => {
  navState.search = "";
  replaceMock.mockReset();
  pushMock.mockReset();
  TASKS_URLS.length = 0;
  // The Completed tab resolves finished-by names via /api/team for
  // team.manage holders; an empty roster means "no name to show".
  server.use(
    http.get("/api/team", () =>
      HttpResponse.json({ memberships: [], roles: [], permission_groups: [], permission_labels: {} }),
    ),
  );
});

describe("TasksPage URL state", () => {
  it("rewrites a deep-linked awaiting tab the viewer cannot see back to today", async () => {
    server.use(
      permissionsHandler(["tasks.view"]),
      tasksHandler(
        emptyBoard({
          today: [makeTask({ id: 1, title: "Morning feed" })],
          today_total: 1,
        }),
      ),
    );
    navState.search = "?tab=awaiting";
    renderWithProviders(<TasksPage />);

    expect(await within(await screen.findByRole("table")).findByText("Morning feed")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Today (1)" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith("/tasks?tab=today"),
    );
  });

  it("keeps a legitimate awaiting deep link and never rewrites a canonical one", async () => {
    server.use(
      permissionsHandler(["tasks.view", "tasks.verify"]),
      tasksHandler(
        emptyBoard({
          awaiting: [
            makeTask({ id: 2, title: "Awaited duty", status: "DONE", needs_verification: true }),
          ],
          awaiting_total: 1,
        }),
      ),
    );
    navState.search = "?tab=awaiting";
    renderWithProviders(<TasksPage />);

    expect(await within(await screen.findByRole("table")).findByText("Awaited duty")).toBeInTheDocument();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("canonicalizes a malformed offset out of the URL", async () => {
    server.use(tasksHandler(emptyBoard()));
    navState.search = "?tab=overdue&overdue_offset=05";
    renderWithProviders(<TasksPage />);

    await screen.findByRole("tab", { name: "Overdue (0)" });
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/tasks?tab=overdue"));
  });

  it("clamps an offset past a shrunken bucket and never requests negative offsets", async () => {
    server.use(tasksHandler(emptyBoard()));
    navState.search = "?tab=today&today_offset=100";
    renderWithProviders(<TasksPage />);

    await screen.findByRole("tab", { name: "Today (0)" });
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/tasks?tab=today"));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    for (const url of TASKS_URLS) {
      expect(url).not.toMatch(/_offset=-\d/);
    }
    expect(TASKS_URLS[0]).toContain("active_limit=50");
  });

  it("writes tab changes back to the URL, preserving foreign params", async () => {
    server.use(
      tasksHandler(
        emptyBoard({
          today: [makeTask({ id: 1, title: "Morning feed" })],
          today_total: 1,
        }),
      ),
    );
    navState.search = "?tab=today&from=dashboard";
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);
    await within(await screen.findByRole("table")).findByText("Morning feed");

    await user.click(screen.getByRole("tab", { name: "Overdue (0)" }));

    expect(replaceMock).toHaveBeenCalledWith("/tasks?tab=overdue&from=dashboard");
  });
});

describe("TasksPage board-settling banner", () => {
  it("announces Updating the duty board while a page turn settles, then clears", async () => {
    let release!: () => void;
    let announce!: () => void;
    const started = new Promise<void>((resolve) => {
      announce = resolve;
    });
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get("/api/tasks", ({ request }) => {
        const url = new URL(request.url).search;
        TASKS_URLS.push(url);
        if (url.includes("today_offset=50")) {
          announce();
          return gate.then(() =>
            HttpResponse.json(
              emptyBoard({ today_total: 60, today_offset: 50 }),
            ),
          );
        }
        return HttpResponse.json(emptyBoard({ today_total: 60 }));
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);
    await screen.findByRole("tab", { name: "Today (60)" });

    await user.click(
      within(screen.getByRole("navigation", { name: "today tasks pagination" })).getByRole(
        "button",
        { name: "Next" },
      ),
    );
    await started;

    expect(screen.getByText("Updating the duty board…")).toBeInTheDocument();

    await act(async () => {
      release();
    });
    await waitFor(() =>
      expect(screen.queryByText("Updating the duty board…")).not.toBeInTheDocument(),
    );
  });

  it("shows no settling banner on an idle board", async () => {
    server.use(
      tasksHandler(
        emptyBoard({
          today: [makeTask({ id: 1, title: "Morning feed" })],
          today_total: 1,
        }),
      ),
    );
    renderWithProviders(<TasksPage />);
    await within(await screen.findByRole("table")).findByText("Morning feed");
    expect(screen.queryByText("Updating the duty board…")).not.toBeInTheDocument();
  });
});

describe("TasksPage row guards and annotations", () => {
  it("locks a future auto-generated duty behind the not-due hint", async () => {
    const futureAuto = makeTask({
      id: 3,
      title: "Weigh batch kids",
      auto_generated: true,
      due_date: TOMORROW,
    });
    server.use(
      tasksHandler(
        emptyBoard({ today: [futureAuto], today_total: 1 }),
      ),
    );
    renderWithProviders(<TasksPage />);

    await within(await screen.findByRole("table")).findByText("Weigh batch kids");
    const row = rowOf("Weigh batch kids");
    expect(within(row).queryByRole("button", { name: "Complete" })).not.toBeInTheDocument();
    expect(within(row).queryByRole("link", { name: "Open form" })).not.toBeInTheDocument();
    expect(
      within(row).getByText("Not due yet — actions open on the due date."),
    ).toBeInTheDocument();
  });

  it("labels task categories through the enum map", async () => {
    const tasks = [
      makeTask({ id: 1, title: "Feed the herd", category: "FEED" }),
      makeTask({ id: 2, title: "Scrub troughs", category: "CLEANING" }),
      makeTask({ id: 3, title: "Odd job", category: "OTHER" }),
    ];
    server.use(tasksHandler(emptyBoard({ today: tasks, today_total: 3 })));
    renderWithProviders(<TasksPage />);

    await within(await screen.findByRole("table")).findByText("Feed the herd");
    expect(within(rowOf("Feed the herd")).getByText("Feeding")).toBeInTheDocument();
    expect(within(rowOf("Scrub troughs")).getByText("Cleaning")).toBeInTheDocument();
    expect(within(rowOf("Odd job")).getByText("Other")).toBeInTheDocument();
  });

  it("shows the send-back note on a pending duty", async () => {
    const sentBack = makeTask({
      id: 4,
      title: "Vaccinate herd",
      verification_note: "fix the dosage",
    });
    server.use(tasksHandler(emptyBoard({ today: [sentBack], today_total: 1 })));
    renderWithProviders(<TasksPage />);

    await within(await screen.findByRole("table")).findByText("Vaccinate herd");
    const row = rowOf("Vaccinate herd");
    expect(within(row).getByText("Sent back: fix the dosage")).toBeInTheDocument();
  });

  it("annotates skipped history on the completed tab only", async () => {
    const skipped = makeTask({
      id: 5,
      title: "Foot bath",
      status: "SKIPPED",
      skip_reason: "out of meds",
      skipped_at: "2026-08-20T09:15:00",
    });
    const done = makeTask({
      id: 6,
      title: "Clean pens",
      status: "DONE",
      completed_by_id: TEST_USER.id + 5,
      completed_at: "2026-08-21T10:00:00",
    });
    const pendingWithReason = makeTask({
      id: 7,
      title: "Pending duty",
      skip_reason: "stale reason",
    });
    server.use(
      tasksHandler(
        emptyBoard({
          today: [pendingWithReason],
          today_total: 1,
          completed: [skipped, done],
          completed_total: 2,
        }),
      ),
    );
    navState.search = "?tab=completed";
    renderWithProviders(<TasksPage />);

    await within(await screen.findByRole("table")).findByText("Foot bath");
    const skippedRow = rowOf("Foot bath");
    expect(within(skippedRow).getByText("Reason: out of meds")).toBeInTheDocument();
    expect(within(rowOf("Clean pens")).queryByText(/Reason:/)).not.toBeInTheDocument();

    // Back on Today, a pending duty never shows a skip annotation.
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Today (1)" }));
    expect(within(rowOf("Pending duty")).queryByText(/Reason:/)).not.toBeInTheDocument();
  });
});

describe("TasksPage create-duty dialog", () => {
  let createBody: Record<string, unknown> | null;
  let createCalls: number;

  const TEAM_DIRECTORY = {
    memberships: [
      {
        id: 2,
        user_id: 22,
        email: "ravi@example.com",
        name: "Ravi Kumar",
        role_id: 10,
        role_name: "Helper",
        is_active: true,
        can_reset_password: true,
        reset_password_block_reason: null,
      },
      {
        id: 3,
        user_id: 23,
        email: "sita@example.com",
        name: null,
        role_id: 10,
        role_name: "Helper",
        is_active: false,
        can_reset_password: false,
        reset_password_block_reason: null,
      },
    ],
    roles: [{ id: 10, code: null, name: "Helper", description: null, permissions: [], revision: 1, member_count: 1 }],
    permission_groups: [],
    permission_labels: {},
  };

  beforeEach(() => {
    createBody = null;
    createCalls = 0;
    server.use(
      tasksHandler(emptyBoard()),
      http.get("/api/team", () => HttpResponse.json(TEAM_DIRECTORY)),
      http.get("/api/animals", () => HttpResponse.json({ animals: [], total: 0 })),
      http.post("/api/tasks", async ({ request }) => {
        createCalls += 1;
        createBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(makeTask({ id: 99 }), { status: 201 });
      }),
    );
  });

  async function openDialog() {
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);
    await screen.findByRole("tab", { name: "Today (0)" });
    await user.click(screen.getByRole("button", { name: "New duty" }));
    const dialog = await screen.findByRole("dialog");
    return { user, dialog };
  }

  it("offers only active workers and directory roles in the assignment selects", async () => {
    const { user, dialog } = await openDialog();

    await user.click(within(dialog).getByRole("combobox", { name: /assign to worker/i }));
    expect(await screen.findByRole("option", { name: /Ravi Kumar/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /sita@example.com/ })).not.toBeInTheDocument();

    await user.click(within(dialog).getByRole("combobox", { name: /assign to role/i }));
    expect(await screen.findByRole("option", { name: "Helper" })).toBeInTheDocument();
  });

  it("trims the title and maps NONE sentinels to null", async () => {
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText(/title/i), "  Water troughs  ");
    await user.click(within(dialog).getByRole("button", { name: "Create duty" }));

    await waitFor(() => expect(createBody).not.toBeNull());
    expect(createBody).toMatchObject({
      title: "Water troughs",
      animal_id: null,
      recur_days: null,
      assigned_role_id: null,
      assigned_user_id: null,
    });
  });

  it("maps a chosen role to its id and clears the worker, and vice versa", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);
    await screen.findByRole("tab", { name: "Today (0)" });

    // First create: assign a role.
    await user.click(screen.getByRole("button", { name: "New duty" }));
    let dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/title/i), "Duty");
    await user.click(within(dialog).getByRole("combobox", { name: /assign to role/i }));
    await user.click(await screen.findByRole("option", { name: "Helper" }));
    await user.click(within(dialog).getByRole("button", { name: "Create duty" }));

    await waitFor(() => expect(createCalls).toBe(1));
    expect(createBody).toMatchObject({ assigned_role_id: 10, assigned_user_id: null });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    // Second create: assign a worker instead; the role resets to none.
    await user.click(screen.getByRole("button", { name: "New duty" }));
    dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/title/i), "Duty two");
    await user.click(within(dialog).getByRole("combobox", { name: /assign to worker/i }));
    await user.click(await screen.findByRole("option", { name: /Ravi Kumar/ }));
    await user.click(within(dialog).getByRole("button", { name: "Create duty" }));

    await waitFor(() => expect(createCalls).toBe(2));
    expect(createBody).toMatchObject({ assigned_role_id: null, assigned_user_id: 22 });
  });

  it("rejects recurrence values outside the whole-number window", async () => {
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText(/title/i), "Duty");
    await user.type(within(dialog).getByLabelText(/repeats every/i), "0");
    await user.click(within(dialog).getByRole("button", { name: "Create duty" }));

    expect(
      await within(dialog).findByText("Must be a whole number of days (1–3650)"),
    ).toBeInTheDocument();
    expect(createCalls).toBe(0);
  });

  it("refetches the board after a successful create and clears the error on reopen", async () => {
    let calls = 0;
    server.use(
      tasksHandler(emptyBoard()),
      http.get("/api/team", () => HttpResponse.json(TEAM_DIRECTORY)),
      http.get("/api/animals", () => HttpResponse.json({ animals: [], total: 0 })),
      http.post("/api/tasks", async () => {
        calls += 1;
        if (calls === 1) {
          return HttpResponse.json({ detail: "title too spicy" }, { status: 400 });
        }
        return HttpResponse.json(makeTask({ id: 99 }), { status: 201 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);
    await screen.findByRole("tab", { name: "Today (0)" });
    const getsBefore = TASKS_URLS.length;

    await user.click(screen.getByRole("button", { name: "New duty" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/title/i), "Duty");
    await user.click(within(dialog).getByRole("button", { name: "Create duty" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("title too spicy");
    expect(within(dialog).getByRole("button", { name: "Retry create" })).toBeEnabled();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "New duty" }));
    const reopened = await screen.findByRole("dialog");
    expect(within(reopened).queryByRole("alert")).not.toBeInTheDocument();
    await user.type(within(reopened).getByLabelText(/title/i), "Duty");
    await user.click(within(reopened).getByRole("button", { name: "Create duty" }));

    await waitFor(() => expect(TASKS_URLS.length).toBeGreaterThan(getsBefore));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });

  it("keeps the dialog open while a create is in flight", async () => {
    let release!: () => void;
    let announce!: () => void;
    const started = new Promise<void>((resolve) => {
      announce = resolve;
    });
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.post("/api/tasks", async () => {
        announce();
        await gate;
        return HttpResponse.json(makeTask({ id: 99 }), { status: 201 });
      }),
    );
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText(/title/i), "Duty");
    await user.click(within(dialog).getByRole("button", { name: "Create duty" }));
    await started;

    expect(within(dialog).getByRole("button", { name: "Creating…" })).toBeDisabled();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await act(async () => {
      release();
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});

describe("TasksPage action + error branches", () => {
  it("completes a duty and refreshes the board", async () => {
    const manual = makeTask({ id: 8, title: "Clean water troughs" });
    server.use(
      tasksHandler(emptyBoard({ today: [manual], today_total: 1 })),
      http.post("/api/tasks/8/complete", () => HttpResponse.json(manual, { status: 200 })),
    );
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);
    await within(await screen.findByRole("table")).findByText("Clean water troughs");
    const getsBefore = TASKS_URLS.length;

    await user.click(within(rowOf("Clean water troughs")).getByRole("button", { name: "Complete" }));

    await waitFor(() => expect(TASKS_URLS.length).toBeGreaterThan(getsBefore));
  });

  it("shows the generic tasks error and retries into the board", async () => {
    let calls = 0;
    server.use(
      http.get("/api/tasks", () => {
        calls += 1;
        TASKS_URLS.push("");
        if (calls === 1) return HttpResponse.error();
        return HttpResponse.json(
          emptyBoard({ today: [makeTask({ id: 9, title: "Backlog duty" })], today_total: 1 }),
        );
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not load tasks.");
    await user.click(screen.getByRole("button", { name: "Retry tasks" }));

    expect(await within(await screen.findByRole("table")).findByText("Backlog duty")).toBeInTheDocument();
  });
});

describe("TasksPage round-2 mutation survivors", () => {
  it("badges recurring duties with their cadence", async () => {
    server.use(
      tasksHandler(
        emptyBoard({ today: [makeTask({ id: 3, title: "Foot bath", recur_days: 7 })], today_total: 1 }),
      ),
    );
    renderWithProviders(<TasksPage />);

    expect(await within(await screen.findByRole("table")).findByText("Foot bath")).toBeInTheDocument();
    expect(tableScope().getByText("every 7d")).toBeInTheDocument();
  });

  it("keeps the send-back note off completed duties", async () => {
    server.use(
      tasksHandler(
        emptyBoard({
          completed: [makeTask({ id: 4, title: "Old check", status: "DONE", verification_note: "redo properly" })],
          completed_total: 1,
        }),
      ),
    );
    renderWithProviders(<TasksPage />);
    await screen.findByRole("tab", { name: "Today (0)" });

    await userEvent.setup().click(screen.getByRole("tab", { name: /Completed/ }));
    expect(await within(await screen.findByRole("table")).findByText("Old check")).toBeInTheDocument();
    expect(screen.queryByText(/Sent back:/)).not.toBeInTheDocument();
  });

  it("recovers the board when the permissions retry succeeds", async () => {
    let calls = 0;
    server.use(
      http.get("/api/auth/permissions", () => {
        calls += 1;
        return calls === 1
          ? new HttpResponse(null, { status: 500 })
          : HttpResponse.json({ is_owner: true, permissions: ["tasks.view", "tasks.create"] });
      }),
      tasksHandler(emptyBoard({ today: [makeTask({ id: 5, title: "Pending duty" })], today_total: 1 })),
    );
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);

    expect(
      await screen.findByText("Could not load your permissions — refresh the page to try again."),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry permissions" }));

    expect(await within(await screen.findByRole("table")).findByText("Pending duty")).toBeInTheDocument();
    expect(calls).toBeGreaterThanOrEqual(2);
  });
});

describe("TasksPage round-2 mutation survivors (recurrence lock)", () => {
  it("locks a future recurring duty even when it is not auto-generated", async () => {
    const futureRecurring = makeTask({
      id: 6,
      title: "Rotate pasture",
      auto_generated: false,
      recur_days: 7,
      due_date: TOMORROW,
    });
    server.use(tasksHandler(emptyBoard({ today: [futureRecurring], today_total: 1 })));
    renderWithProviders(<TasksPage />);

    await within(await screen.findByRole("table")).findByText("Rotate pasture");
    const row = rowOf("Rotate pasture");
    expect(within(row).queryByRole("button", { name: "Complete" })).not.toBeInTheDocument();
    expect(
      within(row).getByText("Not due yet — actions open on the due date."),
    ).toBeInTheDocument();
  });
});
