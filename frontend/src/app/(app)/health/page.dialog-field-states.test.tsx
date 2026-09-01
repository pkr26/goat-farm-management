/**
 * Health page — the Add-event dialog's field states and its in-flight states.
 *
 * Covers the `aria-invalid` contract on every validated control (silent while
 * the operator has done nothing wrong, `true` on each offending field once a
 * submit is actually blocked), the reviewed-target snapshot when a bulk scope
 * resolves to no animals, the seeded-programme select (disabled when the type
 * has no programmes, labelled with the picked programme), the linked-duty
 * loading state, the statutory dates a cleared scheduled-disease box must drop,
 * and what the Save button says — and whether it is disabled — while a preview
 * or a write is in flight.
 */

import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { HealthEventOut } from "@/api/generated/models";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { addDays, farmToday } from "@/lib/format";

import HealthPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/health",
  useSearchParams: () => new URLSearchParams(window.location.search),
  useParams: () => ({}),
}));

type User = ReturnType<typeof userEvent.setup>;

beforeAll(() => {
  // jsdom lacks APIs the Select/Popper touch when opening the listbox.
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

const ANIMAL_OPTIONS = [
  { id: 3, tag_number: "G-003", name: "Kaveri", current_bucket: "LACTATING" },
  { id: 4, tag_number: "G-004", name: null, current_bucket: "BREEDING" },
];

function makeEvent(overrides: Partial<HealthEventOut> = {}): HealthEventOut {
  return {
    id: 1,
    animal_id: 3,
    purchase_batch_id: null,
    date: TODAY,
    type: "VACCINE",
    product_name: "PPR vaccine",
    disease_target: "PPR",
    dose: "1 ml",
    route: "SC",
    vet_name: "Dr. Patil",
    cost: 100,
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

const EMPTY_TASK_TABS = {
  today: [],
  overdue: [],
  upcoming: [],
  awaiting: [],
  completed: [],
  completed_total: 0,
  completed_limit: 50,
  completed_offset: 0,
};

/** Every dialog control that announces itself invalid from a field error. */
const VALIDATED_FIELD_LABELS = [
  "Date (defaults to today)",
  "Product name",
  "Disease target",
  "Dose",
  "Vet",
  "Total cost (₹, split evenly)",
  "Next due date",
  "Schedule/template name",
  "Next-due authority",
  "Product manufactured",
  "Product expires",
  "Vaccine valid until",
  "Withdrawal until",
  "Authority notified date",
  "Isolation started date",
  "Notes",
];

async function pickOption(user: User, trigger: HTMLElement, name: string | RegExp) {
  await user.click(trigger);
  await user.click(await screen.findByRole("option", { name }));
}

describe("HealthPage dialog field and in-flight states", () => {
  let postBody: Record<string, unknown> | null;
  let postCalls: number;
  let previewCount: number;

  beforeEach(() => {
    postBody = null;
    postCalls = 0;
    previewCount = 2;
    server.use(
      http.get("/api/health/events", () =>
        HttpResponse.json({ events: [], total: 0, limit: 50, offset: 0 }),
      ),
      http.post("/api/health/events", async ({ request }) => {
        postCalls += 1;
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json([makeEvent({ id: 99 })], { status: 201 });
      }),
      http.post("/api/health/events/preview", async ({ request }) => {
        const target = (await request.json()) as Record<string, unknown>;
        const animals = ANIMAL_OPTIONS.slice(0, previewCount).map((animal) => ({
          id: animal.id,
          tag_number: animal.tag_number,
          name: animal.name,
        }));
        return HttpResponse.json({
          scope: target.scope,
          bucket: target.bucket ?? null,
          purchase_batch_id: target.purchase_batch_id ?? null,
          task_id: target.task_id ?? null,
          target_animal_ids: animals.map((animal) => animal.id),
          target_animals: animals,
          target_count: animals.length,
          max_targets: 250,
        });
      }),
      http.get("/api/health/animals", () =>
        HttpResponse.json({
          animals: ANIMAL_OPTIONS,
          total: ANIMAL_OPTIONS.length,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get("/api/health/purchase-batches", () =>
        HttpResponse.json({ batches: [], total: 0, limit: 50, offset: 0 }),
      ),
      http.get("/api/tasks", () => HttpResponse.json(EMPTY_TASK_TABS)),
      http.get("/api/tasks/:taskId", () =>
        HttpResponse.json({ detail: "Task not found" }, { status: 404 }),
      ),
    );
  });

  async function openDialog() {
    const user = userEvent.setup();
    renderWithProviders(<HealthPage />);
    await screen.findByText("Event log");
    await user.click(screen.getByRole("button", { name: "Add event" }));
    return { user, dialog: await screen.findByRole("dialog") };
  }

  async function openAdvanced(user: User, dialog: HTMLElement) {
    await user.click(within(dialog).getByText("Advanced traceability & compliance"));
  }

  // ---------- aria-invalid contract ----------

  it("announces no dialog field as invalid before a submit has failed", async () => {
    const { user, dialog } = await openDialog();
    await openAdvanced(user, dialog);
    // The two statutory dates only exist once the disease box is ticked.
    await user.click(
      within(dialog).getByRole("checkbox", {
        name: /Suspected scheduled\/notifiable disease/,
      }),
    );

    for (const label of VALIDATED_FIELD_LABELS) {
      // Not `aria-invalid="false"` either: the attribute must be absent, or
      // every field is announced with a validity state it never earned.
      expect(within(dialog).getByLabelText(label)).not.toHaveAttribute("aria-invalid");
    }
  });

  it("marks every offending field invalid and names the rule it broke", async () => {
    const { user, dialog } = await openDialog();
    await openAdvanced(user, dialog);
    // TREATMENT takes a free-text schedule name (VACCINE/DEWORMING take a
    // seeded programme from a select), so this exercises the text input.
    await pickOption(user, within(dialog).getByLabelText("Type"), "TREATMENT");
    const schedule = within(dialog).getByLabelText("Schedule/template name");
    expect(schedule.tagName).toBe("INPUT");
    expect(schedule).not.toHaveAttribute("aria-invalid");

    await user.click(
      within(dialog).getByRole("checkbox", {
        name: /Suspected scheduled\/notifiable disease/,
      }),
    );
    const change = (label: string, value: string) =>
      fireEvent.change(within(dialog).getByLabelText(label), { target: { value } });
    change("Date (defaults to today)", TOMORROW);
    change("Product name", "p".repeat(121));
    change("Dose", "d".repeat(61));
    change("Vet", "v".repeat(121));
    change("Total cost (₹, split evenly)", "not-a-number");
    change("Next due date", TODAY);
    change("Product manufactured", TOMORROW);
    change("Product expires", TODAY);
    change("Vaccine valid until", TODAY);
    change("Withdrawal until", TODAY);
    change("Authority notified date", TOMORROW);
    change("Isolation started date", TOMORROW);
    change("Notes", "n".repeat(4001));

    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() =>
      expect(within(dialog).getByLabelText("Date (defaults to today)")).toHaveAttribute(
        "aria-invalid",
        "true",
      ),
    );
    for (const label of VALIDATED_FIELD_LABELS) {
      expect(within(dialog).getByLabelText(label)).toHaveAttribute("aria-invalid", "true");
    }
    expect(within(dialog).getByText("Cost must be a number ≥ 0")).toBeInTheDocument();
    expect(
      within(dialog).getByText("Next due date must be after the event date"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText("Name the schedule used for a next-due date"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText("Record the authority for this next-due date"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText("Manufacture date cannot be in the future"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText("Expiry cannot be before manufacture date"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText("Vaccine validity cannot be before the event date"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText("Withdrawal date cannot be before the event date"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText("Name the suspected scheduled disease"),
    ).toBeInTheDocument();
    // Event date, authority-notified date and isolation date all reject a
    // future value.
    expect(within(dialog).getAllByText("Date cannot be in the future")).toHaveLength(3);
    expect(within(dialog).getByText("Notes cannot exceed 4000 characters")).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("marks the seeded programme select invalid when a next-due date has no schedule", async () => {
    const { user, dialog } = await openDialog();
    await openAdvanced(user, dialog);
    // Type stays VACCINE, so the schedule field is the seeded-programme select.
    const schedule = within(dialog).getByLabelText("Schedule/template name");
    expect(schedule.tagName).toBe("BUTTON");
    expect(schedule).not.toHaveAttribute("aria-invalid");

    fireEvent.change(within(dialog).getByLabelText("Next due date"), {
      target: { value: addDays(TODAY, 30) },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() => expect(schedule).toHaveAttribute("aria-invalid", "true"));
    expect(schedule).toHaveAttribute("aria-describedby", "schedule-template-error");
    expect(postCalls).toBe(0);
  });

  // ---------- reviewed bulk target snapshot ----------

  it("says a reviewed bucket is empty instead of rendering an empty animal list", async () => {
    previewCount = 0;
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    await pickOption(user, within(dialog).getByLabelText("Bucket *"), "Breeding");
    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));

    const snapshot = await within(dialog).findByRole("status");
    expect(snapshot).toHaveTextContent("Reviewed target snapshot: 0 active animals");
    expect(
      within(dialog).getByText("No active animals are in this reviewed target."),
    ).toBeInTheDocument();
    expect(
      within(dialog).queryByRole("list", { name: "Reviewed target animals" }),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).getByText("No active animal IDs were returned."),
    ).toBeInTheDocument();
  });

  it("counts a single reviewed animal in the singular on the confirm button", async () => {
    previewCount = 1;
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    await pickOption(user, within(dialog).getByLabelText("Bucket *"), "Recovery");
    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));

    expect(
      await within(dialog).findByRole("button", { name: "Confirm for 1 animal" }),
    ).toBeInTheDocument();
    const reviewed = within(dialog).getByRole("list", { name: "Reviewed target animals" });
    expect(within(reviewed).getAllByRole("listitem")).toHaveLength(1);
    expect(within(dialog).getByText("3")).toBeInTheDocument();
  });

  // ---------- select display states ----------

  it("shows an em dash for an unset route and the picked route afterwards", async () => {
    const { user, dialog } = await openDialog();
    const route = within(dialog).getByLabelText("Route");
    expect(route).toHaveTextContent("—");

    await pickOption(user, route, "IM");

    await waitFor(() => expect(route).toHaveTextContent("IM"));
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], /G-003 · Kaveri/);
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));
    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({ route: "IM" });
  });

  it("disables the programme select when the event type has no seeded programme", async () => {
    server.use(
      http.get("/api/health/schedule-templates", () =>
        HttpResponse.json({ templates: [] }),
      ),
    );
    const { user, dialog } = await openDialog();
    await openAdvanced(user, dialog);

    const schedule = within(dialog).getByLabelText("Schedule/template name");
    await waitFor(() => expect(schedule).toHaveTextContent("Select a seeded programme"));
    expect(schedule).toBeDisabled();
  });

  it("keeps the picked programme's name in the closed trigger and posts it", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], /G-003 · Kaveri/);
    await openAdvanced(user, dialog);
    const schedule = within(dialog).getByLabelText("Schedule/template name");
    await pickOption(user, schedule, /^FMD/);

    await waitFor(() => expect(schedule).toHaveTextContent("FMD"));
    expect(schedule).not.toHaveTextContent("Select a seeded programme");

    await user.click(within(dialog).getByRole("button", { name: "Save event" }));
    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({ schedule_template_name: "FMD" });
  });

  // ---------- linked duty loading ----------

  it("announces the linked-duty lookup while it is in flight and stops once it lands", async () => {
    let releaseTasks!: () => void;
    const tasksGate = new Promise<void>((resolve) => {
      releaseTasks = resolve;
    });
    server.use(
      http.get("/api/tasks", async () => {
        await tasksGate;
        return HttpResponse.json({
          ...EMPTY_TASK_TABS,
          today: [
            {
              id: 5,
              title: "PPR vaccination",
              due_date: TODAY,
              status: "PENDING",
              category: "VACCINE",
              auto_generated: true,
              animal_id: 3,
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
            },
          ],
        });
      }),
    );
    const { dialog } = await openDialog();

    expect(await within(dialog).findByText("Loading linked duties…")).toBeInTheDocument();

    releaseTasks();

    expect(
      await within(dialog).findByLabelText("Linked duty (completes it)"),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText("Loading linked duties…")).not.toBeInTheDocument();
  });

  // ---------- scheduled-disease hold ----------

  it("drops the statutory dates it hides when the disease box is cleared", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], /G-003 · Kaveri/);
    await openAdvanced(user, dialog);
    const checkbox = within(dialog).getByRole("checkbox", {
      name: /Suspected scheduled\/notifiable disease/,
    });
    await user.click(checkbox);
    // Dates that would fail validation if they survived the box being cleared —
    // and their errors render nowhere, so the save would stall in silence.
    fireEvent.change(within(dialog).getByLabelText("Authority notified date"), {
      target: { value: TOMORROW },
    });
    fireEvent.change(within(dialog).getByLabelText("Isolation started date"), {
      target: { value: TOMORROW },
    });
    await user.click(checkbox);

    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() => expect(postBody).not.toBeNull());
    expect(postBody).toMatchObject({
      suspected_scheduled_disease: false,
      authority_notified_at: null,
      isolation_started_at: null,
    });
  });

  // ---------- submit button states ----------

  it("disables Save and says it is reviewing while the bulk preview is in flight", async () => {
    let releasePreview!: () => void;
    const previewGate = new Promise<void>((resolve) => {
      releasePreview = resolve;
    });
    server.use(
      http.post("/api/health/events/preview", async ({ request }) => {
        const target = (await request.json()) as Record<string, unknown>;
        await previewGate;
        return HttpResponse.json({
          scope: target.scope,
          bucket: target.bucket ?? null,
          purchase_batch_id: null,
          task_id: null,
          target_animal_ids: [3, 4],
          target_animals: ANIMAL_OPTIONS.map((animal) => ({
            id: animal.id,
            tag_number: animal.tag_number,
            name: animal.name,
          })),
          target_count: 2,
          max_targets: 250,
        });
      }),
    );
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("radio", { name: "Whole bucket" }));
    await pickOption(user, within(dialog).getByLabelText("Bucket *"), "Breeding");
    await user.click(within(dialog).getByRole("button", { name: "Review target animals" }));

    const reviewing = await within(dialog).findByRole("button", {
      name: "Reviewing targets…",
    });
    expect(reviewing).toBeDisabled();

    releasePreview();

    expect(
      await within(dialog).findByRole("button", { name: "Confirm for 2 animals" }),
    ).toBeEnabled();
  });

  it("keeps Save disabled and saying 'Saving…' after a duplicate submit", async () => {
    let releaseRecord!: () => void;
    const recordGate = new Promise<void>((resolve) => {
      releaseRecord = resolve;
    });
    server.use(
      http.post("/api/health/events", async ({ request }) => {
        postCalls += 1;
        postBody = (await request.json()) as Record<string, unknown>;
        await recordGate;
        return HttpResponse.json([makeEvent({ id: 99 })], { status: 201 });
      }),
    );
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], /G-003 · Kaveri/);
    const form = within(dialog).getByRole("button", { name: "Save event" }).closest("form")!;

    // The second submit settles immediately (the single-flight guard drops it)
    // and clears react-hook-form's isSubmitting, so only the single-flight
    // pending flag still holds the button while the first write runs.
    fireEvent.submit(form);
    fireEvent.submit(form);
    await waitFor(() => expect(postCalls).toBe(1));
    await act(async () => {
      await Promise.resolve();
    });

    const save = within(dialog).getByRole("button", { name: "Saving…" });
    expect(save).toHaveAttribute("disabled");

    releaseRecord();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(postCalls).toBe(1);
  });
});
