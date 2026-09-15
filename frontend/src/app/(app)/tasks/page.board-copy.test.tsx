/**
 * Tasks page — a second pass over the copy and wiring the board itself
 * promises: due-date urgency badges, the toast + row alert of every duty
 * transition (and their clearing on a successful retry), in-flight and retry
 * labels, the accessible description of each duty-form message, the labels the
 * category/animal/assignment pickers display once something is chosen, and the
 * URL bookkeeping (canonicalisation, per-bucket pagers, back navigation) that
 * decides which bucket is on screen.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { toast } from "sonner";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { TaskOut } from "@/api/generated/models";
import {
  ALL_PERMISSIONS,
  permissionsHandler,
  server,
  TEST_ACCESS_TOKEN,
  TEST_USER,
} from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";
import { addDays, farmToday, formatDate } from "@/lib/format";

import TasksPage from "./page";

const nav = vi.hoisted(() => {
  const state = { search: "", publish: true };
  const applyUrl = (url: string) => {
    // A same-route navigation only republishes search params once Next
    // commits them; `publish: false` reproduces the render before that.
    if (!state.publish) return;
    state.search = url.includes("?") ? url.slice(url.indexOf("?") + 1) : "";
  };
  const push = vi.fn((url: string) => applyUrl(url));
  const replace = vi.fn((url: string) => applyUrl(url));
  return { state, push, replace, router: { push, replace, prefetch: vi.fn() } };
});

vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  usePathname: () => "/tasks",
  useSearchParams: () => new URLSearchParams(nav.state.search),
  useParams: () => ({}),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

type User = ReturnType<typeof userEvent.setup>;

beforeAll(() => {
  // jsdom lacks the pointer-capture / observer APIs Base UI Select relies on.
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

/** Farm-calendar fixture dates: due dates follow the active farm timezone. */
const TODAY = farmToday();
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
const SECOND_TODAY_TASK = makeTask({ id: 2, title: "Scrub water troughs" });
const FORM_TASK = makeTask({
  id: 3,
  title: "Vaccinate Radha",
  category: "VACCINE",
  animal_id: 7,
  animal_tag: "G-007",
  action_url: "/health/new?task=3",
});
const OVERDUE_TASK = makeTask({ id: 4, title: "Trim hooves", due_date: THREE_DAYS_AGO });
const UPCOMING_TASK = makeTask({ id: 5, title: "Rotate buck", due_date: NEXT_WEEK });
const AWAITING_TASK = makeTask({
  id: 6,
  title: "Deep-clean kidding pen",
  category: "CLEANING",
  status: "DONE",
  needs_verification: true,
  completed_by_id: 999,
  completed_at: "2026-08-05T14:07:00",
});
const SECOND_AWAITING_TASK = makeTask({
  id: 7,
  title: "Weigh the kids",
  status: "DONE",
  needs_verification: true,
  completed_by_id: 999,
  completed_at: "2026-08-05T15:07:00",
});
const VERIFIED_TASK = makeTask({
  id: 8,
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
      id: 3,
      user_id: 11,
      email: "sita@goatfarm.test",
      name: "Sita",
      role_id: null,
      role_name: null,
      is_active: true,
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
    today: [TODAY_TASK, SECOND_TODAY_TASK, FORM_TASK],
    overdue: [OVERDUE_TASK],
    upcoming: [UPCOMING_TASK],
    awaiting: [AWAITING_TASK, SECOND_AWAITING_TASK],
    completed: [VERIFIED_TASK],
    today_total: 3,
    today_offset: 0,
    overdue_total: 1,
    overdue_offset: 0,
    upcoming_total: 1,
    upcoming_offset: 0,
    awaiting_total: 2,
    awaiting_offset: 0,
    active_limit: 50,
    completed_total: 1,
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

function dueCellOf(title: string): HTMLElement {
  return rowOf(title).querySelector("td") as HTMLElement;
}

async function pickOption(user: User, trigger: HTMLElement, name: string | RegExp) {
  await user.click(trigger);
  await user.click(await screen.findByRole("option", { name }));
}

/** A response the test releases by hand, to observe in-flight copy. */
function createGate() {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { gate, release };
}

describe("TasksPage board copy", () => {
  let payload: TabsPayload;
  let listCalls: number;
  let actionCalls: { action: string; taskId: string; body: unknown }[];

  beforeEach(() => {
    nav.state.search = "";
    nav.state.publish = true;
    nav.push.mockClear();
    nav.replace.mockClear();
    vi.mocked(toast.success).mockClear();
    vi.mocked(toast.error).mockClear();
    payload = fullPayload();
    listCalls = 0;
    actionCalls = [];
    server.use(
      http.get("/api/tasks", () => {
        listCalls += 1;
        return HttpResponse.json(payload);
      }),
      http.get("/api/team", () => HttpResponse.json(TEAM_PAYLOAD)),
      http.get("/api/animals", () =>
        HttpResponse.json({
          animals: [{ id: 7, tag_number: "G-007", name: "Radha" }],
          total: 1,
        }),
      ),
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
    const view = renderWithProviders(<TasksPage />);
    await screen.findByRole("tab", { name: "Today (3)" });
    return view;
  }

  async function openAwaiting(): Promise<User> {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("tab", { name: "Awaiting verification (2)" }));
    await within(await screen.findByRole("table")).findByText("Deep-clean kidding pen");
    return user;
  }

  // ---------- due-date urgency ----------

  it("badges each pending row by urgency and spaces the days-late suffix", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    // Due today: warning-tint "due soon", never the overdue destructive tint.
    const dueToday = within(dueCellOf("Morning feed count")).getByText(formatDate(TODAY));
    expect(dueToday).toHaveClass("bg-warning-tint");
    expect(dueToday).not.toHaveClass("bg-destructive/10");

    await user.click(screen.getByRole("tab", { name: "Overdue (1)" }));
    await within(await screen.findByRole("table")).findByText("Trim hooves");
    const dueLate = within(dueCellOf("Trim hooves")).getByText(formatDate(THREE_DAYS_AGO));
    expect(dueLate).toHaveClass("bg-destructive/10");
    expect(dueLate).not.toHaveClass("bg-warning-tint");
    // The lateness suffix is separated from the date, not glued to it.
    expect(dueCellOf("Trim hooves")).toHaveTextContent(
      `${formatDate(THREE_DAYS_AGO)} (3d late)`,
    );
    expect(dueCellOf("Trim hooves").textContent).toBe(
      `${formatDate(THREE_DAYS_AGO)} (3d late)`,
    );

    // A week out is neither overdue nor due soon.
    await user.click(screen.getByRole("tab", { name: "Upcoming (1)" }));
    await within(await screen.findByRole("table")).findByText("Rotate buck");
    const dueLater = within(dueCellOf("Rotate buck")).getByText(formatDate(NEXT_WEEK));
    expect(dueLater).not.toHaveClass("bg-warning-tint");
    expect(dueLater).not.toHaveClass("bg-destructive/10");
  });

  it("renders the Open form link as an outline control the size of the row's buttons", async () => {
    await renderLoaded();

    const row = rowOf("Vaccinate Radha");
    const openForm = within(row).getByRole("link", { name: "Open form" });
    // sm buttons are 36px touch targets now (see ui/button.tsx).
    expect(openForm).toHaveClass("h-9");
    // Row actions are outline — one primary action per view.
    expect(openForm).toHaveClass("border-border");
    expect(openForm).not.toHaveClass("bg-primary");
    expect(within(row).getByRole("button", { name: "Skip" })).toHaveClass("h-9");
  });

  // ---------- transition toasts and error recovery ----------

  it("announces a completed and a skipped duty with their own toasts", async () => {
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(
      within(rowOf("Morning feed count")).getByRole("button", { name: "Complete" }),
    );
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Task completed."));

    await user.click(
      within(rowOf("Scrub water troughs")).getByRole("button", { name: "Skip" }),
    );
    const skipDialog = await screen.findByRole("dialog", { name: "Skip this task?" });
    await user.type(within(skipDialog).getByLabelText("Reason *"), "not needed");
    await user.click(within(skipDialog).getByRole("button", { name: "Skip task" }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Task skipped."));
  });

  it("announces a verified and a sent-back duty with their own toasts", async () => {
    const user = await openAwaiting();

    await user.click(
      within(rowOf("Deep-clean kidding pen")).getByRole("button", { name: "Verify" }),
    );
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Task verified."));

    // Rejection is dialog-gated: the row button opens it, the confirm only
    // fires after a reason is typed.
    await user.click(within(rowOf("Weigh the kids")).getByRole("button", { name: "Reject…" }));
    const dialog = await screen.findByRole("dialog", { name: "Reject duty" });
    await user.type(within(dialog).getByLabelText("Reason *"), "kids not weighed");
    await user.click(within(dialog).getByRole("button", { name: "Reject duty" }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Task sent back."));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(actionCalls).toContainEqual({
      action: "reject",
      taskId: "7",
      body: { note: "kids not weighed" },
    });
  });

  it("toasts a failed completion and clears the row alert once the retry succeeds", async () => {
    let attempts = 0;
    server.use(
      http.post("/api/tasks/1/complete", () => {
        attempts += 1;
        return attempts === 1
          ? HttpResponse.json({ detail: "duty is locked until its due date" }, { status: 409 })
          : HttpResponse.json(TODAY_TASK);
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    const row = rowOf("Morning feed count");

    await user.click(within(row).getByRole("button", { name: "Complete" }));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("duty is locked until its due date"),
    );
    expect(await within(row).findByRole("alert")).toHaveTextContent(
      "duty is locked until its due date Review the duty, then try again.",
    );

    await user.click(within(row).getByRole("button", { name: "Retry complete" }));
    await waitFor(() =>
      expect(
        within(rowOf("Morning feed count")).queryByRole("alert"),
      ).not.toBeInTheDocument(),
    );
    expect(
      within(rowOf("Morning feed count")).getByRole("button", { name: "Complete" }),
    ).toBeInTheDocument();
  });

  it("falls back to a generic message when a transition never reaches the server", async () => {
    server.use(http.post("/api/tasks/1/complete", () => HttpResponse.error()));
    const user = userEvent.setup();
    await renderLoaded();
    const row = rowOf("Morning feed count");

    await user.click(within(row).getByRole("button", { name: "Complete" }));

    expect(await within(row).findByRole("alert")).toHaveTextContent(
      "Something went wrong Review the duty, then try again.",
    );
    expect(toast.error).toHaveBeenCalledWith("Something went wrong");
  });

  it("keeps the skip retry label until it succeeds, then reopens on a blank reason", async () => {
    let attempts = 0;
    server.use(
      http.post("/api/tasks/1/skip", async ({ request }) => {
        attempts += 1;
        if (attempts === 1) {
          return HttpResponse.json({ detail: "only assignees can skip" }, { status: 403 });
        }
        actionCalls.push({ action: "skip", taskId: "1", body: await request.json() });
        return HttpResponse.json(TODAY_TASK);
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(
      within(rowOf("Morning feed count")).getByRole("button", { name: "Skip" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "Skip this task?" });
    await user.type(within(dialog).getByLabelText("Reason *"), "feed already issued");
    await user.click(within(dialog).getByRole("button", { name: "Skip task" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "only assignees can skip Check the reason, then try again.",
    );
    await user.click(within(dialog).getByRole("button", { name: "Retry skip" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(actionCalls).toContainEqual({
      action: "skip",
      taskId: "1",
      body: { reason: "feed already issued" },
    });

    // Reopening starts a clean skip: no stale reason, no stale failure.
    await user.click(
      within(rowOf("Morning feed count")).getByRole("button", { name: "Skip" }),
    );
    const reopened = await screen.findByRole("dialog", { name: "Skip this task?" });
    expect(within(reopened).getByLabelText("Reason *")).toHaveValue("");
    expect(within(reopened).getByRole("button", { name: "Skip task" })).toBeInTheDocument();
    expect(within(reopened).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("labels the skip button while the skip is in flight", async () => {
    const { gate, release } = createGate();
    server.use(
      http.post("/api/tasks/1/skip", async () => {
        await gate;
        return HttpResponse.json(TODAY_TASK);
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(
      within(rowOf("Morning feed count")).getByRole("button", { name: "Skip" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "Skip this task?" });
    await user.type(within(dialog).getByLabelText("Reason *"), "not needed");
    await user.click(within(dialog).getByRole("button", { name: "Skip task" }));

    expect(await within(dialog).findByRole("button", { name: "Skipping…" })).toBeDisabled();
    release();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("closes the skip dialog from its Cancel button without skipping", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(
      within(rowOf("Morning feed count")).getByRole("button", { name: "Skip" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "Skip this task?" });

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(actionCalls).toHaveLength(0);
  });

  it("clears a failed verification once the retry succeeds", async () => {
    let attempts = 0;
    server.use(
      http.post("/api/tasks/6/verify", () => {
        attempts += 1;
        return attempts === 1
          ? HttpResponse.json({ detail: "already sent back" }, { status: 400 })
          : HttpResponse.json(AWAITING_TASK);
      }),
    );
    const user = await openAwaiting();
    const row = rowOf("Deep-clean kidding pen");

    await user.click(within(row).getByRole("button", { name: "Verify" }));
    expect(await within(row).findByRole("alert")).toHaveTextContent("already sent back");

    await user.click(within(row).getByRole("button", { name: "Retry verify" }));
    await waitFor(() =>
      expect(
        within(rowOf("Deep-clean kidding pen")).queryByRole("alert"),
      ).not.toBeInTheDocument(),
    );
    expect(
      within(rowOf("Deep-clean kidding pen")).getByRole("button", { name: "Verify" }),
    ).toBeInTheDocument();
  });

  it("clears a failed rejection once the retry succeeds", async () => {
    let attempts = 0;
    server.use(
      http.post("/api/tasks/6/reject", () => {
        attempts += 1;
        return attempts === 1
          ? HttpResponse.json({ detail: "verification already changed" }, { status: 409 })
          : HttpResponse.json(AWAITING_TASK);
      }),
    );
    const user = await openAwaiting();
    const row = rowOf("Deep-clean kidding pen");

    // The failure surfaces inside the reject dialog now; the retry lives there.
    await user.click(within(row).getByRole("button", { name: "Reject…" }));
    const dialog = await screen.findByRole("dialog", { name: "Reject duty" });
    await user.type(within(dialog).getByLabelText("Reason *"), "redo properly");
    await user.click(within(dialog).getByRole("button", { name: "Reject duty" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "verification already changed",
    );

    // The confirm flips to a retry label and the second attempt succeeds.
    await user.click(within(dialog).getByRole("button", { name: "Retry reject" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(
      within(rowOf("Deep-clean kidding pen")).getByRole("button", { name: "Reject…" }),
    ).toBeInTheDocument();
    expect(attempts).toBe(2);
  });

  it("shows a plain message when the task list never reaches the server", async () => {
    server.use(http.get("/api/tasks", () => HttpResponse.error()));
    renderWithProviders(<TasksPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load tasks.");
    expect(screen.getByRole("button", { name: "Retry tasks" })).toBeInTheDocument();
  });

  it("waits for the permission answer instead of denying access while it loads", async () => {
    const { gate, release } = createGate();
    server.use(
      http.get("/api/auth/permissions", async () => {
        await gate;
        return HttpResponse.json({ is_owner: true, permissions: ALL_PERMISSIONS });
      }),
    );
    renderWithProviders(<TasksPage />);

    // The page holds its header + skeleton, not a bare "Loading…" line.
    expect(await screen.findByRole("heading", { name: "Tasks" })).toBeInTheDocument();
    expect(document.querySelector('[data-slot="skeleton"]')).not.toBeNull();
    expect(
      screen.queryByText("You don't have access to this page."),
    ).not.toBeInTheDocument();

    release();
    expect(await screen.findByRole("tab", { name: "Today (3)" })).toBeInTheDocument();
  });

  it("renders the board even before the session user is known", async () => {
    // A cached permission answer can outlive the session it was fetched for
    // (expired refresh): the board must still render rather than assume a user.
    const { gate, release } = createGate();
    server.use(
      http.post("/api/auth/refresh", async () => {
        await gate;
        return HttpResponse.json({
          access_token: TEST_ACCESS_TOKEN,
          user: TEST_USER,
        });
      }),
    );
    const queryClient = createTestQueryClient();
    queryClient.setQueryData(["/api/auth/permissions"], {
      data: { is_owner: true, permissions: ALL_PERMISSIONS },
      status: 200,
      headers: new Headers(),
    });
    renderWithProviders(<TasksPage />, queryClient);

    // While the session is still unknown the RBAC gate also waits for the
    // farm selection, so the board holds its header + skeleton — it must
    // never read that window as "no access".
    expect(await screen.findByRole("heading", { name: "Tasks" })).toBeInTheDocument();
    expect(document.querySelector('[data-slot="skeleton"]')).not.toBeNull();
    expect(
      screen.queryByText("You don't have access to this page."),
    ).not.toBeInTheDocument();

    release();
    expect(await within(await screen.findByRole("table")).findByText("Morning feed count")).toBeInTheDocument();
    expect(listCalls).toBeGreaterThanOrEqual(1);
  });

  // ---------- tabs, pagers and URL bookkeeping ----------

  it("opens the tab named in ?tab=upcoming", async () => {
    nav.state.search = "tab=upcoming";
    renderWithProviders(<TasksPage />);

    expect(await within(await screen.findByRole("table")).findByText("Rotate buck")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Upcoming (1)" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(tableScope().queryByText("Morning feed count")).not.toBeInTheDocument();
  });

  it("names every bucket's pager after the bucket it pages", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    expect(
      screen.getByRole("navigation", { name: "today tasks pagination" }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Upcoming (1)" }));
    expect(
      await screen.findByRole("navigation", { name: "upcoming tasks pagination" }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Awaiting verification (2)" }));
    expect(
      await screen.findByRole("navigation", {
        name: "awaiting verification tasks pagination",
      }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Completed (1)" }));
    expect(
      await screen.findByRole("navigation", { name: "completed tasks pagination" }),
    ).toBeInTheDocument();
  });

  it("leaves an already canonical URL alone", async () => {
    nav.state.search = "tab=today";
    await renderLoaded();
    await within(await screen.findByRole("table")).findByText("Morning feed count");

    expect(nav.replace).not.toHaveBeenCalled();
    expect(nav.push).not.toHaveBeenCalled();
    expect(listCalls).toBe(1);
  });

  it("names the fallback tab when canonicalizing an unknown ?tab= deep link", async () => {
    nav.state.search = "tab=bogus&today_offset=abc";
    await renderLoaded();

    await waitFor(() => expect(nav.replace).toHaveBeenLastCalledWith("/tasks?tab=today"));
    expect(screen.getByRole("tab", { name: "Today (3)" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("restores the tab named by the URL after a back navigation", async () => {
    nav.state.search = "tab=today";
    const user = userEvent.setup();
    const { rerender } = await renderLoaded();
    await within(await screen.findByRole("table")).findByText("Morning feed count");

    await user.click(screen.getByRole("tab", { name: "Overdue (1)" }));
    expect(await within(await screen.findByRole("table")).findByText("Trim hooves")).toBeInTheDocument();
    expect(nav.state.search).toBe("tab=overdue");

    // Back: the URL is authoritative again, so the click's bridging override
    // must no longer decide the tab.
    nav.state.search = "tab=today";
    rerender(<TasksPage />);

    expect(await within(await screen.findByRole("table")).findByText("Morning feed count")).toBeInTheDocument();
    expect(tableScope().queryByText("Trim hooves")).not.toBeInTheDocument();
  });
});

describe("TasksPage offset canonicalization", () => {
  let totals: Record<"today" | "overdue" | "upcoming" | "awaiting" | "completed", number>;
  let seenParams: URLSearchParams[];

  beforeEach(() => {
    nav.state.search = "";
    nav.state.publish = true;
    nav.push.mockClear();
    nav.replace.mockClear();
    totals = { today: 10, overdue: 200, upcoming: 0, awaiting: 0, completed: 0 };
    seenParams = [];
    server.use(
      permissionsHandler(["tasks.view", "tasks.complete"]),
      http.get("/api/tasks", ({ request }) => {
        const params = new URL(request.url).searchParams;
        seenParams.push(params);
        const offset = (bucket: keyof typeof totals) =>
          Number(params.get(`${bucket}_offset`) ?? 0);
        const page = (bucket: keyof typeof totals, task: TaskOut) =>
          offset(bucket) < totals[bucket] ? [task] : [];
        return HttpResponse.json({
          today: page("today", makeTask({ id: 1, title: "Today row" })),
          overdue: page(
            "overdue",
            makeTask({ id: 2, title: "Overdue row", due_date: THREE_DAYS_AGO }),
          ),
          upcoming: page(
            "upcoming",
            makeTask({ id: 3, title: "Upcoming row", due_date: NEXT_WEEK }),
          ),
          awaiting: [],
          completed: [],
          today_total: totals.today,
          today_offset: offset("today"),
          overdue_total: totals.overdue,
          overdue_offset: offset("overdue"),
          upcoming_total: totals.upcoming,
          upcoming_offset: offset("upcoming"),
          awaiting_total: totals.awaiting,
          awaiting_offset: offset("awaiting"),
          active_limit: 50,
          completed_total: totals.completed,
          completed_limit: 50,
          completed_offset: offset("completed"),
        });
      }),
    );
  });

  it("rejects offsets the API would never mint, before the first request", async () => {
    // Exponent and hex forms parse as numbers but are not the canonical
    // decimal offsets the pager emits.
    nav.state.search = "tab=today&today_offset=1e2&overdue_offset=0x10";
    renderWithProviders(<TasksPage />);
    await within(await screen.findByRole("table")).findByText("Today row");

    expect(seenParams[0].get("today_offset")).toBe("0");
    expect(seenParams[0].get("overdue_offset")).toBe("0");
    await waitFor(() => expect(nav.replace).toHaveBeenLastCalledWith("/tasks?tab=today"));
  });

  it("clamps one stale bucket without disturbing its healthy siblings", async () => {
    nav.state.search = "tab=overdue&today_offset=100&overdue_offset=50";
    renderWithProviders(<TasksPage />);
    await within(await screen.findByRole("table")).findByText("Overdue row");

    await waitFor(() =>
      expect(nav.replace).toHaveBeenLastCalledWith("/tasks?tab=overdue&overdue_offset=50"),
    );
    await waitFor(() => expect(seenParams.at(-1)?.get("today_offset")).toBe("0"));
    expect(seenParams.at(-1)?.get("overdue_offset")).toBe("50");
  });

  it("shows the clamped page before the router republishes the URL", async () => {
    // router.replace() lands, but Next has not yet published the new search
    // params — the board still has to leave the empty page it was stranded on.
    nav.state.publish = false;
    nav.state.search = "tab=today&today_offset=100";
    renderWithProviders(<TasksPage />);

    expect(await within(await screen.findByRole("table")).findByText("Today row")).toBeInTheDocument();
    expect(nav.replace).toHaveBeenCalledWith("/tasks?tab=today");
    expect(seenParams.at(-1)?.get("today_offset")).toBe("0");
  });
});

describe("TasksPage new-duty dialog copy", () => {
  let createBody: Record<string, unknown> | null;

  beforeEach(() => {
    nav.state.search = "";
    nav.state.publish = true;
    nav.push.mockClear();
    nav.replace.mockClear();
    vi.mocked(toast.success).mockClear();
    vi.mocked(toast.error).mockClear();
    createBody = null;
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json({
          ...fullPayload(),
          today: [],
          overdue: [],
          upcoming: [],
          awaiting: [],
          completed: [],
          today_total: 0,
          overdue_total: 0,
          upcoming_total: 0,
          awaiting_total: 0,
          completed_total: 0,
        }),
      ),
      http.get("/api/team", () => HttpResponse.json(TEAM_PAYLOAD)),
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
    );
  });

  async function openDialog() {
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);
    await screen.findByRole("tab", { name: "Today (0)" });
    await user.click(screen.getByRole("button", { name: "New duty" }));
    return { user, dialog: await screen.findByRole("dialog") };
  }

  function fieldsOf(dialog: HTMLElement) {
    return {
      title: within(dialog).getByLabelText("Title *"),
      dueDate: within(dialog).getByLabelText("Due date *"),
      recurDays: within(dialog).getByLabelText("Repeats every (days)"),
      create: within(dialog).getByRole("button", { name: "Create duty" }),
    };
  }

  it("offers every duty category and shows the one chosen in the trigger", async () => {
    const { user, dialog } = await openDialog();
    const category = within(dialog).getByRole("combobox", { name: "Category" });
    // The select shows humanized labels; the API codes stay the values.
    expect(category).toHaveTextContent("Other");

    await user.click(category);
    const options = await screen.findAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual([
      "Feeding",
      "Cleaning",
      "Other",
    ]);

    await user.click(screen.getByRole("option", { name: "Feeding" }));
    expect(category).toHaveTextContent("Feeding");
  });

  it.each([
    ["Feeding", "FEED"],
    ["Cleaning", "CLEANING"],
  ])("creates a duty in the %s category", async (label, apiCategory) => {
    const { user, dialog } = await openDialog();
    await user.type(fieldsOf(dialog).title, "Refill mineral licks");
    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Category" }),
      label,
    );
    await user.click(fieldsOf(dialog).create);

    await waitFor(() => expect(createBody).not.toBeNull());
    expect(createBody).toMatchObject({ category: apiCategory });
  });

  it("starts the animal picker on its none option and names the animal once chosen", async () => {
    const { user, dialog } = await openDialog();
    const picker = within(dialog).getByRole("combobox", { name: "Animal (optional)" });
    expect(picker).toHaveTextContent("— none —");

    await pickOption(user, picker, "G-007 — Radha");

    expect(picker).toHaveTextContent("G-007 — Radha");
  });

  it("wires each duty-form message to the field it describes", async () => {
    const { user, dialog } = await openDialog();
    const { title, dueDate, recurDays, create } = fieldsOf(dialog);
    await user.type(title, "Check fences");
    fireEvent.change(dueDate, { target: { value: "" } });
    fireEvent.change(recurDays, { target: { value: "abc" } });
    await user.click(create);

    await within(dialog).findByText("Due date is required");
    expect(dueDate).toHaveAccessibleDescription("Due date is required");
    expect(dueDate).toHaveAttribute("aria-invalid", "true");
    expect(recurDays).toHaveAccessibleDescription("Must be a whole number of days (1–3650)");
    expect(recurDays).toHaveAttribute("aria-invalid", "true");
    expect(createBody).toBeNull();
  });

  it("rejects a whitespace-only title", async () => {
    const { user, dialog } = await openDialog();
    await user.type(fieldsOf(dialog).title, "   ");
    await user.click(fieldsOf(dialog).create);

    expect(await within(dialog).findByText("Title is required")).toBeInTheDocument();
    expect(createBody).toBeNull();
  });

  it("rejects a due date that is not a four-digit-year calendar date", async () => {
    const { user, dialog } = await openDialog();
    const { title, dueDate, create } = fieldsOf(dialog);
    await user.type(title, "Far-future inspection");
    fireEvent.change(dueDate, { target: { value: "20261-08-10" } });
    await user.click(create);

    expect(await within(dialog).findByText("Pick a valid due date")).toBeInTheDocument();
    expect(createBody).toBeNull();
  });

  it("blocks a multi-day recurrence whose next occurrence leaves the calendar", async () => {
    const { user, dialog } = await openDialog();
    const { title, dueDate, recurDays, create } = fieldsOf(dialog);
    await user.type(title, "Decade inspection");
    fireEvent.change(dueDate, { target: { value: "2100-12-31" } });
    fireEvent.change(recurDays, { target: { value: "10" } });
    await user.click(create);

    expect(
      await within(dialog).findByText(
        "Recurring due date is too late to schedule its next occurrence",
      ),
    ).toBeInTheDocument();
    expect(createBody).toBeNull();

    // The last due date that still leaves room for the next occurrence
    // inside the backend's 2000–2100 band.
    fireEvent.change(dueDate, { target: { value: "2100-12-21" } });
    await user.click(create);

    await waitFor(() => expect(createBody).not.toBeNull());
    expect(createBody).toMatchObject({ due_date: "2100-12-21", recur_days: 10 });
  });

  it.each([" 10", "10 ", "3651"])(
    "reports only the recurrence problem for %j",
    async (recur) => {
      const { user, dialog } = await openDialog();
      const { title, dueDate, recurDays, create } = fieldsOf(dialog);
      await user.type(title, "Malformed recurrence");
      fireEvent.change(dueDate, { target: { value: "2100-12-31" } });
      fireEvent.change(recurDays, { target: { value: recur } });
      await user.click(create);

      expect(
        await within(dialog).findByText("Must be a whole number of days (1–3650)"),
      ).toBeInTheDocument();
      expect(
        within(dialog).queryByText(
          "Recurring due date is too late to schedule its next occurrence",
        ),
      ).not.toBeInTheDocument();
      expect(createBody).toBeNull();
    },
  );

  it("clears an already chosen role when a worker is picked instead", async () => {
    const { user, dialog } = await openDialog();
    await user.type(fieldsOf(dialog).title, "Herd check");
    const combos = () => within(dialog).getAllByRole("combobox");

    // Order: category, animal, role, worker.
    await pickOption(user, combos()[2], "Vet");
    await pickOption(user, combos()[3], "Raju (Vet)");

    expect(combos()[2]).toHaveTextContent("— none —");
    expect(combos()[3]).toHaveTextContent("Raju (Vet)");
    await user.click(fieldsOf(dialog).create);

    await waitFor(() => expect(createBody).not.toBeNull());
    expect(createBody).toMatchObject({ assigned_role_id: null, assigned_user_id: 9 });
  });

  it("treats — none — as a real selection in both assignment selects", async () => {
    const { dialog } = await openDialog();
    await within(dialog).findByLabelText("Assign to role");
    const combos = within(dialog).getAllByRole("combobox");

    for (const trigger of [combos[2], combos[3]]) {
      expect(trigger).toHaveTextContent("— none —");
      // The sentinel has to be a real item value: an empty string would leave
      // the select unfilled, greying the label out as a placeholder.
      expect(trigger).not.toHaveAttribute("data-placeholder");
    }
  });

  it("labels a worker who holds no role as a plain worker", async () => {
    const { user, dialog } = await openDialog();
    const combos = () => within(dialog).getAllByRole("combobox");

    await pickOption(user, combos()[3], "Sita (worker)");

    expect(combos()[3]).toHaveTextContent("Sita (worker)");
  });

  it("offers only the none option when the directory carries no roles", async () => {
    server.use(
      http.get("/api/team", () => HttpResponse.json({ ...TEAM_PAYLOAD, roles: null })),
    );
    const { user, dialog } = await openDialog();

    await user.click(within(dialog).getAllByRole("combobox")[2]);

    const options = await screen.findAllByRole("option");
    expect(options).toHaveLength(1);
    expect(options[0]).toHaveTextContent("— none —");
  });

  it("explains an assignment lookup that never reached the server", async () => {
    server.use(http.get("/api/team", () => HttpResponse.error()));
    const { dialog } = await openDialog();

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Could not load assignment options.",
    );
    expect(
      within(dialog).getByRole("button", { name: "Retry assignments" }),
    ).toBeInTheDocument();
  });

  it("confirms a created duty with a toast", async () => {
    const { user, dialog } = await openDialog();
    await user.type(fieldsOf(dialog).title, "Check fences");
    await user.click(fieldsOf(dialog).create);

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Duty created."));
  });

  it("toasts a failed create and drops the notice when the retry starts", async () => {
    const { gate, release } = createGate();
    let attempts = 0;
    server.use(
      http.post("/api/tasks", async ({ request }) => {
        attempts += 1;
        if (attempts === 1) {
          return HttpResponse.json({ detail: "role not on this farm" }, { status: 422 });
        }
        createBody = (await request.json()) as Record<string, unknown>;
        await gate;
        return HttpResponse.json(makeTask({ id: 50 }), { status: 201 });
      }),
    );
    const { user, dialog } = await openDialog();
    await user.type(fieldsOf(dialog).title, "Check fences");
    await user.click(fieldsOf(dialog).create);

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("role not on this farm"));
    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "role not on this farm Check the duty details, then try again.",
    );

    await user.click(within(dialog).getByRole("button", { name: "Retry create" }));

    // The stale failure disappears as soon as the retry is under way.
    await waitFor(() =>
      expect(within(dialog).queryByText(/role not on this farm/)).not.toBeInTheDocument(),
    );
    expect(within(dialog).getByRole("button", { name: "Creating…" })).toBeDisabled();

    release();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(createBody).toMatchObject({ title: "Check fences" });
  });
});
