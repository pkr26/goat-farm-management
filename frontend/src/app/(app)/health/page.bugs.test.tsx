// REGRESSION TESTS — fixed bug: raw value shown in closed Select triggers.
//
// Health page (`src/app/(app)/health/page.tsx`): when the Add-event dialog is
// prefilled programmatically — via the "Linked duty" select (applyTask) or the
// /health/new?task_id=…&animal_id=… URL prefill — the animal Select trigger
// used to display the raw id ("3") instead of the animal's tag/name label
// ("G-003 · Kaveri — LACTATING"), and the linked-duty trigger showed the raw
// task id. Base UI's `Select.Value` only resolves labels from the root
// `items` prop; the page now passes value → label maps, so these tests assert
// the corrected display.

import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { AnimalOut, TaskOut, AnimalOutCurrentBucket} from "@/api/generated/models";
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

const ANIMAL: AnimalOut = {
  id: 3,
  tag_number: "G-003",
  name: "Kaveri",
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
};

const DUTY: TaskOut = {
  id: 8,
  title: "Deworm Kaveri",
  due_date: TODAY,
  status: "PENDING",
  category: "DEWORMING",
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
};

const ANIMAL_LABEL = "G-003 · Kaveri — LACTATING";

async function pickOption(user: User, trigger: HTMLElement, name: string | RegExp) {
  await user.click(trigger);
  await user.click(await screen.findByRole("option", { name }));
}

