/**
 * Health page: event-log rendering (₹ cost formatting, batch/animal/fallback
 * target cells, dashes), the per-animal schedule picker navigation, RBAC
 * gating (health.view / health.manage / tasks.view), and the Add-event
 * dialog — scope switching (animal/bucket/batch) with per-scope zod
 * validation, future-date and cost rules, linked-duty prefill from
 * VACCINE/DEWORMING tasks, URL ?task_id prefill, payload mapping
 * (NONE sentinel → null, trimmed strings, numeric cost) and server errors.
 */

import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { AnimalOut, HealthEventOut, TaskOut } from "@/api/generated/models";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { addDays, farmToday, formatDate } from "@/lib/format";
import { setCurrentFarmId } from "@/lib/api-client";

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
  // jsdom lacks APIs Radix Select/Popper touch when opening the listbox.
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

function makeAnimal(overrides: Partial<AnimalOut>): AnimalOut {
  return {
    id: 1,
    tag_number: "G-001",
    name: null,
    breed: "Osmanabadi",
    sex: "F",
    date_of_birth: "2024-01-01",
    estimated_dob: null,
    birth_type: null,
    source: "BORN",
    dam_id: null,
    sire_id: null,
    birth_weight: null,
    current_bucket: "LACTATING",
    status: "ACTIVE",
    status_date: null,
    sale_price: null,
    purchase_date: null,
    purchase_price: null,
    seller_name: null,
    cull_candidate: false,
    movement_restricted: false,
    restriction_reason: null,
    suspected_scheduled_disease: false,
    suspected_disease: null,
    authority_notified_at: null,
    restriction_cleared_at: null,
    restriction_cleared_by_id: null,
    restriction_clearance_reference: null,
    restriction_version: 0,
    mortality_cause: null,
    mortality_reported_at: null,
    notes: null,
    created_at: "2026-01-01T05:30:00Z",
    ...overrides,
  };
}

const ANIMAL_A = makeAnimal({ id: 3, tag_number: "G-003", name: "Kaveri" });
const ANIMAL_B = makeAnimal({ id: 4, tag_number: "G-004" });

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
const OTHER_TASK = makeTask({ id: 7, title: "Scrub feeders", category: "CLEANING" });

const BATCHES = [
  {
    id: 2,
    date: "2026-06-01",
    supplier: "Sharma Traders",
    count: 12,
    avg_age_months: 7,
    avg_weight_kg: 18,
    total_price: 60000,
    notes: null,
  },
];

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

