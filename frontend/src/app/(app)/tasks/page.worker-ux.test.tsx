/**
 * Worker-UX remediations on the tasks board (UI/UX audit):
 * - the below-md mobile card list (md:hidden) with ≥44px touch-target
 *   actions alongside the untouched desktop table (hidden md:block);
 * - the recurring-complete confirmation stating the next occurrence date
 *   (non-recurring duties stay one-tap);
 * - finished-by names on the Completed tab via /api/team for team.manage
 *   holders, and nothing (never a raw id) for workers;
 * - the whole board rendering in Telugu when the language is set to te.
 *
 * Reject-dialog behaviour is covered in page.extended.test.tsx and
 * page.board-copy.test.tsx.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, afterEach, describe, expect, it, vi } from "vitest";

import type { TaskOut } from "@/api/generated/models";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { LanguageProvider, LANGUAGE_STORAGE_KEY } from "@/lib/i18n";
import { addDays, farmToday, formatDate } from "@/lib/format";

import TasksPage from "./page";

const nav = vi.hoisted(() => ({
  state: { search: "" },
  router: { push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  usePathname: () => "/tasks",
  useSearchParams: () => new URLSearchParams(nav.state.search),
  useParams: () => ({}),
}));

beforeAll(() => {
  // jsdom lacks the pointer-capture / observer APIs Base UI relies on.
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

const TODAY = farmToday();
const NEXT_WEEK = addDays(TODAY, 7);

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

const SIMPLE_TASK = makeTask({ id: 1, title: "Scrub water troughs" });
const RECURRING_TASK = makeTask({
  id: 2,
  title: "Daily pen cleaning",
  recur_days: 7,
  recurring_series_id: "00000000-0000-4000-8000-000000000002",
});
const AWAITING_TASK = makeTask({
  id: 3,
  title: "Weigh the kids",
  status: "DONE",
  needs_verification: true,
  completed_by_id: 999,
  completed_at: "2026-08-05T14:07:00",
});
const COMPLETED_BY_RAJU = makeTask({
  id: 4,
  title: "Weekly sweep",
  status: "VERIFIED",
  completed_by_id: 9,
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
      can_reset_password: true,
      reset_password_block_reason: null,
    },
  ],
  roles: [{ id: 2, code: "VET", name: "Vet", description: null, permissions: [], revision: 1, member_count: 1 }],
  permission_groups: [],
  permission_labels: {},
};

function boardPayload(extra: Record<string, unknown> = {}) {
  return {
    today: [SIMPLE_TASK, RECURRING_TASK],
    overdue: [],
    upcoming: [],
    awaiting: [],
    completed: [],
    today_total: 2,
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

/** The page under LanguageProvider so a test can preselect Telugu. */
function renderTasks() {
  return renderWithProviders(
    <LanguageProvider>
      <TasksPage />
    </LanguageProvider>,
  );
}

async function loadedBoard() {
  await screen.findByRole("tab", { name: "Today (2)" });
}

/** The below-md card list container (class-based lookup keeps jsdom honest —
 * Tailwind responsive classes are the source of truth for the breakpoint). */
function mobileCardList(): HTMLElement {
  const list = document.querySelector('[class~="md:hidden"]');
  expect(list).not.toBeNull();
  return list as HTMLElement;
}

let actionCalls: { action: string; taskId: string }[];

