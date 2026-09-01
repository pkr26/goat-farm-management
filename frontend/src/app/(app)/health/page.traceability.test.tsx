/**
 * Health page — event-log traceability cell, target-picker wiring and the
 * reviewed-target snapshot panel.
 *
 * Complements page.extended.test.tsx rather than repeating it: this file pins
 * every labelled traceability line (and the single "—" that stands in when an
 * event carries none of them), the aria-invalid / re-validation wiring of the
 * animal, bucket and batch targets, the guard that keeps a linked duty
 * attached when the already-selected target is re-confirmed, and the snapshot
 * panel's scope gating, singular noun and dismissal epoch.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { HealthEventOut, TaskOut } from "@/api/generated/models";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
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

type User = ReturnType<typeof userEvent.setup>;

beforeAll(() => {
  // jsdom lacks APIs Base UI Select/Popper touch when opening the listbox.
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
    date: "2026-07-15",
    type: "VACCINE",
    product_name: "PPR vaccine",
    disease_target: "PPR",
    dose: "1 ml",
    route: "SC",
    vet_name: "Dr. Patil",
    cost: 1250.5,
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

const VACCINE_TASK = makeTask({
  id: 5,
  title: "PPR vaccination",
  category: "VACCINE",
  animal_id: 3,
});
const DEWORM_BATCH_TASK = makeTask({
  id: 6,
  title: "Deworm batch #2",
  category: "DEWORMING",
  purchase_batch_id: 2,
});

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

/** "Traceability & holds" — cell 10 since the Notes column landed before it. */
function traceabilityCell(row: HTMLElement): HTMLElement {
  return within(row).getAllByRole("cell")[10];
}

