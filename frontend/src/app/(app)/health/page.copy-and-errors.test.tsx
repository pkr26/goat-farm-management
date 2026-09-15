/**
 * Health page: the copy and accessibility wiring the operator reads rather
 * than the flow that produces it — per-column em dashes in the event log,
 * the schedule/authority separator, singular-vs-plural bulk review counts,
 * save confirmations, the deep-link return route, generic failure copy for
 * responses that carry no detail, and the aria-describedby link between
 * every validated control and its own error message.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { toast } from "sonner";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  HealthBulkTargetPreviewOut,
  HealthEventOut,
  TaskOut,
} from "@/api/generated/models";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { addDays, farmToday } from "@/lib/format";

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
const TOMORROW = addDays(TODAY, 1);
const YESTERDAY = addDays(TODAY, -1);

function makeEvent(overrides: Partial<HealthEventOut>): HealthEventOut {
  return {
    id: 1,
    animal_id: 3,
    purchase_batch_id: null,
    date: "2026-07-15",
    type: "VACCINE",
    product_name: "PPR vaccine",
    disease_target: "PPR",
    dose: "1 ml",
    route: "SC",
    vet_name: "Dr. Patil",
    cost: 1250.5,
    next_due_date: "2027-07-15",
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

async function pickOption(user: User, trigger: HTMLElement, name: string | RegExp) {
  await user.click(trigger);
  await user.click(await screen.findByRole("option", { name }));
}

/** A validated control must point at its own message element, and that
 *  element must be what assistive technology announces for the field. */
function expectDescribedByError(control: HTMLElement, errorId: string, message?: string) {
  expect(control).toHaveAttribute("aria-describedby", errorId);
  const error = document.getElementById(errorId);
  expect(error).toHaveAttribute("role", "alert");
  const text = error?.textContent?.trim() ?? "";
  expect(text).not.toBe("");
  if (message !== undefined) expect(text).toBe(message);
  expect(control).toHaveAccessibleDescription(text);
}

/** The remote pickers append their own value node to aria-describedby, so
 *  the error id has to be one of the listed ids rather than the whole value. */
function expectPickerDescribedByError(
  trigger: HTMLElement,
  errorId: string,
  message: string,
) {
  expect(trigger.getAttribute("aria-describedby")?.split(" ")).toContain(errorId);
  expect(document.getElementById(errorId)).toHaveTextContent(message);
}

