/**
 * Health event dialog DOM caps: every text input the schema bounds is capped in the
 * DOM at its exact limit (120/80/60), and the notes box is 2 rows. The schedule
 * free-text pair (template name, next-due authority) only renders for non-seeded
 * types with a next-due date, so that path is driven too.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/msw-server";
import { renderWithProviders } from "@/test/render";
import { addDays, farmToday } from "@/lib/format";

import HealthPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/health",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

beforeAll(() => {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => {};
  Element.prototype.releasePointerCapture = () => {};
  Element.prototype.scrollIntoView = () => {};
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (window as unknown as { ResizeObserver: typeof RO }).ResizeObserver = RO;
});

type User = ReturnType<typeof userEvent.setup>;

const TODAY = farmToday();
const ANIMAL_OPTIONS = [
  { id: 3, tag_number: "G-003", name: "Kaveri", current_bucket: "LACTATING" },
  { id: 4, tag_number: "G-004", name: null, current_bucket: "BREEDING" },
];

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

async function pickOption(user: User, trigger: HTMLElement, name: string | RegExp) {
  await user.click(trigger);
  await user.click(await screen.findByRole("option", { name }));
}

describe("HealthPage event dialog DOM caps", () => {
  beforeEach(() => {
    server.use(
      http.get("/api/health/events", () =>
        HttpResponse.json({ events: [], total: 0, limit: 50, offset: 0 }),
      ),
      http.post("/api/health/events", () => HttpResponse.json([], { status: 201 })),
      http.post("/api/health/events/preview", () =>
        HttpResponse.json({
          scope: "ALL_ANIMALS" as unknown,
          target_animal_ids: [3],
          target_animals: [{ id: 3, tag_number: "G-003", name: "Kaveri" }],
          target_count: 1,
          max_targets: 250,
        }),
      ),
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
    );
  });

  async function openDialog() {
    const user = userEvent.setup();
    renderWithProviders(<HealthPage />);
    await screen.findByText("Event log");
    await user.click(screen.getByRole("button", { name: "Add event" }));
    return { user, dialog: await screen.findByRole("dialog") };
  }

  it("caps the base and advanced fields at their schema limits", async () => {
    const { user, dialog } = await openDialog();

    expect(within(dialog).getByLabelText(/product name/i)).toHaveAttribute("maxlength", "120");
    expect(within(dialog).getByLabelText(/disease target/i)).toHaveAttribute("maxlength", "120");
    expect(within(dialog).getByLabelText(/^dose/i)).toHaveAttribute("maxlength", "60");
    expect(within(dialog).getByLabelText(/^vet/i)).toHaveAttribute("maxlength", "120");
    expect(within(dialog).getByLabelText(/^notes/i)).toHaveAttribute("rows", "2");

    await user.click(within(dialog).getByText("Advanced traceability & compliance"));
    expect(within(dialog).getByLabelText(/lot/i)).toHaveAttribute("maxlength", "120");
    expect(within(dialog).getByLabelText(/administered by/i)).toHaveAttribute("maxlength", "120");
    expect(within(dialog).getByLabelText(/certificate/i)).toHaveAttribute("maxlength", "120");
    expect(within(dialog).getByLabelText(/official tag/i)).toHaveAttribute("maxlength", "80");
  });

  it("caps the free-text schedule pair for a treatment with a next due date", async () => {
    const { user, dialog } = await openDialog();
    await pickOption(user, within(dialog).getByLabelText("Type"), "Treatment");
    // A next-due date is what surfaces the template/authority pair.
    const nextDue = within(dialog).getByLabelText(/next due date/i);
    await user.clear(nextDue);
    await user.type(nextDue, addDays(TODAY, 30));

    const template = await within(dialog).findByLabelText(/template/i);
    expect(template).toHaveAttribute("maxlength", "120");
    expect(within(dialog).getByLabelText(/authority/i)).toHaveAttribute("maxlength", "120");
  });
});
