/**
 * Tasks board branch guards (companion to page.test.tsx, page.extended.test.tsx,
 * page.deeplink.test.tsx and page.pagination.test.tsx): the conditions that
 * decide what the board draws and when it navigates — URL canonicalisation
 * guards, the due-date urgency bands, the per-row conditional cells, the skip
 * dialog's in-flight lock, and the new-duty dialog's validation, assignment and
 * submit interlocks.
 */

import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { TaskOut } from "@/api/generated/models";
import { addDays, farmToday, formatDate } from "@/lib/format";
import { ALL_PERMISSIONS, permissionsHandler, server, TEST_USER } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import TasksPage from "./page";

/** A router that records without applying: the page must survive the window
 *  between asking for a URL and Next publishing the new search params. Tests
 *  that need the URL to move drive `nav.state.search` themselves. */
const nav = vi.hoisted(() => {
  const state = { search: "" };
  const push = vi.fn();
  const replace = vi.fn();
  return { state, push, replace, router: { push, replace, prefetch: vi.fn() } };
});

vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  usePathname: () => "/tasks",
  useSearchParams: () => new URLSearchParams(nav.state.search),
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

/** Farm-calendar fixture dates: due dates follow the active farm timezone. */
const TODAY = farmToday();
const THREE_DAYS_AGO = addDays(TODAY, -3);
const TOMORROW = addDays(TODAY, 1);
const IN_TWO_DAYS = addDays(TODAY, 2);
const IN_THREE_DAYS = addDays(TODAY, 3);

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

const TODAY_ROW = makeTask({ id: 1, title: "Morning feed count" });
const USER_ONLY_ROW = makeTask({ id: 2, title: "Feed the kids", assigned_user_name: "Sunita" });
const SENT_BACK_ROW = makeTask({
  id: 3,
  title: "Scrub water troughs",
  verification_note: "far pen still dirty",
});
const OVERDUE_ROW = makeTask({
  id: 4,
  title: "Trim hooves",
  due_date: THREE_DAYS_AGO,
  recur_days: 30,
  assigned_user_name: "Raju",
  assigned_role_name: "Vet",
  animal_id: 3,
  animal_tag: "G-003",
});
const TOMORROW_ROW = makeTask({ id: 5, title: "Rotate buck", due_date: TOMORROW });
const IN_TWO_DAYS_ROW = makeTask({ id: 6, title: "Weigh the kids", due_date: IN_TWO_DAYS });
const IN_THREE_DAYS_ROW = makeTask({
  id: 7,
  title: "Restock minerals",
  due_date: IN_THREE_DAYS,
  assigned_role_name: "Mover",
  // The linked goat is gone from the join, so the row has an id but no tag.
  animal_id: 9,
});
const AWAITING_ROW = makeTask({
  id: 8,
  title: "Deep-clean kidding pen",
  status: "DONE",
  category: "CLEANING",
  needs_verification: true,
  completed_by_id: TEST_USER.id + 100,
  completed_at: "2026-08-05T14:07:00",
});
const VERIFIED_ROW = makeTask({
  id: 9,
  title: "Weekly sweep",
  due_date: THREE_DAYS_AGO,
  status: "VERIFIED",
  needs_verification: true,
  completed_by_id: TEST_USER.id + 100,
  completed_at: "2026-08-01T09:30:00",
  verified_at: "2026-08-01T10:00:00",
});
const SKIPPED_WITH_REASON = makeTask({
  id: 10,
  title: "Evening ration check",
  status: "SKIPPED",
  skipped_at: "2026-08-05T13:00:00",
  skip_reason: "No animals in pen",
});
const SKIPPED_WITHOUT_REASON = makeTask({
  id: 11,
  title: "Midday shade check",
  status: "SKIPPED",
  skipped_at: "2026-08-05T13:30:00",
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
  overdue_total: number;
  upcoming_total: number;
  awaiting_total: number;
  active_limit: number;
  completed_total: number;
  completed_limit: number;
};

function fullPayload(): TabsPayload {
  return {
    today: [TODAY_ROW, USER_ONLY_ROW, SENT_BACK_ROW],
    overdue: [OVERDUE_ROW],
    upcoming: [TOMORROW_ROW, IN_TWO_DAYS_ROW, IN_THREE_DAYS_ROW],
    awaiting: [AWAITING_ROW],
    completed: [VERIFIED_ROW, SKIPPED_WITH_REASON, SKIPPED_WITHOUT_REASON],
    today_total: 3,
    overdue_total: 1,
    upcoming_total: 3,
    awaiting_total: 1,
    active_limit: 50,
    completed_total: 3,
    completed_limit: 50,
  };
}

const RED_BAND = "bg-red-100";
const AMBER_BAND = "bg-amber-100";

function rowOf(title: string): HTMLElement {
  const row = screen.getByText(title).closest("tr");
  expect(row).not.toBeNull();
  return row as HTMLElement;
}

/** Due, Task, Category, Assigned to, Animal, [Status, Finished], actions. */
function cellOf(row: HTMLElement, index: number): HTMLElement {
  return within(row).getAllByRole("cell")[index];
}

function dueBand(row: HTMLElement, iso: string): HTMLElement {
  return within(row).getByText(formatDate(iso));
}

/** Lets pending macrotasks (the deferred navigation bridge) run. */
async function settle(ms = 30) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, ms));
  });
}

