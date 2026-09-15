/**
 * TasksPage — fresh-domain mutation campaign kills (2026-09): per-tab empty
 * copy, completed-tab card shapes (skipped, sent back, recurring, overdue),
 * the reject-dialog contract, duty-form schema gates, the create payload's
 * NONE handling and trimming, bogus deep-linked tabs, and farm-switch guards
 * on action continuations.
 */

import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { TaskOut } from "@/api/generated/models";

import { addDays, farmToday, formatDate } from "@/lib/format";
import { enumLabel } from "@/lib/enum-labels";
import { LanguageProvider, translate } from "@/lib/i18n";
import type { Language } from "@/lib/i18n";
import { useLanguage } from "@/lib/i18n";
import { setCurrentFarmId } from "@/lib/api-client";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import { makeDutySchema, default as TasksPage } from "./page";

const nav = vi.hoisted(() => ({
  state: { search: "" },
  router: { push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() },
}));

const toastMocks = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMocks }));

vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  usePathname: () => "/tasks",
  useSearchParams: () => new URLSearchParams(nav.state.search),
  useParams: () => ({}),
}));

beforeAll(() => {
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
const t = (
  key: Parameters<typeof translate>[1],
  vars?: Record<string, string | number>,
) => translate("en", key, vars);

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
  roles: [
    { id: 2, code: "VET", name: "Vet", description: null, permissions: [], revision: 1, member_count: 1 },
  ],
  permission_groups: [],
  permission_labels: {},
};

function boardPayload(extra: Record<string, unknown> = {}) {
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

let actionCalls: { action: string; taskId: string }[];

beforeEach(() => {
  nav.state.search = "";
  nav.router.push.mockClear();
  nav.router.replace.mockClear();
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

async function loadedBoard(total = 0) {
  await screen.findByRole("tab", { name: `Today (${total})` });
}

/** The below-md card list — one of the two surfaces every row renders on. */
function mobileCardList(): HTMLElement {
  const list = document.querySelector('[class~="md:hidden"]');
  expect(list).not.toBeNull();
  return list as HTMLElement;
}

describe("TasksPage — campaign kills", () => {
  it.each([
    ["today", "No tasks for today.", "Nothing is due today. Late work shows on the Overdue tab."],
    ["overdue", "No overdue tasks.", "Nothing is overdue. Today's duties show on the Today tab."],
    ["upcoming", "No upcoming tasks.", t("tasks.guidance.upcoming")],
    ["awaiting", "No tasks awaiting verification.", t("tasks.guidance.awaiting")],
    ["completed", "No completed tasks.", t("tasks.guidance.completed")],
  ])("renders the %s tab's empty title and guidance from the catalog", async (tab, title, guidance) => {
    nav.state.search = `?tab=${tab}`;
    renderWithProviders(<TasksPage />);
    await loadedBoard();
    await userEvent.click(screen.getByRole("tab", { name: new RegExp(tab, "i") }));

    expect(await screen.findByText(title)).toBeInTheDocument();
    expect(screen.getByText(guidance)).toBeInTheDocument();
  });

  it("falls back to the Today tab for a bogus deep-linked tab", async () => {
    nav.state.search = "?tab=nonsense";
    renderWithProviders(<TasksPage />);
    await loadedBoard();
    // The fallback title is the today tab's, never a raw key.
    expect(await screen.findByText("No tasks for today.")).toBeInTheDocument();
    expect(screen.queryByText(/tasks\./)).not.toBeInTheDocument();
  });

  it("renders skipped, sent-back, recurring and overdue card shapes on the board", async () => {
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json(
          boardPayload({
            today: [
              makeTask({
                id: 1,
                title: "Overdue trough",
                due_date: addDays(TODAY, -2),
              }),
              makeTask({ id: 2, title: "Weekly pen check", recur_days: 7 }),
            ],
            completed: [
              makeTask({
                id: 3,
                title: "Skipped sweep",
                status: "SKIPPED",
                skipped_by_id: 9,
                skipped_at: "2026-08-20T08:00:00",
                skip_reason: "Rain flooded the pen",
              }),
              makeTask({
                id: 4,
                title: "Sent-back weigh-in",
                status: "PENDING",
                verification_note: "Weights do not add up",
              }),
            ],
            today_total: 2,
            completed_total: 2,
          }),
        ),
      ),
    );
    renderWithProviders(<TasksPage />);
    await loadedBoard(2);

    // Overdue + recurring badges live on the card surface, and the recurring
    // badge appears exactly once — on the recurring duty's card only.
    const cards = mobileCardList();
    expect(within(cards).getByText("Overdue trough")).toBeInTheDocument();
    expect(within(cards).getByText("(2d late)")).toBeInTheDocument();
    expect(within(cards).getAllByText(t("tasks.everyDays", { days: 7 }))).toHaveLength(1);

    await userEvent.click(screen.getByRole("tab", { name: /completed/i }));
    const doneCards = mobileCardList();
    expect(await within(doneCards).findByText("Skipped sweep")).toBeInTheDocument();
    // Member names resolve through the team payload ("by Raju" attribution).
    expect(await within(doneCards).findByText(/by Raju/)).toBeInTheDocument();
    expect(
      within(doneCards).getByText(t("tasks.skipReasonLabel", { reason: "Rain flooded the pen" })),
    ).toBeInTheDocument();
    expect(await within(doneCards).findByText("Sent-back weigh-in")).toBeInTheDocument();
    expect(
      within(doneCards).getByText(t("tasks.sentBack", { note: "Weights do not add up" })),
    ).toBeInTheDocument();
  });

  it("enforces the reject-dialog contract", async () => {
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json(
          boardPayload({
            awaiting: [
              makeTask({
                id: 5,
                title: "Awaiting check",
                status: "DONE",
                needs_verification: true,
                completed_by_id: 999,
                completed_at: "2026-08-05T14:07:00",
              }),
            ],
            awaiting_total: 1,
          }),
        ),
      ),
    );
    renderWithProviders(<TasksPage />);
    await loadedBoard();
    await userEvent.click(screen.getByRole("tab", { name: /awaiting/i }));
    await within(mobileCardList()).findByText("Awaiting check");

    await userEvent.click(
      within(mobileCardList()).getByRole("button", { name: t("tasks.reject") }),
    );
    const dialog = await screen.findByRole("dialog");
    // Submitting without a reason marks the field invalid, not a silent send.
    await userEvent.click(within(dialog).getByRole("button", { name: t("tasks.reject.confirm") }));
    expect(
      await within(dialog).findByText(t("tasks.reject.reasonMissing")),
    ).toBeInTheDocument();

    await userEvent.type(
      within(dialog).getByLabelText(t("tasks.reject.reason")),
      "Measurements were wrong",
    );
    // Typing a reason clears the missing flag.
    expect(within(dialog).queryByText(t("tasks.reject.reasonMissing"))).not.toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole("button", { name: t("common.cancel") }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("trims the title and maps NONE selections to nulls in the create payload", async () => {
    let createdBody: Record<string, unknown> | null = null;
    server.use(
      http.post("/api/tasks", async ({ request }) => {
        createdBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(makeTask({ id: 77 }), { status: 201 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);
    await loadedBoard();

    await user.click(screen.getByRole("button", { name: t("tasks.newDuty") }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(t("tasks.form.titleLabel")), "  Scrub troughs  ");
    // The due date is prefilled with today by dutyDefaults().
    expect(within(dialog).getByLabelText(t("tasks.form.dueDateLabel"))).toHaveValue(TODAY);
    await user.click(within(dialog).getByRole("button", { name: t("tasks.form.create") }));

    await waitFor(() => expect(createdBody).not.toBeNull());
    expect(createdBody).toMatchObject({
      title: "Scrub troughs",
      due_date: TODAY,
      animal_id: null,
      assigned_role_id: null,
      assigned_user_id: null,
    });
  });

  it("stays silent when the farm changes under an in-flight completion", async () => {
    await assertFarmSwitchSuppressesContinuation("today", [
      makeTask({ id: 6, title: "Milk the does" }),
    ], "complete", t("tasks.complete"), 6);
  });

  it("stays silent when the farm changes under an in-flight skip", async () => {
    await assertFarmSwitchSuppressesContinuation("today", [
      makeTask({ id: 7, title: "Top up chalk" }),
    ], "skip", t("tasks.skip"), 7);
  });

  it("stays silent when the farm changes under an in-flight verification", async () => {
    await assertFarmSwitchSuppressesContinuation("awaiting", [
      makeTask({
        id: 8,
        title: "Awaiting check",
        status: "DONE",
        needs_verification: true,
        completed_by_id: 999,
        completed_at: "2026-08-05T14:07:00",
      }),
    ], "verify", t("tasks.verify"), 8);
  });

  it("stays silent when the farm changes under an in-flight rejection", async () => {
    let rejectRelease!: () => void;
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json(
          boardPayload({
            awaiting: [
              makeTask({
                id: 9,
                title: "Reject me",
                status: "DONE",
                needs_verification: true,
                completed_by_id: 999,
                completed_at: "2026-08-05T14:07:00",
              }),
            ],
            awaiting_total: 1,
          }),
        ),
      ),
      http.post("/api/tasks/9/reject", () => new Promise((resolve) => {
        rejectRelease = () => resolve(HttpResponse.json(makeTask({ id: 9 })));
      })),
    );
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);
    await loadedBoard();
    await userEvent.click(screen.getByRole("tab", { name: /awaiting/i }));
    await within(mobileCardList()).findByText("Reject me");

    await user.click(within(mobileCardList()).getByRole("button", { name: t("tasks.reject") }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(t("tasks.reject.reason")), "Wrong weights");
    await user.click(within(dialog).getByRole("button", { name: t("tasks.reject.confirm") }));
    await waitFor(() => expect(rejectRelease).toBeDefined());
    expect(await within(dialog).findByText(t("tasks.reject.inFlight"))).toBeInTheDocument();
    toastMocks.success.mockClear();
    toastMocks.error.mockClear();
    act(() => setCurrentFarmId("77"));
    await act(async () => {
      rejectRelease();
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(toastMocks.success).not.toHaveBeenCalled();
    expect(toastMocks.error).not.toHaveBeenCalled();
    expect(screen.queryByText(t("tasks.actionErrorSuffix"))).not.toBeInTheDocument();
  });

  async function assertFarmSwitchSuppressesContinuation(
    tab: "today" | "awaiting",
    tasks: TaskOut[],
    action: string,
    buttonName: string,
    taskId: number,
    fail = false,
  ) {
    let releaseAction!: () => void;
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json(
          tab === "today"
            ? boardPayload({ today: tasks, today_total: tasks.length })
            : boardPayload({ awaiting: tasks, awaiting_total: tasks.length }),
        ),
      ),
      http.post(`/api/tasks/:taskId/${action}`, () =>
        new Promise((resolve, reject) => {
          releaseAction = fail
            ? () => reject(new Error("boom"))
            : () => resolve(HttpResponse.json(makeTask({ id: taskId })));
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);
    await loadedBoard(tab === "today" ? tasks.length : 0);
    if (tab === "awaiting") {
      await userEvent.click(screen.getByRole("tab", { name: /awaiting/i }));
    }
    await within(mobileCardList()).findByText(tasks[0]!.title);

    await user.click(within(mobileCardList()).getByRole("button", { name: buttonName }));
    if (action === "skip") {
      // Skip is dialog-mediated: give the (now required) reason, then confirm
      // to put the write on the wire.
      const dialog = await screen.findByRole("dialog");
      await user.type(within(dialog).getByLabelText(t("tasks.skip.reason")), "not needed");
      await user.click(within(dialog).getByRole("button", { name: t("tasks.skip.confirm") }));
    }
    await waitFor(() => expect(releaseAction).toBeDefined());
    // Switch farms, THEN settle the write: the continuation (invalidation,
    // success toast, error banner) belongs to the previous farm and must not
    // fire on the new one.
    toastMocks.success.mockClear();
    toastMocks.error.mockClear();
    act(() => setCurrentFarmId("77"));
    await act(async () => {
      releaseAction();
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(toastMocks.success).not.toHaveBeenCalled();
    expect(toastMocks.error).not.toHaveBeenCalled();
    // The inline action-error banner (with its review suffix) is equally
    // forbidden on the new farm.
    expect(screen.queryByText(/Review the duty, then try again\./)).not.toBeInTheDocument();
  }

  it.each([
    ["complete", t("tasks.complete")],
    ["skip", t("tasks.skip")],
  ] as const)("stays silent when a FAILED %s settles after the farm changed", async (action, label) => {
    await assertFarmSwitchSuppressesContinuation(
      "today",
      [makeTask({ id: 61, title: `Failing ${action}` })],
      action,
      label,
      61,
      true,
    );
  });

  it("stays silent when a FAILED verification settles after the farm changed", async () => {
    await assertFarmSwitchSuppressesContinuation(
      "awaiting",
      [
        makeTask({
          id: 62,
          title: "Verify me late",
          status: "DONE",
          needs_verification: true,
          completed_by_id: 999,
          completed_at: "2026-08-05T14:07:00",
        }),
      ],
      "verify",
      t("tasks.verify"),
      62,
      true,
    );
  });

  it("stays silent when a FAILED rejection settles after the farm changed", async () => {
    let rejectFail!: () => void;
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json(
          boardPayload({
            awaiting: [
              makeTask({
                id: 63,
                title: "Reject me late",
                status: "DONE",
                needs_verification: true,
                completed_by_id: 999,
                completed_at: "2026-08-05T14:07:00",
              }),
            ],
            awaiting_total: 1,
          }),
        ),
      ),
      http.post("/api/tasks/63/reject", () =>
        new Promise((_resolve, reject) => {
          rejectFail = () => reject(new Error("boom"));
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);
    await loadedBoard();
    await userEvent.click(screen.getByRole("tab", { name: /awaiting/i }));
    await within(mobileCardList()).findByText("Reject me late");

    await user.click(within(mobileCardList()).getByRole("button", { name: t("tasks.reject") }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(t("tasks.reject.reason")), "Wrong weights");
    await user.click(within(dialog).getByRole("button", { name: t("tasks.reject.confirm") }));
    await waitFor(() => expect(rejectFail).toBeDefined());
    toastMocks.success.mockClear();
    toastMocks.error.mockClear();
    act(() => setCurrentFarmId("77"));
    await act(async () => {
      rejectFail();
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(toastMocks.success).not.toHaveBeenCalled();
    expect(toastMocks.error).not.toHaveBeenCalled();
    expect(screen.queryByText(t("tasks.actionErrorSuffix"))).not.toBeInTheDocument();
  });
});

describe("makeDutySchema — campaign kills", () => {
  const schema = makeDutySchema((key, vars) => translate("en", key, vars));
  const base = { title: "Scrub troughs", due_date: "2026-09-01", category: "OTHER" as const };

  function issuesOf(input: Record<string, unknown>): string[] {
    const result = schema.safeParse({ ...base, ...input });
    return result.success ? [] : result.error.issues.map((i) => i.message);
  }

  it("validates the due-date grammar and year band", () => {
    expect(issuesOf({ due_date: "2026-9-1" })).toContain(t("tasks.form.dueInvalid"));
    expect(issuesOf({ due_date: "1999-12-31" })).toContain(t("tasks.form.yearBand"));
    expect(issuesOf({ due_date: "2101-01-01" })).toContain(t("tasks.form.yearBand"));
    expect(issuesOf({ due_date: "2000-01-01" })).toEqual([]);
    expect(issuesOf({ due_date: "2100-12-31" })).toEqual([]);
  });

  it("validates the recurrence window", () => {
    expect(issuesOf({ recur_days: "0" })).toContain(
      t("tasks.form.recurInvalid", { max: 3650 }),
    );
    expect(issuesOf({ recur_days: "1.5" })).toContain(
      t("tasks.form.recurInvalid", { max: 3650 }),
    );
    expect(issuesOf({ recur_days: "" })).toEqual([]);
    // A series whose first occurrence already leaves the 2100 band.
    expect(issuesOf({ recur_days: "3650", due_date: "2100-12-31" })).toContain(
      t("tasks.form.recurTooLate"),
    );
  });

  it("requires a title and due date", () => {
    expect(issuesOf({ title: "   " })).toContain(t("tasks.form.titleRequired"));
    expect(issuesOf({ due_date: "" })).toContain(t("tasks.form.dueRequired"));
  });
});

/** Card root for a duty's mobile card (the below-md surface). */
function cardFor(title: string): HTMLElement {
  const card = within(mobileCardList()).getByText(title).closest('[class*="rounded-xl"]');
  expect(card).not.toBeNull();
  return card as HTMLElement;
}

describe("TasksPage — campaign kills, second wave", () => {
  beforeEach(() => {
    nav.state.search = "";
    nav.router.push.mockClear();
    nav.router.replace.mockClear();
    toastMocks.success.mockClear();
    toastMocks.error.mockClear();
  });

  it("rewrites a bogus deep link with a broken offset to the today tab", async () => {
    nav.state.search = "?tab=nonsense&today_offset=abc";
    renderWithProviders(<TasksPage />);
    await loadedBoard();
    await waitFor(() =>
      expect(nav.router.replace).toHaveBeenCalledWith(expect.stringContaining("tab=today")),
    );
    expect(screen.getByText("No tasks for today.")).toBeInTheDocument();
  });

  it("pages a deep-linked offset through to the board query", async () => {
    const seenOffsets: (string | null)[] = [];
    server.use(
      http.get("/api/tasks", ({ request }) => {
        seenOffsets.push(new URL(request.url).searchParams.get("today_offset"));
        return HttpResponse.json(boardPayload({ today_total: 120 }));
      }),
    );
    nav.state.search = "?tab=today&today_offset=50";
    renderWithProviders(<TasksPage />);
    await screen.findByRole("tab", { name: "Today (120)" });
    await waitFor(() => expect(seenOffsets).toContain("50"));
    expect(seenOffsets).not.toContain("0");
  });

  it("treats an exponential-notation offset as absent", async () => {
    const seenOffsets: (string | null)[] = [];
    server.use(
      http.get("/api/tasks", ({ request }) => {
        seenOffsets.push(new URL(request.url).searchParams.get("today_offset"));
        return HttpResponse.json(boardPayload());
      }),
    );
    nav.state.search = "?tab=today&today_offset=1e3";
    renderWithProviders(<TasksPage />);
    await screen.findByRole("tab", { name: /Today/ });
    await waitFor(() => expect(seenOffsets.length).toBeGreaterThan(0));
    expect(seenOffsets).not.toContain("1000");
  });

  it("rewrites an awaiting deep link to today without tasks.verify", async () => {
    server.use(permissionsHandler(["tasks.view"]));
    nav.state.search = "?tab=awaiting";
    renderWithProviders(<TasksPage />);
    await loadedBoard();
    await waitFor(() =>
      expect(nav.router.replace).toHaveBeenCalledWith(expect.stringContaining("tab=today")),
    );
  });

  it("renders the loading header copy while the board is in flight", async () => {
    let releaseBoard!: () => void;
    server.use(
      http.get(
        "/api/tasks",
        () =>
          new Promise((resolve) => {
            releaseBoard = () => resolve(HttpResponse.json(boardPayload()));
          }),
      ),
    );
    renderWithProviders(<TasksPage />);
    expect(await screen.findByText(t("tasks.loadingTasks"))).toBeInTheDocument();
    expect(screen.getByText("Tasks")).toBeInTheDocument();
    expect(screen.getByText(t("tasks.description"))).toBeInTheDocument();

    await act(async () => {
      releaseBoard();
    });
    await loadedBoard();
  });

  it("shows the stale-data banner only when a background refresh fails, and retries", async () => {
    let calls = 0;
    server.use(
      http.get("/api/tasks", () => {
        calls += 1;
        if (calls === 2) return HttpResponse.json({ detail: "boom" }, { status: 500 });
        return HttpResponse.json(
          boardPayload({ today: [makeTask({ id: 71, title: "Steady row" })], today_total: 1 }),
        );
      }),
      http.post("/api/tasks/71/complete", () =>
        HttpResponse.json(makeTask({ id: 71, status: "DONE" })),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);
    await loadedBoard(1);
    expect(
      screen.queryByText("Could not refresh — showing the last loaded data."),
    ).toBeNull();

    // Completing invalidates the board; the refresh fails while the previous
    // payload stays on screen behind the stale-data banner.
    await user.click(
      within(mobileCardList()).getByRole("button", { name: t("tasks.complete") }),
    );
    expect(
      await screen.findByText("Could not refresh — showing the last loaded data."),
    ).toBeInTheDocument();
    expect(within(mobileCardList()).getByText("Steady row")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() =>
      expect(
        screen.queryByText("Could not refresh — showing the last loaded data."),
      ).not.toBeInTheDocument(),
    );
  });

  it("keeps the team directory off unless the completed tab shows rows", async () => {
    let teamGets = 0;
    server.use(
      http.get("/api/team", () => {
        teamGets += 1;
        return HttpResponse.json(TEAM_PAYLOAD);
      }),
      http.get("/api/tasks", () =>
        HttpResponse.json(
          boardPayload({
            today: [makeTask({ id: 72, title: "Solo duty" })],
            today_total: 1,
            completed_total: 0,
          }),
        ),
      ),
    );
    renderWithProviders(<TasksPage />);
    await loadedBoard(1);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(teamGets).toBe(0);

    await userEvent.click(screen.getByRole("tab", { name: /completed/i }));
    await screen.findByText("No completed tasks.");
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(teamGets).toBe(0);
  });

  it("tints only overdue and due-today badges and keeps future rows neutral", async () => {
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json(
          boardPayload({
            today: [
              makeTask({ id: 81, title: "Future duty", due_date: addDays(TODAY, 2) }),
              makeTask({ id: 82, title: "Due now", due_date: TODAY }),
              makeTask({ id: 83, title: "Late duty", due_date: addDays(TODAY, -1) }),
            ],
            today_total: 3,
          }),
        ),
      ),
    );
    renderWithProviders(<TasksPage />);
    await loadedBoard(3);
    const cards = mobileCardList();

    const futureBadge = within(cards).getByText(formatDate(addDays(TODAY, 2)));
    expect(futureBadge.className).toContain("bg-secondary");
    expect(futureBadge.className).not.toContain("bg-warning-tint");
    expect(futureBadge.className).not.toContain("bg-destructive/10");

    const todayBadge = within(cards).getByText(formatDate(TODAY));
    expect(todayBadge.className).toContain("bg-warning-tint");

    const lateBadge = within(cards).getByText(formatDate(addDays(TODAY, -1)));
    expect(lateBadge.className).toContain("bg-destructive/10");
  });

  it("maps completed rows: status badge, skip reason, finished stamp — without leaking into other rows", async () => {
    server.use(
      http.get("/api/team", () => HttpResponse.json(TEAM_PAYLOAD)),
      http.get("/api/tasks", () =>
        HttpResponse.json(
          boardPayload({
            today: [
              makeTask({
                id: 91,
                title: "Pending with leftovers",
                skip_reason: "stale note",
                completed_at: "2026-08-20T08:00:00",
              }),
            ],
            today_total: 1,
            completed: [
              makeTask({
                id: 92,
                title: "Skipped sweep",
                status: "SKIPPED",
                skipped_by_id: 9,
                skipped_at: "2026-08-20T08:00:00",
                skip_reason: "Rain flooded the pen",
              }),
              makeTask({
                id: 93,
                title: "Done and dusted",
                status: "DONE",
                completed_by_id: 9,
                completed_at: "2026-08-21T09:00:00",
                skip_reason: "should not leak",
              }),
            ],
            completed_total: 2,
          }),
        ),
      ),
    );
    renderWithProviders(<TasksPage />);
    await loadedBoard(1);
    // Pending row: no status badge, no skip-reason line, no finished stamp.
    const pendingCard = cardFor("Pending with leftovers");
    expect(within(pendingCard).queryByText("PENDING")).not.toBeInTheDocument();
    expect(within(pendingCard).queryByText(/Reason:/)).not.toBeInTheDocument();
    expect(within(pendingCard).queryByText(/by Raju/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: /completed/i }));
    const doneCards = mobileCardList();
    await within(doneCards).findByText("Skipped sweep");
    const skippedCard = cardFor("Skipped sweep");
    expect(within(skippedCard).getByText("SKIPPED")).toBeInTheDocument();
    expect(
      within(skippedCard).getByText(t("tasks.skipReasonLabel", { reason: "Rain flooded the pen" })),
    ).toBeInTheDocument();

    const doneCard = cardFor("Done and dusted");
    expect(within(doneCard).getByText("DONE")).toBeInTheDocument();
    expect(within(doneCard).queryByText(/Reason:/)).not.toBeInTheDocument();
    expect(within(doneCard).getByText(/by Raju/)).toBeInTheDocument();

    // The desktop table carries the same skip-reason line.
    const table = document.querySelector(".md\\:block table");
    expect(table).not.toBeNull();
    expect(within(table as HTMLElement).getByText(/Rain flooded the pen/)).toBeInTheDocument();
    expect(within(table as HTMLElement).queryByText(/should not leak/)).not.toBeInTheDocument();
  });

  it("renders assignment attribution, animal links and category labels on cards", async () => {
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json(
          boardPayload({
            today: [
              makeTask({
                id: 101,
                title: "Personal duty",
                assigned_user_name: "Raju",
                assigned_role_name: "Vet",
              }),
              makeTask({
                id: 102,
                title: "Role duty",
                assigned_role_name: "Mover",
              }),
              makeTask({ id: 103, title: "Unassigned duty" }),
              makeTask({
                id: 104,
                title: "Tagged duty",
                animal_id: 3,
                animal_tag: "G-003",
              }),
            ],
            today_total: 4,
          }),
        ),
      ),
    );
    renderWithProviders(<TasksPage />);
    await loadedBoard(4);
    const cards = mobileCardList();

    expect(
      within(cardFor("Personal duty")).getByText(`Raju ${t("tasks.viaRole", { role: "Vet" })}`),
    ).toBeInTheDocument();
    expect(within(cardFor("Role duty")).getByText("Mover")).toBeInTheDocument();
    expect(within(cardFor("Unassigned duty")).getByText("—")).toBeInTheDocument();

    const tag = within(cards).getByText("G-003");
    expect(tag).toHaveAttribute("href", expect.stringContaining("/animals/3"));
    const taggedCard = cardFor("Tagged duty");
    expect(within(taggedCard).getByText("·")).toBeInTheDocument();

    const bareCard = cardFor("Unassigned duty");
    expect(within(bareCard).queryByText(/^· —$/)).not.toBeInTheDocument();
    expect(within(cards).getAllByText(enumLabel("taskCategory", "OTHER", "en"))).toHaveLength(4);
  });

  it("locks future auto-generated duties out of early completion", async () => {
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json(
          boardPayload({
            today: [
              makeTask({
                id: 111,
                title: "Future vaccine",
                due_date: addDays(TODAY, 3),
                auto_generated: true,
              }),
            ],
            today_total: 1,
          }),
        ),
      ),
    );
    renderWithProviders(<TasksPage />);
    await loadedBoard(1);
    const cards = mobileCardList();
    expect(within(cards).getByText(t("tasks.notDueActions"))).toBeInTheDocument();
    expect(within(cards).queryByRole("button", { name: t("tasks.complete") })).not.toBeInTheDocument();
  });

  it("styles the Open-form link for touch and table surfaces", async () => {
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json(
          boardPayload({
            today: [
              makeTask({
                id: 121,
                title: "Linked breeding form",
                action_url: "/breeding/records/new?animal=4",
              }),
            ],
            today_total: 1,
          }),
        ),
      ),
    );
    renderWithProviders(<TasksPage />);
    await loadedBoard(1);

    const cards = mobileCardList();
    const cardLink = within(cards).getByRole("link", { name: t("tasks.openForm") });
    expect(cardLink.className).toContain("h-11");
    expect(cardLink.className).toContain("px-4");
    expect(cardLink.className).toContain("border-border");

    const table = document.querySelector(".md\\:block table");
    const tableLink = within(table as HTMLElement).getByRole("link", { name: t("tasks.openForm") });
    expect(tableLink.className).not.toContain("h-11");
    expect(tableLink.className).toContain("bg-background");
  });

  it("hides self-verification for the current worker only (non-owner)", async () => {
    server.use(permissionsHandler(["tasks.view", "tasks.verify"]));
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json(
          boardPayload({
            awaiting: [
              makeTask({
                id: 131,
                title: "My own work",
                status: "DONE",
                needs_verification: true,
                completed_by_id: 1,
                completed_at: "2026-08-05T14:07:00",
              }),
              makeTask({
                id: 132,
                title: "Someone else's work",
                status: "DONE",
                needs_verification: true,
                completed_by_id: 999,
                completed_at: "2026-08-05T14:07:00",
              }),
            ],
            awaiting_total: 2,
          }),
        ),
      ),
    );
    renderWithProviders(<TasksPage />);
    await loadedBoard();
    await userEvent.click(screen.getByRole("tab", { name: /awaiting/i }));
    await within(mobileCardList()).findByText("My own work");

    const own = cardFor("My own work");
    expect(within(own).queryByRole("button", { name: t("tasks.verify") })).not.toBeInTheDocument();
    const other = cardFor("Someone else's work");
    expect(within(other).getByRole("button", { name: t("tasks.verify") })).toBeInTheDocument();
  });

  it("renders finished stamps without a team directory for workers", async () => {
    server.use(permissionsHandler(["tasks.view", "tasks.complete"]));
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json(
          boardPayload({
            completed: [
              makeTask({
                id: 141,
                title: "Worker's finished duty",
                status: "DONE",
                completed_by_id: 9,
                completed_at: "2026-08-21T09:00:00",
              }),
            ],
            completed_total: 1,
          }),
        ),
      ),
    );
    renderWithProviders(<TasksPage />);
    await loadedBoard();
    await userEvent.click(screen.getByRole("tab", { name: /completed/i }));
    const cards = mobileCardList();
    expect(await within(cards).findByText("Worker's finished duty")).toBeInTheDocument();
    expect(within(cards).queryByText(/by /)).not.toBeInTheDocument();
  });
});

describe("TasksPage dialogs — campaign kills", () => {
  beforeEach(() => {
    nav.state.search = "";
    nav.router.push.mockClear();
    nav.router.replace.mockClear();
    toastMocks.success.mockClear();
    toastMocks.error.mockClear();
  });

  it("opens the reject dialog clean, validates aria state, and closes on Escape", async () => {
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json(
          boardPayload({
            awaiting: [
              makeTask({
                id: 5,
                title: "Awaiting check",
                status: "DONE",
                needs_verification: true,
                completed_by_id: 999,
                completed_at: "2026-08-05T14:07:00",
              }),
            ],
            awaiting_total: 1,
          }),
        ),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);
    await loadedBoard();
    await userEvent.click(screen.getByRole("tab", { name: /awaiting/i }));
    await within(mobileCardList()).findByText("Awaiting check");

    await user.click(within(mobileCardList()).getByRole("button", { name: t("tasks.reject") }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(t("tasks.reject.body"))).toBeInTheDocument();
    const reason = within(dialog).getByLabelText(t("tasks.reject.reason"));
    // A fresh dialog per opening: no stale reason, no stale required flag.
    expect(reason).toHaveValue("");
    expect(within(dialog).queryByText(t("tasks.reject.reasonMissing"))).not.toBeInTheDocument();
    expect(reason).not.toHaveAttribute("aria-invalid");

    await user.click(within(dialog).getByRole("button", { name: t("tasks.reject.confirm") }));
    expect(await within(dialog).findByText(t("tasks.reject.reasonMissing"))).toBeInTheDocument();
    expect(reason).toHaveAttribute("aria-invalid", "true");
    expect(reason.getAttribute("aria-describedby")).toBe("reject-reason-missing-5");
    expect(document.getElementById("reject-reason-missing-5")).not.toBeNull();

    // A whitespace-only reason does not clear the missing flag…
    await user.type(reason, " ");
    expect(within(dialog).getByText(t("tasks.reject.reasonMissing"))).toBeInTheDocument();
    // …a real one does.
    await user.type(reason, "Wrong");
    expect(within(dialog).queryByText(t("tasks.reject.reasonMissing"))).not.toBeInTheDocument();
    expect(reason).not.toHaveAttribute("aria-invalid");

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("labels the reject control for retry and shows the error suffix after a failed reject", async () => {
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json(
          boardPayload({
            awaiting: [
              makeTask({
                id: 151,
                title: "Reject retry",
                status: "DONE",
                needs_verification: true,
                completed_by_id: 999,
                completed_at: "2026-08-05T14:07:00",
              }),
            ],
            awaiting_total: 1,
          }),
        ),
      ),
      http.post("/api/tasks/151/reject", () =>
        HttpResponse.json({ detail: "Duty already verified." }, { status: 409 }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);
    await loadedBoard();
    await userEvent.click(screen.getByRole("tab", { name: /awaiting/i }));
    await within(mobileCardList()).findByText("Reject retry");
    expect(within(mobileCardList()).getByRole("button", { name: t("tasks.reject") }));

    await user.click(within(mobileCardList()).getByRole("button", { name: t("tasks.reject") }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(t("tasks.reject.reason")), "Wrong weights");
    await user.click(within(dialog).getByRole("button", { name: t("tasks.reject.confirm") }));
    const liveDialog = await screen.findByRole("dialog");
    expect(
      await within(liveDialog).findByText(/Duty already verified\./),
    ).toBeInTheDocument();
    expect(within(liveDialog).getByText(/Review the duty, then try again\./)).toBeInTheDocument();
    await waitFor(() =>
      expect(
        within(liveDialog).getByRole("button", { name: t("tasks.retryReject") }),
      ).toBeInTheDocument(),
    );
    // The row-level control relabels too (visible once the dialog closes —
    // the open modal inerts the row behind it).
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(
      within(mobileCardList()).getByRole("button", { name: t("tasks.retryReject") }),
    ).toBeInTheDocument();
  });

  it("labels a failed verification with its banner and retry control", async () => {
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json(
          boardPayload({
            awaiting: [
              makeTask({
                id: 161,
                title: "Verify retry",
                status: "DONE",
                needs_verification: true,
                completed_by_id: 999,
                completed_at: "2026-08-05T14:07:00",
              }),
            ],
            awaiting_total: 1,
          }),
        ),
      ),
      http.post("/api/tasks/161/verify", () =>
        HttpResponse.json({ detail: "Already verified elsewhere." }, { status: 409 }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);
    await loadedBoard();
    await userEvent.click(screen.getByRole("tab", { name: /awaiting/i }));
    await within(mobileCardList()).findByText("Verify retry");

    await user.click(within(mobileCardList()).getByRole("button", { name: t("tasks.verify") }));
    expect(await within(mobileCardList()).findByText(/Already verified elsewhere\./)).toBeInTheDocument();
    expect(within(mobileCardList()).getByText(/Review the duty, then try again\./)).toBeInTheDocument();
    expect(
      within(mobileCardList()).getByRole("button", { name: t("tasks.retryVerify") }),
    ).toBeInTheDocument();
  });

  it("shows the skip dialog copy and its in-flight label while the write is out", async () => {
    let releaseSkip!: () => void;
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json(
          boardPayload({ today: [makeTask({ id: 171, title: "Skippable" })], today_total: 1 }),
        ),
      ),
      http.post("/api/tasks/171/skip", () =>
        new Promise((resolve) => {
          releaseSkip = () => resolve(HttpResponse.json(makeTask({ id: 171 })));
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);
    await loadedBoard(1);
    await within(mobileCardList()).findByText("Skippable");

    await user.click(within(mobileCardList()).getByRole("button", { name: t("tasks.skip") }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(t("tasks.skip.title"))).toBeInTheDocument();
    expect(within(dialog).getByText(t("tasks.skip.body"))).toBeInTheDocument();

    await user.type(within(dialog).getByLabelText(t("tasks.skip.reason")), "not needed");
    await user.click(within(dialog).getByRole("button", { name: t("tasks.skip.confirm") }));
    expect(await within(dialog).findByText(t("tasks.skip.inFlight"))).toBeInTheDocument();
    // Dismissal is locked while the write is in flight.
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    releaseSkip();
    await waitFor(() => expect(toastMocks.success).toHaveBeenCalledWith(t("tasks.toast.skipped")));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("locks the recurring confirm dialog against dismissal while the write is out", async () => {
    let releaseComplete!: () => void;
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json(
          boardPayload({ today: [makeTask({ id: 183, title: "Daily pen check", recur_days: 1 })], today_total: 1 }),
        ),
      ),
      http.post("/api/tasks/183/complete", () =>
        new Promise((resolve) => {
          releaseComplete = () => resolve(HttpResponse.json(makeTask({ id: 183 })));
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);
    await loadedBoard(1);
    await within(mobileCardList()).findByText("Daily pen check");

    await user.click(within(mobileCardList()).getByRole("button", { name: t("tasks.complete") }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: t("tasks.recurConfirm.confirm") }));
    // The write is on the wire: Escape must not dismiss the dialog.
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    releaseComplete();
    await waitFor(() => expect(toastMocks.success).toHaveBeenCalledWith(t("tasks.toast.completed")));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("completes a recurring duty through its confirm dialog, with copy, error banner and Escape", async () => {
    let rejectComplete!: () => void;
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json(
          boardPayload({
            today: [makeTask({ id: 181, title: "Weekly pen check", recur_days: 7 })],
            today_total: 1,
          }),
        ),
      ),
      http.post("/api/tasks/181/complete", () =>
        new Promise((_resolve, reject) => {
          rejectComplete = () => reject(new Error("boom"));
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);
    await loadedBoard(1);
    await within(mobileCardList()).findByText("Weekly pen check");

    await user.click(within(mobileCardList()).getByRole("button", { name: t("tasks.complete") }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(t("tasks.recurConfirm.title"))).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        t("tasks.recurConfirm.body", {
          days: 7,
          date: formatDate(addDays(TODAY, 7)),
        }),
        { exact: false },
      ),
    ).toBeInTheDocument();

    // Escape closes the confirm without completing.
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    // A failed completion surfaces the banner inside the still-open dialog.
    await user.click(within(mobileCardList()).getByRole("button", { name: t("tasks.complete") }));
    const dialogAgain = await screen.findByRole("dialog");
    await user.click(
      within(dialogAgain).getByRole("button", { name: t("tasks.recurConfirm.confirm") }),
    );
    await waitFor(() => expect(rejectComplete).toBeDefined());
    await act(async () => {
      rejectComplete();
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(
      await within(screen.getByRole("dialog")).findByText(/Review the duty, then try again\./),
    ).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(
      within(mobileCardList()).getByRole("button", { name: t("tasks.retryComplete") }),
    ).toBeInTheDocument();
  });
});

describe("TasksPage create dialog — campaign kills", () => {
  beforeEach(() => {
    nav.state.search = "";
    nav.router.push.mockClear();
    nav.router.replace.mockClear();
    toastMocks.success.mockClear();
    toastMocks.error.mockClear();
  });

  async function openCreateDialog(user: ReturnType<typeof userEvent.setup>) {
    await screen.findByRole("tab", { name: `Today (0)` });
    await user.click(screen.getByRole("button", { name: t("tasks.newDuty") }));
    return await screen.findByRole("dialog");
  }

  it("carries the catalog copy for every labeled control, including the animal picker title", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);
    const dialog = await openCreateDialog(user);

    expect(within(dialog).getByText(t("tasks.newDuty"))).toBeInTheDocument();
    expect(within(dialog).getByText(t("tasks.form.intro"))).toBeInTheDocument();
    expect(within(dialog).getByLabelText(t("tasks.form.titleLabel"))).toBeInTheDocument();
    expect(within(dialog).getByPlaceholderText(t("tasks.form.titlePlaceholder"))).toBeInTheDocument();
    expect(within(dialog).getByLabelText(t("tasks.form.dueDateLabel"))).toBeInTheDocument();
    expect(within(dialog).getByPlaceholderText(t("tasks.form.recurPlaceholder"))).toBeInTheDocument();
    expect(within(dialog).getByLabelText(t("tasks.form.animalLabel"))).toBeInTheDocument();
    expect(
      within(dialog).getByText(t("common.none"), { selector: "#duty-animal-picker-value" }),
    ).toBeInTheDocument();
    expect(within(dialog).getByText(t("tasks.form.animalHelp"))).toBeInTheDocument();
    expect(within(dialog).getByText(t("tasks.form.assignment"))).toBeInTheDocument();

    // The animal picker's browse dialog inherits the page-provided title.
    await user.click(within(dialog).getByRole("combobox", { name: t("tasks.form.animalLabel") }));
    const pickerDialog = await screen.findByRole("dialog", {
      name: t("tasks.form.animalDialogTitle"),
    });
    expect(pickerDialog).toBeInTheDocument();
  });

  it("lists exactly the team's roles and active workers in the assignment selects", async () => {
    server.use(
      http.get("/api/team", () =>
        HttpResponse.json({
          ...TEAM_PAYLOAD,
          roles: [
            ...TEAM_PAYLOAD.roles,
            ...([] as typeof TEAM_PAYLOAD.roles),
          ],
          memberships: [
            ...TEAM_PAYLOAD.memberships,
            {
              id: 2,
              user_id: 10,
              email: "inactive@goatfarm.test",
              name: "Kiran",
              role_id: 2,
              role_name: "Vet",
              is_active: false,
              can_reset_password: true,
              reset_password_block_reason: null,
            },
          ],
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);
    const dialog = await openCreateDialog(user);

    await user.click(within(dialog).getByRole("combobox", { name: t("tasks.form.assignToRole") }));
    const roleOptions = await within(document.body).findAllByRole("option");
    expect(roleOptions.map((o) => o.textContent)).toEqual([t("common.none"), "Vet"]);
    await user.click(roleOptions[0]!);

    await user.click(within(dialog).getByRole("combobox", { name: t("tasks.form.assignToWorker") }));
    const workerOptions = await within(document.body).findAllByRole("option");
    expect(workerOptions.map((o) => o.textContent)).toEqual([
      t("common.none"),
      "Raju (Vet)",
    ]);
    await user.click(workerOptions[0]!);
  });

  it("toasts success, closes, and resets the form for the next duty", async () => {
    server.use(
      http.post("/api/tasks", () => HttpResponse.json(makeTask({ id: 77 }), { status: 201 })),
    );
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);
    const dialog = await openCreateDialog(user);

    await user.type(within(dialog).getByLabelText(t("tasks.form.titleLabel")), "Scrub troughs");
    await user.click(within(dialog).getByRole("button", { name: t("tasks.form.create") }));
    await waitFor(() => expect(toastMocks.success).toHaveBeenCalledWith(t("tasks.toast.created")));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    const freshDialog = await openCreateDialog(user);
    expect(within(freshDialog).getByLabelText(t("tasks.form.titleLabel"))).toHaveValue("");
  });

  it("surfaces a failed create inline, toasts, and clears the error when reopened", async () => {
    server.use(
      http.post("/api/tasks", () =>
        HttpResponse.json({ detail: "Due date is in the past." }, { status: 422 }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);
    const dialog = await openCreateDialog(user);

    await user.type(within(dialog).getByLabelText(t("tasks.form.titleLabel")), "Bad duty");
    await user.click(within(dialog).getByRole("button", { name: t("tasks.form.create") }));
    expect(await within(dialog).findByText(/Due date is in the past\./)).toBeInTheDocument();
    expect(within(dialog).getByText(/Check the duty details, then try again\./)).toBeInTheDocument();
    expect(toastMocks.error).toHaveBeenCalledWith("Due date is in the past.");
    expect(
      within(dialog).getByRole("button", { name: t("tasks.form.retryCreate") }),
    ).toBeInTheDocument();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const freshDialog = await openCreateDialog(user);
    expect(
      within(freshDialog).queryByText(/Check the duty details, then try again\./),
    ).not.toBeInTheDocument();
  });

  it("stays silent when a create FAILS after the farm changed", async () => {
    let failCreate!: () => void;
    server.use(
      http.post("/api/tasks", () =>
        new Promise((_resolve, reject) => {
          failCreate = () => reject(new Error("boom"));
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<TasksPage />);
    const dialog = await openCreateDialog(user);

    await user.type(within(dialog).getByLabelText(t("tasks.form.titleLabel")), "Never lands");
    await user.click(within(dialog).getByRole("button", { name: t("tasks.form.create") }));
    await waitFor(() => expect(failCreate).toBeDefined());
    toastMocks.success.mockClear();
    toastMocks.error.mockClear();
    act(() => setCurrentFarmId("77"));
    await act(async () => {
      failCreate();
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(toastMocks.error).not.toHaveBeenCalled();
    expect(toastMocks.success).not.toHaveBeenCalled();
  });
});

describe("duty schema rebuild on language change — campaign kill", () => {
  function LangSwitcher({ to }: { to: Language }) {
    const { setLanguage } = useLanguage();
    return (
      <button type="button" onClick={() => setLanguage(to)}>
        switch language
      </button>
    );
  }

  it("revalidates with Telugu messages after the language flips mid-session", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <LanguageProvider>
        <LangSwitcher to="te" />
        <TasksPage />
      </LanguageProvider>,
    );
    await screen.findByRole("tab", { name: /Today/ });
    // Flip the language while no modal dialog inerts the switcher.
    await user.click(screen.getByRole("button", { name: "switch language" }));
    await user.click(await screen.findByRole("button", { name: "కొత్త పని" }));
    const dialog = await screen.findByRole("dialog");

    fireEvent.submit(dialog.querySelector("form")!);
    expect(await within(dialog).findByText("శీర్షిక అవసరం")).toBeInTheDocument();
  });
});

describe("makeDutySchema — campaign stragglers", () => {
  const schema = makeDutySchema((key, vars) => translate("en", key, vars));
  const base = { title: "Scrub troughs", due_date: "2026-09-01", category: "OTHER" as const };

  function issuesOf(input: Record<string, unknown>): { messages: string[]; codes: string[] } {
    const result = schema.safeParse({ ...base, ...input });
    return result.success
      ? { messages: [], codes: [] }
      : {
          messages: result.error.issues.map((i) => i.message),
          codes: result.error.issues.map((i) => i.code),
        };
  }

  it("rejects due dates with trailing garbage behind a valid prefix", () => {
    const { messages } = issuesOf({ due_date: "2026-09-011" });
    expect(messages).toContain(t("tasks.form.dueInvalid"));
    const ok = issuesOf({ due_date: "2026-09-01" });
    expect(ok.messages).toEqual([]);
  });

  it("does not double-report a recurrence out of window as too-late", () => {
    const outOfWindow = issuesOf({ recur_days: "9999", due_date: "2026-01-01" });
    expect(outOfWindow.messages).toContain(
      t("tasks.form.recurInvalid", { max: 3650 }),
    );
    expect(outOfWindow.messages).not.toContain(t("tasks.form.recurTooLate"));
    // Even at the band's very end, an out-of-window recurrence stays merely
    // invalid — the early return must keep suppressing the too-late issue.
    const atBandEnd = issuesOf({ recur_days: "9999", due_date: "2100-12-31" });
    expect(atBandEnd.messages).toContain(t("tasks.form.recurInvalid", { max: 3650 }));
    expect(atBandEnd.messages).not.toContain(t("tasks.form.recurTooLate"));
  });

  it("files the too-late issue with the custom zod code", () => {
    const { messages, codes } = issuesOf({ recur_days: "3650", due_date: "2100-12-31" });
    expect(messages).toContain(t("tasks.form.recurTooLate"));
    expect(codes).toContain("custom");
  });
});