describe("TasksPage mobile card list", () => {
  beforeEach(() => {
    nav.state.search = "";
    actionCalls = [];
    server.use(
      http.get("/api/tasks", () => HttpResponse.json(boardPayload())),
      http.get("/api/team", () => HttpResponse.json(TEAM_PAYLOAD)),
      http.post("/api/tasks/:taskId/:action", ({ params }) => {
        actionCalls.push({ action: String(params.action), taskId: String(params.taskId) });
        return HttpResponse.json(makeTask({ id: Number(params.taskId) }));
      }),
    );
  });

  it("renders a below-md card list and keeps the desktop table, both class-gated", async () => {
    renderTasks();
    await loadedBoard();

    const cards = mobileCardList();
    // One card per duty, with the title present on the phone surface.
    expect(within(cards).getByText("Scrub water troughs")).toBeInTheDocument();
    expect(within(cards).getByText("Daily pen cleaning")).toBeInTheDocument();

    // The table stays untouched and hidden below md.
    const tableWrapper = document.querySelector('[class~="md:block"]');
    expect(tableWrapper).not.toBeNull();
    expect(tableWrapper!.querySelector("table")).not.toBeNull();
    // The table keeps its wide-floor contract for tablets.
    expect(tableWrapper!.querySelector("table")).toHaveClass("min-w-[720px]");
  });

  it("gives card actions ≥44px touch targets while table actions stay compact", async () => {
    renderTasks();
    await loadedBoard();

    const card = mobileCardList().querySelectorAll(":scope > div")[0] as HTMLElement;
    const cardComplete = within(card).getByRole("button", { name: "Complete" });
    expect(cardComplete).toHaveClass("h-11");
    const cardSkip = within(card).getByRole("button", { name: "Skip" });
    expect(cardSkip).toHaveClass("h-11");

    // The desktop table's row buttons keep their compact size class.
    const table = document.querySelector('[class~="md:block"] table') as HTMLElement;
    const tableRow = within(table).getByText("Scrub water troughs").closest("tr") as HTMLElement;
    expect(within(tableRow).getByRole("button", { name: "Complete" })).toHaveClass("h-9");
    expect(within(tableRow).getByRole("button", { name: "Complete" })).not.toHaveClass("h-11");
  });

  it("badges card due dates by urgency: overdue red, today amber, later muted", async () => {
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json(
          boardPayload({
            today: [
              SIMPLE_TASK,
              makeTask({ id: 5, title: "Trim hooves", due_date: addDays(TODAY, -2) }),
              makeTask({ id: 6, title: "Rotate buck", due_date: NEXT_WEEK }),
            ],
            today_total: 3,
          }),
        ),
      ),
    );
    renderTasks();
    await screen.findByRole("tab", { name: "Today (3)" });

    const cards = mobileCardList();
    const cardOf = (title: string) =>
      within(cards).getByText(title).parentElement?.parentElement as HTMLElement;

    const todayBadge = within(cardOf("Scrub water troughs")).getByText(formatDate(TODAY));
    expect(todayBadge).toHaveClass("bg-warning-tint");

    const overdueCard = cardOf("Trim hooves");
    const overdueBadge = within(overdueCard).getByText(formatDate(addDays(TODAY, -2)));
    expect(overdueBadge).toHaveClass("bg-destructive/10");
    expect(within(overdueCard).getByText("(2d late)")).toBeInTheDocument();

    const laterBadge = within(cardOf("Rotate buck")).getByText(formatDate(NEXT_WEEK));
    expect(laterBadge).not.toHaveClass("bg-warning-tint");
    expect(laterBadge).not.toHaveClass("bg-destructive/10");
  });
});

