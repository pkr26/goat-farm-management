/**
 * Health page — request windows, query gating and value→label maps.
 *
 * Complements page.extended.test.tsx by pinning the parts of the Add-event
 * dialog that only show up on the wire or in a *closed* control: the paged
 * event-log request, the exact-duty lookup that must stay disabled without a
 * deep link, the once-fetched seeded-programme list, the route/bucket
 * trigger labels, the seeded-vs-free-text schedule split, and the values a
 * removed duty link has to hand back.
 */

import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { AnimalOut, HealthEventOut, TaskOut, AnimalOutCurrentBucket} from "@/api/generated/models";
import { SCHEDULE_TEMPLATES, server } from "@/test/msw-server";
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
    current_bucket: "LACTATING" as AnimalOutCurrentBucket,
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

describe("HealthPage wire contracts and closed-control labels", () => {
  let eventsCalls: number;
  let eventQueries: string[];
  let postBody: Record<string, unknown> | null;
  let previewBodies: Record<string, unknown>[];
  let events: HealthEventOut[];
  let eventTotal: number;
  let tasks: TaskOut[];

  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
    eventsCalls = 0;
    eventQueries = [];
    postBody = null;
    previewBodies = [];
    events = [makeEvent({ id: 1 })];
    eventTotal = 1;
    tasks = [VACCINE_TASK];
    server.use(
      http.get("/api/health/events", ({ request }) => {
        eventsCalls += 1;
        const url = new URL(request.url);
        eventQueries.push(url.search);
        return HttpResponse.json({
          events,
          total: eventTotal,
          limit: 50,
          offset: Number(url.searchParams.get("offset") ?? 0),
        });
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
    await within(screen.getByRole("table")).findByText("PPR vaccine");
  }

  async function openDialog() {
    const user = userEvent.setup();
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Add event" }));
    return { user, dialog: await screen.findByRole("dialog") };
  }

  // ---------- event-log request window ----------

  it("asks for one 50-row event window and re-asks at the next offset", async () => {
    eventTotal = 120;
    const user = userEvent.setup();
    // The page turn now lives in the URL. The mocked router does not commit
    // search params, so the test plays the browser's part: assert the write,
    // then land the URL it describes and re-render, exactly as a real
    // useSearchParams commit would.
    const { rerender } = renderWithProviders(<HealthPage />);
    await screen.findByText("Event log");
    await within(screen.getByRole("table")).findByText("PPR vaccine");

    await waitFor(() => expect(eventQueries).toEqual(["?limit=50&offset=0"]));
    expect(screen.getByText("Showing 1–50 of 120 health events")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Next" }));

    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith("/health?offset=50", { scroll: false }),
    );
    window.history.replaceState({}, "", "/health?offset=50");
    rerender(<HealthPage />);

    await waitFor(() =>
      expect(eventQueries).toEqual(["?limit=50&offset=0", "?limit=50&offset=50"]),
    );
    expect(await screen.findByText("Showing 51–100 of 120 health events")).toBeInTheDocument();
  });

  // ---------- closed-trigger labels ----------

  it("labels the route sentinel and every route it offers", async () => {
    const { user, dialog } = await openDialog();
    const routeTrigger = within(dialog).getByLabelText("Route");
    // The closed trigger resolves its label from the root `items` map.
    expect(routeTrigger).toHaveTextContent("—");

    await user.click(routeTrigger);
    expect((await screen.findAllByRole("option")).map((option) => option.textContent)).toEqual([
      "—",
      "SC",
      "Oral",
      "IM",
    ]);

    await user.click(screen.getByRole("option", { name: "Oral" }));
    expect(routeTrigger).toHaveTextContent("Oral");

    await pickOption(user, within(dialog).getByRole("combobox", { name: "Animal *" }), /G-003/);
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));
    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({ route: "Oral", animal_id: 3 });
  });

  it("opens with no target preselected on either target scope", async () => {
    const { user, dialog } = await openDialog();
    expect(within(dialog).getByRole("combobox", { name: "Animal *" })).toHaveTextContent(
      "Pick an animal",
    );

    await user.click(within(dialog).getByRole("radio", { name: "Purchase batch" }));
    expect(
      within(dialog).getByRole("combobox", { name: "Purchase batch *" }),
    ).toHaveTextContent("Pick a batch");
  });

  it("shows the picked bucket in its closed trigger and reviews that bucket", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    const bucketTrigger = within(dialog).getByRole("combobox", { name: "Bucket *" });
    expect(bucketTrigger).toHaveTextContent("Pick a bucket");

    await pickOption(user, bucketTrigger, "Breeding");
    expect(bucketTrigger).toHaveTextContent("Breeding");

    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));
    expect(await within(dialog).findByRole("status")).toHaveTextContent(
      "Reviewed target snapshot: 2 active animals",
    );
    expect(previewBodies).toEqual([{ scope: "bucket", bucket: "BREEDING" }]);
  });

  // ---------- event types ----------

  it.each([
    // [visible label, API wire code] — the select shows humanized labels now.
    ["Treatment", "TREATMENT"],
    ["Foot bath", "FOOTBATH"],
    ["Vitamin / supplement", "VITAMIN"],
  ])("records a %s event under its own API type", async (label, apiType) => {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getByRole("combobox", { name: "Animal *" }), /G-003/);
    await pickOption(user, within(dialog).getByLabelText("Type"), label);
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({ type: apiType, animal_id: 3 });
  });

  // ---------- deep-link dismissal ----------

  it.each(["task_id=5", "animal_id=3", "purchase_batch_id=2"])(
    "drops the ?%s deep link from the URL when the dialog is dismissed",
    async (query) => {
      window.history.replaceState({}, "", `/health?${query}`);
      const user = userEvent.setup();
      renderWithProviders(<HealthPage />);
      const dialog = await screen.findByRole("dialog");

      await user.click(within(dialog).getByRole("button", { name: "Close" }));

      await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/health"));
      expect(pushMock).not.toHaveBeenCalled();
    },
  );

  it("leaves the URL alone when a hand-opened dialog is dismissed", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("button", { name: "Close" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(replaceMock).not.toHaveBeenCalled();
    expect(pushMock).not.toHaveBeenCalled();
  });

  // ---------- linked-duty queries ----------

  it("never resolves a duty by id while no duty is deep-linked", async () => {
    const exactLookups: string[] = [];
    server.use(
      http.get("/api/tasks/:taskId", ({ params }) => {
        exactLookups.push(String(params.taskId));
        return HttpResponse.json({ detail: "Task not found" }, { status: 404 });
      }),
    );
    const { dialog } = await openDialog();

    await within(dialog).findByLabelText("Linked duty (completes it)");
    expect(exactLookups).toEqual([]);
  });

  it("hides the linked-duty picker while the duty list is unavailable", async () => {
    server.use(
      http.get("/api/tasks", () =>
        HttpResponse.json({ detail: "duties unavailable" }, { status: 503 }),
      ),
    );
    const { dialog } = await openDialog();

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("duties unavailable");
    expect(
      within(dialog).queryByLabelText("Linked duty (completes it)"),
    ).not.toBeInTheDocument();
  });

  it("links a deep-linked deworming duty from outside the bounded duty tabs", async () => {
    const outsideTabs = makeTask({
      id: 812,
      title: "Deworm G-003 — Fenbendazole oral",
      category: "DEWORMING",
      animal_id: 3,
    });
    window.history.replaceState({}, "", "/health?task_id=812");
    server.use(http.get("/api/tasks/812", () => HttpResponse.json(outsideTabs)));
    renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog");

    expect(await within(dialog).findByLabelText(/Linked duty/)).toHaveTextContent(
      "Deworm G-003 — Fenbendazole oral",
    );
    expect(within(dialog).queryByText(/Could not link duty/)).not.toBeInTheDocument();
    expect(within(dialog).getByLabelText("Type")).toHaveTextContent("Deworming");

    await userEvent.setup().click(within(dialog).getByRole("button", { name: "Save event" }));
    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({
      task_id: 812,
      type: "DEWORMING",
      animal_id: 3,
      product_name: "Fenbendazole oral",
      disease_target: "Deworming",
    });
  });

  // ---------- duty prefill ownership ----------

  it("keeps hand-typed product and disease text when a duty is linked", async () => {
    tasks = [
      makeTask({
        id: 12,
        title: "Deworm herd — Albendazole",
        category: "DEWORMING",
        animal_id: 3,
      }),
    ];
    const { user, dialog } = await openDialog();
    fireEvent.change(within(dialog).getByLabelText(/product name/i), {
      target: { value: "House mix" },
    });
    fireEvent.change(within(dialog).getByLabelText(/disease target/i), {
      target: { value: "Roundworm" },
    });

    await pickOption(user, within(dialog).getByLabelText(/Linked duty/), /Deworm herd/);

    expect(within(dialog).getByLabelText(/product name/i)).toHaveValue("House mix");
    expect(within(dialog).getByLabelText(/disease target/i)).toHaveValue("Roundworm");

    await user.click(within(dialog).getByRole("button", { name: "Save event" }));
    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({
      product_name: "House mix",
      disease_target: "Roundworm",
      task_id: 12,
      type: "DEWORMING",
    });
  });

  it("hands back the product and disease fields when the duty link is removed", async () => {
    tasks = [
      makeTask({
        id: 13,
        title: "Deworm G-003 — Fenbendazole oral",
        category: "DEWORMING",
        animal_id: 3,
      }),
    ];
    const { user, dialog } = await openDialog();
    const dutySelect = within(dialog).getByLabelText(/Linked duty/);
    await pickOption(user, dutySelect, /Deworm G-003/);
    expect(within(dialog).getByLabelText(/product name/i)).toHaveValue("Fenbendazole oral");
    expect(within(dialog).getByLabelText(/disease target/i)).toHaveValue("Deworming");

    await pickOption(user, dutySelect, /none/);

    expect(within(dialog).getByLabelText(/product name/i)).toHaveValue("");
    expect(within(dialog).getByLabelText(/disease target/i)).toHaveValue("");
    expect(dutySelect).toHaveTextContent("— none —");
  });

  it("clears a failed save error when the linked duty is removed", async () => {
    server.use(
      http.post("/api/health/events", () =>
        HttpResponse.json({ detail: "duty already completed" }, { status: 409 }),
      ),
    );
    const { user, dialog } = await openDialog();
    const dutySelect = within(dialog).getByLabelText(/Linked duty/);
    await pickOption(user, dutySelect, /PPR vaccination/);
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));
    expect(await within(dialog).findByText(/duty already completed/)).toBeInTheDocument();

    await pickOption(user, dutySelect, /none/);

    expect(within(dialog).queryByText(/duty already completed/)).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Save event" })).toBeInTheDocument();
  });

  // ---------- seeded schedule programmes ----------

  it("offers the seeded deworming programmes once the type switches", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getByLabelText("Type"), "Deworming");
    await user.click(within(dialog).getByText("Advanced traceability & compliance"));

    await user.click(
      within(dialog).getByRole("combobox", { name: "Schedule/template name" }),
    );
    expect(await screen.findByRole("option", { name: /^Deworming/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /^FMD/ })).not.toBeInTheDocument();
    await user.keyboard("{Escape}");
  });

  it("disables the programme picker when the seeded list cannot be loaded", async () => {
    server.use(
      http.get("/api/health/schedule-templates", () =>
        HttpResponse.json({ detail: "reference data unavailable" }, { status: 500 }),
      ),
    );
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByText("Advanced traceability & compliance"));

    const programmePicker = within(dialog).getByLabelText("Schedule/template name");
    await waitFor(() => expect(programmePicker).toBeDisabled());
    expect(programmePicker).toHaveTextContent("Select a seeded programme");
  });

  it("refreshes farm data but not the static programme catalogue on reconnect", async () => {
    let templateCalls = 0;
    server.use(
      http.get("/api/health/schedule-templates", () => {
        templateCalls += 1;
        return HttpResponse.json({ templates: SCHEDULE_TEMPLATES });
      }),
    );
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByText("Advanced traceability & compliance"));
    await waitFor(() =>
      expect(within(dialog).getByLabelText("Schedule/template name")).toBeEnabled(),
    );
    const fetchedProgrammes = templateCalls;
    const loadedEventPages = eventsCalls;

    act(() => {
      window.dispatchEvent(new Event("offline"));
      window.dispatchEvent(new Event("online"));
    });

    // The event log is farm data and comes back stale from a dropped
    // connection; the seeded programme names are global reference data.
    await waitFor(() => expect(eventsCalls).toBe(loadedEventPages + 1));
    expect(templateCalls).toBe(fetchedProgrammes);
  });
});