describe("HealthPage traceability cell and event targets", () => {
  let eventsCalls: number;
  let postBody: Record<string, unknown> | null;
  let events: HealthEventOut[];
  let tasks: TaskOut[];

  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
    eventsCalls = 0;
    postBody = null;
    events = [makeEvent({ id: 1 })];
    tasks = [VACCINE_TASK, DEWORM_BATCH_TASK];
    server.use(
      http.get("/api/health/events", () => {
        eventsCalls += 1;
        return HttpResponse.json({ events, total: events.length, limit: 50, offset: 0 });
      }),
      http.post("/api/health/events", async ({ request }) => {
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json([makeEvent({ id: 99 })], { status: 201 });
      }),
      http.post("/api/health/events/preview", async ({ request }) => {
        const target = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          scope: target.scope,
          bucket: target.bucket ?? null,
          purchase_batch_id: target.purchase_batch_id ?? null,
          task_id: target.task_id ?? null,
          target_animal_ids: [3, 4],
          target_animals: [
            { id: 3, tag_number: "G-003", name: "Kaveri" },
            { id: 4, tag_number: "G-004", name: null },
          ],
          target_count: 2,
          max_targets: target.scope === "bucket" ? 250 : 1000,
        });
      }),
      http.get("/api/health/animals", () =>
        HttpResponse.json({
          animals: [
            {
              id: 3,
              tag_number: "G-003",
              name: "Kaveri",
              current_bucket: "LACTATING",
              movement_restricted: false,
              restriction_version: 0,
            },
            {
              id: 4,
              tag_number: "G-004",
              name: null,
              current_bucket: "LACTATING",
              movement_restricted: false,
              restriction_version: 0,
            },
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

  async function renderLoaded() {
    renderWithProviders(<HealthPage />);
    await screen.findByText("Event log");
    await screen.findByText("PPR vaccine");
  }

  async function openDialog() {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Add event" }));
    return { user, dialog: await screen.findByRole("dialog") };
  }

  // ---------- event log: traceability & holds column ----------

  it("labels every dated traceability line and formats its date", async () => {
    events = [
      makeEvent({
        product_lot: "LOT-9",
        certificate_number: "CERT-9",
        official_tag_number: "TAG-9",
        administered_by: "Dr Rao",
        product_manufactured_on: "2026-01-10",
        product_expires_on: "2027-01-10",
        vaccine_valid_until: "2026-12-01",
        withdrawal_until: "2026-07-22",
        suspected_scheduled_disease: true,
        authority_notified_at: "2026-07-15",
        isolation_started_at: "2026-07-16",
      }),
    ];
    await renderLoaded();
    const cell = traceabilityCell(screen.getByText("PPR vaccine").closest("tr")!);

    expect(within(cell).getByText("Scheduled disease suspected")).toBeInTheDocument();
    expect(within(cell).getByText("Lot: LOT-9")).toBeInTheDocument();
    expect(within(cell).getByText("Manufactured: 10 Jan 2026")).toBeInTheDocument();
    expect(within(cell).getByText("Expires: 10 Jan 2027")).toBeInTheDocument();
    expect(within(cell).getByText("Vaccine valid until: 1 Dec 2026")).toBeInTheDocument();
    expect(within(cell).getByText("Certificate: CERT-9")).toBeInTheDocument();
    expect(within(cell).getByText("Official tag: TAG-9")).toBeInTheDocument();
    expect(within(cell).getByText("Administered by: Dr Rao")).toBeInTheDocument();
    expect(within(cell).getByText("Withdrawal until: 22 Jul 2026")).toBeInTheDocument();
    expect(within(cell).getByText("Authority notified: 15 Jul 2026")).toBeInTheDocument();
    expect(within(cell).getByText("Isolation started: 16 Jul 2026")).toBeInTheDocument();
    // Every line is present, so the "nothing recorded" dash must not appear.
    expect(cell).not.toHaveTextContent("—");
  });

  it("collapses an event with no traceability data to exactly one dash", async () => {
    await renderLoaded();
    const cell = traceabilityCell(screen.getByText("PPR vaccine").closest("tr")!);

    expect(cell).toHaveTextContent(/^—$/);
  });

  it.each([
    ["suspected_scheduled_disease", { suspected_scheduled_disease: true }],
    ["product_lot", { product_lot: "LOT-9" }],
    ["certificate_number", { certificate_number: "CERT-9" }],
    ["official_tag_number", { official_tag_number: "TAG-9" }],
    ["administered_by", { administered_by: "Dr Rao" }],
    ["withdrawal_until", { withdrawal_until: "2026-07-22" }],
    ["product_manufactured_on", { product_manufactured_on: "2026-01-10" }],
    ["product_expires_on", { product_expires_on: "2027-01-10" }],
    ["vaccine_valid_until", { vaccine_valid_until: "2026-12-01" }],
    ["authority_notified_at", { authority_notified_at: "2026-07-15" }],
    ["isolation_started_at", { isolation_started_at: "2026-07-16" }],
  ] as [string, Partial<HealthEventOut>][])(
    "drops the traceability dash when only %s is recorded",
    async (_field, overrides) => {
      events = [makeEvent(overrides)];
      await renderLoaded();
      const cell = traceabilityCell(screen.getByText("PPR vaccine").closest("tr")!);

      expect(cell).not.toHaveTextContent("—");
      expect(cell.textContent?.trim()).not.toBe("");
    },
  );

  // ---------- dialog: per-scope target wiring ----------

  it("flags the animal target invalid only after a submit without one", async () => {
    const { user, dialog } = await openDialog();
    const picker = within(dialog).getByRole("combobox", { name: "Animal *" });
    expect(picker).not.toHaveAttribute("aria-invalid");

    await user.click(within(dialog).getByRole("button", { name: "Save event" }));
    const animalError = await within(dialog).findByRole("alert");
    expect(animalError).toHaveTextContent("Pick an animal");
    expect(animalError).toHaveAttribute("id", "event-animal-error");
    expect(picker).toHaveAttribute("aria-invalid", "true");

    // shouldValidate on the picker's own setValue: choosing a target must
    // retire the message straight away, not wait for a second submit.
    await pickOption(user, picker, /G-003 · Kaveri/);
    await waitFor(() =>
      expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument(),
    );
    expect(picker).not.toHaveAttribute("aria-invalid");
    expect(postBody).toBeNull();
  });

  it("flags the bucket target invalid only after a submit without one, then shows the pick", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    const bucket = within(dialog).getByRole("combobox", { name: "Bucket *" });
    expect(bucket).not.toHaveAttribute("aria-invalid");
    expect(bucket).toHaveTextContent("Pick a bucket");

    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));
    const bucketError = await within(dialog).findByRole("alert");
    expect(bucketError).toHaveTextContent("Pick a bucket");
    expect(bucketError).toHaveAttribute("id", "event-bucket-error");
    expect(bucket).toHaveAttribute("aria-invalid", "true");

    await pickOption(user, bucket, "Breeding");
    await waitFor(() => expect(bucket).toHaveTextContent("Breeding"));
    expect(bucket).not.toHaveAttribute("aria-invalid");
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("flags the purchase-batch target invalid only after a submit without one", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Purchase batch" }));
    const batch = within(dialog).getByRole("combobox", { name: "Purchase batch *" });
    expect(batch).not.toHaveAttribute("aria-invalid");

    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));
    const batchError = await within(dialog).findByRole("alert");
    expect(batchError).toHaveTextContent("Pick a batch");
    expect(batchError).toHaveAttribute("id", "event-batch-error");
    expect(batch).toHaveAttribute("aria-invalid", "true");

    await pickOption(user, batch, /Batch #2/);
    await waitFor(() =>
      expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument(),
    );
    expect(batch).not.toHaveAttribute("aria-invalid");
  });

  // ---------- pickers: re-confirming the current target ----------

  it("keeps the linked duty when the already-selected animal is re-confirmed", async () => {
    const { user, dialog } = await openDialog();
    const duty = await within(dialog).findByLabelText(/Linked duty/);
    await pickOption(user, duty, /PPR vaccination/);

    const picker = within(dialog).getByRole("combobox", { name: "Animal *" });
    await waitFor(() => expect(picker).toHaveTextContent("G-003 · Kaveri"));
    expect(within(dialog).getByLabelText("Disease target")).toHaveValue("PPR vaccination");

    // RemotePicker fires onValueChange even when the selected row is clicked
    // again to confirm it; that is not a retarget and must not unlink.
    await pickOption(user, picker, /G-003 · Kaveri/);

    expect(duty).toHaveTextContent("PPR vaccination");
    expect(within(dialog).getByLabelText("Disease target")).toHaveValue("PPR vaccination");
  });

  it("keeps the linked duty when the already-selected purchase batch is re-confirmed", async () => {
    const { user, dialog } = await openDialog();
    const duty = await within(dialog).findByLabelText(/Linked duty/);
    await pickOption(user, duty, /Deworm batch #2/);

    const picker = await within(dialog).findByRole("combobox", { name: "Purchase batch *" });
    await waitFor(() => expect(picker).toHaveTextContent("Batch #2"));
    expect(within(dialog).getByLabelText("Disease target")).toHaveValue("Deworming");
    expect(within(dialog).getByRole("combobox", { name: "Type" })).toHaveTextContent(
      "DEWORMING",
    );

    await pickOption(user, picker, /Batch #2/);

    expect(duty).toHaveTextContent("Deworm batch #2");
    expect(within(dialog).getByLabelText("Disease target")).toHaveValue("Deworming");
    expect(within(dialog).getByRole("combobox", { name: "Type" })).toHaveTextContent(
      "DEWORMING",
    );
  });

  // ---------- reviewed-target snapshot ----------

  it("counts a one-animal reviewed snapshot in the singular", async () => {
    server.use(
      http.post("/api/health/events/preview", async ({ request }) => {
        const target = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          scope: target.scope,
          bucket: target.bucket ?? null,
          purchase_batch_id: null,
          task_id: null,
          target_animal_ids: [3],
          target_animals: [{ id: 3, tag_number: "G-003", name: "Kaveri" }],
          target_count: 1,
          max_targets: 250,
        });
      }),
    );
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Bucket *" }),
      "Breeding",
    );
    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));

    expect(await within(dialog).findByText(/Reviewed target snapshot/)).toHaveTextContent(
      /^Reviewed target snapshot: 1 active animal$/,
    );
  });

  it("hides a stale reviewed snapshot once a duty deep link resets the dialog to the animal scope", async () => {
    let releaseTask!: () => void;
    const taskGate = new Promise<void>((resolve) => {
      releaseTask = resolve;
    });
    server.use(
      http.get("/api/tasks/999", async () => {
        await taskGate;
        return HttpResponse.json({ detail: "Task not found" }, { status: 404 });
      }),
    );
    const user = userEvent.setup();
    const view = renderWithProviders(<HealthPage />);
    await screen.findByText("Event log");
    await screen.findByText("PPR vaccine");
    await user.click(screen.getByRole("button", { name: "Add event" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Bucket *" }),
      "Breeding",
    );
    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));
    expect(await within(dialog).findByText(/Reviewed target snapshot/)).toBeInTheDocument();

    // A query-only navigation to a duty deep link starts a fresh dialog
    // session on the animal scope; the previous bucket's reviewed snapshot
    // describes a target set this event no longer has.
    window.history.replaceState({}, "", "/health?task_id=999");
    view.rerender(<HealthPage />);
    await waitFor(() =>
      expect(within(dialog).getByRole("radio", { name: "Single animal" })).toBeChecked(),
    );

    expect(within(dialog).queryByText(/Reviewed target snapshot/)).not.toBeInTheDocument();
    releaseTask();
  });

  // ---------- dismissal while a write is in flight ----------

  it("does not repeat the deep-link navigation when the dialog is dismissed mid-save", async () => {
    let releaseWrite!: () => void;
    let markWriteStarted!: () => void;
    const writeStarted = new Promise<void>((resolve) => {
      markWriteStarted = resolve;
    });
    const writeGate = new Promise<void>((resolve) => {
      releaseWrite = resolve;
    });
    server.use(
      http.post("/api/health/events", async ({ request }) => {
        postBody = (await request.json()) as Record<string, unknown>;
        markWriteStarted();
        await writeGate;
        return HttpResponse.json([makeEvent({ id: 99 })], { status: 201 });
      }),
    );
    window.history.replaceState({}, "", "/health?animal_id=3");
    renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog");
    const user = userEvent.setup();

    await user.click(within(dialog).getByRole("button", { name: "Save event" }));
    await writeStarted;
    // Dismissing is deliberately allowed while the write is in flight; it ends
    // this dialog session, so the write's own continuation must not navigate
    // a second time on top of the dismissal's return.
    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(replaceMock).toHaveBeenCalledTimes(1);
    expect(replaceMock).toHaveBeenCalledWith("/health");

    const refreshesBefore = eventsCalls;
    releaseWrite();
    // The write still lands and still refreshes the farm views.
    await waitFor(() => expect(eventsCalls).toBeGreaterThan(refreshesBefore));
    expect(postBody).not.toBeNull();
    expect(replaceMock).toHaveBeenCalledTimes(1);
    expect(pushMock).not.toHaveBeenCalled();
  });
});
