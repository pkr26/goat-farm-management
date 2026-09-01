/**
 * Health page behaviour: which control is responsible for dropping which
 * piece of stale dialog state (a reviewed target snapshot, a failed save, an
 * unresolved duty link), the whitespace rules that decide whether a value
 * counts as filled in at all, and the lifecycle boundaries a deep link, a
 * dismissal or an unmount draws around one dialog session.
 */

import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
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
import { enumLabel } from "@/lib/enum-labels";
import { addDays, farmToday, formatDate } from "@/lib/format";

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

describe("HealthPage dialog state ownership", () => {
  let events: HealthEventOut[];
  let tasks: TaskOut[];
  let postBody: Record<string, unknown> | null;
  let postCalls: number;
  let previewBodies: Record<string, unknown>[];
  let eventListCalls: number;

  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
    vi.mocked(toast.success).mockClear();
    vi.mocked(toast.error).mockClear();
    events = [];
    tasks = [];
    postBody = null;
    postCalls = 0;
    previewBodies = [];
    eventListCalls = 0;
    server.use(
      http.get("/api/health/events", () => {
        eventListCalls += 1;
        return HttpResponse.json({ events, total: events.length, limit: 50, offset: 0 });
      }),
      http.post("/api/health/events", async ({ request }) => {
        postBody = (await request.json()) as Record<string, unknown>;
        postCalls += 1;
        return HttpResponse.json([makeEvent({ id: 99 })], { status: 201 });
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
    window.history.replaceState({}, "", "/health");
  });

  async function renderLoaded() {
    const user = userEvent.setup();
    const view = renderWithProviders(<HealthPage />);
    await screen.findByText("Event log");
    return { user, view };
  }

  async function openDialog() {
    const { user, view } = await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Add event" }));
    return {
      user,
      view,
      dialog: await screen.findByRole("dialog", { name: "Add health event" }),
    };
  }

  function saveButton(dialog: HTMLElement) {
    return within(dialog).getByRole("button", { name: /Save event|Retry save/ });
  }

  function reviewButton(dialog: HTMLElement) {
    return within(dialog).getByRole("button", { name: "Review target animals" });
  }

  function failedSaveNotice(dialog: HTMLElement) {
    return within(dialog).queryByText(/Check the event details, then try again\./);
  }

  // ---------- permission gate ----------

  it("asks for nothing and judges nothing until the permission answer lands", async () => {
    // `can()` answers false while the grant set is still unknown, so a page
    // that stops guarding on `permsLoading` both tells a full-rights operator
    // the module is not theirs and skips the event query it will need.
    server.use(
      http.get("/api/auth/permissions", async () => {
        await delay("infinite");
        return HttpResponse.json({ is_owner: true, permissions: [] });
      }),
    );
    renderWithProviders(<HealthPage />);

    // The page holds its header + skeleton, not a bare "Loading…" line.
    expect(
      await screen.findByRole("heading", { name: "Health" }),
    ).toBeInTheDocument();
    expect(document.querySelector('[data-slot="skeleton"]')).not.toBeNull();
    expect(
      screen.queryByText("You don't have access to this page."),
    ).not.toBeInTheDocument();
    expect(eventListCalls).toBe(0);
  });

  // ---------- whitespace is not a value ----------

  it("does not accept spaces as a schedule name or a next-due authority", async () => {
    // Both fields are mandatory documentation for a next-due date; a blank
    // that only looks filled in would be persisted as the authority for a
    // date the herd is then held to.
    const { user, dialog } = await openDialog();
    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Animal *" }),
      /G-003 · Kaveri/,
    );
    await pickOption(user, within(dialog).getByRole("combobox", { name: "Type" }), "Treatment");
    fireEvent.change(within(dialog).getByLabelText("Next due date"), {
      target: { value: addDays(TODAY, 30) },
    });
    await user.click(saveButton(dialog));
    await screen.findByText("Name the schedule used for a next-due date");

    await user.type(within(dialog).getByLabelText("Schedule/template name"), "   ");
    await user.type(within(dialog).getByLabelText("Next-due authority"), "   ");
    await user.click(saveButton(dialog));

    await waitFor(() =>
      expect(
        within(dialog).getByText("Name the schedule used for a next-due date"),
      ).toBeVisible(),
    );
    expect(
      within(dialog).getByText("Record the authority for this next-due date"),
    ).toBeVisible();
    expect(postBody).toBeNull();
  });

  it("posts nothing for the optional fields that hold only spaces", async () => {
    // Every optional string is trimmed to null before it is written, so a
    // field the operator only tapped the space bar in never becomes a lot
    // number, a certificate or an administering vet in the immutable record.
    const { user, dialog } = await openDialog();
    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Animal *" }),
      /G-003 · Kaveri/,
    );
    await pickOption(user, within(dialog).getByRole("combobox", { name: "Type" }), "Treatment");
    const details = within(dialog)
      .getByText("Advanced traceability & compliance")
      .closest("details") as HTMLDetailsElement;
    act(() => {
      details.open = true;
    });

    for (const label of [
      "Product name",
      "Disease target",
      "Dose",
      "Vet",
      "Schedule/template name",
      "Next-due authority",
      "Product lot/batch",
      "Administered by",
      "Certificate number",
      "Official tag number",
      "Notes",
    ]) {
      await user.type(within(dialog).getByLabelText(label), "   ");
    }
    await user.click(saveButton(dialog));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({
      product_name: null,
      disease_target: null,
      dose: null,
      vet_name: null,
      schedule_template_name: null,
      next_due_authority: null,
      product_lot: null,
      administered_by: null,
      certificate_number: null,
      official_tag_number: null,
      notes: null,
    });
  });

  it("treats product and disease text that is only spaces as unwritten", async () => {
    // The linked duty's guesses are withheld only for text the operator
    // actually typed. Whitespace would otherwise post an event whose product
    // and disease are blank while the duty knew both.
    tasks = [
      makeTask({
        id: 8,
        title: "Deworm the herd — Albendazole",
        category: "DEWORMING",
        animal_id: 3,
      }),
    ];
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText("Product name"), "   ");
    await user.type(within(dialog).getByLabelText("Disease target"), "  ");

    await pickOption(
      user,
      within(dialog).getByLabelText("Linked duty (completes it)"),
      /Deworm the herd/,
    );

    await waitFor(() =>
      expect(within(dialog).getByLabelText("Product name")).toHaveValue("Albendazole"),
    );
    expect(within(dialog).getByLabelText("Disease target")).toHaveValue("Deworming");
  });

  // ---------- linked duty prefills ----------

  it("hands the second duty's guesses over, not the first duty's", async () => {
    tasks = [
      makeTask({
        id: 8,
        title: "Deworm Kaveri — Albendazole",
        category: "DEWORMING",
        animal_id: 3,
      }),
      makeTask({ id: 9, title: "Vaccinate PPR", category: "VACCINE", animal_id: 3 }),
    ];
    const { user, dialog } = await openDialog();
    const dutySelect = within(dialog).getByLabelText("Linked duty (completes it)");

    await pickOption(user, dutySelect, /Deworm Kaveri/);
    await waitFor(() =>
      expect(within(dialog).getByLabelText("Product name")).toHaveValue("Albendazole"),
    );

    await pickOption(user, dutySelect, /Vaccinate PPR/);

    await waitFor(() =>
      expect(within(dialog).getByLabelText("Disease target")).toHaveValue("PPR"),
    );
    // Albendazole is the deworming duty's drug; carrying it into a PPR
    // vaccination would attribute the wrong product to the recorded event.
    expect(within(dialog).getByLabelText("Product name")).toHaveValue("");
  });

  it("offers a deep-linked duty the tabs already carry exactly once", async () => {
    tasks = [
      makeTask({ id: 8, title: "Deworm Kaveri", category: "DEWORMING", animal_id: 3 }),
    ];
    window.history.replaceState({}, "", "/health?task_id=8");
    const user = userEvent.setup();
    renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog", { name: "Add health event" });
    await waitFor(() =>
      expect(within(dialog).getByLabelText("Linked duty (completes it)")).toHaveTextContent(
        /Deworm Kaveri/,
      ),
    );

    await user.click(within(dialog).getByLabelText("Linked duty (completes it)"));

    const options = await screen.findAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual([
      "— none —",
      `Deworm Kaveri (due ${formatDate(TODAY)})`,
    ]);
  });

  it("does not re-apply a resolved duty over edits made after it was applied", async () => {
    // The exact lookup resolves the deep link while the duty tabs are down.
    // Once that prefill has been applied it is spent: the tab answer arriving
    // later is the same duty a second time, and must not rewrite the event
    // type the operator has since corrected.
    tasks = [
      makeTask({
        id: 8,
        title: "Deworm Kaveri — Albendazole",
        category: "DEWORMING",
        animal_id: 3,
      }),
    ];
    let tabsFail = true;
    server.use(
      http.get("/api/tasks", () =>
        tabsFail
          ? HttpResponse.json({ detail: "Duty tabs unavailable" }, { status: 503 })
          : HttpResponse.json(tasksPayload(tasks)),
      ),
    );
    window.history.replaceState({}, "", "/health?task_id=8");
    const user = userEvent.setup();
    renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog", { name: "Add health event" });
    await waitFor(() =>
      expect(within(dialog).getByRole("combobox", { name: "Animal *" })).toHaveTextContent(
        /G-003/,
      ),
    );

    await pickOption(user, within(dialog).getByRole("combobox", { name: "Type" }), "Treatment");
    tabsFail = false;
    await user.click(within(dialog).getByRole("button", { name: "Retry linked duties" }));
    await waitFor(() =>
      expect(
        within(dialog).queryByRole("button", { name: "Retry linked duties" }),
      ).not.toBeInTheDocument(),
    );
    await settle();

    expect(within(dialog).getByRole("combobox", { name: "Type" })).toHaveTextContent(
      "Treatment",
    );
    expect(within(dialog).getByLabelText("Linked duty (completes it)")).toHaveTextContent(
      /Deworm Kaveri/,
    );
  });

  it("keeps a duty dropped mid-lookup dropped, then and on every later answer", async () => {
    // Retargeting the event unlinks the duty. The lookup that was already in
    // flight must be consumed without being applied — and consumed for good,
    // so a later duty-tab answer cannot revive it on top of fresh edits.
    tasks = [
      makeTask({
        id: 8,
        title: "Deworm Kaveri — Albendazole",
        category: "DEWORMING",
        animal_id: 3,
      }),
    ];
    const exact = requestGate();
    let tabsFail = true;
    server.use(
      http.get("/api/tasks", () =>
        tabsFail
          ? HttpResponse.json({ detail: "Duty tabs unavailable" }, { status: 503 })
          : HttpResponse.json(tasksPayload(tasks)),
      ),
      http.get("/api/tasks/:taskId", async () => {
        exact.markReached();
        await exact.released;
        return HttpResponse.json(tasks[0]);
      }),
    );
    window.history.replaceState({}, "", "/health?task_id=8");
    const user = userEvent.setup();
    renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog", { name: "Add health event" });
    await exact.reached;

    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    exact.release();
    // The duty picker appears only once the exact lookup has landed, so this
    // is the commit in which the resolution was decided.
    const dutySelect = await within(dialog).findByLabelText("Linked duty (completes it)");
    await settle();

    // The operator chose the bucket scope over the duty's animal.
    expect(within(dialog).getByRole("radio", { name: "Whole bucket" })).toBeChecked();
    expect(dutySelect).toHaveTextContent("— none —");

    // Re-linking the same duty by hand and then correcting the type is an
    // edit the abandoned deep link has no business undoing.
    await pickOption(user, dutySelect, /Deworm Kaveri/);
    await pickOption(user, within(dialog).getByRole("combobox", { name: "Type" }), "Treatment");
    tabsFail = false;
    await user.click(within(dialog).getByRole("button", { name: "Retry linked duties" }));
    await settle();

    await waitFor(() =>
      expect(
        within(dialog).queryByRole("button", { name: "Retry linked duties" }),
      ).not.toBeInTheDocument(),
    );
    expect(within(dialog).getByRole("combobox", { name: "Type" })).toHaveTextContent(
      "Treatment",
    );
  });

  it("refuses to save against a duty id the API rendered in exponent form", async () => {
    // String(1e21) is "1e+21": Number() would happily read it as a target,
    // so the id is rejected rather than posted as some other duty.
    tasks = [
      makeTask({ id: 1e21, title: "Duty with an exponent id", category: "VACCINE", animal_id: 3 }),
    ];
    const { user, dialog } = await openDialog();
    await pickOption(
      user,
      within(dialog).getByLabelText("Linked duty (completes it)"),
      /Duty with an exponent id/,
    );

    await user.click(saveButton(dialog));
    await settle();
    await settle();

    expect(postBody).toBeNull();
    expect(within(dialog).getByLabelText("Linked duty (completes it)")).toHaveTextContent(
      /Duty with an exponent id/,
    );
  });

  it("clears the unresolved-duty warning before the next dialog session", async () => {
    // The warning belongs to the deep link that opened the previous dialog.
    // Left standing it accuses every later event of failing to close a duty
    // it never referenced.
    window.history.replaceState({}, "", "/health?task_id=99");
    const user = userEvent.setup();
    renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog", { name: "Add health event" });
    await within(dialog).findByText(/Could not link duty #99/);

    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await user.click(await screen.findByRole("button", { name: "Add event" }));

    const reopened = await screen.findByRole("dialog", { name: "Add health event" });
    expect(within(reopened).queryByText(/Could not link duty/)).not.toBeInTheDocument();
  });

  it("discards the previous deep link's draft when the link changes", async () => {
    window.history.replaceState({}, "", "/health?animal_id=3");
    const user = userEvent.setup();
    const view = renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog", { name: "Add health event" });
    await user.type(within(dialog).getByLabelText("Product name"), "Hand typed");

    window.history.replaceState({}, "", "/health?animal_id=4");
    view.rerender(<HealthPage />);

    await waitFor(() =>
      expect(within(dialog).getByRole("combobox", { name: "Animal *" })).toHaveTextContent(
        /G-004/,
      ),
    );
    // A new URL intent starts a new dialog session: the draft written for the
    // previous animal must not ride along into it.
    expect(within(dialog).getByLabelText("Product name")).toHaveValue("");
  });

  // ---------- stale reviewed snapshots ----------

  async function openBucketReview(bucket = "BREEDING") {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    // Options are labeled species-aware while the value stays the raw code.
    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Bucket *" }),
      enumLabel("bucket", bucket),
    );
    await user.click(reviewButton(dialog));
    await within(dialog).findByText(/Reviewed target snapshot/);
    return { user, dialog };
  }

  it("drops the reviewed snapshot when a herd-wide duty is linked", async () => {
    // Linking a duty changes what the write does to the herd, so the snapshot
    // the operator reviewed is no longer the target set they are confirming.
    tasks = [makeTask({ id: 8, title: "Quarterly herd deworming", category: "DEWORMING" })];
    const { user, dialog } = await openBucketReview();

    await pickOption(
      user,
      within(dialog).getByLabelText("Linked duty (completes it)"),
      /Quarterly herd deworming/,
    );

    await waitFor(() =>
      expect(within(dialog).queryByText(/Reviewed target snapshot/)).not.toBeInTheDocument(),
    );
    expect(within(dialog).getByRole("radio", { name: "Whole bucket" })).toBeChecked();
    expect(reviewButton(dialog)).toBeInTheDocument();
  });

  it("drops the reviewed snapshot and the failed save when the scope changes", async () => {
    server.use(
      http.post("/api/health/events", () =>
        HttpResponse.json({ detail: "Bucket is closed for treatment." }, { status: 409 }),
      ),
    );
    const { user, dialog } = await openBucketReview();

    await user.click(within(dialog).getByRole("radio", { name: "Purchase batch" }));
    expect(within(dialog).queryByText(/Reviewed target snapshot/)).not.toBeInTheDocument();

    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Purchase batch *" }),
      "Batch #2 — 12 active in quarantine",
    );
    await user.click(reviewButton(dialog));
    await user.click(await within(dialog).findByRole("button", { name: "Confirm for 2 animals" }));
    await within(dialog).findByText(/Bucket is closed for treatment\./);

    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));

    expect(failedSaveNotice(dialog)).toBeNull();
  });

  it("drops the reviewed snapshot and the failed save when another bucket is picked", async () => {
    server.use(
      http.post("/api/health/events", () =>
        HttpResponse.json({ detail: "Bucket is mid-transfer." }, { status: 409 }),
      ),
    );
    const { user, dialog } = await openBucketReview();
    await user.click(within(dialog).getByRole("button", { name: "Confirm for 2 animals" }));
    await within(dialog).findByText(/Bucket is mid-transfer\./);

    await pickOption(user, within(dialog).getByRole("combobox", { name: "Bucket *" }), "Resting");
    expect(failedSaveNotice(dialog)).toBeNull();

    await user.click(reviewButton(dialog));
    await within(dialog).findByText(/Reviewed target snapshot/);
    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Bucket *" }),
      "Breeding",
    );

    // The snapshot named RESTING's animals; BREEDING has to be reviewed again.
    expect(within(dialog).queryByText(/Reviewed target snapshot/)).not.toBeInTheDocument();
    expect(reviewButton(dialog)).toBeInTheDocument();
  });

  async function openBatchReview(batch = "Batch #2 — 12 active in quarantine") {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Purchase batch" }));
    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Purchase batch *" }),
      batch,
    );
    await user.click(reviewButton(dialog));
    await within(dialog).findByText(/Reviewed target snapshot/);
    return { user, dialog };
  }

  it("drops the reviewed snapshot and the failed save when another batch is picked", async () => {
    server.use(
      http.post("/api/health/events", () =>
        HttpResponse.json({ detail: "Batch is under audit." }, { status: 409 }),
      ),
    );
    const { user, dialog } = await openBatchReview();
    await user.click(within(dialog).getByRole("button", { name: "Confirm for 2 animals" }));
    await within(dialog).findByText(/Batch is under audit\./);
    expect(toast.error).toHaveBeenCalledWith("Batch is under audit.");

    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Purchase batch *" }),
      "Batch #3 — 5 active in quarantine",
    );
    expect(failedSaveNotice(dialog)).toBeNull();

    await user.click(reviewButton(dialog));
    await within(dialog).findByText(/Reviewed target snapshot/);
    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Purchase batch *" }),
      "Batch #2 — 12 active in quarantine",
    );

    expect(within(dialog).queryByText(/Reviewed target snapshot/)).not.toBeInTheDocument();
    expect(reviewButton(dialog)).toBeInTheDocument();
  });

  it("clears the failed review the operator is retrying", async () => {
    let previewFails = true;
    server.use(
      http.post("/api/health/events/preview", async ({ request }) => {
        const target = (await request.json()) as Record<string, unknown>;
        previewBodies.push(target);
        return previewFails
          ? HttpResponse.json({ detail: "Batch is under audit." }, { status: 409 })
          : HttpResponse.json(previewFor(target));
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
    await within(dialog).findByText(/Batch is under audit\./);
    expect(toast.error).toHaveBeenCalledWith("Batch is under audit.");

    previewFails = false;
    await user.click(reviewButton(dialog));
    await within(dialog).findByText(/Reviewed target snapshot/);

    // The review that just succeeded answered the failure being retried.
    expect(failedSaveNotice(dialog)).toBeNull();
    expect(previewBodies).toHaveLength(2);
  });

  // ---------- stale save failures ----------

  it("clears a failed save when another animal is picked", async () => {
    server.use(
      http.post("/api/health/events", () =>
        HttpResponse.json({ detail: "Animal is already treated today." }, { status: 409 }),
      ),
    );
    const { user, dialog } = await openDialog();
    const animalPicker = within(dialog).getByRole("combobox", { name: "Animal *" });
    await pickOption(user, animalPicker, /G-003 · Kaveri/);
    await user.click(saveButton(dialog));

    await within(dialog).findByText(/Animal is already treated today\./);
    expect(toast.error).toHaveBeenCalledWith("Animal is already treated today.");
    expect(within(dialog).getByRole("button", { name: "Retry save" })).toBeInTheDocument();

    await pickOption(user, animalPicker, /G-004/);

    expect(failedSaveNotice(dialog)).toBeNull();
    expect(within(dialog).getByRole("button", { name: "Save event" })).toBeInTheDocument();
  });

  it("hides the failed save while its retry is in flight", async () => {
    const write = requestGate();
    let firstWrite = true;
    server.use(
      http.post("/api/health/events", async ({ request }) => {
        postBody = (await request.json()) as Record<string, unknown>;
        postCalls += 1;
        if (firstWrite) {
          firstWrite = false;
          return HttpResponse.json({ detail: "Vet signature missing." }, { status: 409 });
        }
        write.markReached();
        await write.released;
        return HttpResponse.json([makeEvent({ id: 99 })], { status: 201 });
      }),
    );
    const { user, dialog } = await openDialog();
    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Animal *" }),
      /G-003 · Kaveri/,
    );
    await user.click(saveButton(dialog));
    await within(dialog).findByText(/Vet signature missing\./);

    await user.click(within(dialog).getByRole("button", { name: "Retry save" }));
    await write.reached;

    // The previous failure is not the state of the request now running.
    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Saving…" })).toBeInTheDocument(),
    );
    expect(failedSaveNotice(dialog)).toBeNull();
    write.release();
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Health event recorded."));
  });

  it("does not carry a dismissed dialog's save failure into a duty deep link", async () => {
    // A herd-wide duty prefills no target, so nothing but the dismissal
    // itself can have retired the previous session's failure.
    tasks = [makeTask({ id: 8, title: "Quarterly herd deworming", category: "DEWORMING" })];
    server.use(
      http.post("/api/health/events", () =>
        HttpResponse.json({ detail: "Animal is already treated today." }, { status: 409 }),
      ),
    );
    const { user, view, dialog } = await openDialog();
    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Animal *" }),
      /G-003 · Kaveri/,
    );
    await user.click(saveButton(dialog));
    await within(dialog).findByText(/Animal is already treated today\./);

    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    window.history.replaceState({}, "", "/health?task_id=8");
    view.rerender(<HealthPage />);

    const reopened = await screen.findByRole("dialog", { name: "Add health event" });
    expect(failedSaveNotice(reopened)).toBeNull();
    expect(within(reopened).getByRole("button", { name: "Save event" })).toBeInTheDocument();
  });

  it("clears the target requirement when the scope leaves and comes back", async () => {
    const { user, dialog } = await openDialog();
    await user.click(saveButton(dialog));
    // The picker's placeholder reads the same as the message, so pin the
    // message element itself.
    await waitFor(() =>
      expect(within(dialog).getByText("Pick an animal", { selector: "p" })).toBeVisible(),
    );

    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    await user.click(within(dialog).getByRole("radio", { name: "Single animal" }));

    // The animal requirement was answered by leaving the animal scope; it is
    // not an outstanding complaint about a target nobody has re-submitted.
    expect(
      within(dialog).queryByText("Pick an animal", { selector: "p" }),
    ).not.toBeInTheDocument();
    expect(within(dialog).getByRole("combobox", { name: "Animal *" })).not.toHaveAttribute(
      "aria-invalid",
    );
  });

  // ---------- session boundaries ----------

  it("returns a lone purchase-batch deep link to the plain log on dismissal", async () => {
    window.history.replaceState({}, "", "/health?purchase_batch_id=2");
    const user = userEvent.setup();
    renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog", { name: "Add health event" });
    await waitFor(() =>
      expect(within(dialog).getByRole("radio", { name: "Purchase batch" })).toBeChecked(),
    );

    await user.click(within(dialog).getByRole("button", { name: "Close" }));

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/health"));
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("returns to the originating page once, not once per dismissal and write", async () => {
    const write = requestGate();
    server.use(
      http.post("/api/health/events", async ({ request }) => {
        postBody = (await request.json()) as Record<string, unknown>;
        postCalls += 1;
        write.markReached();
        await write.released;
        return HttpResponse.json([makeEvent({ id: 99 })], { status: 201 });
      }),
    );
    window.history.replaceState(
      {},
      "",
      "/health?animal_id=3&returnTo=%2Ftasks%3Ftab%3Doverdue",
    );
    const user = userEvent.setup();
    renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog", { name: "Add health event" });

    await user.click(saveButton(dialog));
    await write.reached;
    // Dismissal is deliberately allowed mid-write; it ends this session and
    // performs the return, so the write's continuation owns neither.
    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/tasks?tab=overdue"));

    write.release();
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Health event recorded."));
    await settle();

    expect(pushMock).toHaveBeenCalledTimes(1);
  });

  it("lets no write that lands after the page is gone navigate it", async () => {
    const write = requestGate();
    server.use(
      http.post("/api/health/events", async ({ request }) => {
        postBody = (await request.json()) as Record<string, unknown>;
        postCalls += 1;
        write.markReached();
        await write.released;
        return HttpResponse.json([makeEvent({ id: 99 })], { status: 201 });
      }),
    );
    window.history.replaceState({}, "", "/health?animal_id=3");
    const user = userEvent.setup();
    const view = renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog", { name: "Add health event" });

    await user.click(saveButton(dialog));
    await write.reached;
    view.unmount();
    write.release();
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Health event recorded."));
    await settle();

    // The event is confirmed and the caches refreshed, but this page no longer
    // exists: its deep-link cleanup would navigate whatever replaced it.
    expect(replaceMock).not.toHaveBeenCalled();
    expect(pushMock).not.toHaveBeenCalled();
    expect(postCalls).toBe(1);
  });
});