async function pickOption(user: User, trigger: HTMLElement, name: string | RegExp) {
  await user.click(trigger);
  await user.click(await screen.findByRole("option", { name }));
}

describe("TasksPage branch guards", () => {
  let payload: TabsPayload;
  let seenParams: URLSearchParams[];
  let createBody: Record<string, unknown> | null;
  let skipCalls: number;
  let teamAttempts: number;
  let teamFails: boolean;

  beforeEach(() => {
    nav.state.search = "";
    nav.push.mockClear();
    nav.replace.mockClear();
    payload = fullPayload();
    seenParams = [];
    createBody = null;
    skipCalls = 0;
    teamAttempts = 0;
    teamFails = false;
    server.use(
      http.get("/api/tasks", ({ request }) => {
        const params = new URL(request.url).searchParams;
        seenParams.push(params);
        const echo = (tab: string) => Number(params.get(`${tab}_offset`) ?? 0);
        return HttpResponse.json({
          ...payload,
          today_offset: echo("today"),
          overdue_offset: echo("overdue"),
          upcoming_offset: echo("upcoming"),
          awaiting_offset: echo("awaiting"),
          completed_offset: echo("completed"),
        });
      }),
      http.get("/api/team", () => {
        teamAttempts += 1;
        return teamFails
          ? HttpResponse.json({ detail: "team directory unavailable" }, { status: 503 })
          : HttpResponse.json(TEAM_PAYLOAD);
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
      http.post("/api/tasks/:taskId/skip", async () => {
        skipCalls += 1;
        return HttpResponse.json(makeTask({ id: 1, status: "SKIPPED" }));
      }),
    );
  });

  async function renderLoaded() {
    const utils = renderWithProviders(<TasksPage />);
    await screen.findByRole("tab", { name: `Today (${payload.today_total})` });
    return utils;
  }

  async function openTab(user: User, name: string | RegExp) {
    await user.click(screen.getByRole("tab", { name }));
  }

  async function openDialog() {
    const user = userEvent.setup();
    const utils = await renderLoaded();
    await user.click(screen.getByRole("button", { name: "New duty" }));
    return { user, utils, dialog: await screen.findByRole("dialog") };
  }

  // ---------- URL canonicalisation guards ----------

  describe("URL canonicalisation", () => {
    it("leaves an already-canonical URL alone", async () => {
      nav.state.search = "tab=today&today_offset=50&from=dashboard";
      payload.today_total = 137;
      await renderLoaded();

      expect(seenParams[0].get("today_offset")).toBe("50");
      expect(
        screen.getByRole("navigation", { name: "today tasks pagination" }),
      ).toHaveTextContent("Showing 51–100 of 137 today tasks");
      await settle();
      expect(nav.replace).not.toHaveBeenCalled();
    });

    it("issues one canonicalisation for a malformed offset, even before the router applies it", async () => {
      nav.state.search = "tab=today&today_offset=abc&from=dashboard";
      await renderLoaded();

      await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/tasks?tab=today&from=dashboard"));
      // The mocked router never publishes the new params, so every later render
      // recomputes the same target: it must not be replaced over and over.
      await settle();
      expect(nav.replace).toHaveBeenCalledTimes(1);
    });

    it("canonicalises an unknown ?tab= back to today", async () => {
      nav.state.search = "tab=bogus&today_offset=abc";
      await renderLoaded();

      await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/tasks?tab=today"));
      expect(screen.getByRole("tab", { name: "Today (3)" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
    });

    it("does not navigate when the API reports a degenerate page size", async () => {
      nav.state.search = "tab=today";
      payload.active_limit = 0;
      await renderLoaded();

      expect(screen.getByText("Morning feed count")).toBeInTheDocument();
      await settle();
      expect(nav.replace).not.toHaveBeenCalled();
    });

    it("does not push a duplicate history entry at the offset ceiling", async () => {
      nav.state.search = "tab=today&today_offset=1000000";
      payload.today_total = 2_000_000;
      const user = userEvent.setup();
      await renderLoaded();

      const pager = screen.getByRole("navigation", { name: "today tasks pagination" });
      expect(pager).toHaveTextContent("Showing 1000001–1000050 of 2000000 today tasks");

      await user.click(within(pager).getByRole("button", { name: "Next" }));
      await settle();
      expect(nav.push).not.toHaveBeenCalled();
      expect(nav.replace).not.toHaveBeenCalled();
      expect(seenParams).toHaveLength(1);
    });

    it("does not resurrect a superseded tab bridge when the URL returns to it", async () => {
      nav.state.search = "tab=today";
      const user = userEvent.setup();
      const { rerender } = await renderLoaded();

      // The bridge shows the new tab while the router catches up…
      await openTab(user, "Overdue (1)");
      expect(await screen.findByText("Trim hooves")).toBeInTheDocument();

      // …but the URL lands somewhere else entirely, which wins.
      nav.state.search = "tab=upcoming";
      await act(async () => {
        rerender(<TasksPage />);
      });
      expect(await screen.findByText("Rotate buck")).toBeInTheDocument();

      // Coming back to the earlier URL must show that URL's tab, not the
      // abandoned bridge.
      nav.state.search = "tab=today";
      await act(async () => {
        rerender(<TasksPage />);
      });
      expect(await screen.findByText("Morning feed count")).toBeInTheDocument();
      expect(screen.queryByText("Trim hooves")).not.toBeInTheDocument();
    });

    it("canonicalises a new URL from that URL's offsets, not a superseded page's", async () => {
      nav.state.search = "tab=today&today_offset=50";
      payload.today_total = 137;
      const user = userEvent.setup();
      const { rerender } = await renderLoaded();

      const pager = screen.getByRole("navigation", { name: "today tasks pagination" });
      await user.click(within(pager).getByRole("button", { name: "Next" }));
      await waitFor(() => expect(seenParams.at(-1)?.get("today_offset")).toBe("100"));
      nav.replace.mockClear();

      nav.state.search = "tab=today&today_offset=abc";
      await act(async () => {
        rerender(<TasksPage />);
      });
      await waitFor(() => expect(nav.replace).toHaveBeenCalled());
      await settle();
      expect(nav.replace.mock.calls.map((call) => call[0])).toEqual(["/tasks?tab=today"]);
    });
  });

  // ---------- row rendering ----------

  describe("row rendering", () => {
    it("bands an overdue duty red and counts how late it is", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      await openTab(user, "Overdue (1)");

      const row = rowOf("Trim hooves");
      const band = dueBand(row, THREE_DAYS_AGO);
      expect(band).toHaveClass(RED_BAND);
      expect(band).not.toHaveClass(AMBER_BAND);
      expect(within(row).getByText(/\(3d late\)/)).toBeInTheDocument();
    });

    it("bands duties due within two days amber and leaves later ones plain", async () => {
      const user = userEvent.setup();
      await renderLoaded();

      const todayRow = rowOf("Morning feed count");
      expect(dueBand(todayRow, TODAY)).toHaveClass(AMBER_BAND);
      expect(dueBand(todayRow, TODAY)).not.toHaveClass(RED_BAND);
      expect(within(todayRow).queryByText(/late\)/)).not.toBeInTheDocument();

      await openTab(user, "Upcoming (3)");
      expect(dueBand(rowOf("Rotate buck"), TOMORROW)).toHaveClass(AMBER_BAND);
      expect(dueBand(rowOf("Weigh the kids"), IN_TWO_DAYS)).toHaveClass(AMBER_BAND);

      const plain = dueBand(rowOf("Restock minerals"), IN_THREE_DAYS);
      expect(plain).not.toHaveClass(AMBER_BAND);
      expect(plain).not.toHaveClass(RED_BAND);
    });

    it("leaves a finished duty unbanded even when its due date has passed", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      await openTab(user, "Completed (3)");

      const row = rowOf("Weekly sweep");
      const band = dueBand(row, THREE_DAYS_AGO);
      expect(band).not.toHaveClass(RED_BAND);
      expect(band).not.toHaveClass(AMBER_BAND);
      expect(within(row).queryByText(/late\)/)).not.toBeInTheDocument();
    });

    it("adds the status and finished columns only in completed history", async () => {
      const user = userEvent.setup();
      await renderLoaded();

      const headers = () =>
        screen.getAllByRole("columnheader").map((cell) => cell.textContent);
      expect(headers()).toEqual(["Due", "Task", "Category", "Assigned to", "Animal", ""]);

      await openTab(user, "Completed (3)");
      expect(headers()).toEqual([
        "Due",
        "Task",
        "Category",
        "Assigned to",
        "Animal",
        "Status",
        "Finished",
        "",
      ]);
    });

    it("badges the cadence of a recurring duty only", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      expect(within(rowOf("Morning feed count")).queryByText(/every/)).not.toBeInTheDocument();

      await openTab(user, "Overdue (1)");
      expect(within(rowOf("Trim hooves")).getByText("every 30d")).toBeInTheDocument();
    });

    it("shows the sent-back note only on the duty that carries one", async () => {
      await renderLoaded();

      expect(
        within(rowOf("Scrub water troughs")).getByText("Sent back: far pen still dirty"),
      ).toBeInTheDocument();
      expect(within(rowOf("Morning feed count")).queryByText(/Sent back/)).not.toBeInTheDocument();
    });

    it("shows a skip reason only when the skipped duty recorded one", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      expect(within(rowOf("Morning feed count")).queryByText(/Reason:/)).not.toBeInTheDocument();

      await openTab(user, "Completed (3)");
      expect(
        within(rowOf("Evening ration check")).getByText("Reason: No animals in pen"),
      ).toBeInTheDocument();
      expect(within(rowOf("Midday shade check")).queryByText(/Reason:/)).not.toBeInTheDocument();
    });

    it("names the assignee and keeps its continuity role as a sub-label", async () => {
      const user = userEvent.setup();
      await renderLoaded();

      expect(cellOf(rowOf("Morning feed count"), 3)).toHaveTextContent("—");
      const personal = cellOf(rowOf("Feed the kids"), 3);
      expect(personal).toHaveTextContent("Sunita");
      expect(personal).not.toHaveTextContent("via");

      await openTab(user, "Overdue (1)");
      const both = cellOf(rowOf("Trim hooves"), 3);
      expect(both).toHaveTextContent("Raju");
      expect(both).toHaveTextContent("via Vet");

      await openTab(user, "Upcoming (3)");
      const roleOnly = cellOf(rowOf("Restock minerals"), 3);
      expect(roleOnly).toHaveTextContent("Mover");
      expect(roleOnly).not.toHaveTextContent("via");
    });

    it("links an animal only when the row carries the tag to name it", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      await openTab(user, "Overdue (1)");
      expect(within(cellOf(rowOf("Trim hooves"), 4)).getByRole("link", { name: "G-003" })).toHaveAttribute(
        "href",
        "/animals/3?returnTo=%2Ftasks%3Ftab%3Doverdue",
      );

      await openTab(user, "Upcoming (3)");
      const untagged = cellOf(rowOf("Restock minerals"), 4);
      expect(untagged).toHaveTextContent("—");
      expect(within(untagged).queryByRole("link")).not.toBeInTheDocument();
    });

    it("keeps an untagged animal an em dash without animals.view too", async () => {
      server.use(permissionsHandler(["tasks.view", "tasks.complete"]));
      const user = userEvent.setup();
      await renderLoaded();
      await openTab(user, "Upcoming (3)");

      const untagged = cellOf(rowOf("Restock minerals"), 4);
      expect(untagged).toHaveTextContent("—");
      expect(within(untagged).queryByRole("link")).not.toBeInTheDocument();
    });

    it("offers verification controls only in the awaiting queue", async () => {
      const user = userEvent.setup();
      await renderLoaded();
      await openTab(user, "Completed (3)");

      const finished = rowOf("Weekly sweep");
      expect(within(finished).queryByRole("button", { name: "Verify" })).not.toBeInTheDocument();
      expect(within(finished).queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();

      await openTab(user, "Awaiting verification (1)");
      const queued = rowOf("Deep-clean kidding pen");
      expect(within(queued).getByRole("button", { name: "Verify" })).toBeInTheDocument();
      expect(within(queued).getByRole("button", { name: "Reject" })).toBeInTheDocument();
    });
  });

  // ---------- skip dialog ----------

  describe("skip dialog", () => {
    async function openSkip() {
      const user = userEvent.setup();
      await renderLoaded();
      await user.click(within(rowOf("Morning feed count")).getByRole("button", { name: "Skip" }));
      return { user, dialog: await screen.findByRole("dialog", { name: "Skip this task?" }) };
    }

    it("dismisses the skip dialog from Cancel without skipping", async () => {
      const { user, dialog } = await openSkip();
      await user.type(within(dialog).getByLabelText("Reason (optional)"), "changed my mind");
      await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(skipCalls).toBe(0);
    });

    it("holds the skip dialog open while the skip is in flight, then closes it", async () => {
      let releaseSkip!: () => void;
      let markSkipStarted!: () => void;
      const skipGate = new Promise<void>((resolve) => {
        releaseSkip = resolve;
      });
      const skipStarted = new Promise<void>((resolve) => {
        markSkipStarted = resolve;
      });
      server.use(
        http.post("/api/tasks/:taskId/skip", async () => {
          skipCalls += 1;
          markSkipStarted();
          await skipGate;
          return HttpResponse.json(makeTask({ id: 1, status: "SKIPPED" }));
        }),
      );
      const { user, dialog } = await openSkip();
      await user.click(within(dialog).getByRole("button", { name: "Skip task" }));
      await skipStarted;

      await user.keyboard("{Escape}");
      expect(screen.getByRole("dialog", { name: "Skip this task?" })).toBeInTheDocument();
      expect(within(dialog).getByRole("button", { name: "Skipping…" })).toBeDisabled();

      releaseSkip();
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(skipCalls).toBe(1);
    });
  });

  // ---------- new duty dialog ----------

  describe("new duty dialog", () => {
    it("marks only the field that failed validation as invalid", async () => {
      const { user, dialog } = await openDialog();
      const title = within(dialog).getByLabelText("Title *");
      const dueDate = within(dialog).getByLabelText("Due date *");
      const recurrence = within(dialog).getByLabelText("Repeats every (days)");
      expect(title).not.toHaveAttribute("aria-invalid");
      expect(dueDate).not.toHaveAttribute("aria-invalid");
      expect(recurrence).not.toHaveAttribute("aria-invalid");

      await user.click(within(dialog).getByRole("button", { name: "Create duty" }));

      expect(await within(dialog).findByText("Title is required")).toBeInTheDocument();
      expect(title).toHaveAttribute("aria-invalid", "true");
      expect(dueDate).not.toHaveAttribute("aria-invalid");
      expect(recurrence).not.toHaveAttribute("aria-invalid");
    });

    it("marks the due date and recurrence invalid when they are the failures", async () => {
      const { user, dialog } = await openDialog();
      const title = within(dialog).getByLabelText("Title *");
      const dueDate = within(dialog).getByLabelText("Due date *");
      const recurrence = within(dialog).getByLabelText("Repeats every (days)");
      await user.type(title, "Check fences");
      fireEvent.change(dueDate, { target: { value: "" } });
      fireEvent.change(recurrence, { target: { value: "abc" } });

      await user.click(within(dialog).getByRole("button", { name: "Create duty" }));

      expect(await within(dialog).findByText("Due date is required")).toBeInTheDocument();
      expect(dueDate).toHaveAttribute("aria-invalid", "true");
      expect(recurrence).toHaveAttribute("aria-invalid", "true");
      expect(title).not.toHaveAttribute("aria-invalid");
      expect(createBody).toBeNull();
    });

    it("locks the form and the submit control from the first moment of a submit", async () => {
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
      await user.type(within(dialog).getByLabelText("Title *"), "Check fences");
      const form = within(dialog).getByRole("button", { name: "Create duty" }).closest("form")!;

      // react-hook-form flags the submission before the write leaves the
      // browser: the fieldset, the submit control itself and its label must
      // already say so, and a dismissal arriving in that window is ignored.
      fireEvent.submit(form);
      expect(within(dialog).getByLabelText("Title *")).toBeDisabled();
      const submit = within(dialog).getByRole("button", { name: "Creating…" });
      expect(submit).toHaveAttribute("disabled");
      fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));

      await createStarted;
      expect(screen.getByRole("dialog")).toBeInTheDocument();

      releaseCreate();
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(createBody).toMatchObject({ title: "Check fences" });
    });

    it("waits for the assignment directory before offering its selects", async () => {
      let releaseTeam!: () => void;
      const teamGate = new Promise<void>((resolve) => {
        releaseTeam = resolve;
      });
      server.use(
        http.get("/api/team", async () => {
          teamAttempts += 1;
          await teamGate;
          return HttpResponse.json(TEAM_PAYLOAD);
        }),
      );
      const { dialog } = await openDialog();

      expect(await within(dialog).findByRole("status")).toHaveTextContent(
        "Loading assignment options…",
      );
      expect(within(dialog).queryByLabelText("Assign to role")).not.toBeInTheDocument();

      releaseTeam();
      expect(await within(dialog).findByLabelText("Assign to role")).toBeInTheDocument();
      expect(within(dialog).queryByRole("status")).not.toBeInTheDocument();
    });

    it("blocks a submit whose chosen role can no longer be validated", async () => {
      const { user, utils, dialog } = await openDialog();
      const combos = () => within(dialog).getAllByRole("combobox");
      await user.type(within(dialog).getByLabelText("Title *"), "Herd check");
      await pickOption(user, combos()[2], "Vet");

      teamFails = true;
      await act(async () => {
        await utils.queryClient.invalidateQueries({ queryKey: ["/api/team"] });
      });

      await waitFor(() =>
        expect(within(dialog).queryByLabelText("Assign to role")).not.toBeInTheDocument(),
      );
      expect(within(dialog).getByRole("button", { name: "Create duty" })).toBeDisabled();
      expect(teamAttempts).toBe(2);
    });

    it("blocks a submit whose chosen worker can no longer be validated", async () => {
      const { user, utils, dialog } = await openDialog();
      const combos = () => within(dialog).getAllByRole("combobox");
      await user.type(within(dialog).getByLabelText("Title *"), "Pen check");
      await pickOption(user, combos()[3], /Raju \(Vet\)/);

      teamFails = true;
      await act(async () => {
        await utils.queryClient.invalidateQueries({ queryKey: ["/api/team"] });
      });

      await waitFor(() =>
        expect(within(dialog).queryByLabelText("or assign to worker")).not.toBeInTheDocument(),
      );
      expect(within(dialog).getByRole("button", { name: "Create duty" })).toBeDisabled();
    });

    it("swaps a previously chosen role for the worker that replaces it", async () => {
      const { user, dialog } = await openDialog();
      const combos = () => within(dialog).getAllByRole("combobox");
      await user.type(within(dialog).getByLabelText("Title *"), "Herd check");
      await pickOption(user, combos()[2], "Vet");
      await pickOption(user, combos()[3], /Raju \(Vet\)/);

      expect(combos()[2]).toHaveTextContent("— none —");
      await user.click(within(dialog).getByRole("button", { name: "Create duty" }));

      await waitFor(() => expect(createBody).not.toBeNull());
      expect(createBody).toMatchObject({ assigned_role_id: null, assigned_user_id: 9 });
    });

    it("leaves the other assignment alone when one select is cleared", async () => {
      const { user, dialog } = await openDialog();
      const combos = () => within(dialog).getAllByRole("combobox");
      await user.type(within(dialog).getByLabelText("Title *"), "Shed check");

      await pickOption(user, combos()[3], /Raju \(Vet\)/);
      await pickOption(user, combos()[2], "— none —");
      expect(combos()[3]).toHaveTextContent("Raju (Vet)");

      await pickOption(user, combos()[2], "Vet");
      expect(combos()[3]).toHaveTextContent("— none —");
      await pickOption(user, combos()[3], "— none —");
      expect(combos()[2]).toHaveTextContent("Vet");

      await user.click(within(dialog).getByRole("button", { name: "Create duty" }));
      await waitFor(() => expect(createBody).not.toBeNull());
      expect(createBody).toMatchObject({ assigned_role_id: 2, assigned_user_id: null });
    });

    it("shows the chosen animal in the picker trigger", async () => {
      const { user, dialog } = await openDialog();
      const trigger = within(dialog).getByRole("combobox", { name: "Animal (optional)" });
      expect(trigger).toHaveTextContent("— none —");

      await pickOption(user, trigger, "G-007 — Radha");

      expect(trigger).toHaveTextContent("G-007 — Radha");
      await user.type(within(dialog).getByLabelText("Title *"), "Check Radha");
      await user.click(within(dialog).getByRole("button", { name: "Create duty" }));
      await waitFor(() => expect(createBody).not.toBeNull());
      expect(createBody).toMatchObject({ animal_id: 7 });
    });

    it("rejects the maximum recurrence when its successor would overflow the calendar", async () => {
      const { user, dialog } = await openDialog();
      await user.type(within(dialog).getByLabelText("Title *"), "Decade check");
      fireEvent.change(within(dialog).getByLabelText("Due date *"), {
        target: { value: "9999-12-31" },
      });
      fireEvent.change(within(dialog).getByLabelText("Repeats every (days)"), {
        target: { value: "3650" },
      });

      await user.click(within(dialog).getByRole("button", { name: "Create duty" }));

      expect(
        await within(dialog).findByText(
          "Recurring due date is too late to schedule its next occurrence",
        ),
      ).toBeInTheDocument();
      expect(createBody).toBeNull();
    });

    it.each(["1e3", "4000"])(
      "reports only the recurrence itself as wrong for %s",
      async (recurrence) => {
        const { user, dialog } = await openDialog();
        await user.type(within(dialog).getByLabelText("Title *"), "Far-future inspection");
        fireEvent.change(within(dialog).getByLabelText("Due date *"), {
          target: { value: "9999-12-31" },
        });
        fireEvent.change(within(dialog).getByLabelText("Repeats every (days)"), {
          target: { value: recurrence },
        });

        await user.click(within(dialog).getByRole("button", { name: "Create duty" }));

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

    it("waits for the permission set instead of flashing a denial", async () => {
      let releasePermissions!: () => void;
      let permissionsRequested = 0;
      const permissionsGate = new Promise<void>((resolve) => {
        releasePermissions = resolve;
      });
      server.use(
        http.get("/api/auth/permissions", async () => {
          permissionsRequested += 1;
          await permissionsGate;
          return HttpResponse.json({ is_owner: true, permissions: ALL_PERMISSIONS });
        }),
      );
      renderWithProviders(<TasksPage />);

      await waitFor(() => expect(permissionsRequested).toBe(1));
      expect(screen.getByText("Loading…")).toBeInTheDocument();
      expect(screen.queryByText("You don't have access to this page.")).not.toBeInTheDocument();

      releasePermissions();
      expect(await screen.findByRole("tab", { name: "Today (3)" })).toBeInTheDocument();
    });
  });
});
