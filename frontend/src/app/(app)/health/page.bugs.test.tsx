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

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { AnimalOut, TaskOut } from "@/api/generated/models";
import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";

import HealthPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/health",
  useSearchParams: () => new URLSearchParams(),
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

function localISO(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}
const TODAY = localISO(new Date());

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
  completed_by_id: null,
  completed_at: null,
  verified_by_id: null,
  verified_at: null,
  verification_note: null,
  action_url: null,
};

const ANIMAL_LABEL = "G-003 · Kaveri — LACTATING";

async function pickOption(user: User, trigger: HTMLElement, name: string | RegExp) {
  await user.click(trigger);
  await user.click(await screen.findByRole("option", { name }));
}

describe("HealthPage prefill display (regression: labels, not raw values)", () => {
  beforeEach(() => {
    server.use(
      http.get("/api/health/events", () => HttpResponse.json([])),
      http.get("/api/animals", () =>
        HttpResponse.json({ animals: [ANIMAL], total: 1 }),
      ),
      http.get("/api/purchases", () => HttpResponse.json([])),
      http.get("/api/tasks", () =>
        HttpResponse.json({
          today: [DUTY],
          overdue: [],
          upcoming: [],
          awaiting: [],
          completed: [],
        }),
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
    await user.click(screen.getByRole("button", { name: "+ Add event" }));
    const dialog = await screen.findByRole("dialog");

    const combos = within(dialog).getAllByRole("combobox");
    await pickOption(user, combos[combos.length - 1], /Deworm Kaveri/);

    const animalTrigger = within(dialog).getAllByRole("combobox")[0];
    expect(animalTrigger).toHaveTextContent(ANIMAL_LABEL);
    expect(animalTrigger).not.toHaveTextContent(/^3$/);
  });

  it("the label persists after the dropdown has been opened and closed", async () => {
    const user = userEvent.setup();
    renderWithProviders(<HealthPage />);
    await screen.findByText("Event log");
    await user.click(screen.getByRole("button", { name: "+ Add event" }));
    const dialog = await screen.findByRole("dialog");

    const combos = within(dialog).getAllByRole("combobox");
    await pickOption(user, combos[combos.length - 1], /Deworm Kaveri/);

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
    const combos = within(dialog).getAllByRole("combobox");
    expect(combos[combos.length - 1]).toHaveTextContent(/Deworm Kaveri/);
  });
});
