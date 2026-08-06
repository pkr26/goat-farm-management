/**
 * Health page: event-log rendering (₹ cost formatting, batch/animal/fallback
 * target cells, dashes), the per-animal schedule picker navigation, RBAC
 * gating (health.view / health.manage / tasks.view), and the Add-event
 * dialog — scope switching (animal/bucket/batch) with per-scope zod
 * validation, future-date and cost rules, linked-duty prefill from
 * VACCINE/DEWORMING tasks, URL ?task_id prefill, payload mapping
 * (NONE sentinel → null, trimmed strings, numeric cost) and server errors.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { AnimalOut, HealthEventOut, TaskOut } from "@/api/generated/models";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import HealthPage from "./page";

const pushMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/health",
  useSearchParams: () => new URLSearchParams(),
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

/** Local YYYY-MM-DD (mirrors the page's localToday). */
function localISO(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}
const TODAY = localISO(new Date());
const TOMORROW = localISO(new Date(Date.now() + 86_400_000));

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
    completed_by_id: null,
    completed_at: null,
    verified_by_id: null,
    verified_at: null,
    verification_note: null,
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
  return { today: tasks, overdue: [], upcoming: [], awaiting: [], completed: [] };
}

async function pickOption(user: User, trigger: HTMLElement, name: string | RegExp) {
  await user.click(trigger);
  await user.click(await screen.findByRole("option", { name }));
}

describe("HealthPage", () => {
  let eventsCalls: number;
  let postBody: Record<string, unknown> | null;
  let events: HealthEventOut[];
  let tasks: TaskOut[];

  beforeEach(() => {
    pushMock.mockClear();
    eventsCalls = 0;
    postBody = null;
    events = [makeEvent({ id: 1 })];
    tasks = [VACCINE_TASK, DEWORM_BATCH_TASK, OTHER_TASK];
    server.use(
      http.get("/api/health/events", () => {
        eventsCalls += 1;
        return HttpResponse.json(events);
      }),
      http.post("/api/health/events", async ({ request }) => {
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(makeEvent({ id: 99 }), { status: 201 });
      }),
      http.get("/api/animals", () =>
        HttpResponse.json({ animals: [ANIMAL_A, ANIMAL_B], total: 2 }),
      ),
      http.get("/api/purchases", () => HttpResponse.json(BATCHES)),
      http.get("/api/tasks", () => HttpResponse.json(tasksPayload(tasks))),
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
      "/animals/3",
    );
    expect(within(row).getByText("PPR")).toBeInTheDocument();
    expect(within(row).getByText("1 ml")).toBeInTheDocument();
    expect(within(row).getByText("SC")).toBeInTheDocument();
    expect(within(row).getByText("₹1,250.50")).toBeInTheDocument();
    expect(within(row).getByText("15 Jul 2027")).toBeInTheDocument();
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
    expect(within(row).getAllByText("—")).toHaveLength(6);
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
    server.use(
      http.get("/api/health/events", () =>
        HttpResponse.json({ detail: "events table missing" }, { status: 500 }),
      ),
    );
    renderWithProviders(<HealthPage />);
    expect(await screen.findByText("events table missing")).toBeInTheDocument();
  });

  // ---------- RBAC ----------

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
    expect(pushMock).toHaveBeenCalledWith("/health/schedule/3");
  });

  // ---------- dialog: scope switching & validation ----------

  async function openDialog() {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "+ Add event" }));
    return { user, dialog: await screen.findByRole("dialog") };
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
      await screen.findByRole("option", { name: /#2 — 1 Jun 2026 Sharma Traders \(12\)/ }),
    ).toBeInTheDocument();
  });

  it("requires an animal on the animal scope", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));
    // Error message appears alongside the select's placeholder text.
    await waitFor(() =>
      expect(within(dialog).getAllByText("Pick an animal").length).toBeGreaterThanOrEqual(2),
    );
    expect(postBody).toBeNull();
  });

  it("requires a bucket on the bucket scope", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));
    await waitFor(() =>
      expect(within(dialog).getAllByText("Pick a bucket").length).toBeGreaterThanOrEqual(2),
    );
    expect(postBody).toBeNull();
  });

  it("requires a batch on the batch scope", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Purchase batch" }));
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));
    await waitFor(() =>
      expect(within(dialog).getAllByText("Pick a batch").length).toBeGreaterThanOrEqual(2),
    );
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
      notes: null,
      task_id: null,
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(eventsCalls).toBeGreaterThanOrEqual(2));
  });

  it("posts a bucket-scoped event with the other targets nulled", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], "BREEDING");
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({
      scope: "bucket",
      bucket: "BREEDING",
      animal_id: null,
      purchase_batch_id: null,
    });
  });

  it("posts a batch-scoped event with the batch id", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Purchase batch" }));
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], /#2 — 1 Jun 2026/);
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({
      scope: "batch",
      purchase_batch_id: 2,
      animal_id: null,
      bucket: null,
    });
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
  });

  // ---------- linked duty ----------

  it("offers only pending VACCINE/DEWORMING duties as linked duties", async () => {
    const { user, dialog } = await openDialog();
    const combos = within(dialog).getAllByRole("combobox");
    const dutySelect = combos[combos.length - 1];
    await user.click(dutySelect);
    const options = await screen.findAllByRole("option");
    const names = options.map((o) => o.textContent ?? "");
    expect(names.some((n) => n.includes("PPR vaccination"))).toBe(true);
    expect(names.some((n) => n.includes("Deworm batch #2"))).toBe(true);
    expect(names.some((n) => n.includes("Scrub feeders"))).toBe(false);
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

  it("prefills scope, animal and type when an animal duty is linked", async () => {
    tasks = [makeTask({ id: 8, title: "Deworm Kaveri", category: "DEWORMING", animal_id: 3 })];
    const { user, dialog } = await openDialog();
    const combos = within(dialog).getAllByRole("combobox");
    await pickOption(user, combos[combos.length - 1], /Deworm Kaveri/);

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
    const combos = within(dialog).getAllByRole("combobox");
    await pickOption(user, combos[combos.length - 1], /Deworm batch #2/);

    expect(within(dialog).getByRole("radio", { name: "Purchase batch" })).toBeChecked();
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));
    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({
      scope: "batch",
      purchase_batch_id: 2,
      type: "DEWORMING",
      task_id: 6,
    });
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

  it("does not auto-open the dialog from URL params without health.manage", async () => {
    server.use(permissionsHandler(["health.view"]));
    window.history.replaceState({}, "", "/health?task_id=5&animal_id=3");
    await renderLoaded();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