describe("HealthPage prefill display (regression: labels, not raw values)", () => {
  let healthAnimalQueries: Array<string | null>;

  beforeEach(() => {
    healthAnimalQueries = [];
    server.use(
      http.get("/api/health/events", () =>
        HttpResponse.json({ events: [], total: 0, limit: 50, offset: 0 }),
      ),
      http.get("/api/health/animals", ({ request }) => {
        healthAnimalQueries.push(new URL(request.url).searchParams.get("q"));
        return HttpResponse.json({
          animals: [
            {
              id: ANIMAL.id,
              tag_number: ANIMAL.tag_number,
              name: ANIMAL.name,
              current_bucket: ANIMAL.current_bucket,
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
        });
      }),
      http.get("/api/health/purchase-batches", () =>
        HttpResponse.json({ batches: [], total: 0, limit: 50, offset: 0 }),
      ),
      http.get("/api/tasks", () =>
        HttpResponse.json({
          today: [DUTY],
          overdue: [],
          upcoming: [],
          awaiting: [],
          completed: [],
          completed_total: 0,
          completed_limit: 50,
          completed_offset: 0,
        }),
      ),
      http.get("/api/tasks/:taskId", ({ params }) =>
        params.taskId === String(DUTY.id)
          ? HttpResponse.json(DUTY)
          : new HttpResponse(null, { status: 404 }),
      ),
    );
  });

  afterEach(() => {
    window.history.replaceState({}, "", "/health");
  });

  it("linked-duty prefill renders the animal's tag label in the trigger, not the raw id", async () => {
    const user = userEvent.setup();
    renderWithProviders(<HealthPage />);
    await screen.findByText("Event log");
    await user.click(screen.getByRole("button", { name: "Add event" }));
    const dialog = await screen.findByRole("dialog");

    const dutyTrigger = within(dialog).getByLabelText("Linked duty (completes it)");
    await pickOption(user, dutyTrigger, /Deworm Kaveri/);

    const animalTrigger = within(dialog).getAllByRole("combobox")[0];
    await waitFor(() => expect(animalTrigger).toHaveTextContent(ANIMAL_LABEL));
    expect(animalTrigger).not.toHaveTextContent(/^3$/);
  });

  it("the label persists after the dropdown has been opened and closed", async () => {
    const user = userEvent.setup();
    renderWithProviders(<HealthPage />);
    await screen.findByText("Event log");
    await user.click(screen.getByRole("button", { name: "Add event" }));
    const dialog = await screen.findByRole("dialog");

    const dutyTrigger = within(dialog).getByLabelText("Linked duty (completes it)");
    await pickOption(user, dutyTrigger, /Deworm Kaveri/);

    const animalTrigger = within(dialog).getAllByRole("combobox")[0];
    await user.click(animalTrigger);
    await screen.findByRole("option", { name: /G-003/ });
    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(
        screen.queryByRole("option", { name: /G-003/ }),
      ).not.toBeInTheDocument(),
    );
    expect(animalTrigger).toHaveTextContent(ANIMAL_LABEL);
  });

  it("URL prefill (?task_id&animal_id) renders labels in both triggers", async () => {
    window.history.replaceState({}, "", "/health?task_id=8&animal_id=3");
    renderWithProviders(<HealthPage />);
    const dialog = await screen.findByRole("dialog");

    await waitFor(() => {
      const combos = within(dialog).getAllByRole("combobox");
      expect(combos[0]).toHaveTextContent(ANIMAL_LABEL);
    });
    expect(within(dialog).getByLabelText("Linked duty (completes it)")).toHaveTextContent(
      /Deworm Kaveri/,
    );
    expect(healthAnimalQueries).toContain("#3");
  });
});

// REGRESSION TESTS — bug fixed; these tests pin the fix.
//
// The Add-event form's `date` default was captured once at page mount and
// react-hook-form's bare `reset()` restored that snapshot, so a tab left open
// across the farm's midnight recorded health events (and the withdrawal /
// next-due dates derived from them) one day early. Both reset call sites now
// pass freshly computed defaults.

describe("HealthPage date default survives a farm-midnight rollover", () => {
  beforeEach(() => {
    server.use(
      http.get("/api/health/events", () =>
        HttpResponse.json({ events: [], total: 0, limit: 50, offset: 0 }),
      ),
      http.get("/api/health/animals", () =>
        HttpResponse.json({ animals: [], total: 0, limit: 50, offset: 0 }),
      ),
      http.get("/api/health/purchase-batches", () =>
        HttpResponse.json({ batches: [], total: 0, limit: 50, offset: 0 }),
      ),
      http.get("/api/tasks", () =>
        HttpResponse.json({
          today: [],
          overdue: [],
          upcoming: [],
          awaiting: [],
          completed: [],
          completed_total: 0,
          completed_limit: 50,
          completed_offset: 0,
        }),
      ),
    );
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("prefills the farm's current date, not the date the tab was opened", async () => {
    vi.setSystemTime(new Date("2026-08-09T12:30:00Z")); // 18:00 IST
    const user = userEvent.setup();
    renderWithProviders(<HealthPage />);
    await screen.findByText("Event log");

    vi.setSystemTime(new Date("2026-08-10T12:30:00Z")); // 18:00 IST, next day
    await user.click(screen.getByRole("button", { name: "Add event" }));

    const dateInput = await screen.findByLabelText("Date (defaults to today)");
    expect(dateInput).toHaveValue("2026-08-10");
    expect(dateInput).toHaveAttribute("max", "2026-08-10");
  });
});

// REGRESSION TESTS — fixed bug: silent submit failure behind the collapsed
// Advanced section.
//
// Setting a Next due date (visible grid) makes schedule_template_name and
// next_due_authority mandatory, but both fields — and their error messages —
// live inside the collapsed "Advanced traceability & compliance" <details>.
// The section had no error-driven open state, and react-hook-form cannot
// focus an input the browser is not rendering, so clicking "Save event"
// blocked the submit with zero visible feedback. The section is now
// controlled: a failed submit with errors inside it forces it open, while
// the user's own toggles are still honored.

describe("HealthPage collapsed Advanced section validation errors", () => {
  beforeEach(() => {
    server.use(
      http.get("/api/health/events", () =>
        HttpResponse.json({ events: [], total: 0, limit: 50, offset: 0 }),
      ),
      http.get("/api/health/animals", () =>
        HttpResponse.json({ animals: [], total: 0, limit: 50, offset: 0 }),
      ),
      http.get("/api/health/purchase-batches", () =>
        HttpResponse.json({ batches: [], total: 0, limit: 50, offset: 0 }),
      ),
      http.get("/api/tasks", () =>
        HttpResponse.json({
          today: [],
          overdue: [],
          upcoming: [],
          awaiting: [],
          completed: [],
          completed_total: 0,
          completed_limit: 50,
          completed_offset: 0,
        }),
      ),
    );
  });

  async function openDialogWithNextDue(user: User) {
    renderWithProviders(<HealthPage />);
    await screen.findByText("Event log");
    await user.click(screen.getByRole("button", { name: "Add event" }));
    const dialog = await screen.findByRole("dialog");
    const details = within(dialog)
      .getByText("Advanced traceability & compliance")
      .closest("details");
    expect(details).not.toBeNull();
    fireEvent.change(within(dialog).getByLabelText("Next due date"), {
      target: { value: addDays(TODAY, 30) },
    });
    return { dialog, details: details as HTMLDetailsElement };
  }

  it("opens the section and reveals its errors when saving with only a next-due date", async () => {
    const user = userEvent.setup();
    const { dialog, details } = await openDialogWithNextDue(user);
    expect(details).not.toHaveAttribute("open");

    await user.click(within(dialog).getByRole("button", { name: "Save event" }));

    await waitFor(() => expect(details).toHaveAttribute("open"));
    expect(
      within(dialog).getByText("Name the schedule used for a next-due date"),
    ).toBeVisible();
    expect(
      within(dialog).getByText("Record the authority for this next-due date"),
    ).toBeVisible();
  });

  it("honors a manual re-collapse and re-opens on the next failed save", async () => {
    const user = userEvent.setup();
    const { dialog, details } = await openDialogWithNextDue(user);
    await user.click(within(dialog).getByRole("button", { name: "Save event" }));
    await waitFor(() => expect(details).toHaveAttribute("open"));

    // The user closes the section without fixing anything (jsdom's summary
    // has no activation behavior, so toggle the element the way the browser
    // would: flip `open`, which also fires the toggle event).
    act(() => {
      details.open = false;
    });
    await waitFor(() => expect(details).not.toHaveAttribute("open"));

    await user.click(within(dialog).getByRole("button", { name: "Save event" }));
    await waitFor(() => expect(details).toHaveAttribute("open"));
  });
});