describe("TasksPage recurring-complete confirmation", () => {
  beforeEach(() => {
    nav.state.search = "";
    actionCalls = [];
    server.use(
      http.get("/api/tasks", () => HttpResponse.json(boardPayload())),
      http.post("/api/tasks/:taskId/complete", ({ params }) => {
        actionCalls.push({ action: "complete", taskId: String(params.taskId) });
        return HttpResponse.json(makeTask({ id: Number(params.taskId) }));
      }),
    );
  });

  it("confirms a recurring completion with the next occurrence date before mutating", async () => {
    const user = userEvent.setup();
    renderTasks();
    await loadedBoard();

    const table = document.querySelector('[class~="md:block"] table') as HTMLElement;
    const row = within(table).getByText("Daily pen cleaning").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Complete" }));

    // No POST yet — the dialog gates the series-advancing action.
    expect(actionCalls).toHaveLength(0);
    const dialog = await screen.findByRole("dialog", { name: "Complete recurring duty?" });
    expect(
      within(dialog).getByText(
        `This duty repeats every 7 days. Completing it now schedules the next occurrence for ${formatDate(addDays(TODAY, 7))}.`,
      ),
    ).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Complete duty" }));
    await waitFor(() => expect(actionCalls).toContainEqual({ action: "complete", taskId: "2" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("keeps one-off completion a single tap with no dialog", async () => {
    const user = userEvent.setup();
    renderTasks();
    await loadedBoard();

    const table = document.querySelector('[class~="md:block"] table') as HTMLElement;
    const row = within(table).getByText("Scrub water troughs").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Complete" }));

    await waitFor(() => expect(actionCalls).toContainEqual({ action: "complete", taskId: "1" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("cancelling the confirmation does not complete the duty", async () => {
    const user = userEvent.setup();
    renderTasks();
    await loadedBoard();

    const table = document.querySelector('[class~="md:block"] table') as HTMLElement;
    const row = within(table).getByText("Daily pen cleaning").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Complete" }));
    const dialog = await screen.findByRole("dialog", { name: "Complete recurring duty?" });
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(actionCalls).toHaveLength(0);
  });
});

describe("TasksPage finished-by names (Completed tab)", () => {
  beforeEach(() => {
    nav.state.search = "";
    actionCalls = [];
  });

  it("resolves and shows the worker's name next to the timestamp for team.manage holders", async () => {
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json(boardPayload({ completed: [COMPLETED_BY_RAJU], completed_total: 1 })),
      ),
      http.get("/api/team", () => HttpResponse.json(TEAM_PAYLOAD)),
    );
    nav.state.search = "tab=completed";
    renderTasks();
    await screen.findByRole("tab", { name: "Completed (1)" });

    const table = document.querySelector('[class~="md:block"] table') as HTMLElement;
    const row = within(table).getByText("Weekly sweep").closest("tr") as HTMLElement;
    expect(within(row).getByText("by Raju")).toBeInTheDocument();
    // The timestamp itself still renders (and never a raw user id).
    expect(within(row).getByText("1 Aug 2026, 3:00 pm")).toBeInTheDocument();
    expect(within(row).queryByText(/999/)).not.toBeInTheDocument();

    // The phone card shows the same attribution.
    const cards = mobileCardList();
    expect(within(cards).getByText(/by Raju/)).toBeInTheDocument();
  });

  it("renders no attribution (and fetches no directory) without team.manage", async () => {
    let teamCalls = 0;
    server.use(
      permissionsHandler(["tasks.view", "tasks.complete"]),
      http.get("/api/tasks", () =>
        HttpResponse.json(boardPayload({ completed: [COMPLETED_BY_RAJU], completed_total: 1 })),
      ),
      http.get("/api/team", () => {
        teamCalls += 1;
        return HttpResponse.json(TEAM_PAYLOAD);
      }),
    );
    nav.state.search = "tab=completed";
    renderTasks();
    await screen.findByRole("tab", { name: "Completed (1)" });

    const table = document.querySelector('[class~="md:block"] table') as HTMLElement;
    const row = within(table).getByText("Weekly sweep").closest("tr") as HTMLElement;
    expect(within(row).queryByText(/by /)).not.toBeInTheDocument();
    // Workers hold no permitted name-resolution endpoint: no request, no id.
    expect(teamCalls).toBe(0);
    // Match only a raw id, never a date: due_date is farmToday(), which
    // renders "9 Sep 2026" (etc.) and would flake any /\d/ date assertion.
    expect(within(row).queryByText(/^\d+$/)).not.toBeInTheDocument();
  });

  it("renders no attribution when the directory lookup fails", async () => {
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json(boardPayload({ completed: [COMPLETED_BY_RAJU], completed_total: 1 })),
      ),
      http.get("/api/team", () =>
        HttpResponse.json({ detail: "team directory unavailable" }, { status: 503 }),
      ),
    );
    nav.state.search = "tab=completed";
    renderTasks();
    await screen.findByRole("tab", { name: "Completed (1)" });

    const table = document.querySelector('[class~="md:block"] table') as HTMLElement;
    const row = within(table).getByText("Weekly sweep").closest("tr") as HTMLElement;
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(within(row).queryByText(/by /)).not.toBeInTheDocument();
    expect(within(row).getByText("1 Aug 2026, 3:00 pm")).toBeInTheDocument();
  });
});

describe("TasksPage in Telugu", () => {
  beforeEach(() => {
    nav.state.search = "";
    actionCalls = [];
    localStorage.setItem(LANGUAGE_STORAGE_KEY, "te");
    server.use(
      http.get("/api/tasks", () => HttpResponse.json(boardPayload())),
      http.get("/api/team", () => HttpResponse.json(TEAM_PAYLOAD)),
      http.post("/api/tasks/:taskId/:action", ({ params }) => {
        actionCalls.push({ action: String(params.action), taskId: String(params.taskId) });
        return HttpResponse.json(makeTask({ id: Number(params.taskId) }));
      }),
    );
  });
  afterEach(() => {
    localStorage.removeItem(LANGUAGE_STORAGE_KEY);
    document.documentElement.lang = "en";
  });

  it("renders tabs, buttons and the empty tab guidance in Telugu", async () => {
    renderTasks();

    expect(await screen.findByRole("tab", { name: "ఈ రోజు (2)" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "ఆలస్యం (0)" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "రాబోయేవి (0)" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "పూర్తైనవి (0)" })).toBeInTheDocument();
    expect(document.documentElement.lang).toBe("te");

    const table = document.querySelector('[class~="md:block"] table') as HTMLElement;
    const headers = within(table).getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toContain("గడువు");
    expect(headers).toContain("పని");
    expect(headers).toContain("మేక");
    // Both rows carry Complete/Skip controls.
    expect(within(table).getAllByRole("button", { name: "పూర్తి చేయండి" })).toHaveLength(2);
    expect(within(table).getAllByRole("button", { name: "వదిలివేయి" })).toHaveLength(2);
    // Card actions carry the same Telugu labels at touch height.
    for (const button of within(mobileCardList()).getAllByRole("button", { name: "పూర్తి చేయండి" })) {
      expect(button).toHaveClass("h-11");
    }
    // Page header included.
    expect(screen.getByRole("heading", { name: "పనులు" })).toBeInTheDocument();

    // Empty-tab guidance is translated too.
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "ఆలస్యం (0)" }));
    expect(await screen.findByText("ఆలస్య పనులు లేవు.")).toBeInTheDocument();
  });

  it("translates the recurring-complete confirmation dialog", async () => {
    const user = userEvent.setup();
    renderTasks();
    await screen.findByRole("tab", { name: "ఈ రోజు (2)" });

    const table = document.querySelector('[class~="md:block"] table') as HTMLElement;
    const row = within(table).getByText("Daily pen cleaning").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "పూర్తి చేయండి" }));

    const dialog = await screen.findByRole("dialog", { name: "పునరావృత పనిని పూర్తి చేయాలా?" });
    expect(
      within(dialog).getByText(
        `ఈ పని ప్రతి 7 రోజులకు ఒకసారి వస్తుంది. ఇప్పుడు పూర్తి చేస్తే తర్వాతి పని ${formatDate(addDays(TODAY, 7))} నాడు షెడ్యూల్ అవుతుంది.`,
      ),
    ).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "రద్దు చేయి" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(actionCalls).toHaveLength(0);
  });

  it("translates the awaiting-tab verify controls and reject dialog", async () => {
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json(boardPayload({ awaiting: [AWAITING_TASK], awaiting_total: 1 })),
      ),
    );
    nav.state.search = "tab=awaiting";
    renderTasks();
    await screen.findByRole("tab", { name: "ధృవీకరణ కోసం వేచివున్నవి (1)" });

    const table = document.querySelector('[class~="md:block"] table') as HTMLElement;
    const row = within(table).getByText("Weigh the kids").closest("tr") as HTMLElement;
    expect(within(row).getByRole("button", { name: "ధృవీకరించు" })).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(within(row).getByRole("button", { name: "తిరస్కరించు…" }));
    const dialog = await screen.findByRole("dialog", { name: "పనిని తిరస్కరించు" });
    await user.click(within(dialog).getByRole("button", { name: "పనిని తిరస్కరించు" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("కారణం అవసరం.");
  });
});

/** Mutation-hardening (2026-09-23 campaign): the recurring-confirm anchor is
 *  max(due_date, today) — an existing test pins the today (overdue) leg;
 *  this pins the future-due leg, plus the 255-char skip-reason cap. */
describe("TasksPage skip-reason cap", () => {
  beforeEach(() => {
    nav.state.search = "";
    actionCalls = [];
    server.use(
      http.get("/api/tasks", () => HttpResponse.json(boardPayload())),
      http.post("/api/tasks/:taskId/skip", ({ params }) => {
        actionCalls.push({ action: "skip", taskId: String(params.taskId) });
        return HttpResponse.json(makeTask({ id: Number(params.taskId) }));
      }),
    );
  });

  it("caps the skip reason at 255 characters", async () => {
    const user = userEvent.setup();
    renderTasks();
    await loadedBoard();

    const table = document.querySelector('[class~="md:block"] table') as HTMLElement;
    const row = within(table).getByText("Scrub water troughs").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Skip" }));

    const dialog = await screen.findByRole("dialog", { name: /skip this task/i });
    const reason = within(dialog).getByLabelText(/reason \*/i) as HTMLTextAreaElement;
    await user.type(reason, "x".repeat(256));
    expect(reason.value.length).toBe(255);
  });
});