describe("HealthPage", () => {
  let eventsCalls: number;
  let postBody: Record<string, unknown> | null;
  let previewBodies: Record<string, unknown>[];
  let events: HealthEventOut[];
  let tasks: TaskOut[];

  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
    eventsCalls = 0;
    postBody = null;
    previewBodies = [];
    events = [makeEvent({ id: 1 })];
    tasks = [VACCINE_TASK, DEWORM_BATCH_TASK, OTHER_TASK];
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
        previewBodies.push(target);
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
          animals: [ANIMAL_A, ANIMAL_B].map((animal) => ({
            id: animal.id,
            tag_number: animal.tag_number,
            name: animal.name,
            current_bucket: animal.current_bucket,
            movement_restricted: animal.movement_restricted,
            restriction_version: animal.restriction_version,
          })),
          total: 2,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get("/api/health/purchase-batches", () =>
        HttpResponse.json({
          batches: BATCHES.map((batch) => ({
            id: batch.id,
            active_quarantine_animal_count: batch.count,
          })),
          total: BATCHES.length,
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

  // ---------- event log ----------

  it("renders the event log with formatted date, ₹ cost and next-due", async () => {
    await renderLoaded();
    const row = screen.getByText("PPR vaccine").closest("tr")!;
    expect(within(row).getByText("15 Jul 2026")).toBeInTheDocument();
    expect(within(row).getByText("VACCINE")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "G-003" })).toHaveAttribute(
      "href",
      "/animals/3?returnTo=%2Fhealth",
    );
    expect(within(row).getByText("PPR")).toBeInTheDocument();
    expect(within(row).getByText("1 ml")).toBeInTheDocument();
    expect(within(row).getByText("SC")).toBeInTheDocument();
    expect(within(row).getByText("₹1,250.50")).toBeInTheDocument();
    expect(within(row).getByText("15 Jul 2027")).toBeInTheDocument();
  });

  it("distinguishes overdue, due-this-week, and later next-due dates", async () => {
    events = [
      makeEvent({ id: 11, product_name: "Overdue dose", next_due_date: addDays(TODAY, -1) }),
      makeEvent({ id: 12, product_name: "Due today", next_due_date: TODAY }),
      makeEvent({ id: 13, product_name: "Due in seven", next_due_date: addDays(TODAY, 7) }),
      makeEvent({ id: 14, product_name: "Due later", next_due_date: addDays(TODAY, 8) }),
    ];
    renderWithProviders(<HealthPage />);
    await screen.findByText("Event log");
    await screen.findByText("Overdue dose");

    const overdueRow = screen.getByText("Overdue dose").closest("tr")!;
    const todayRow = screen.getByText("Due today").closest("tr")!;
    const sevenDayRow = screen.getByText("Due in seven").closest("tr")!;
    const laterRow = screen.getByText("Due later").closest("tr")!;
    expect(within(overdueRow).getByText(formatDate(addDays(TODAY, -1)))).toHaveClass(
      "bg-red-100",
    );
    expect(within(todayRow).getByText(formatDate(TODAY))).toHaveClass("bg-amber-100");
    expect(within(sevenDayRow).getByText(formatDate(addDays(TODAY, 7)))).toHaveClass(
      "bg-amber-100",
    );
    expect(laterRow.querySelector(".bg-red-100, .bg-amber-100")).toBeNull();
  });

  it("renders dashes for empty optional cells", async () => {
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
    const row = (await screen.findByText("VACCINE")).closest("tr")!;
    expect(within(row).getAllByText("—").length).toBeGreaterThanOrEqual(6);
  });

  it("surfaces product, schedule and disease-hold traceability in the event log", async () => {
    events = [
      makeEvent({
        schedule_template_name: "Annual PPR programme",
        next_due_authority: "Farm veterinarian",
        product_lot: "LOT-PPR-26",
        product_manufactured_on: "2026-01-10",
        product_expires_on: "2027-01-10",
        certificate_number: "CERT-88",
        official_tag_number: "TAG-003",
        administered_by: "Dr Rao",
        withdrawal_until: "2026-07-22",
        suspected_scheduled_disease: true,
        authority_notified_at: "2026-07-15",
        isolation_started_at: "2026-07-15",
      }),
    ];

    await renderLoaded();
    const row = screen.getByText("PPR vaccine").closest("tr")!;
    expect(within(row).getByText(/Annual PPR programme · Farm veterinarian/)).toBeInTheDocument();
    expect(within(row).getByText("Scheduled disease suspected")).toBeInTheDocument();
    expect(within(row).getByText("Lot: LOT-PPR-26")).toBeInTheDocument();
    expect(within(row).getByText("Certificate: CERT-88")).toBeInTheDocument();
    expect(within(row).getByText("Official tag: TAG-003")).toBeInTheDocument();
    expect(within(row).getByText("Administered by: Dr Rao")).toBeInTheDocument();
  });

  it("shows batch-scoped events as 'batch #N' and untagged animals as '#id'", async () => {
    events = [
      makeEvent({ id: 3, animal_id: 0, purchase_batch_id: 2, animal_tag: null }),
      makeEvent({ id: 4, animal_id: 9, animal_tag: null }),
    ];
    renderWithProviders(<HealthPage />);
    expect(await screen.findByText("batch #2")).toBeInTheDocument();
    expect(screen.getByText("#9")).toBeInTheDocument();
  });

  it("shows the empty state when no events exist", async () => {
    events = [];
    renderWithProviders(<HealthPage />);
    expect(
      await screen.findByText("No health events recorded yet."),
    ).toBeInTheDocument();
  });

  it("shows the server error detail when events fail to load", async () => {
    let attempts = 0;
    server.use(
      http.get("/api/health/events", () => {
        attempts += 1;
        return attempts === 1
          ? HttpResponse.json({ detail: "events table missing" }, { status: 500 })
          : HttpResponse.json({ events, total: events.length, limit: 50, offset: 0 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<HealthPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("events table missing");
    await user.click(screen.getByRole("button", { name: "Retry health events" }));
    expect(await screen.findByText("Event log")).toBeInTheDocument();
    expect(attempts).toBe(2);
  });

  // ---------- RBAC ----------

  it("fails closed when permissions cannot be loaded", async () => {
    server.use(
      http.get("/api/auth/permissions", () =>
        HttpResponse.json({ detail: "permissions unavailable" }, { status: 503 }),
      ),
    );
    renderWithProviders(<HealthPage />);

    expect(
      await screen.findByText("Could not load your permissions — refresh the page to try again."),
    ).toBeInTheDocument();
    expect(eventsCalls).toBe(0);
  });

  it("blocks the page without health.view", async () => {
    server.use(permissionsHandler(["health.manage"]));
    renderWithProviders(<HealthPage />);
    expect(
      await screen.findByText("You don't have access to this page."),
    ).toBeInTheDocument();
  });

  it("hides the Add-event button without health.manage", async () => {
    server.use(permissionsHandler(["health.view"]));
    await renderLoaded();
    expect(
      screen.queryByRole("button", { name: "+ Add event" }),
    ).not.toBeInTheDocument();
  });

  it("uses health-scoped targets and plain tags for a module-only manager", async () => {
    let generalAnimalCalls = 0;
    let purchaseLedgerCalls = 0;
    server.use(
      permissionsHandler(["health.view", "health.manage"]),
      http.get("/api/animals", () => {
        generalAnimalCalls += 1;
        return HttpResponse.json({ animals: [], total: 0 });
      }),
      http.get("/api/purchases", () => {
        purchaseLedgerCalls += 1;
        return HttpResponse.json({ batches: [], total: 0, limit: 50, offset: 0 });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();

    expect(screen.queryByRole("link", { name: "G-003" })).not.toBeInTheDocument();
    const card = screen.getByText("Vaccination schedule per animal").closest(
      "[data-slot='card']",
    ) as HTMLElement;
    await pickOption(user, within(card).getByRole("combobox"), /G-003 · Kaveri/);

    await user.click(screen.getByRole("button", { name: "+ Add event" }));
    const dialog = await screen.findByRole("dialog", { name: "Add health event" });
    await user.click(within(dialog).getByRole("radio", { name: "Purchase batch" }));
    await user.click(within(dialog).getByRole("combobox", { name: "Purchase batch *" }));
    expect(
      await screen.findByRole("option", { name: "Batch #2 — 12 active in quarantine" }),
    ).toBeInTheDocument();
    expect(generalAnimalCalls).toBe(0);
    expect(purchaseLedgerCalls).toBe(0);
  });

  // ---------- schedule picker ----------

  it("keeps View disabled until an animal is picked, then navigates", async () => {
    const user = userEvent.setup();
    await renderLoaded();
    const viewButton = screen.getByRole("button", { name: "View" });
    expect(viewButton).toBeDisabled();

    const card = screen.getByText("Vaccination schedule per animal").closest(
      "[data-slot='card']",
    ) as HTMLElement;
    await pickOption(user, within(card).getByRole("combobox"), /G-003 · Kaveri/);
    expect(viewButton).toBeEnabled();
    await user.click(viewButton);
    expect(pushMock).toHaveBeenCalledWith(
      "/health/schedule/3?returnTo=%2Fhealth%3Fschedule_animal_id%3D3",
    );
  });

  it("synchronizes the schedule picker when a query-only navigation changes its animal", async () => {
    window.history.replaceState({}, "", "/health?schedule_animal_id=3");
    const view = renderWithProviders(<HealthPage />);
    await screen.findByText("Event log");
    const card = screen.getByText("Vaccination schedule per animal").closest(
      "[data-slot='card']",
    ) as HTMLElement;
    const picker = within(card).getByRole("combobox");
    await waitFor(() => expect(picker).toHaveTextContent("G-003 · Kaveri"));

    window.history.replaceState({}, "", "/health?schedule_animal_id=4");
    view.rerender(<HealthPage />);

    await waitFor(() => expect(picker).toHaveTextContent("G-004"));
  });

  // ---------- dialog: scope switching & validation ----------

  async function openDialog() {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "+ Add event" }));
    return { user, dialog: await screen.findByRole("dialog") };
  }

  async function reviewAndConfirmBulk(user: User, dialog: HTMLElement) {
    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));
    expect(await within(dialog).findByRole("status")).toHaveTextContent(
      "Reviewed target snapshot: 2 active animals",
    );
    const reviewedAnimals = within(dialog).getByRole("list", {
      name: "Reviewed target animals",
    });
    const reviewedRows = within(reviewedAnimals).getAllByRole("listitem");
    expect(reviewedRows[0]).toHaveTextContent("G-003 · Kaveri");
    expect(reviewedRows[1]).toHaveTextContent("G-004");
    expect(within(dialog).getByText("3, 4")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Confirm for 2 animals" }));
  }

  it("opens on the animal scope with the animal picker", async () => {
    const { dialog } = await openDialog();
    expect(within(dialog).getByRole("radio", { name: "Single animal" })).toBeChecked();
    expect(within(dialog).getByText("Animal *")).toBeInTheDocument();
    expect(within(dialog).queryByText("Bucket *")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("Purchase batch *")).not.toBeInTheDocument();
  });

  it("switches to the bucket scope with all ten buckets", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    expect(within(dialog).queryByText("Animal *")).not.toBeInTheDocument();

    await user.click(within(dialog).getAllByRole("combobox")[0]);
    const options = await screen.findAllByRole("option");
    const names = options.map((o) => o.textContent);
    for (const bucket of [
      "QUARANTINE",
      "FOUNDATION",
      "BREEDING",
      "PREGNANCY_EARLY",
      "PREGNANCY_LATE",
      "DELIVERY",
      "RECOVERY",
      "RESTING",
      "MALE_KIDS",
      "FEMALE_KIDS",
    ]) {
      expect(names).toContain(bucket);
    }
  });

  it("switches to the batch scope listing purchase batches", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Purchase batch" }));

    await user.click(within(dialog).getAllByRole("combobox")[0]);
    expect(
      await screen.findByRole("option", {
        name: "Batch #2 — 12 active in quarantine",
      }),
    ).toBeInTheDocument();
  });

  it("clears an old target whenever its conditional scope is hidden", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], /G-003 · Kaveri/);
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], "BREEDING");

    await user.click(within(dialog).getByRole("radio", { name: "Single animal" }));
    expect(within(dialog).getByRole("combobox", { name: "Animal *" })).toHaveTextContent(
      "Pick an animal",
    );
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    expect(within(dialog).getByRole("combobox", { name: "Bucket *" })).toHaveTextContent(
      "Pick a bucket",
    );
  });

  it("requires an animal on the animal scope", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Pick an animal");
    expect(
      within(dialog)
        .getByRole("combobox", { name: "Animal *" })
        .getAttribute("aria-describedby"),
    ).toContain("event-animal-error");
    expect(postBody).toBeNull();
  });

  it("requires a bucket on the bucket scope", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Pick a bucket");
    expect(postBody).toBeNull();
  });

  it("requires a batch on the batch scope", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Purchase batch" }));
    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Pick a batch");
    expect(postBody).toBeNull();
  });

  it("rejects a future event date", async () => {
    const { user, dialog } = await openDialog();
    fireEvent.change(within(dialog).getByLabelText(/^date/i), {
      target: { value: TOMORROW },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));
    expect(
      await within(dialog).findByText("Date cannot be in the future"),
    ).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/^date/i)).toHaveFocus();
    expect(within(dialog).getByLabelText(/^date/i)).toHaveAccessibleDescription(
      "Date cannot be in the future",
    );
    expect(postBody).toBeNull();
  });

  it.each(["-5", "abc"])("rejects an invalid cost %s", async (cost) => {
    const { user, dialog } = await openDialog();
    fireEvent.change(within(dialog).getByLabelText(/total cost/i), {
      target: { value: cost },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));
    expect(
      await within(dialog).findByText("Cost must be a number ≥ 0"),
    ).toBeInTheDocument();
    expect(postBody).toBeNull();
  });

  it("rejects a non-zero total cost below half a paisa", async () => {
    const { user, dialog } = await openDialog();
    fireEvent.change(within(dialog).getByLabelText(/total cost/i), {
      target: { value: "0.004" },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    expect(await within(dialog).findByText("Amount must be ₹0 or at least ₹0.005"))
      .toBeInTheDocument();
    expect(postBody).toBeNull();
  });

  it("accepts the exact zero-cost boundary", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], /G-003 · Kaveri/);
    fireEvent.change(within(dialog).getByLabelText(/total cost/i), {
      target: { value: "0" },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({ cost: 0 });
  });

  it("requires a named schedule and authority whenever a next-due date is recorded", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], /G-003 · Kaveri/);
    fireEvent.change(within(dialog).getByLabelText("Next due date"), {
      target: { value: addDays(TODAY, 30) },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    expect(
      await within(dialog).findByText("Name the schedule used for a next-due date"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText("Record the authority for this next-due date"),
    ).toBeInTheDocument();
    expect(postBody).toBeNull();
  });

  it("validates dependent dates against farm today when the blank event date uses its API default", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], /G-003 · Kaveri/);
    fireEvent.change(within(dialog).getByLabelText(/^date/i), { target: { value: "" } });
    fireEvent.change(within(dialog).getByLabelText("Next due date"), {
      target: { value: TODAY },
    });
    await user.click(within(dialog).getByText("Advanced traceability & compliance"));
    await pickOption(user, within(dialog).getByLabelText("Schedule/template name"), /^PPR/);
    fireEvent.change(within(dialog).getByLabelText("Next-due authority"), {
      target: { value: "Farm veterinarian" },
    });

    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    expect(
      await within(dialog).findByText("Next due date must be after the event date"),
    ).toBeInTheDocument();
    expect(postBody).toBeNull();
  });

  it("mirrors the API's chronology and storage ceilings, accepting every exact boundary", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], /G-003 · Kaveri/);
    await user.click(within(dialog).getByText("Advanced traceability & compliance"));

    const eventDate = addDays(TODAY, -30);
    fireEvent.change(within(dialog).getByLabelText(/^date/i), {
      target: { value: eventDate },
    });

    const save = within(dialog).getByRole("button", { name: "Save event" });
    const submitInvalid = async (label: string | RegExp, value: string, message: string) => {
      fireEvent.change(within(dialog).getByLabelText(label), { target: { value } });
      await user.click(save);
      expect(await within(dialog).findByText(message)).toBeInTheDocument();
      expect(postBody).toBeNull();
    };

    await submitInvalid(
      "Product manufactured",
      addDays(eventDate, 1),
      "Manufacture date cannot be after the event date",
    );
    // Clear manufacture temporarily so the expiry-vs-event guard is the
    // field's first (and therefore rendered) issue, rather than the separate
    // expiry-vs-manufacture guard masking it.
    fireEvent.change(within(dialog).getByLabelText("Product manufactured"), {
      target: { value: "" },
    });

    await submitInvalid(
      "Product expires",
      addDays(eventDate, -1),
      "Product was expired on the event date",
    );
    fireEvent.change(within(dialog).getByLabelText("Product expires"), {
      target: { value: eventDate },
    });
    fireEvent.change(within(dialog).getByLabelText("Product manufactured"), {
      target: { value: eventDate },
    });

    await submitInvalid(
      "Vaccine valid until",
      addDays(eventDate, -1),
      "Vaccine validity cannot be before the event date",
    );
    fireEvent.change(within(dialog).getByLabelText("Vaccine valid until"), {
      target: { value: eventDate },
    });

    await submitInvalid(
      "Withdrawal until",
      addDays(eventDate, -1),
      "Withdrawal date cannot be before the event date",
    );
    await submitInvalid(
      "Withdrawal until",
      addDays(eventDate, 731),
      "Withdrawal date cannot be more than 730 days after the event",
    );
    fireEvent.change(within(dialog).getByLabelText("Withdrawal until"), {
      target: { value: addDays(eventDate, 730) },
    });

    await pickOption(user, within(dialog).getByLabelText("Schedule/template name"), /^PPR/);
    fireEvent.change(within(dialog).getByLabelText("Next-due authority"), {
      target: { value: "Farm veterinarian" },
    });
    await submitInvalid(
      "Next due date",
      eventDate,
      "Next due date must be after the event date",
    );
    fireEvent.change(within(dialog).getByLabelText("Next due date"), {
      target: { value: addDays(eventDate, 1) },
    });

    await submitInvalid(
      /total cost/i,
      "1000000001",
      "Cost cannot exceed ₹1,000,000,000",
    );
    fireEvent.change(within(dialog).getByLabelText(/total cost/i), {
      target: { value: "1000000000" },
    });

    await submitInvalid(
      "Notes",
      "n".repeat(4_001),
      "Notes cannot exceed 4000 characters",
    );
    fireEvent.change(within(dialog).getByLabelText("Notes"), {
      target: { value: "n".repeat(4_000) },
    });

    await user.click(save);
    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({
      date: eventDate,
      product_manufactured_on: eventDate,
      product_expires_on: eventDate,
      vaccine_valid_until: eventDate,
      withdrawal_until: addDays(eventDate, 730),
      next_due_date: addDays(eventDate, 1),
      cost: 1_000_000_000,
      notes: "n".repeat(4_000),
    });
  });

  it("rejects every independent traceability chronology violation", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], /G-003 · Kaveri/);
    await user.click(within(dialog).getByText("Advanced traceability & compliance"));
    const save = within(dialog).getByRole("button", { name: "Save event" });

    fireEvent.change(within(dialog).getByLabelText("Product manufactured"), {
      target: { value: TOMORROW },
    });
    await user.click(save);
    expect(await within(dialog).findByText("Manufacture date cannot be in the future"))
      .toBeInTheDocument();

    fireEvent.change(within(dialog).getByLabelText(/^date/i), {
      target: { value: addDays(TODAY, -10) },
    });
    fireEvent.change(within(dialog).getByLabelText("Product manufactured"), {
      target: { value: addDays(TODAY, -5) },
    });
    fireEvent.change(within(dialog).getByLabelText("Product expires"), {
      target: { value: addDays(TODAY, -6) },
    });
    await user.click(save);
    expect(await within(dialog).findByText("Expiry cannot be before manufacture date"))
      .toBeInTheDocument();

    fireEvent.change(within(dialog).getByLabelText("Product manufactured"), {
      target: { value: addDays(TODAY, -10) },
    });
    fireEvent.change(within(dialog).getByLabelText("Product expires"), {
      target: { value: addDays(TODAY, 10) },
    });
    fireEvent.change(within(dialog).getByLabelText("Vaccine valid until"), {
      target: { value: addDays(TODAY, 11) },
    });
    await user.click(save);
    expect(
      await within(dialog).findByText("Vaccine validity cannot extend beyond product expiry"),
    ).toBeInTheDocument();
    expect(postBody).toBeNull();
  });

  it("requires a disease name and rejects future statutory reporting dates", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], /G-003 · Kaveri/);
    await user.click(within(dialog).getByText("Advanced traceability & compliance"));
    await user.click(
      within(dialog).getByRole("checkbox", {
        name: "Suspected scheduled/notifiable disease — apply movement restriction",
      }),
    );
    fireEvent.change(within(dialog).getByLabelText(/disease target/i), {
      target: { value: "   " },
    });
    fireEvent.change(within(dialog).getByLabelText("Authority notified date"), {
      target: { value: TOMORROW },
    });
    fireEvent.change(within(dialog).getByLabelText("Isolation started date"), {
      target: { value: TOMORROW },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    expect(await within(dialog).findByText("Name the suspected scheduled disease"))
      .toBeInTheDocument();
    expect(within(dialog).getAllByText("Date cannot be in the future")).toHaveLength(2);
    expect(postBody).toBeNull();
  });

  // ---------- dialog: submit mapping ----------

  it("posts an animal-scoped event with NONE sentinels mapped to null", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], /G-003 · Kaveri/);
    fireEvent.change(within(dialog).getByLabelText(/product name/i), {
      target: { value: "  PPR vaccine  " },
    });
    fireEvent.change(within(dialog).getByLabelText(/total cost/i), {
      target: { value: "250" },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toEqual({
      scope: "animal",
      animal_id: 3,
      bucket: null,
      purchase_batch_id: null,
      date: TODAY,
      type: "VACCINE",
      product_name: "PPR vaccine",
      disease_target: null,
      dose: null,
      route: null,
      vet_name: null,
      cost: 250,
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
      task_id: null,
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(eventsCalls).toBeGreaterThanOrEqual(2));
  });

  it("posts the complete traceability and scheduled-disease audit trail", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], /G-003 · Kaveri/);
    await user.click(within(dialog).getByText("Advanced traceability & compliance"));
    fireEvent.change(within(dialog).getByLabelText(/disease target/i), {
      target: { value: "  PPR  " },
    });
    fireEvent.change(within(dialog).getByLabelText("Dose"), {
      target: { value: "  1 ml  " },
    });
    fireEvent.change(within(dialog).getByLabelText("Vet"), {
      target: { value: "  Dr Rao  " },
    });
    fireEvent.change(within(dialog).getByLabelText("Notes"), {
      target: { value: "  statutory record  " },
    });
    fireEvent.change(within(dialog).getByLabelText("Next due date"), {
      target: { value: addDays(TODAY, 30) },
    });
    const values: Record<string, string> = {
      "Next-due authority": "  Farm veterinarian  ",
      "Product lot/batch": "  LOT-PPR-26  ",
      "Administered by": "  Dr Rao  ",
      "Product manufactured": addDays(TODAY, -30),
      "Product expires": addDays(TODAY, 365),
      "Vaccine valid until": addDays(TODAY, 300),
      "Withdrawal until": addDays(TODAY, 7),
      "Certificate number": "  CERT-88  ",
      "Official tag number": "  TAG-003  ",
    };
    for (const [label, value] of Object.entries(values)) {
      fireEvent.change(within(dialog).getByLabelText(label), { target: { value } });
    }
    // A next-due date requires a schedule, and a VACCINE event accepts only a
    // seeded programme name — so it is chosen, not typed.
    await pickOption(user, within(dialog).getByLabelText("Schedule/template name"), /^PPR/);
    await user.click(
      within(dialog).getByRole("checkbox", {
        name: "Suspected scheduled/notifiable disease — apply movement restriction",
      }),
    );
    fireEvent.change(within(dialog).getByLabelText("Authority notified date"), {
      target: { value: TODAY },
    });
    fireEvent.change(within(dialog).getByLabelText("Isolation started date"), {
      target: { value: TODAY },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({
      disease_target: "PPR",
      dose: "1 ml",
      vet_name: "Dr Rao",
      notes: "statutory record",
      next_due_date: addDays(TODAY, 30),
      schedule_template_name: "PPR",
      next_due_authority: "Farm veterinarian",
      product_lot: "LOT-PPR-26",
      product_manufactured_on: addDays(TODAY, -30),
      product_expires_on: addDays(TODAY, 365),
      vaccine_valid_until: addDays(TODAY, 300),
      certificate_number: "CERT-88",
      official_tag_number: "TAG-003",
      administered_by: "Dr Rao",
      withdrawal_until: addDays(TODAY, 7),
      suspected_scheduled_disease: true,
      authority_notified_at: TODAY,
      isolation_started_at: TODAY,
    });
  });

  it("posts a bucket-scoped event with the other targets nulled", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], "BREEDING");
    await reviewAndConfirmBulk(user, dialog);

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({
      scope: "bucket",
      bucket: "BREEDING",
      animal_id: null,
      purchase_batch_id: null,
      expected_animal_ids: [3, 4],
    });
    expect(previewBodies).toEqual([{ scope: "bucket", bucket: "BREEDING" }]);
  });

  it("clears a linked animal duty before previewing a bucket scope", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(
      user,
      within(dialog).getByLabelText(/Linked duty/),
      /PPR vaccination/,
    );
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    await pickOption(user, within(dialog).getByRole("combobox", { name: "Bucket *" }), "BREEDING");

    expect(within(dialog).getByLabelText(/Linked duty/)).toHaveTextContent("— none —");
    await user.click(
      within(dialog).getByRole("button", { name: "Review target animals" }),
    );

    await waitFor(() => expect(previewBodies).toHaveLength(1));
    expect(previewBodies[0]).toEqual({ scope: "bucket", bucket: "BREEDING" });
  });

  it("posts a batch-scoped event with the batch id", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Purchase batch" }));
    await pickOption(
      user,
      within(dialog).getAllByRole("combobox")[0],
      "Batch #2 — 12 active in quarantine",
    );
    await reviewAndConfirmBulk(user, dialog);

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({
      scope: "batch",
      purchase_batch_id: 2,
      animal_id: null,
      bucket: null,
      expected_animal_ids: [3, 4],
    });
    expect(previewBodies).toEqual([{ scope: "batch", purchase_batch_id: 2 }]);
  });

  it("auto-opens a bare purchase-batch deep link on the batch scope", async () => {
    window.history.replaceState({}, "", "/health?purchase_batch_id=2");
    renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog");

    expect(within(dialog).getByRole("radio", { name: "Purchase batch" })).toBeChecked();
    await waitFor(() =>
      expect(within(dialog).getByRole("combobox", { name: "Purchase batch *" }))
        .toHaveTextContent("Batch #2"),
    );
  });

  it("discards a deferred preview when the linked duty changes for the same batch", async () => {
    const followUpTask = makeTask({
      id: 16,
      title: "Follow-up deworming for batch #2",
      category: "DEWORMING",
      purchase_batch_id: 2,
    });
    tasks = [DEWORM_BATCH_TASK, followUpTask];

    let releaseFirstPreview!: () => void;
    let markFirstPreviewStarted!: () => void;
    const firstPreviewGate = new Promise<void>((resolve) => {
      releaseFirstPreview = resolve;
    });
    const firstPreviewStarted = new Promise<void>((resolve) => {
      markFirstPreviewStarted = resolve;
    });
    const deferredBodies: Record<string, unknown>[] = [];
    server.use(
      http.post("/api/health/events/preview", async ({ request }) => {
        const target = (await request.json()) as Record<string, unknown>;
        deferredBodies.push(target);
        if (deferredBodies.length === 1) {
          markFirstPreviewStarted();
          await firstPreviewGate;
        }
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
          max_targets: 1000,
        });
      }),
    );

    const { user, dialog } = await openDialog();
    const linkedDuty = within(dialog).getByLabelText(/Linked duty/);
    await pickOption(user, linkedDuty, /Deworm batch #2/);
    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));
    await firstPreviewStarted;

    await pickOption(user, within(dialog).getByLabelText(/Linked duty/), /Follow-up deworming/);
    releaseFirstPreview();

    await waitFor(() =>
      expect(
        within(dialog).getByRole("button", { name: "Review target animals" }),
      ).toBeEnabled(),
    );
    expect(within(dialog).queryByRole("status")).not.toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));
    expect(await within(dialog).findByRole("status")).toHaveTextContent(
      "Reviewed target snapshot: 2 active animals",
    );
    expect(deferredBodies).toEqual([
      { scope: "batch", purchase_batch_id: 2, task_id: 6 },
      { scope: "batch", purchase_batch_id: 2, task_id: 16 },
    ]);
  });

  it("does not leak a deferred bulk preview into a newly opened dialog session", async () => {
    let releasePreview!: () => void;
    let markPreviewStarted!: () => void;
    const previewGate = new Promise<void>((resolve) => {
      releasePreview = resolve;
    });
    const previewStarted = new Promise<void>((resolve) => {
      markPreviewStarted = resolve;
    });
    server.use(
      http.post("/api/health/events/preview", async ({ request }) => {
        const target = (await request.json()) as Record<string, unknown>;
        markPreviewStarted();
        await previewGate;
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
          max_targets: 250,
        });
      }),
    );

    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], "BREEDING");
    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));
    await previewStarted;

    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    await user.click(screen.getByRole("button", { name: "+ Add event" }));
    const reopened = await screen.findByRole("dialog");
    await user.click(within(reopened).getByRole("radio", { name: "Whole bucket" }));
    await pickOption(user, within(reopened).getAllByRole("combobox")[0], "BREEDING");

    releasePreview();
    await waitFor(() =>
      expect(
        within(reopened).getByRole("button", { name: "Review target animals" }),
      ).toBeEnabled(),
    );
    expect(within(reopened).queryByRole("status")).not.toBeInTheDocument();
  });

  it("does not apply a deferred preview after the operator changes the bucket", async () => {
    let releasePreview!: () => void;
    let markPreviewStarted!: () => void;
    const previewGate = new Promise<void>((resolve) => {
      releasePreview = resolve;
    });
    const previewStarted = new Promise<void>((resolve) => {
      markPreviewStarted = resolve;
    });
    server.use(
      http.post("/api/health/events/preview", async ({ request }) => {
        const target = (await request.json()) as Record<string, unknown>;
        markPreviewStarted();
        await previewGate;
        return HttpResponse.json({
          scope: target.scope,
          bucket: target.bucket,
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
    const bucket = within(dialog).getByRole("combobox", { name: "Bucket *" });
    await pickOption(user, bucket, "BREEDING");
    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));
    await previewStarted;

    await pickOption(user, bucket, "FOUNDATION");
    releasePreview();
    await waitFor(() =>
      expect(
        within(dialog).getByRole("button", { name: "Review target animals" }),
      ).toBeEnabled(),
    );
    expect(within(dialog).queryByRole("status")).not.toBeInTheDocument();
  });

  it("surfaces a bulk-preview failure and leaves the target reviewable", async () => {
    server.use(
      http.post("/api/health/events/preview", () =>
        HttpResponse.json({ detail: "preview service unavailable" }, { status: 503 }),
      ),
    );
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Bucket *" }),
      "BREEDING",
    );
    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "preview service unavailable",
    );
    expect(within(dialog).getByRole("button", { name: "Review target animals" }))
      .toBeEnabled();
    expect(postBody).toBeNull();
  });

  it("requires a fresh review after the server rejects a stale bulk snapshot", async () => {
    let recordAttempts = 0;
    server.use(
      http.post("/api/health/events", async ({ request }) => {
        recordAttempts += 1;
        postBody = (await request.json()) as Record<string, unknown>;
        return recordAttempts === 1
          ? HttpResponse.json(
              { detail: "Bulk target membership changed; preview and confirm again" },
              { status: 409 },
            )
          : HttpResponse.json([makeEvent({ id: 100 })], { status: 201 });
      }),
    );
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], "BREEDING");

    await reviewAndConfirmBulk(user, dialog);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Bulk target membership changed",
    );
    expect(
      within(dialog).getByRole("button", { name: "Review target animals" }),
    ).toBeInTheDocument();
    expect(previewBodies).toHaveLength(1);

    await reviewAndConfirmBulk(user, dialog);
    await waitFor(() => expect(recordAttempts).toBe(2));
    expect(previewBodies).toHaveLength(2);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("sends a picked route and event type", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], /G-004/);
    const combos = () => within(dialog).getAllByRole("combobox");
    // Order: animal, type, route, linked duty.
    await pickOption(user, combos()[1], "DEWORMING");
    await pickOption(user, combos()[2], "IM");
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({ type: "DEWORMING", route: "IM" });
  });

  it("submits at most one event across same-render duplicate form events", async () => {
    let calls = 0;
    let releaseRecord!: () => void;
    const recordGate = new Promise<void>((resolve) => {
      releaseRecord = resolve;
    });
    server.use(
      http.post("/api/health/events", async ({ request }) => {
        calls += 1;
        postBody = (await request.json()) as Record<string, unknown>;
        await recordGate;
        return HttpResponse.json([makeEvent({ id: 100 })], { status: 201 });
      }),
    );
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], /G-003 · Kaveri/);
    const form = within(dialog).getByRole("button", { name: "Save event" }).closest("form")!;

    fireEvent.submit(form);
    fireEvent.submit(form);
    await waitFor(() => expect(calls).toBeGreaterThan(0));
    expect(within(dialog).getByLabelText(/disease target/i)).toBeDisabled();
    releaseRecord();

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(calls).toBe(1);
  });

  it("keeps the dialog open when the server rejects the event", async () => {
    server.use(
      http.post("/api/health/events", () =>
        HttpResponse.json({ detail: "animal not on this farm" }, { status: 404 }),
      ),
    );
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], /G-003 · Kaveri/);
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() => expect(eventsCalls).toBe(1));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(within(dialog).getByRole("alert")).toHaveTextContent("animal not on this farm");
    expect(within(dialog).getByRole("button", { name: "Retry save" })).toBeInTheDocument();
  });

  it("surfaces a generic write error after a network failure", async () => {
    server.use(http.post("/api/health/events", () => HttpResponse.error()));
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], /G-003 · Kaveri/);
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Could not save the health event.",
    );
    expect(dialog).toBeInTheDocument();
  });

  it("clears hidden statutory dates when scheduled-disease reporting is turned off", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByText("Advanced traceability & compliance"));
    const checkbox = within(dialog).getByRole("checkbox", {
      name: /Suspected scheduled\/notifiable disease/,
    });
    await user.click(checkbox);
    fireEvent.change(within(dialog).getByLabelText("Authority notified date"), {
      target: { value: TODAY },
    });
    fireEvent.change(within(dialog).getByLabelText("Isolation started date"), {
      target: { value: TODAY },
    });

    await user.click(checkbox);
    expect(within(dialog).queryByLabelText("Authority notified date")).not.toBeInTheDocument();
    await user.click(checkbox);
    expect(within(dialog).getByLabelText("Authority notified date")).toHaveValue("");
    expect(within(dialog).getByLabelText("Isolation started date")).toHaveValue("");
  });

  // ---------- linked duty ----------

  it("offers only pending VACCINE/DEWORMING duties as linked duties", async () => {
    const { user, dialog } = await openDialog();
    const dutySelect = within(dialog).getByLabelText("Linked duty (completes it)");
    await user.click(dutySelect);
    const options = await screen.findAllByRole("option");
    const names = options.map((o) => o.textContent ?? "");
    expect(names.some((n) => n.includes("PPR vaccination"))).toBe(true);
    expect(names.some((n) => n.includes("Deworm batch #2"))).toBe(true);
    expect(names.some((n) => n.includes("Scrub feeders"))).toBe(false);
  });

  // REGRESSION — the picker listed the tasks page's "upcoming" bucket too, but
  // api/health.py returns 409 for an event dated before the duty's due date and
  // the event date can never be in the future, so every future duty in the list
  // was a guaranteed dead end.
  it("omits duties that are not due yet from the linked-duty picker", async () => {
    tasks = [
      VACCINE_TASK,
      makeTask({
        id: 9,
        title: "Pre-kidding ET+TT vaccine",
        category: "VACCINE",
        animal_id: 3,
        due_date: addDays(TODAY, 5),
      }),
    ];
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByLabelText("Linked duty (completes it)"));

    expect(await screen.findByRole("option", { name: /PPR vaccination/ })).toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: /Pre-kidding ET\+TT vaccine/ }),
    ).not.toBeInTheDocument();
  });

  // REGRESSION — /api/tasks returns a bounded window, so a deep-linked duty
  // outside it silently left an unlabeled id in the select with none of the
  // scope/type/product prefill applied.
  it("drops an unresolvable deep-linked duty and says so", async () => {
    window.history.replaceState({}, "", "/health?task_id=734&animal_id=3");
    tasks = [VACCINE_TASK];
    renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog");

    expect(
      await within(dialog).findByText(/Could not link duty #734/),
    ).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Linked duty (completes it)")).toHaveTextContent(
      "— none —",
    );

    await userEvent.setup().click(within(dialog).getByRole("button", { name: "Save event" }));
    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({ animal_id: 3 });
    expect(postBody!.task_id ?? null).toBeNull();
  });

  it("resolves a deep-linked duty that is outside the bounded task tabs", async () => {
    const exactTask = makeTask({
      id: 734,
      title: "PPR vaccination outside current tab window",
      category: "VACCINE",
      animal_id: 3,
    });
    window.history.replaceState({}, "", "/health?task_id=734");
    tasks = [VACCINE_TASK];
    server.use(
      http.get("/api/tasks/734", () => HttpResponse.json(exactTask)),
    );

    renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog");

    expect(await within(dialog).findByLabelText(/Linked duty/)).toHaveTextContent(
      "PPR vaccination outside current tab window",
    );
    expect(within(dialog).getByRole("radio", { name: "Single animal" })).toBeChecked();
    await userEvent.setup().click(within(dialog).getByRole("button", { name: "Save event" }));
    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({ animal_id: 3, type: "VACCINE", task_id: 734 });
  });

  it("hides the linked-duty select when no health duties are pending", async () => {
    tasks = [];
    const { dialog } = await openDialog();
    expect(within(dialog).queryByText(/Linked duty/)).not.toBeInTheDocument();
  });

  it("hides the linked-duty select without tasks.view", async () => {
    server.use(permissionsHandler(["health.view", "health.manage"]));
    const { dialog } = await openDialog();
    expect(within(dialog).queryByText(/Linked duty/)).not.toBeInTheDocument();
  });

  it("announces a linked-duty query failure and retries it", async () => {
    let attempts = 0;
    server.use(
      http.get("/api/tasks", () => {
        attempts += 1;
        return attempts === 1
          ? HttpResponse.json({ detail: "linked duties unavailable" }, { status: 503 })
          : HttpResponse.json(tasksPayload(tasks));
      }),
    );
    const { user, dialog } = await openDialog();

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "linked duties unavailable",
    );
    await user.click(within(dialog).getByRole("button", { name: "Retry linked duties" }));
    await waitFor(() => expect(attempts).toBe(2));
    expect(await within(dialog).findByLabelText(/Linked duty/)).toBeInTheDocument();
  });

  it("prefills scope, animal and type when an animal duty is linked", async () => {
    tasks = [makeTask({ id: 8, title: "Deworm Kaveri", category: "DEWORMING", animal_id: 3 })];
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getByLabelText("Linked duty (completes it)"), /Deworm Kaveri/);

    expect(within(dialog).getByRole("radio", { name: "Single animal" })).toBeChecked();
    // Type switched to the duty's category.
    expect(within(dialog).getAllByRole("combobox")[1]).toHaveTextContent("DEWORMING");

    await user.click(within(dialog).getByRole("button", { name: "Save event" }));
    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({ animal_id: 3, type: "DEWORMING", task_id: 8 });
  });

  it("prefills the batch scope when a batch duty is linked", async () => {
    tasks = [DEWORM_BATCH_TASK];
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getByLabelText("Linked duty (completes it)"), /Deworm batch #2/);

    expect(within(dialog).getByRole("radio", { name: "Purchase batch" })).toBeChecked();
    await reviewAndConfirmBulk(user, dialog);
    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({
      scope: "batch",
      purchase_batch_id: 2,
      type: "DEWORMING",
      task_id: 6,
    });
    expect(previewBodies).toEqual([
      { scope: "batch", purchase_batch_id: 2, task_id: 6 },
    ]);
  });

  it("unlinks an animal duty and reverts its prefills when the animal changes", async () => {
    tasks = [
      makeTask({
        id: 8,
        title: "Deworm Kaveri",
        category: "DEWORMING",
        animal_id: 3,
      }),
    ];
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getByLabelText(/Linked duty/), /Deworm Kaveri/);

    expect(within(dialog).getByLabelText(/Linked duty/)).toHaveTextContent("Deworm Kaveri");
    expect(within(dialog).getAllByRole("combobox")[1]).toHaveTextContent("DEWORMING");
    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Animal *" }),
      /G-004/,
    );

    expect(within(dialog).getByLabelText(/Linked duty/)).toHaveTextContent("— none —");
    expect(within(dialog).getAllByRole("combobox")[1]).toHaveTextContent("VACCINE");
    expect(within(dialog).getByLabelText(/disease target/i)).toHaveValue("");
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({ animal_id: 4, type: "VACCINE", task_id: null });
  });

  it("unlinks a batch duty and reverts its prefills when the batch changes", async () => {
    tasks = [DEWORM_BATCH_TASK];
    server.use(
      http.get("/api/health/purchase-batches", () =>
        HttpResponse.json({
          batches: [
            { id: 2, active_quarantine_animal_count: 12 },
            { id: 3, active_quarantine_animal_count: 7 },
          ],
          total: 2,
          limit: 50,
          offset: 0,
        }),
      ),
    );
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getByLabelText(/Linked duty/), /Deworm batch #2/);
    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Purchase batch *" }),
      /Batch #3/,
    );

    expect(within(dialog).getByLabelText(/Linked duty/)).toHaveTextContent("— none —");
    expect(within(dialog).getAllByRole("combobox")[1]).toHaveTextContent("VACCINE");
    expect(within(dialog).getByLabelText(/disease target/i)).toHaveValue("");
    await reviewAndConfirmBulk(user, dialog);

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({
      scope: "batch",
      purchase_batch_id: 3,
      type: "VACCINE",
      task_id: null,
    });
    expect(previewBodies).toEqual([{ scope: "batch", purchase_batch_id: 3 }]);
  });

  it("forgets duty-prefill metadata when a dialog is closed and reopened", async () => {
    tasks = [VACCINE_TASK];
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getByLabelText(/Linked duty/), /PPR vaccination/);
    expect(within(dialog).getByLabelText(/disease target/i)).toHaveValue(
      "PPR vaccination",
    );

    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "+ Add event" }));
    const reopened = await screen.findByRole("dialog");

    // This happens to equal the previous duty's inferred value, but it belongs
    // to a fresh form and must be treated as a manual edit rather than stale
    // prefill metadata when the target changes.
    fireEvent.change(within(reopened).getByLabelText(/disease target/i), {
      target: { value: "PPR vaccination" },
    });
    await pickOption(
      user,
      within(reopened).getByRole("combobox", { name: "Animal *" }),
      /G-004/,
    );

    expect(within(reopened).getByLabelText(/disease target/i)).toHaveValue(
      "PPR vaccination",
    );
    expect(within(reopened).getByLabelText(/Linked duty/)).toHaveTextContent("— none —");
  });

  it("prefills the disease target from a vaccine duty's title", async () => {
    tasks = [
      makeTask({
        id: 9,
        title: "[Sharma Traders #2] Day 10: vaccinate PPR (live viral, SC)",
        category: "VACCINE",
        purchase_batch_id: 2,
      }),
    ];
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getByLabelText("Linked duty (completes it)"), /vaccinate PPR/);

    expect(within(dialog).getByLabelText(/disease target/i)).toHaveValue("PPR");

    await reviewAndConfirmBulk(user, dialog);
    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({
      scope: "batch",
      type: "VACCINE",
      disease_target: "PPR",
      task_id: 9,
    });
  });

  it("prefills product and target from a deworming duty's title", async () => {
    tasks = [
      makeTask({
        id: 10,
        title: "[Sharma Traders #2] Day 4: deworm — Albendazole/Closantel oral + Ivermectin SC",
        category: "DEWORMING",
        purchase_batch_id: 2,
      }),
    ];
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getByLabelText("Linked duty (completes it)"), /deworm/);

    expect(within(dialog).getByLabelText(/product name/i)).toHaveValue(
      "Albendazole/Closantel oral + Ivermectin SC",
    );
    expect(within(dialog).getByLabelText(/disease target/i)).toHaveValue("Deworming");

    await reviewAndConfirmBulk(user, dialog);
    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({
      type: "DEWORMING",
      product_name: "Albendazole/Closantel oral + Ivermectin SC",
      disease_target: "Deworming",
      task_id: 10,
    });
  });

  it("switching the linked duty back to '— none —' reverts the prefills", async () => {
    tasks = [DEWORM_BATCH_TASK];
    const { user, dialog } = await openDialog();
    const dutySelect = within(dialog).getByLabelText("Linked duty (completes it)");
    await pickOption(user, dutySelect, /Deworm batch #2/);

    expect(within(dialog).getByRole("radio", { name: "Purchase batch" })).toBeChecked();
    expect(within(dialog).getAllByRole("combobox")[1]).toHaveTextContent("DEWORMING");
    expect(within(dialog).getByLabelText(/disease target/i)).toHaveValue("Deworming");

    await pickOption(user, dutySelect, /none/);

    expect(within(dialog).getByRole("radio", { name: "Single animal" })).toBeChecked();
    expect(within(dialog).getAllByRole("combobox")[1]).toHaveTextContent("VACCINE");
    expect(within(dialog).getByLabelText(/disease target/i)).toHaveValue("");
  });

  // ---------- URL prefill (/health/new?task_id=…) ----------

  it("auto-opens the dialog prefilled from ?task_id and ?animal_id", async () => {
    window.history.replaceState({}, "", "/health?task_id=5&animal_id=3");
    renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog");

    expect(within(dialog).getByRole("radio", { name: "Single animal" })).toBeChecked();
    await userEvent
      .setup()
      .click(within(dialog).getByRole("button", { name: "Save event" }));
    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({ animal_id: 3, task_id: 5, type: "VACCINE" });
  });

  it("auto-opens and prefills an animal even when there is no linked task", async () => {
    window.history.replaceState({}, "", "/health?animal_id=3");
    renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog");

    expect(within(dialog).getByRole("radio", { name: "Single animal" })).toBeChecked();
    await waitFor(() =>
      expect(within(dialog).getAllByRole("combobox")[0]).toHaveTextContent("G-003 · Kaveri"),
    );
  });

  it("hydrates a new deep link when Next reuses the health page", async () => {
    window.history.replaceState({}, "", "/health?animal_id=3");
    const view = renderWithProviders(<HealthPage />);
    const firstDialog = await screen.findByRole("dialog");
    await waitFor(() =>
      expect(within(firstDialog).getAllByRole("combobox")[0]).toHaveTextContent(
        "G-003 · Kaveri",
      ),
    );

    await userEvent
      .setup()
      .click(within(firstDialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    window.history.replaceState({}, "", "/health");
    view.rerender(<HealthPage />);
    window.history.replaceState({}, "", "/health?animal_id=4");
    view.rerender(<HealthPage />);

    const nextDialog = await screen.findByRole("dialog");
    await waitFor(() =>
      expect(within(nextDialog).getAllByRole("combobox")[0]).toHaveTextContent("G-004"),
    );
  });

  it("does not let an unresolved task prefill consume a newer cached deep link", async () => {
    const firstTask = makeTask({
      id: 21,
      title: "First deferred vaccine",
      category: "VACCINE",
      animal_id: 3,
    });
    const secondTask = makeTask({
      id: 22,
      title: "Second cached vaccine",
      category: "VACCINE",
      animal_id: 4,
    });
    let releaseFirst!: () => void;
    let markFirstStarted!: () => void;
    const firstGate = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });
    const firstStarted = new Promise<void>((resolve) => {
      markFirstStarted = resolve;
    });
    tasks = [];
    server.use(
      http.get("/api/tasks/21", async () => {
        markFirstStarted();
        await firstGate;
        return HttpResponse.json(firstTask);
      }),
      http.get("/api/tasks/22", () => HttpResponse.json(secondTask)),
    );

    // Resolve task 22 once so the query-only return to it can settle from the
    // cache in the same effect flush that hydrates its URL.
    window.history.replaceState({}, "", "/health?task_id=22");
    const view = renderWithProviders(<HealthPage />);
    let dialog = await screen.findByRole("dialog");
    await waitFor(() =>
      expect(within(dialog).getByRole("combobox", { name: "Animal *" })).toHaveTextContent(
        "G-004",
      ),
    );
    await userEvent.setup().click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    window.history.replaceState({}, "", "/health");
    view.rerender(<HealthPage />);

    window.history.replaceState({}, "", "/health?task_id=21");
    view.rerender(<HealthPage />);
    await firstStarted;
    dialog = await screen.findByRole("dialog");

    window.history.replaceState({}, "", "/health?task_id=22");
    view.rerender(<HealthPage />);

    await waitFor(() =>
      expect(within(dialog).getByRole("combobox", { name: "Animal *" })).toHaveTextContent(
        "G-004",
      ),
    );
    expect(within(dialog).getByLabelText(/Linked duty/)).toHaveTextContent(
      "Second cached vaccine",
    );
    releaseFirst();
  });

  it("does not apply a late exact-duty lookup after the operator retargets the event", async () => {
    const exactTask = makeTask({
      id: 734,
      title: "Late PPR duty",
      category: "VACCINE",
      animal_id: 3,
    });
    let releaseLookup!: () => void;
    let markLookupStarted!: () => void;
    const lookupGate = new Promise<void>((resolve) => {
      releaseLookup = resolve;
    });
    const lookupStarted = new Promise<void>((resolve) => {
      markLookupStarted = resolve;
    });
    window.history.replaceState({}, "", "/health?task_id=734&animal_id=3");
    server.use(
      http.get("/api/tasks/734", async () => {
        markLookupStarted();
        await lookupGate;
        return HttpResponse.json(exactTask);
      }),
    );
    renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog");
    await lookupStarted;
    const user = userEvent.setup();
    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Animal *" }),
      /G-004/,
    );

    releaseLookup();
    await waitFor(() =>
      expect(within(dialog).getByLabelText(/Linked duty/)).toHaveTextContent("— none —"),
    );
    expect(within(dialog).getByRole("combobox", { name: "Animal *" }))
      .toHaveTextContent("G-004");
    expect(within(dialog).queryByText(/Could not link duty/)).not.toBeInTheDocument();
  });

  it.each(["0", "1e2", "9007199254740992", "not-an-id"])(
    "ignores the malformed animal deep-link id %s instead of coercing a target",
    async (animalId) => {
      window.history.replaceState({}, "", `/health?animal_id=${animalId}`);
      await renderLoaded();

      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(postBody).toBeNull();
    },
  );

  it("returns to the originating task tab after a deep-linked event is saved", async () => {
    window.history.replaceState(
      {},
      "",
      "/health?animal_id=3&returnTo=%2Ftasks%3Ftab%3Doverdue",
    );
    renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog");

    await userEvent.setup().click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(pushMock).toHaveBeenCalledWith("/tasks?tab=overdue");
  });

  it("returns to the originating page when a deep-linked dialog is dismissed", async () => {
    window.history.replaceState(
      {},
      "",
      "/health?animal_id=3&returnTo=%2Ftasks%3Ftab%3Doverdue",
    );
    renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog");

    await userEvent.setup().click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/tasks?tab=overdue"));
  });

  it("does not let an old-farm event completion navigate the newly selected farm", async () => {
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
    window.history.replaceState(
      {},
      "",
      "/health?animal_id=3&returnTo=%2Ftasks%3Ftab%3Doverdue",
    );
    renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog");
    const user = userEvent.setup();
    const saveButton = within(dialog).getByRole("button", { name: "Save event" });

    await user.click(saveButton);
    await writeStarted;
    // AuthProvider performs this synchronously before React commits the
    // keyed farm subtree's unmount, which is the narrow race being pinned.
    act(() => setCurrentFarmId("2"));
    await act(async () => {
      releaseWrite();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitFor(() => expect(saveButton).toBeEnabled());

    expect(pushMock).not.toHaveBeenCalled();
    expect(replaceMock).not.toHaveBeenCalled();
    setCurrentFarmId(null);
  });

  it("does not auto-open the dialog from URL params without health.manage", async () => {
    server.use(permissionsHandler(["health.view"]));
    window.history.replaceState({}, "", "/health?task_id=5&animal_id=3");
    await renderLoaded();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("offers only seeded programme names for a vaccine event's schedule", async () => {
    // validated_template accepts an exact vaccine_templates.name and nothing
    // else, while a next-due date REQUIRES a schedule name. The field used to
    // be free text with no hint and no endpoint exposing the seeded names, so
    // every value an operator could invent returned 422 "Unknown schedule
    // template" and the only escape was to delete the next-due date.
    const user = userEvent.setup();
    renderWithProviders(<HealthPage />);
    await screen.findByText("Event log");
    await user.click(screen.getByRole("button", { name: "+ Add event" }));
    const dialog = await screen.findByRole("dialog");

    await pickOption(user, within(dialog).getAllByRole("combobox")[0], /G-003 · Kaveri/);
    await user.click(within(dialog).getByText("Advanced traceability & compliance"));
    fireEvent.change(within(dialog).getByLabelText("Next due date"), {
      target: { value: addDays(TODAY, 30) },
    });

    // Type defaults to VACCINE: the vaccine programmes are offered, and the
    // DEWORMING-only programme is not.
    await user.click(within(dialog).getByLabelText("Schedule/template name"));
    expect(await screen.findByRole("option", { name: /^FMD/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /^PPR/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /^Deworming/ })).not.toBeInTheDocument();
    await user.keyboard("{Escape}");
  });

});
