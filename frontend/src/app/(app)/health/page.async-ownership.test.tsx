/**
 * Health page ownership guards: the deep-link hydration latch, the duty
 * resolution effect's wait/apply/consume decisions, and every check an async
 * bulk preview or event write must pass before it is allowed to touch the
 * dialog the operator is looking at — still permitted, same farm, same dialog
 * session, same target selection.
 */

import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, delay, http } from "msw";
import { toast } from "sonner";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  HealthBulkTargetPreviewOut,
  HealthEventOut,
  TaskOut,
} from "@/api/generated/models";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { setCurrentFarmId } from "@/lib/api-client";
import { farmToday } from "@/lib/format";

import HealthPage from "./page";

const pushMock = vi.fn();
const replaceMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => "/health",
  useSearchParams: () => new URLSearchParams(window.location.search),
  useParams: () => ({}),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

type User = ReturnType<typeof userEvent.setup>;

beforeAll(() => {
  // jsdom lacks APIs the select/popper layer touches when opening a listbox.
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

function makeEvent(overrides: Partial<HealthEventOut>): HealthEventOut {
  return {
    id: 1,
    animal_id: 3,
    purchase_batch_id: null,
    date: TODAY,
    type: "VACCINE",
    product_name: "PPR vaccine",
    disease_target: "PPR",
    dose: null,
    route: null,
    vet_name: null,
    cost: null,
    next_due_date: null,
    schedule_template_name: null,
    next_due_authority: null,
    product_lot: null,
    product_manufactured_on: null,
    product_expires_on: null,
    vaccine_valid_until: null,
    certificate_number: null,
    official_tag_number: null,
    administered_by: null,
    withdrawal_until: null,
    suspected_scheduled_disease: false,
    authority_notified_at: null,
    isolation_started_at: null,
    notes: null,
    animal_tag: "G-003",
    ...overrides,
  };
}

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

/** Echoes the requested target back the way the API does, so a test only
 *  states the part of the snapshot it actually cares about. */
function previewFor(
  target: Record<string, unknown>,
  overrides: Partial<HealthBulkTargetPreviewOut> = {},
): HealthBulkTargetPreviewOut {
  return {
    scope: target.scope as HealthBulkTargetPreviewOut["scope"],
    bucket: (target.bucket ?? null) as HealthBulkTargetPreviewOut["bucket"],
    purchase_batch_id: (target.purchase_batch_id ?? null) as number | null,
    task_id: (target.task_id ?? null) as number | null,
    target_animal_ids: [3, 4],
    target_animals: [
      { id: 3, tag_number: "G-003", name: "Kaveri" },
      { id: 4, tag_number: "G-004", name: null },
    ],
    target_count: 2,
    max_targets: 250,
    ...overrides,
  };
}

function tasksPayload(tasks: TaskOut[]) {
  return {
    today: tasks,
    overdue: [],
    upcoming: [],
    awaiting: [],
    completed: [],
    completed_total: 0,
    completed_limit: 50,
    completed_offset: 0,
  };
}

const openGates: Array<() => void> = [];

/** A handler held open by the test: `reached` resolves when the request
 *  arrives, `release()` lets the response go out. Every gate is released in
 *  afterEach so a deliberately stranded request cannot outlive its test. */
function requestGate() {
  let release!: () => void;
  let markReached!: () => void;
  const released = new Promise<void>((resolve) => {
    release = resolve;
  });
  const reached = new Promise<void>((resolve) => {
    markReached = resolve;
  });
  openGates.push(release);
  return { markReached, reached, release, released };
}

/** Lets a released response settle through react-query into the effect that
 *  reads it, the way the real network callback would. */
async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function pickOption(user: User, trigger: HTMLElement, name: string | RegExp) {
  await user.click(trigger);
  await user.click(await screen.findByRole("option", { name }));
}

describe("HealthPage async ownership guards", () => {
  let events: HealthEventOut[];
  let tasks: TaskOut[];
  let postBody: Record<string, unknown> | null;
  let previewBodies: Record<string, unknown>[];
  let recordedEvents: HealthEventOut[];

  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
    vi.mocked(toast.success).mockClear();
    vi.mocked(toast.error).mockClear();
    events = [];
    tasks = [];
    postBody = null;
    previewBodies = [];
    recordedEvents = [makeEvent({ id: 99 })];
    server.use(
      http.get("/api/health/events", () =>
        HttpResponse.json({ events, total: events.length, limit: 50, offset: 0 }),
      ),
      http.post("/api/health/events", async ({ request }) => {
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(recordedEvents, { status: 201 });
      }),
      http.post("/api/health/events/preview", async ({ request }) => {
        const target = (await request.json()) as Record<string, unknown>;
        previewBodies.push(target);
        return HttpResponse.json(previewFor(target));
      }),
      http.get("/api/health/animals", () =>
        HttpResponse.json({
          animals: [
            { id: 3, tag_number: "G-003", name: "Kaveri", current_bucket: "LACTATING" },
            { id: 4, tag_number: "G-004", name: null, current_bucket: "BREEDING" },
          ],
          total: 2,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get("/api/health/purchase-batches", () =>
        HttpResponse.json({
          batches: [
            { id: 2, active_quarantine_animal_count: 12 },
            { id: 3, active_quarantine_animal_count: 5 },
          ],
          total: 2,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get("/api/tasks", () => HttpResponse.json(tasksPayload(tasks))),
      http.get("/api/tasks/:taskId", ({ params }) => {
        const task = tasks.find((candidate) => String(candidate.id) === params.taskId);
        return task
          ? HttpResponse.json(task)
          : HttpResponse.json({ detail: "Task not found" }, { status: 404 });
      }),
    );
  });

  afterEach(() => {
    openGates.splice(0).forEach((release) => release());
    setCurrentFarmId(null);
    window.history.replaceState({}, "", "/health");
  });

  async function openDialog() {
    const user = userEvent.setup();
    renderWithProviders(<HealthPage />);
    await screen.findByText("Event log");
    await user.click(await screen.findByRole("button", { name: "+ Add event" }));
    return { user, dialog: await screen.findByRole("dialog", { name: "Add health event" }) };
  }

  async function openBucketDialog(bucket = "BREEDING") {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    await pickOption(user, within(dialog).getByRole("combobox", { name: "Bucket *" }), bucket);
    return { user, dialog };
  }

  function reviewButton(dialog: HTMLElement) {
    return within(dialog).getByRole("button", { name: "Review target animals" });
  }

  // ---------- permission gate ----------

  it("waits for the permission answer instead of announcing no access", async () => {
    // An unanswered permission query is not evidence of a missing grant: the
    // page must stay on its loading state rather than telling an operator
    // with full rights that this module is not theirs.
    server.use(
      http.get("/api/auth/permissions", async () => {
        await delay("infinite");
        return HttpResponse.json({ is_owner: true, permissions: [] });
      }),
    );
    renderWithProviders(<HealthPage />);

    expect(await screen.findByText("Loading…")).toBeInTheDocument();
    expect(
      screen.queryByText("You don't have access to this page."),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Event log")).not.toBeInTheDocument();
  });

  // ---------- deep-link hydration ----------

  it("leaves an open draft alone when a navigation repeats the same deep link", async () => {
    // Next reuses this page for query-only navigation. A URL that adds a
    // returnTo but points at the same animal is the link already hydrated,
    // so re-running the dialog setup would wipe what the operator has typed.
    window.history.replaceState({}, "", "/health?animal_id=3");
    const user = userEvent.setup();
    const view = renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog", { name: "Add health event" });
    await waitFor(() =>
      expect(within(dialog).getByRole("combobox", { name: "Animal *" })).toHaveTextContent(
        "G-003 · Kaveri — LACTATING",
      ),
    );
    await user.type(within(dialog).getByLabelText("Product name"), "Ivermectin");

    window.history.replaceState({}, "", "/health?animal_id=3&returnTo=%2Ftasks");
    view.rerender(<HealthPage />);

    expect(within(dialog).getByLabelText("Product name")).toHaveValue("Ivermectin");
    expect(within(dialog).getByRole("combobox", { name: "Animal *" })).toHaveTextContent(
      "G-003 · Kaveri — LACTATING",
    );
  });

  it("still requires an animal when the deep-linked duty targets no single animal", async () => {
    // A herd-wide duty carries neither an animal nor a batch, so the link
    // supplies no target at all and the animal scope stays empty — the form
    // must ask for one in its own words.
    tasks = [makeTask({ id: 12, title: "Herd PPR round", category: "VACCINE" })];
    window.history.replaceState({}, "", "/health?task_id=12");
    const user = userEvent.setup();
    renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog", { name: "Add health event" });
    await waitFor(() =>
      expect(within(dialog).getByLabelText("Linked duty (completes it)")).toHaveTextContent(
        "Herd PPR round",
      ),
    );

    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    // The picker's placeholder reads the same, so pin the field's own error.
    await waitFor(() =>
      expect(document.getElementById("event-animal-error")).toHaveTextContent(
        "Pick an animal",
      ),
    );
    expect(postBody).toBeNull();
  });

  // ---------- deep-linked duty resolution ----------

  it("applies a deep-linked duty listed in the duty tabs without awaiting the exact lookup", async () => {
    // The tabs already prove this duty is pending, eligible and due — the
    // by-id lookup is only the fallback for duties outside that window, so a
    // slow one must not hold the prefill back.
    const lookup = requestGate();
    tasks = [
      makeTask({ id: 5, title: "PPR vaccination", category: "VACCINE", animal_id: 3 }),
    ];
    server.use(
      http.get("/api/tasks/5", async () => {
        lookup.markReached();
        await lookup.released;
        return HttpResponse.json(tasks[0]);
      }),
    );
    window.history.replaceState({}, "", "/health?task_id=5");
    renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog", { name: "Add health event" });
    await lookup.reached;

    await waitFor(() =>
      expect(within(dialog).getByRole("combobox", { name: "Animal *" })).toHaveTextContent(
        "G-003 · Kaveri — LACTATING",
      ),
    );
    expect(within(dialog).getByLabelText("Disease target")).toHaveValue("PPR vaccination");
    expect(within(dialog).queryByText(/Could not link duty/)).not.toBeInTheDocument();
  });

  it("applies a duty the exact lookup resolves while the duty tabs are still loading", async () => {
    // The by-id answer is authoritative on its own: a duty outside the
    // bounded tabs must prefill as soon as it lands, not when the tabs do.
    const tabs = requestGate();
    server.use(
      http.get("/api/tasks", async () => {
        await tabs.released;
        return HttpResponse.json(tasksPayload(tasks));
      }),
      http.get("/api/tasks/734", () =>
        HttpResponse.json(
          makeTask({ id: 734, title: "Ring vaccination", category: "VACCINE", animal_id: 3 }),
        ),
      ),
    );
    window.history.replaceState({}, "", "/health?task_id=734");
    renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog", { name: "Add health event" });

    await waitFor(() =>
      expect(within(dialog).getByRole("combobox", { name: "Animal *" })).toHaveTextContent(
        "G-003 · Kaveri — LACTATING",
      ),
    );
    expect(within(dialog).getByLabelText("Linked duty (completes it)")).toHaveTextContent(
      "Ring vaccination",
    );
    expect(within(dialog).getByLabelText("Disease target")).toHaveValue("Ring vaccination");
    // The tabs are still in flight — the prefill did not wait for them.
    expect(within(dialog).getByRole("status")).toHaveTextContent("Loading linked duties…");
  });

  it("waits for the duty tabs before calling a deep-linked duty unresolvable", async () => {
    // A 404 from the by-id lookup only rules out that endpoint's view of the
    // duty. Declaring the link dead while the tabs — which do list it — are
    // still loading would drop a duty the event could still complete.
    const tabs = requestGate();
    const lookup = requestGate();
    tasks = [
      makeTask({ id: 734, title: "PPR booster round", category: "VACCINE", animal_id: 3 }),
    ];
    server.use(
      http.get("/api/tasks", async () => {
        await tabs.released;
        return HttpResponse.json(tasksPayload(tasks));
      }),
      http.get("/api/tasks/734", () => {
        lookup.markReached();
        return HttpResponse.json({ detail: "Task not found" }, { status: 404 });
      }),
    );
    window.history.replaceState({}, "", "/health?task_id=734");
    renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog", { name: "Add health event" });
    await lookup.reached;
    await settle();
    expect(within(dialog).getByRole("status")).toHaveTextContent("Loading linked duties…");

    tabs.release();

    await waitFor(() =>
      expect(within(dialog).getByRole("combobox", { name: "Animal *" })).toHaveTextContent(
        "G-003 · Kaveri — LACTATING",
      ),
    );
    expect(within(dialog).getByLabelText("Linked duty (completes it)")).toHaveTextContent(
      "PPR booster round",
    );
    expect(within(dialog).queryByText(/Could not link duty/)).not.toBeInTheDocument();
  });

  it("does not re-link a duty the operator dropped while its lookup was in flight", async () => {
    // Retargeting the event at another animal unlinks the duty. The lookup
    // that was already on the wire must be consumed without being applied,
    // and without the "could not link" warning the operator never earned.
    const lookup = requestGate();
    const exactTask = makeTask({
      id: 734,
      title: "Late PPR duty",
      category: "VACCINE",
      animal_id: 3,
    });
    server.use(
      http.get("/api/tasks/734", async () => {
        lookup.markReached();
        await lookup.released;
        return HttpResponse.json(exactTask);
      }),
    );
    window.history.replaceState({}, "", "/health?task_id=734&animal_id=3");
    const user = userEvent.setup();
    renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog", { name: "Add health event" });
    await lookup.reached;
    await pickOption(user, within(dialog).getByRole("combobox", { name: "Animal *" }), /G-004/);

    lookup.release();
    // The resolved duty is the only linkable one, so the select appearing at
    // all is the moment the lookup reached the effect: the assertions below
    // are not racing it.
    const dutyTrigger = await within(dialog).findByLabelText("Linked duty (completes it)");
    await settle();

    expect(dutyTrigger).toHaveTextContent("— none —");
    expect(within(dialog).getByRole("combobox", { name: "Animal *" })).toHaveTextContent(
      "G-004",
    );
    expect(within(dialog).queryByText(/Could not link duty/)).not.toBeInTheDocument();
  });

  // ---------- bulk preview: what may become the reviewed snapshot ----------

  it("ignores a bulk review the API answered with no snapshot", async () => {
    server.use(
      http.post("/api/health/events/preview", () => new HttpResponse(null, { status: 204 })),
    );
    const { user, dialog } = await openBucketDialog();

    await user.click(reviewButton(dialog));

    await waitFor(() => expect(reviewButton(dialog)).toBeEnabled());
    expect(within(dialog).queryByRole("status")).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("drops a bulk review that lands after the operator switched farms", async () => {
    const preview = requestGate();
    server.use(
      http.post("/api/health/events/preview", async ({ request }) => {
        const target = (await request.json()) as Record<string, unknown>;
        preview.markReached();
        await preview.released;
        return HttpResponse.json(previewFor(target));
      }),
    );
    const { user, dialog } = await openBucketDialog();
    await user.click(reviewButton(dialog));
    await preview.reached;

    // AuthProvider switches the farm synchronously, before React commits the
    // unmount of the farm-keyed subtree.
    act(() => setCurrentFarmId("2"));
    preview.release();
    await settle();

    await waitFor(() => expect(reviewButton(dialog)).toBeEnabled());
    expect(within(dialog).queryByRole("status")).not.toBeInTheDocument();
  });

  it("drops a bulk review whose snapshot cites a duty the event does not link", async () => {
    // Confirming records exactly the reviewed IDs, so a snapshot that was
    // computed for another duty is not the review of this event's target.
    server.use(
      http.post("/api/health/events/preview", async ({ request }) => {
        const target = (await request.json()) as Record<string, unknown>;
        previewBodies.push(target);
        return HttpResponse.json(previewFor(target, { task_id: 999 }));
      }),
    );
    const { user, dialog } = await openBucketDialog();

    await user.click(reviewButton(dialog));

    await waitFor(() => expect(reviewButton(dialog)).toBeEnabled());
    expect(previewBodies).toEqual([{ scope: "bucket", bucket: "BREEDING" }]);
    expect(within(dialog).queryByRole("status")).not.toBeInTheDocument();
  });

  it("drops a batch review that lands after the operator picked another batch", async () => {
    const preview = requestGate();
    server.use(
      http.post("/api/health/events/preview", async ({ request }) => {
        const target = (await request.json()) as Record<string, unknown>;
        previewBodies.push(target);
        preview.markReached();
        await preview.released;
        return HttpResponse.json(previewFor(target));
      }),
    );
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Purchase batch" }));
    const batchPicker = within(dialog).getByRole("combobox", { name: "Purchase batch *" });
    await pickOption(user, batchPicker, "Batch #2 — 12 active in quarantine");
    await user.click(reviewButton(dialog));
    await preview.reached;

    await pickOption(user, batchPicker, "Batch #3 — 5 active in quarantine");
    preview.release();
    await settle();

    await waitFor(() => expect(reviewButton(dialog)).toBeEnabled());
    expect(previewBodies).toEqual([{ scope: "batch", purchase_batch_id: 2 }]);
    expect(within(dialog).queryByRole("status")).not.toBeInTheDocument();
  });

  it("re-reviews when the returned snapshot describes another purchase batch", async () => {
    // The confirm write is immutable and hits every animal in the snapshot,
    // so a snapshot that does not describe the batch still selected has to
    // send the operator back through a fresh review instead of recording.
    server.use(
      http.post("/api/health/events/preview", async ({ request }) => {
        const target = (await request.json()) as Record<string, unknown>;
        previewBodies.push(target);
        return HttpResponse.json(previewFor(target, { purchase_batch_id: 3 }));
      }),
    );
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Purchase batch" }));
    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Purchase batch *" }),
      "Batch #2 — 12 active in quarantine",
    );

    await user.click(reviewButton(dialog));
    await user.click(
      await within(dialog).findByRole("button", { name: "Confirm for 2 animals" }),
    );

    await waitFor(() => expect(previewBodies).toHaveLength(2));
    expect(previewBodies[1]).toEqual({ scope: "batch", purchase_batch_id: 2 });
    expect(postBody).toBeNull();
  });

  // ---------- bulk preview failures that are no longer the operator's ----------

  it("stays silent about a failed review once the farm has changed", async () => {
    const preview = requestGate();
    server.use(
      http.post("/api/health/events/preview", async () => {
        preview.markReached();
        await preview.released;
        return HttpResponse.json({ detail: "preview service unavailable" }, { status: 503 });
      }),
    );
    const { user, dialog } = await openBucketDialog();
    await user.click(reviewButton(dialog));
    await preview.reached;

    act(() => setCurrentFarmId("2"));
    preview.release();
    await settle();

    await waitFor(() => expect(reviewButton(dialog)).toBeEnabled());
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("stays silent about a failed review from a dismissed dialog session", async () => {
    const preview = requestGate();
    server.use(
      http.post("/api/health/events/preview", async () => {
        preview.markReached();
        await preview.released;
        return HttpResponse.json({ detail: "preview service unavailable" }, { status: 503 });
      }),
    );
    const { user, dialog } = await openBucketDialog();
    await user.click(reviewButton(dialog));
    await preview.reached;

    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    preview.release();
    await settle();

    await user.click(screen.getByRole("button", { name: "+ Add event" }));
    const reopened = await screen.findByRole("dialog", { name: "Add health event" });
    expect(within(reopened).queryByRole("alert")).not.toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();
  });

  // ---------- the write's own session ----------

  it("navigates nowhere when a hand-opened dialog saves an event", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Animal *" }),
      /G-003 · Kaveri/,
    );

    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(pushMock).not.toHaveBeenCalled();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("confirms a write that lands after dismissal without touching the next draft", async () => {
    // The dialog stays dismissible while its write is in flight. The write
    // still happened, so it is confirmed and the farm views refresh — but
    // the session it belonged to is over: closing and resetting the dialog
    // the operator has since opened would pull it out from under them.
    const write = requestGate();
    server.use(
      http.post("/api/health/events", async ({ request }) => {
        postBody = (await request.json()) as Record<string, unknown>;
        write.markReached();
        await write.released;
        return HttpResponse.json(recordedEvents, { status: 201 });
      }),
    );
    const { user, dialog } = await openDialog();
    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Animal *" }),
      /G-003 · Kaveri/,
    );
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));
    await write.reached;

    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "+ Add event" }));
    const reopened = await screen.findByRole("dialog", { name: "Add health event" });

    write.release();
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("Health event recorded."),
    );
    await settle();

    expect(screen.getByRole("dialog", { name: "Add health event" })).toBe(reopened);
    expect(within(reopened).getByRole("button", { name: "Save event" })).toBeEnabled();
    expect(pushMock).not.toHaveBeenCalled();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("stays silent about a failed write once the farm has changed", async () => {
    const write = requestGate();
    server.use(
      http.post("/api/health/events", async () => {
        write.markReached();
        await write.released;
        return HttpResponse.json({ detail: "health writer unavailable" }, { status: 503 });
      }),
    );
    const { user, dialog } = await openDialog();
    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Animal *" }),
      /G-003 · Kaveri/,
    );
    const save = within(dialog).getByRole("button", { name: "Save event" });
    await user.click(save);
    await write.reached;

    act(() => setCurrentFarmId("2"));
    write.release();
    await settle();

    await waitFor(() => expect(save).toBeEnabled());
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("stays silent about a failed write from a dismissed dialog session", async () => {
    const write = requestGate();
    server.use(
      http.post("/api/health/events", async () => {
        write.markReached();
        await write.released;
        return HttpResponse.json({ detail: "health writer unavailable" }, { status: 503 });
      }),
    );
    const { user, dialog } = await openDialog();
    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Animal *" }),
      /G-003 · Kaveri/,
    );
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));
    await write.reached;

    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    write.release();
    await settle();

    await user.click(screen.getByRole("button", { name: "+ Add event" }));
    const reopened = await screen.findByRole("dialog", { name: "Add health event" });
    expect(within(reopened).queryByRole("alert")).not.toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();
  });
});