describe("HealthPage copy and error wiring", () => {
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
    events = [makeEvent({ id: 1 })];
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
          batches: [{ id: 2, active_quarantine_animal_count: 12 }],
          total: 1,
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
    window.history.replaceState({}, "", "/health");
  });

  async function openDialog() {
    const user = userEvent.setup();
    renderWithProviders(<HealthPage />);
    await screen.findByText("Event log");
    await user.click(await screen.findByRole("button", { name: "Add event" }));
    return { user, dialog: await screen.findByRole("dialog", { name: "Add health event" }) };
  }

  async function openAdvanced(user: User, dialog: HTMLElement) {
    await user.click(within(dialog).getByText("Advanced traceability & compliance"));
  }

  // ---------- event log copy ----------

  it("prints an em dash in every optional column the event left empty", async () => {
    events = [
      makeEvent({
        id: 2,
        product_name: null,
        disease_target: null,
        dose: null,
        route: null,
        cost: null,
        next_due_date: null,
      }),
    ];
    renderWithProviders(<HealthPage />);

    // The mobile card list repeats the type label — scope to the desktop table.
    const row = (await within(await screen.findByRole("table")).findByText("Vaccination"))
      .closest("tr")!;
    // Date, Type, Animal, Product, Target, Dose, Route, Cost, Next due, Holds.
    const cells = within(row).getAllByRole("cell");
    expect(cells[3]).toHaveTextContent(/^—$/);
    expect(cells[4]).toHaveTextContent(/^—$/);
    expect(cells[5]).toHaveTextContent(/^—$/);
    expect(cells[6]).toHaveTextContent(/^—$/);
    expect(cells[7]).toHaveTextContent(/^—$/);
    expect(cells[8]).toHaveTextContent(/^—$/);
  });

  it("names the schedule alone when the next-due date cites no authority", async () => {
    events = [
      makeEvent({
        id: 3,
        next_due_date: addDays(TODAY, 200),
        schedule_template_name: "Annual PPR programme",
        next_due_authority: null,
      }),
    ];
    renderWithProviders(<HealthPage />);

    await screen.findByText("Event log");
    // The mobile card repeats the schedule line — scope to the desktop table.
    const schedule = await within(screen.getByRole("table")).findByText(/Annual PPR programme/);
    expect(schedule).toHaveTextContent(/^Annual PPR programme$/);
  });

  // ---------- save confirmations ----------

  it("confirms a single-animal save without a herd count", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], /G-003 · Kaveri/);
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(toast.success).toHaveBeenCalledWith("Health event recorded.");
  });

  it("confirms a bulk save with the number of animals it actually recorded", async () => {
    recordedEvents = [makeEvent({ id: 101 }), makeEvent({ id: 102, animal_id: 4 })];
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    await pickOption(user, within(dialog).getByRole("combobox", { name: "Bucket *" }), "Breeding");
    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));
    await user.click(
      await within(dialog).findByRole("button", { name: "Confirm for 2 animals" }),
    );

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(toast.success).toHaveBeenCalledWith("Health event recorded for 2 animals.");
  });

  it("keeps the singular for a reviewed target of exactly one animal", async () => {
    server.use(
      http.post("/api/health/events/preview", async ({ request }) => {
        const target = (await request.json()) as Record<string, unknown>;
        previewBodies.push(target);
        return HttpResponse.json(
          previewFor(target, {
            target_animal_ids: [4],
            target_animals: [{ id: 4, tag_number: "G-004", name: null }],
            target_count: 1,
          }),
        );
      }),
    );
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    await pickOption(user, within(dialog).getByRole("combobox", { name: "Bucket *" }), "Breeding");
    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));

    const snapshot = await within(dialog).findByRole("status");
    expect(within(snapshot).getByText(/Reviewed target snapshot/)).toHaveTextContent(
      /^Reviewed target snapshot: 1 active animal$/,
    );
    const reviewed = within(dialog).getByRole("list", { name: "Reviewed target animals" });
    // An animal with no name must not trail a naked separator.
    expect(within(reviewed).getByRole("listitem")).toHaveTextContent(/^G-004$/);
    await user.click(within(dialog).getByRole("button", { name: "Confirm for 1 animal" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(toast.success).toHaveBeenCalledWith("Health event recorded for 1 animal.");
  });

  it("says so when the reviewed target contains no active animals", async () => {
    server.use(
      http.post("/api/health/events/preview", async ({ request }) => {
        const target = (await request.json()) as Record<string, unknown>;
        previewBodies.push(target);
        return HttpResponse.json(
          previewFor(target, {
            target_animal_ids: [],
            target_animals: [],
            target_count: 0,
          }),
        );
      }),
    );
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    await pickOption(user, within(dialog).getByRole("combobox", { name: "Bucket *" }), "Resting");
    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));

    const snapshot = await within(dialog).findByRole("status");
    expect(within(snapshot).getByText(/Reviewed target snapshot/)).toHaveTextContent(
      /^Reviewed target snapshot: 0 active animals$/,
    );
    expect(
      within(snapshot).getByText("No active animals are in this reviewed target."),
    ).toBeInTheDocument();
    expect(
      within(snapshot).getByText("No active animal IDs were returned."),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "Confirm for 0 animals" }),
    ).toBeInTheDocument();
  });

  it("returns a bare deep link to the plain health page once the event is saved", async () => {
    window.history.replaceState({}, "", "/health?animal_id=3");
    renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog", { name: "Add health event" });

    await userEvent.setup().click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/health"));
    expect(pushMock).not.toHaveBeenCalled();
  });

  // ---------- failures with no server detail ----------

  it("explains a bulk review that failed without a server detail", async () => {
    server.use(http.post("/api/health/events/preview", () => HttpResponse.error()));
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    await pickOption(user, within(dialog).getByRole("combobox", { name: "Bucket *" }), "Breeding");
    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Could not review the bulk target set.",
    );
    expect(toast.error).toHaveBeenCalledWith("Could not review the bulk target set.");
    expect(postBody).toBeNull();
  });

  it("explains a linked-duty lookup that failed without a server detail", async () => {
    server.use(http.get("/api/tasks", () => HttpResponse.error()));
    const { dialog } = await openDialog();

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Could not load linked duties.",
    );
    expect(
      within(dialog).getByRole("button", { name: "Retry linked duties" }),
    ).toBeInTheDocument();
  });

  it("explains an event log that failed without a server detail", async () => {
    server.use(http.get("/api/health/events", () => HttpResponse.error()));
    renderWithProviders(<HealthPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load health events.",
    );
    expect(screen.getByRole("button", { name: "Retry health events" })).toBeInTheDocument();
  });

  // ---------- bulk target snapshot ----------

  it("carries a herd-wide duty into the bucket preview and the recorded event", async () => {
    tasks = [makeTask({ id: 12, title: "Herd PPR round", category: "VACCINE" })];
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    await pickOption(user, within(dialog).getByRole("combobox", { name: "Bucket *" }), "Breeding");
    await pickOption(user, within(dialog).getByLabelText(/Linked duty/), /Herd PPR round/);
    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));
    await user.click(
      await within(dialog).findByRole("button", { name: "Confirm for 2 animals" }),
    );

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(previewBodies).toEqual([{ scope: "bucket", bucket: "BREEDING", task_id: 12 }]);
    expect(postBody).toMatchObject({ scope: "bucket", bucket: "BREEDING", task_id: 12 });
  });

  it("re-reviews when the returned snapshot describes another bucket", async () => {
    // The confirm write is immutable and hits every animal in the snapshot,
    // so a snapshot that does not describe the bucket still selected must
    // send the operator back through a fresh review instead of recording.
    server.use(
      http.post("/api/health/events/preview", async ({ request }) => {
        const target = (await request.json()) as Record<string, unknown>;
        previewBodies.push(target);
        return HttpResponse.json(previewFor(target, { bucket: "DELIVERY" }));
      }),
    );
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    await pickOption(user, within(dialog).getByRole("combobox", { name: "Bucket *" }), "Breeding");
    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));
    await user.click(
      await within(dialog).findByRole("button", { name: "Confirm for 2 animals" }),
    );

    await waitFor(() => expect(previewBodies).toHaveLength(2));
    expect(previewBodies[1]).toEqual({ scope: "bucket", bucket: "BREEDING" });
    expect(postBody).toBeNull();
  });

  it("drops the reviewed snapshot when the target goes back to a single animal", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    await pickOption(user, within(dialog).getByRole("combobox", { name: "Bucket *" }), "Breeding");
    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));
    await within(dialog).findByRole("status");

    await user.click(within(dialog).getByRole("radio", { name: "Single animal" }));

    // The snapshot named bucket members; nothing about it survives into a
    // single-animal event, so it must not stay on screen or in the payload.
    expect(within(dialog).queryByRole("status")).not.toBeInTheDocument();
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], /G-003 · Kaveri/);
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({ scope: "animal", animal_id: 3, bucket: null });
    expect(postBody?.expected_animal_ids).toBeUndefined();
  });

  it("names the request the submit button is waiting on", async () => {
    let releasePreview!: () => void;
    const previewGate = new Promise<void>((resolve) => {
      releasePreview = resolve;
    });
    let releaseRecord!: () => void;
    const recordGate = new Promise<void>((resolve) => {
      releaseRecord = resolve;
    });
    server.use(
      http.post("/api/health/events/preview", async ({ request }) => {
        const target = (await request.json()) as Record<string, unknown>;
        previewBodies.push(target);
        await previewGate;
        return HttpResponse.json(previewFor(target));
      }),
      http.post("/api/health/events", async ({ request }) => {
        postBody = (await request.json()) as Record<string, unknown>;
        await recordGate;
        return HttpResponse.json(recordedEvents, { status: 201 });
      }),
    );
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    await pickOption(user, within(dialog).getByRole("combobox", { name: "Bucket *" }), "Breeding");
    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));

    // The review only reads; the confirm writes an immutable event. The
    // button has to say which of the two the operator is waiting on.
    expect(
      await within(dialog).findByRole("button", { name: "Reviewing targets…" }),
    ).toBeDisabled();
    releasePreview();

    await user.click(
      await within(dialog).findByRole("button", { name: "Confirm for 2 animals" }),
    );
    expect(await within(dialog).findByRole("button", { name: "Saving…" })).toBeDisabled();
    releaseRecord();

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  // ---------- target validation clears on selection ----------

  it("clears the animal requirement the moment an animal is picked", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Pick an animal");

    await pickOption(user, within(dialog).getAllByRole("combobox")[0], /G-003 · Kaveri/);

    await waitFor(() =>
      expect(within(dialog).queryByText("Pick an animal")).not.toBeInTheDocument(),
    );
  });

  it("clears the bucket requirement the moment a bucket is picked", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));
    await within(dialog).findByRole("alert");
    expectDescribedByError(
      within(dialog).getByRole("combobox", { name: "Bucket *" }),
      "event-bucket-error",
      "Pick a bucket",
    );

    await pickOption(user, within(dialog).getByRole("combobox", { name: "Bucket *" }), "Breeding");

    await waitFor(() => expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument());
    expect(
      within(dialog).getByRole("combobox", { name: "Bucket *" }),
    ).not.toHaveAttribute("aria-describedby");
  });

  it("clears the batch requirement the moment a batch is picked", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Purchase batch" }));
    const batchTrigger = within(dialog).getByRole("combobox", { name: "Purchase batch *" });
    expect(batchTrigger).toHaveTextContent("Pick a batch");

    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));
    await within(dialog).findByRole("alert");
    expectPickerDescribedByError(batchTrigger, "event-batch-error", "Pick a batch");

    await pickOption(user, batchTrigger, "Batch #2 — 12 active in quarantine");

    await waitFor(() => expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument());
  });

  // ---------- error descriptions ----------

  it("describes every free-text field by its own error message", async () => {
    const { user, dialog } = await openDialog();
    const field = (label: RegExp | string) => within(dialog).getByLabelText(label);
    fireEvent.change(field("Product name"), { target: { value: "P".repeat(121) } });
    fireEvent.change(field("Disease target"), { target: { value: "D".repeat(121) } });
    fireEvent.change(field("Dose"), { target: { value: "d".repeat(61) } });
    fireEvent.change(field("Vet"), { target: { value: "V".repeat(121) } });
    fireEvent.change(field(/total cost/i), { target: { value: "abc" } });
    fireEvent.change(field("Notes"), { target: { value: "n".repeat(4001) } });

    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() => expect(field("Notes")).toHaveAttribute("aria-invalid", "true"));
    expectDescribedByError(field("Product name"), "product-name-error");
    expectDescribedByError(field("Disease target"), "disease-target-error");
    expectDescribedByError(field("Dose"), "event-dose-error");
    expectDescribedByError(field("Vet"), "event-vet-error");
    expectDescribedByError(field(/total cost/i), "event-cost-error", "Cost must be a number ≥ 0");
    expectDescribedByError(field("Notes"), "health-notes-error", "Notes cannot exceed 4000 characters");
    expect(postBody).toBeNull();
  });

  it("describes every traceability date by its own error message", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], /G-003 · Kaveri/);
    await openAdvanced(user, dialog);
    const field = (label: string) => within(dialog).getByLabelText(label);
    fireEvent.change(field("Next due date"), { target: { value: TODAY } });
    fireEvent.change(field("Product manufactured"), { target: { value: TOMORROW } });
    fireEvent.change(field("Product expires"), { target: { value: YESTERDAY } });
    fireEvent.change(field("Vaccine valid until"), { target: { value: YESTERDAY } });
    fireEvent.change(field("Withdrawal until"), { target: { value: YESTERDAY } });

    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() =>
      expect(field("Next due date")).toHaveAttribute("aria-invalid", "true"),
    );
    expectDescribedByError(
      field("Next due date"),
      "next-due-date-error",
      "Next due date must be after the event date",
    );
    expectDescribedByError(
      field("Schedule/template name"),
      "schedule-template-error",
      "Name the schedule used for a next-due date",
    );
    expectDescribedByError(
      field("Next-due authority"),
      "next-due-authority-error",
      "Record the authority for this next-due date",
    );
    expectDescribedByError(
      field("Product manufactured"),
      "product-manufactured-error",
      "Manufacture date cannot be in the future",
    );
    expectDescribedByError(
      field("Product expires"),
      "product-expires-error",
      "Expiry cannot be before manufacture date",
    );
    expectDescribedByError(
      field("Vaccine valid until"),
      "vaccine-valid-error",
      "Vaccine validity cannot be before the event date",
    );
    expectDescribedByError(
      field("Withdrawal until"),
      "withdrawal-until-error",
      "Withdrawal date cannot be before the event date",
    );
    expect(postBody).toBeNull();
  });

  it("records the free-text programme a treatment event names", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], /G-003 · Kaveri/);
    await pickOption(user, within(dialog).getByLabelText("Type"), "Treatment");
    await openAdvanced(user, dialog);
    fireEvent.change(within(dialog).getByLabelText("Next due date"), {
      target: { value: addDays(TODAY, 30) },
    });

    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    const scheduleInput = within(dialog).getByLabelText("Schedule/template name");
    await waitFor(() => expect(scheduleInput).toHaveAttribute("aria-invalid", "true"));
    expectDescribedByError(
      scheduleInput,
      "schedule-template-error",
      "Name the schedule used for a next-due date",
    );

    await user.type(scheduleInput, "Vet-directed course");
    await user.type(within(dialog).getByLabelText("Next-due authority"), "Farm veterinarian");
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({
      type: "TREATMENT",
      schedule_template_name: "Vet-directed course",
      next_due_authority: "Farm veterinarian",
      next_due_date: addDays(TODAY, 30),
    });
  });

  it("drops the statutory dates and their errors when the suspicion is withdrawn", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], /G-003 · Kaveri/);
    fireEvent.change(within(dialog).getByLabelText("Disease target"), {
      target: { value: "PPR" },
    });
    await openAdvanced(user, dialog);
    const suspicion = within(dialog).getByRole("checkbox", {
      name: /Suspected scheduled\/notifiable disease/,
    });
    await user.click(suspicion);
    fireEvent.change(within(dialog).getByLabelText("Authority notified date"), {
      target: { value: TOMORROW },
    });
    fireEvent.change(within(dialog).getByLabelText("Isolation started date"), {
      target: { value: TOMORROW },
    });

    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() =>
      expect(within(dialog).getByLabelText("Authority notified date")).toHaveAttribute(
        "aria-invalid",
        "true",
      ),
    );
    expectDescribedByError(
      within(dialog).getByLabelText("Authority notified date"),
      "authority-notified-error",
      "Date cannot be in the future",
    );
    expectDescribedByError(
      within(dialog).getByLabelText("Isolation started date"),
      "isolation-started-error",
      "Date cannot be in the future",
    );

    await user.click(suspicion);
    await user.click(suspicion);

    expect(within(dialog).getByLabelText("Authority notified date")).toHaveValue("");
    expect(within(dialog).getByLabelText("Isolation started date")).toHaveValue("");
    expect(within(dialog).queryAllByText("Date cannot be in the future")).toHaveLength(0);

    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({
      suspected_scheduled_disease: true,
      authority_notified_at: null,
      isolation_started_at: null,
    });
  });

  it("labels the seeded-programme select while it loads and once it is ready", async () => {
    let releaseTemplates!: () => void;
    const templatesGate = new Promise<void>((resolve) => {
      releaseTemplates = resolve;
    });
    server.use(
      http.get("/api/health/schedule-templates", async () => {
        await templatesGate;
        return HttpResponse.json({
          templates: [
            { id: 2, name: "PPR", timing_note: "Core vaccine", event_type: "VACCINE" },
          ],
        });
      }),
    );
    const { user, dialog } = await openDialog();
    await openAdvanced(user, dialog);

    // While the catalogue is in flight the select keeps its neutral
    // placeholder and the shared InlineLoading labels the wait beside it.
    const trigger = within(dialog).getByLabelText("Schedule/template name");
    expect(trigger).toHaveTextContent("Select a seeded programme");
    expect(within(dialog).getByText("Loading programmes…")).toBeInTheDocument();

    releaseTemplates();

    await waitFor(() =>
      expect(within(dialog).queryByText("Loading programmes…")).not.toBeInTheDocument(),
    );
    expect(trigger).toHaveTextContent("Select a seeded programme");
  });
});
