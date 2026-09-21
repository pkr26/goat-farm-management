/**
 * Health record dialog 422 wiring: FastAPI validation issues surface as
 * inline field errors on their inputs (the aria wiring that already renders
 * client-side zod errors), while issues that match no form field degrade to
 * the banner + toast — and only those.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { toast } from "sonner";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { AnimalOut, HealthEventOut, TaskOut } from "@/api/generated/models";
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

function makeAnimal(overrides: Partial<AnimalOut>): AnimalOut {
  return {
    id: 3,
    tag_number: "G-003",
    name: "Kaveri",
    breed: "Osmanabadi",
    sex: "F",
    date_of_birth: null,
    estimated_dob: null,
    birth_type: null,
    source: "BORN",
    dam_id: null,
    sire_id: null,
    birth_weight: null,
    current_bucket: "BREEDING",
    status: "ACTIVE",
    status_notes: null,
    status_date: null,
    sale_price: null,
    sale_weight_kg: null,
    buyer_name: null,
    mortality_cause_code: null,
    disposal_method: null,
    necropsy_done: false,
    necropsy_findings: null,
    coat_color: null,
    horned: null,
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

const ANIMAL_A = makeAnimal({ id: 3, tag_number: "G-003", name: "Kaveri" });

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

describe("HealthPage 422 field mapping", () => {
  beforeEach(() => {
    pushMock.mockClear();
    replaceMock.mockClear();
    server.use(
      http.get("/api/health/events", () =>
        HttpResponse.json({
          events: [makeEvent({ id: 1 })],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get("/api/health/animals", () =>
        HttpResponse.json({
          animals: [ANIMAL_A].map((animal) => ({
            id: animal.id,
            tag_number: animal.tag_number,
            name: animal.name,
            current_bucket: animal.current_bucket,
            movement_restricted: animal.movement_restricted,
            restriction_version: animal.restriction_version,
          })),
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get("/api/tasks", () => HttpResponse.json(tasksPayload([makeTask({})]))),
    );
  });

  afterEach(() => {
    window.history.replaceState({}, "", "/health");
    vi.mocked(toast.error).mockClear();
  });

  async function openDialog() {
    const user = userEvent.setup();
    renderWithProviders(<HealthPage />);
    await screen.findByText("Event log");
    await user.click(screen.getByRole("button", { name: "Add event" }));
    return { user, dialog: await screen.findByRole("dialog") };
  }

  async function saveWith422(
    user: User,
    dialog: HTMLElement,
    detail: Array<{ loc: string[]; msg: string }>,
  ) {
    server.use(
      http.post("/api/health/events", () => HttpResponse.json({ detail }, { status: 422 })),
    );
    await pickOption(user, within(dialog).getAllByRole("combobox")[0], /G-003 · Kaveri/);
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));
  }

  it("maps matching 422 issues onto inline field errors and toasts only the remainder", async () => {
    const { user, dialog } = await openDialog();
    await saveWith422(user, dialog, [
      { loc: ["body", "dose"], msg: "Dose must be at most 60 characters." },
      { loc: ["body", "drifted_field"], msg: "Unknown to this dialog." },
    ]);

    // Field-level error renders through the existing aria wiring.
    const doseError = await within(dialog).findByText("Dose must be at most 60 characters.");
    expect(doseError).toHaveAttribute("id", "event-dose-error");
    expect(
      within(dialog).getByRole("textbox", { name: /^Dose/ }),
    ).toHaveAttribute("aria-invalid", "true");

    // Only the unmapped remainder reaches the inline banner — no toast: the
    // open dialog is the single failure surface (P3, 2026-09-20 audit).
    expect(toast.error).not.toHaveBeenCalled();
    // The banner carries the remainder plus its retry guidance.
    expect(
      await within(dialog).findByText(/Unknown to this dialog\./),
    ).toBeInTheDocument();

    // The dialog stays open so the operator can fix the flagged fields.
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("renders every mapped issue inline and stays quiet when all of them map", async () => {
    const { user, dialog } = await openDialog();
    await saveWith422(user, dialog, [
      { loc: ["body", "dose"], msg: "Dose must be at most 60 characters." },
      { loc: ["body", "vet_name"], msg: "Vet name is not recognised." },
    ]);

    await within(dialog).findByText("Dose must be at most 60 characters.");
    await within(dialog).findByText("Vet name is not recognised.");
    await waitFor(() =>
      expect(within(dialog).getByRole("textbox", { name: /^Vet/ })).toHaveAttribute(
        "aria-invalid",
        "true",
      ),
    );
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("keeps the historical banner for a 422 whose issues match no field", async () => {
    const { user, dialog } = await openDialog();
    await saveWith422(user, dialog, [
      { loc: ["body", "mystery"], msg: "Nothing maps here." },
    ]);

    // Nothing mapped, so the flattened server sentence becomes the inline
    // banner (inline only — the dialog is the single failure surface).
    expect(
      await within(dialog).findByText(/Nothing maps here\./),
    ).toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();
    expect(within(dialog).queryByText("Dose must be")).not.toBeInTheDocument();
  });
});
