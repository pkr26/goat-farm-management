/**
 * Mutation-hardening for the health page: the branches the main suites
 * reach only incidentally — the event-log fallback labels, the mobile card
 * layout next to the desktop table, offset/URL-state pagination (pending
 * bridge, stale-offset re-home, placeholder pages), deep-link signature
 * re-hydration per URL segment, dialog guard rails (scope re-validation,
 * reviewed-snapshot ownership, duty prefill revert), the permissions
 * dead-end retry, the empty-state CTA gate, and strict null mapping in the
 * recorded payload.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { AnimalOut, HealthEventOut, TaskOut } from "@/api/generated/models";
import { toast } from "sonner";
import { permissionsHandler, server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { addDays, farmToday } from "@/lib/format";

import HealthPage from "./page";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

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
    current_bucket: "BREEDING",
    status: "ACTIVE",
    status_date: null,
    sale_price: null,
    sale_weight_kg: null,
    buyer_name: null,
    mortality_cause_code: null,
    disposal_method: null,
    necropsy_done: false,
    necropsy_findings: null,
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
const FOLLOWUP_TASK = makeTask({
  id: 6,
  title: "Follow-up PPR vaccination",
  category: "VACCINE",
  animal_id: 4,
});
const DEWORM_ANIMAL_TASK = makeTask({
  id: 8,
  title: "Deworm — Levamisole",
  category: "DEWORMING",
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

describe("HealthPage (mutation hardening)", () => {
  let events: HealthEventOut[];
  let tasks: TaskOut[];
  let postBody: Record<string, unknown> | null;
  let requestedOffsets: number[];

  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
    events = [makeEvent({ id: 1 })];
    tasks = [VACCINE_TASK];
    postBody = null;
    requestedOffsets = [];
    server.use(
      http.get("/api/health/events", ({ request }) => {
        const offset = Number(new URL(request.url).searchParams.get("offset") ?? 0);
        requestedOffsets.push(offset);
        return HttpResponse.json({
          events: offset === 0 ? events.slice(0, 50) : [],
          total: Math.max(events.length, offset + 1),
          limit: 50,
          offset,
        });
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
          max_targets: 250,
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
          batches: [
            { id: 2, active_quarantine_animal_count: 12 },
            { id: 3, active_quarantine_animal_count: 7 },
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
    window.history.replaceState({}, "", "/health");
  });

  /** Total fixed at `total`; one page of rows per offset. */
  function usePagedEvents(pages: HealthEventOut[][], total: number) {
    server.use(
      http.get("/api/health/events", ({ request }) => {
        const offset = Number(new URL(request.url).searchParams.get("offset") ?? 0);
        requestedOffsets.push(offset);
        const page = pages[offset / 50] ?? [];
        return HttpResponse.json({ events: page, total, limit: 50, offset });
      }),
    );
  }

  async function renderLoaded() {
    const view = renderWithProviders(<HealthPage />);
    await screen.findByText("Event log");
    return view;
  }

  async function openDialog() {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Add event" }));
    return { user, dialog: await screen.findByRole("dialog") };
  }

  // ---------- event log fallback labels ----------

  it("keeps the animal's tag for a module-only manager without animals.view", async () => {
    events = [makeEvent({ id: 1, animal_tag: "G-003", animal_id: 3 })];
    server.use(permissionsHandler(["health.view", "health.manage"]));
    await renderLoaded();

    const row = within(screen.getByRole("table")).getByText("G-003").closest("tr")!;
    expect(row).not.toBeNull();
    // The tag is shown as plain text, never demoted to "#3".
    expect(within(row).queryByText("#3")).not.toBeInTheDocument();
    expect(within(row).queryByRole("link")).not.toBeInTheDocument();
  });

  it("labels a fully untargeted row as bucket-wide, not '#null'", async () => {
    events = [makeEvent({ id: 2, animal_tag: null, animal_id: null, purchase_batch_id: null })];
    await renderLoaded();

    expect(screen.getAllByText("bucket-wide")).toHaveLength(2); // desktop + mobile
  });

  // ---------- mobile card layout beside the desktop table ----------

  it("renders every traceability field on the mobile card with its own wording", async () => {
    events = [
      makeEvent({
        id: 11,
        product_name: "PPR vaccine",
        disease_target: null,
        dose: null,
        route: null,
        product_lot: "LOT-77",
        product_expires_on: "2027-03-01",
        certificate_number: "CERT-9",
        withdrawal_until: "2026-08-01",
        next_due_date: addDays(TODAY, 30),
        schedule_template_name: "Annual PPR programme",
        next_due_authority: "Farm veterinarian",
        suspected_scheduled_disease: true,
      }),
      makeEvent({
        id: 12,
        product_name: null,
        disease_target: null,
        dose: null,
        route: null,
        product_lot: null,
        product_expires_on: null,
        certificate_number: null,
        withdrawal_until: null,
        next_due_date: null,
        animal_tag: null,
        animal_id: null,
        purchase_batch_id: null,
      }),
      makeEvent({ id: 13, product_expires_on: "2027-03-01", next_due_date: null }),
      makeEvent({ id: 14, product_lot: "LOT-88", next_due_date: null }),
    ];
    const { container } = await renderLoaded();

    const mobile = container.querySelector("div[class~='md:hidden']") as HTMLElement;
    const desktop = container.querySelector("div[class~='md:block']") as HTMLElement;
    expect(mobile).not.toBeNull();
    expect(desktop).not.toBeNull();

    // Both layouts label the type with the enum label, not the raw code.
    expect(within(mobile).getAllByText("Vaccination")).toHaveLength(4);
    expect(within(desktop).getAllByText("Vaccination")).toHaveLength(4);

    const cardOf = (text: string | RegExp) =>
      within(mobile)
        .getByText(text)
        .closest("[class~='rounded-xl']") as HTMLElement;

    const rich = cardOf(/Milk hold until/);
    expect(within(rich).getByText("PPR vaccine")).toBeInTheDocument();
    expect(within(rich).getByText(/Milk hold until 1 Aug 2026/)).toBeInTheDocument();
    expect(within(rich).getByText("Lot LOT-77 · Expires 1 Mar 2027")).toBeInTheDocument();
    expect(within(rich).getByText("Certificate CERT-9")).toBeInTheDocument();
    expect(
      within(rich).getByText("Annual PPR programme · Farm veterinarian"),
    ).toBeInTheDocument();
    // The desktop table keeps its own colon'd wording for the same fields.
    expect(within(desktop).getByText("Certificate: CERT-9")).toBeInTheDocument();
    expect(screen.getAllByText("Scheduled disease suspected")).toHaveLength(2);

    // The bare card says so and renders no empty field paragraphs.
    const bare = within(mobile)
      .getAllByText("No next due date")[0]
      .closest("[class~='rounded-xl']") as HTMLElement;
    expect(within(bare).queryByText(/Milk hold until/)).not.toBeInTheDocument();
    expect(within(bare).queryByText(/Certificate/)).not.toBeInTheDocument();
    expect(bare.querySelectorAll("p.mt-0\\.5")).toHaveLength(0);

    // A card with only one of lot/expiry prints exactly that one line.
    expect(within(cardOf("Expires 1 Mar 2027")).getByText("Expires 1 Mar 2027")).toBeInTheDocument();
    expect(within(cardOf("Lot LOT-88")).getByText("Lot LOT-88")).toBeInTheDocument();
  });

  it("prints an em dash in the notes column of an empty event", async () => {
    events = [
      makeEvent({
        id: 21,
        product_name: null,
        disease_target: null,
        dose: null,
        route: null,
        cost: null,
        next_due_date: null,
        notes: null,
        animal_tag: null,
        animal_id: null,
        purchase_batch_id: null,
      }),
    ];
    await renderLoaded();

    const row = within(screen.getByRole("table")).getAllByText("Vaccination")[0].closest("tr")!;
    // product, target, dose, route, cost, next due, notes, traceability.
    expect(within(row).getAllByText("—")).toHaveLength(8);
  });

  // ---------- offset/URL-state pagination ----------

  it("commits page turns to the URL and drops the offset back on page one", async () => {
    usePagedEvents([[makeEvent({ id: 1, product_name: "Alpha dose" })], []], 120);
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith("/health?offset=50", { scroll: false }),
    );

    const previous = screen.getByRole("button", { name: "Previous" });
    await waitFor(() => expect(previous).toBeEnabled());
    await user.click(previous);
    // Returning to the first page strips ?offset=0 from the URL entirely.
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/health", { scroll: false }));
  });

  it("bridges the offset while the URL write has not committed", async () => {
    usePagedEvents([[makeEvent({ id: 1, product_name: "Alpha dose" })], []], 120);
    const user = userEvent.setup();
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText("Showing 51–100 of 120 health events");
    const next = screen.getByRole("button", { name: "Next" });
    await waitFor(() => expect(next).toBeEnabled());
    await user.click(next);

    // The second turn computes from the pending offset (50 → 100), not from
    // the still-uncommitted URL offset (0 → 50 again).
    await waitFor(() => expect(requestedOffsets).toEqual([0, 50, 100]));
  });

  it("re-homes a stale offset that sits beyond the last page", async () => {
    usePagedEvents([[makeEvent({ id: 1, product_name: "Solo dose" })], []], 50);
    window.history.replaceState({}, "", "/health?offset=50");
    await renderLoaded();

    // The out-of-range page is discarded: the operator lands on page one and
    // the stale ?offset=50 is stripped from the URL.
    expect(await screen.findAllByText("Solo dose")).toHaveLength(2); // desktop + mobile
    expect(screen.getByText("Showing 1–50 of 50 health events")).toBeInTheDocument();
    expect(replaceMock).toHaveBeenCalledWith("/health", { scroll: false });
    expect(requestedOffsets).toEqual([50, 0]);
  });

  it("keeps the current page mounted and announces the settling next page", async () => {
    let releasePageTwo!: () => void;
    const gate = new Promise<void>((resolve) => {
      releasePageTwo = resolve;
    });
    server.use(
      http.get("/api/health/events", async ({ request }) => {
        const offset = Number(new URL(request.url).searchParams.get("offset") ?? 0);
        requestedOffsets.push(offset);
        if (offset === 50) await gate;
        return HttpResponse.json({
          events:
            offset === 0
              ? [makeEvent({ id: 1, product_name: "Alpha dose" })]
              : [makeEvent({ id: 2, product_name: "Beta dose" })],
          total: 120,
          limit: 50,
          offset,
        });
      }),
    );
    const user = userEvent.setup();
    await renderLoaded();
    await within(screen.getByRole("table")).findByText("Alpha dose");
    expect(screen.queryByText("Updating health events…")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Next" }));
    // The old rows stay rendered and the settling state is announced.
    expect(await screen.findByText("Updating health events…")).toBeInTheDocument();
    expect(within(screen.getByRole("table")).getByText("Alpha dose")).toBeInTheDocument();

    releasePageTwo();
    await within(screen.getByRole("table")).findByText("Beta dose");
  });

  it("clears the pending offset when the URL moves underneath it", async () => {
    usePagedEvents(
      [
        [makeEvent({ id: 1, product_name: "Alpha dose" })],
        [makeEvent({ id: 2, product_name: "Beta dose" })],
      ],
      120,
    );
    const user = userEvent.setup();
    const view = await renderLoaded();

    await user.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText("Showing 51–100 of 120 health events");

    // A back/forward-style navigation rewinds the URL: the URL wins again.
    window.history.replaceState({}, "", "/health?offset=0");
    view.rerender(<HealthPage />);

    await screen.findByText("Showing 1–50 of 120 health events");
    await within(screen.getByRole("table")).findByText("Alpha dose");
  });

  // ---------- dialog guard rails ----------

  it("starts with the advanced compliance section collapsed", async () => {
    const { dialog } = await openDialog();
    const details = within(dialog)
      .getByText("Advanced traceability & compliance")
      .closest("details");
    expect(details).not.toBeNull();
    expect(details).not.toHaveAttribute("open");
  });

  it("drops the reviewed snapshot when the target scope changes", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    await pickOption(user, within(dialog).getByRole("combobox", { name: "Bucket *" }), "Pregnancy A");
    // The closed trigger carries the seeded label, not the raw bucket code.
    expect(within(dialog).getByRole("combobox", { name: "Bucket *" })).toHaveTextContent(
      "Pregnancy A",
    );

    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));
    expect(await within(dialog).findByText(/Reviewed target snapshot/)).toBeInTheDocument();

    await user.click(within(dialog).getByRole("radio", { name: "Purchase batch" }));
    // A batch scope must review its own animals — the bucket snapshot is gone.
    expect(within(dialog).queryByText(/Reviewed target snapshot/)).not.toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "Review target animals" }),
    ).toBeInTheDocument();
    expect(
      within(dialog).queryByRole("button", { name: "Confirm for 2 animals" }),
    ).not.toBeInTheDocument();
  });

  it("keeps operator edits when the duty link is removed", async () => {
    tasks = [DEWORM_ANIMAL_TASK];
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getByLabelText(/Linked duty/), /Deworm — Levamisole/);
    expect(within(dialog).getByLabelText(/product name/i)).toHaveValue("Levamisole");
    expect(within(dialog).getByLabelText(/disease target/i)).toHaveValue("Deworming");

    fireEvent.change(within(dialog).getByLabelText(/product name/i), {
      target: { value: "Custom mix" },
    });
    fireEvent.change(within(dialog).getByLabelText(/disease target/i), {
      target: { value: "Custom bug" },
    });
    await pickOption(user, within(dialog).getByLabelText(/Linked duty/), "— none —");

    // Hand-typed text is a manual edit, not the duty's guess: it survives.
    expect(within(dialog).getByLabelText(/product name/i)).toHaveValue("Custom mix");
    expect(within(dialog).getByLabelText(/disease target/i)).toHaveValue("Custom bug");

    await user.click(within(dialog).getByRole("button", { name: "Save event" }));
    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({ product_name: "Custom mix", disease_target: "Custom bug" });
  });

  it("keeps a linked duty when the already-selected animal is re-confirmed", async () => {
    tasks = [DEWORM_ANIMAL_TASK];
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getByLabelText(/Linked duty/), /Deworm — Levamisole/);

    const animalPicker = within(dialog).getByRole("combobox", { name: "Animal *" });
    await pickOption(user, animalPicker, /G-003 · Kaveri/);

    expect(within(dialog).getByLabelText(/Linked duty/)).toHaveTextContent(
      "Deworm — Levamisole",
    );
    expect(within(dialog).getByLabelText(/product name/i)).toHaveValue("Levamisole");
  });

  it("keeps a linked batch duty when the already-selected batch is re-confirmed", async () => {
    tasks = [DEWORM_BATCH_TASK];
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getByLabelText(/Linked duty/), /Deworm batch #2/);
    await waitFor(() =>
      expect(within(dialog).getByRole("combobox", { name: "Purchase batch *" })).toHaveTextContent(
        "Batch #2",
      ),
    );

    await pickOption(
      user,
      within(dialog).getByRole("combobox", { name: "Purchase batch *" }),
      /Batch #2/,
    );

    expect(within(dialog).getByLabelText(/Linked duty/)).toHaveTextContent("Deworm batch #2");
  });

  // ---------- deep-link signature re-hydration ----------

  it("re-hydrates a deep link that changes only its task", async () => {
    tasks = [VACCINE_TASK, FOLLOWUP_TASK];
    window.history.replaceState({}, "", "/health?task_id=5");
    const view = renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog");
    await waitFor(() =>
      expect(within(dialog).getByLabelText(/Linked duty/)).toHaveTextContent("PPR vaccination"),
    );

    window.history.replaceState({}, "", "/health?task_id=6");
    view.rerender(<HealthPage />);

    await waitFor(() =>
      expect(within(dialog).getByLabelText(/Linked duty/)).toHaveTextContent(
        "Follow-up PPR vaccination",
      ),
    );
    // The new duty's target prefill replaces the old one.
    await waitFor(() =>
      expect(within(dialog).getByRole("combobox", { name: "Animal *" })).toHaveTextContent(
        "G-004",
      ),
    );
  });

  it("re-hydrates a deep link that changes only its animal", async () => {
    window.history.replaceState({}, "", "/health?animal_id=3");
    const view = renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog");
    await waitFor(() =>
      expect(within(dialog).getByRole("combobox", { name: "Animal *" })).toHaveTextContent(
        "G-003",
      ),
    );

    window.history.replaceState({}, "", "/health?animal_id=4");
    view.rerender(<HealthPage />);

    await waitFor(() =>
      expect(within(dialog).getByRole("combobox", { name: "Animal *" })).toHaveTextContent(
        "G-004",
      ),
    );
  });

  it("re-hydrates a deep link that changes only its purchase batch", async () => {
    window.history.replaceState({}, "", "/health?purchase_batch_id=2");
    const view = renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog");
    await waitFor(() =>
      expect(
        within(dialog).getByRole("combobox", { name: "Purchase batch *" }),
      ).toHaveTextContent("Batch #2"),
    );

    window.history.replaceState({}, "", "/health?purchase_batch_id=3");
    view.rerender(<HealthPage />);

    await waitFor(() =>
      expect(
        within(dialog).getByRole("combobox", { name: "Purchase batch *" }),
      ).toHaveTextContent("Batch #3"),
    );
  });

  it("warns when the linked duty is not due yet, and stops once unlinked", async () => {
    const futureDuty = makeTask({
      id: 9,
      title: "Pre-kidding ET+TT vaccine",
      category: "VACCINE",
      animal_id: 3,
      due_date: addDays(TODAY, 5),
    });
    server.use(http.get("/api/tasks/9", () => HttpResponse.json(futureDuty)));
    window.history.replaceState({}, "", "/health?task_id=9");

    renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText(/Duty #9 is not due until/)).toBeInTheDocument();

    const user = userEvent.setup();
    await pickOption(user, within(dialog).getByLabelText(/Linked duty/), "— none —");
    await waitFor(() =>
      expect(within(dialog).queryByText(/is not due until/)).not.toBeInTheDocument(),
    );
  });

  // ---------- permissions dead end ----------

  it("retries the permission fetch from the permissions dead end", async () => {
    let attempts = 0;
    server.use(
      http.get("/api/auth/permissions", () => {
        attempts += 1;
        return attempts === 1
          ? HttpResponse.json({ detail: "permissions unavailable" }, { status: 503 })
          : HttpResponse.json({ is_owner: true, permissions: ["health.view", "health.manage"] });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<HealthPage />);

    expect(
      await screen.findByText("Could not load your permissions — refresh the page to try again."),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry permissions" }));

    expect(await screen.findByText("Event log")).toBeInTheDocument();
    expect(attempts).toBeGreaterThanOrEqual(2);
  });

  // ---------- empty state CTA ----------

  it("gates and styles the empty-state record CTA on health.manage", async () => {
    events = [];
    await renderLoaded();
    expect(await screen.findByText("No health events recorded yet.")).toBeInTheDocument();

    const cta = screen.getByRole("link", { name: "Add health event" });
    expect(cta).toHaveAttribute("href", "/health/new");
    expect(cta).toHaveClass("text-[0.8rem]", "px-3");
    expect(cta).not.toHaveClass("px-3.5");
  });

  it("withholds the empty-state record CTA without health.manage", async () => {
    events = [];
    server.use(permissionsHandler(["health.view"]));
    await renderLoaded();
    expect(await screen.findByText("No health events recorded yet.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Add health event" })).not.toBeInTheDocument();
  });

  // ---------- payload construction ----------

  it("nulls the other target ids on a bucket-scoped write", async () => {
    server.use(
      http.post("/api/health/events", async ({ request }) => {
        postBody = (await request.json()) as Record<string, unknown>;
        // A non-201 success must not be counted as recorded animals.
        return HttpResponse.json([makeEvent({ id: 201 }), makeEvent({ id: 202 })], {
          status: 200,
        });
      }),
    );
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    await pickOption(user, within(dialog).getByRole("combobox", { name: "Bucket *" }), "Breeding");
    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));
    await user.click(
      await within(dialog).findByRole("button", { name: "Confirm for 2 animals" }),
    );

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody!.animal_id).toBeNull();
    expect(postBody!.purchase_batch_id).toBeNull();
    expect(postBody!.bucket).toBe("BREEDING");
    // Only a 201 answer is a recorded herd: anything else reports zero.
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("Health event recorded for 0 animals."),
    );
  });

  it("nulls the bucket and batch ids on an animal-scoped write", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], /G-003 · Kaveri/);
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody!.animal_id).toBe(3);
    expect(postBody!.bucket).toBeNull();
    expect(postBody!.purchase_batch_id).toBeNull();
  });
});
